"""LLM-as-judge evaluators (§56) — quality scoring that needs a model.

For subjective properties (`correctness` vs a reference, `relevance`,
`faithfulness` to retrieved evidence) there is no deterministic metric; an
LLM grades the output against a rubric. `llm_judge` turns any `LLMProvider`
into an `Evaluator`, so a judge is just another model call — it composes with
the same `CachingLLM`/budget/metrics plumbing as the app's own calls, and is
trivially faked in tests (`FakeLLM(response='{"score": true, …}')`).

The judge is asked for strict JSON (`{"score": …, "comment": …}`); parsing is
tolerant (scans for the first JSON object, then falls back to a number/boolean
in the text) so a chatty model still scores instead of crashing the suite.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from ..providers.contracts import LLMProvider, LLMRequest, Message
from .evaluators import EvalInput, Evaluator, Feedback

JUDGE_SYSTEM = (
    "You are a strict, fair evaluator. Grade the OUTPUT against the rubric and "
    "reply with JSON only, no prose outside the JSON object."
)

_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

JudgeParser = Callable[[str], tuple[float, str]]

CORRECTNESS_INSTRUCTIONS = (
    "The REFERENCE OUTPUTS are the ground truth for the INPUTS. Score 1 if the "
    "OUTPUT is semantically equivalent to the reference (same facts/decision, "
    "wording may differ), 0 otherwise. Partial credit is not used."
)

RELEVANCE_INSTRUCTIONS = (
    "Score 1 if the OUTPUT directly and completely addresses the INPUTS, 0 if "
    "it is off-topic, evasive or ignores the request."
)

FAITHFULNESS_INSTRUCTIONS = (
    "Score 1 if every factual statement in the OUTPUT is supported by the "
    "provided CONTEXT, 0 if it contains claims the context does not support "
    "(hallucination)."
)


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _schema_note(continuous: bool, choices: Sequence[float] | None) -> str:
    if choices is not None:
        return (
            'Reply as JSON: {"score": <one of '
            f'{list(choices)}>, "comment": "<one sentence>"}}'
        )
    if continuous:
        return 'Reply as JSON: {"score": <float 0..1>, "comment": "<one sentence>"}'
    return 'Reply as JSON: {"score": <true|false>, "comment": "<one sentence>"}'


def _judge_messages(
    instructions: str,
    eval_input: EvalInput,
    *,
    include_reference: bool,
    continuous: bool,
    choices: Sequence[float] | None,
) -> list[Message]:
    blocks = [f"INPUTS:\n{_render(dict(eval_input.example.inputs))}"]
    blocks.append(f"OUTPUTS:\n{_render(dict(eval_input.outputs))}")
    if include_reference and eval_input.example.reference_outputs is not None:
        blocks.append(
            f"REFERENCE OUTPUTS:\n{_render(dict(eval_input.example.reference_outputs))}"
        )
    if eval_input.example.metadata:
        blocks.append(f"METADATA:\n{_render(dict(eval_input.example.metadata))}")
    user = (
        f"Rubric:\n{instructions}\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        + _schema_note(continuous, choices)
    )
    return [Message.system(JUDGE_SYSTEM), Message.user(user)]


def _coerce_score(
    raw: Any, text: str, *, continuous: bool, choices: Sequence[float] | None
) -> float:
    if isinstance(raw, bool):
        value = 1.0 if raw else 0.0
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        match = _NUMBER.search(text)
        if match is not None:
            value = float(match.group(0))
        else:
            lowered = text.lower()
            value = 1.0 if ("true" in lowered or "yes" in lowered) else 0.0
    if choices:
        return float(min(choices, key=lambda choice: abs(choice - value)))
    if not continuous:
        return 1.0 if value >= 0.5 else 0.0
    return max(0.0, min(1.0, value))


def parse_judgement(
    text: str, *, continuous: bool = False, choices: Sequence[float] | None = None
) -> tuple[float, str]:
    """Extracts `(score, comment)` from a judge's reply, tolerantly."""
    data: dict[str, Any] = {}
    match = _JSON_OBJECT.search(text)
    if match is not None:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = {}
    raw = data.get("score", data.get("value"))
    comment = str(data.get("comment", data.get("reason", "")) or "")
    return _coerce_score(raw, text, continuous=continuous, choices=choices), comment


def llm_judge(
    llm: LLMProvider,
    *,
    instructions: str,
    key: str = "score",
    model: str = "",
    continuous: bool = False,
    choices: Sequence[float] | None = None,
    include_reference: bool = False,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    parse: JudgeParser | None = None,
) -> Evaluator:
    """Builds an `Evaluator` that grades each example with `llm`.

    `instructions` is the rubric; `key` names the feedback. `continuous` yields
    a 0..1 float instead of true/false; `choices` snaps the score to one of a
    fixed set. `include_reference=True` formats the example's reference outputs
    into the prompt (reference-based grading, e.g. correctness); otherwise the
    judge is reference-free. `parse` overrides the default tolerant parser.
    """

    def evaluate(eval_input: EvalInput) -> Any:
        request = LLMRequest(
            messages=_judge_messages(
                instructions,
                eval_input,
                include_reference=include_reference,
                continuous=continuous,
                choices=choices,
            ),
            temperature=temperature,
            max_tokens=max_tokens,
            extra={"model": model} if model else {},
        )

        async def _score() -> Feedback:
            text = (await llm.complete(request)).text
            score, comment = (
                parse(text)
                if parse is not None
                else parse_judgement(text, continuous=continuous, choices=choices)
            )
            return Feedback(key=key, score=score, comment=comment)

        return _score()

    evaluate.__name__ = key
    return evaluate


def judge_correctness(
    llm: LLMProvider, *, key: str = "correctness", **kwargs: Any
) -> Evaluator:
    """Reference-based judge: is the output equivalent to the reference?"""
    return llm_judge(
        llm,
        instructions=CORRECTNESS_INSTRUCTIONS,
        key=key,
        include_reference=True,
        **kwargs,
    )


def judge_relevance(
    llm: LLMProvider, *, key: str = "relevance", **kwargs: Any
) -> Evaluator:
    """Reference-free judge: does the output address the input?"""
    return llm_judge(llm, instructions=RELEVANCE_INSTRUCTIONS, key=key, **kwargs)


def judge_faithfulness(
    llm: LLMProvider, *, key: str = "faithfulness", **kwargs: Any
) -> Evaluator:
    """Reference-free judge: is the output grounded in the provided context?"""
    return llm_judge(llm, instructions=FAITHFULNESS_INSTRUCTIONS, key=key, **kwargs)


__all__ = [
    "CORRECTNESS_INSTRUCTIONS",
    "FAITHFULNESS_INSTRUCTIONS",
    "JUDGE_SYSTEM",
    "RELEVANCE_INSTRUCTIONS",
    "JudgeParser",
    "judge_correctness",
    "judge_faithfulness",
    "judge_relevance",
    "llm_judge",
    "parse_judgement",
]

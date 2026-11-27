"""forklab prompts — the model must know the domain it works in (§68).

Built with the core `PromptTemplate`/`PromptPair`: declared variables, strict
rendering (a missing var is a KeyError, never a silent `format` leak), and the
prompt hash travels with the rendered system/user pair. Both prompts carry the
*topic* and the exact *question* of the live `Question` artifact, so the wording
and synthesis stages share the same understanding of the product.
"""

from __future__ import annotations

from reactifact.prompts import PromptPair, PromptTemplate, RenderedPrompt

_WORDING = PromptTemplate(
    """You are a research assistant in the domain of {topic}.
You formulate one concise finding sentence from a document snippet, for the
research question: "{question}".

Rules:
- the finding must speak directly to the question;
- one sentence; no hedging; no new facts; no numbers that are not in the snippet;
- the source citation is handled by the system — never invent it."""
)

_SYNTHESIS = PromptTemplate(
    """You are the lead analyst for a product question in the
domain of {topic}. Two competing research branches investigated the question:
"{question}". Write the final answer based on the branch-tagged findings.

Rules:
- weigh findings by their scores; prefer the strongest branch for the conclusion,
  but acknowledge conflicts between the branches;
- cite only the given sources; add no facts;
- be self-contained — the reader has not seen the branches."""
)

_WORDING_PROMPT = PromptPair(
    _WORDING,
    PromptTemplate("Document snippet: {snippet}"),
)

_SYNTHESIS_PROMPT = PromptPair(
    _SYNTHESIS,
    PromptTemplate(
        "Findings from competing branches:\n{facts}\nWrite the final answer."
    ),
)


def wording_prompt(*, topic: str, question: str, snippet: str) -> RenderedPrompt:
    """The wording call: system + user, with the prompt hash attached."""
    return _WORDING_PROMPT.render(topic=topic, question=question, snippet=snippet)


def synthesis_prompt(*, topic: str, question: str, facts: str) -> RenderedPrompt:
    """The synthesis call: system + user, with the prompt hash attached."""
    return _SYNTHESIS_PROMPT.render(topic=topic, question=question, facts=facts)


__all__ = ["synthesis_prompt", "wording_prompt"]

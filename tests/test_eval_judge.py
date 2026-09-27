"""LLM-as-judge evaluators (§56): prompt build, tolerant parsing, FakeLLM."""

from reactifact import FakeLLM
from reactifact.eval import (
    Dataset,
    Feedback,
    evaluate,
    judge_correctness,
    judge_faithfulness,
    judge_relevance,
    llm_judge,
    parse_judgement,
)
from reactifact.providers.contracts import LLMRequest, LLMResponse


class CapturingLLM(FakeLLM):
    def __init__(self, response):
        super().__init__(response=response)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().complete(request)


def _dataset(**reference):
    row = {"inputs": {"q": "what is 2+2?"}, "reference_outputs": reference or None}
    return Dataset.from_list([row])


def _run(report):
    return report.results[0].metrics[0]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_parse_judgement_json_and_tolerance():
    assert parse_judgement('{"score": true, "comment": "ok"}') == (1.0, "ok")
    assert parse_judgement('{"score": false}') == (0.0, "")
    # prose around the object
    score, comment = parse_judgement('Sure! {"score": true, "comment": "good"} done')
    assert score == 1.0 and comment == "good"
    # no JSON: fall back to a number / boolean word
    assert parse_judgement("0.9")[0] == 1.0
    assert parse_judgement("no")[0] == 0.0


def test_parse_judgement_continuous_and_choices():
    assert parse_judgement('{"score": 0.7}', continuous=True)[0] == 0.7
    assert parse_judgement('{"score": 0.7}', choices=[0.0, 0.5, 1.0])[0] == 0.5
    # bool mode floors a non-bool number
    assert parse_judgement('{"score": 0.4}')[0] == 0.0


# --------------------------------------------------------------------------- #
# Evaluators
# --------------------------------------------------------------------------- #


def test_judge_correctness_reference_based():
    llm = CapturingLLM('{"score": true, "comment": "matches"}')
    report = evaluate(
        _dataset(answer="4"),
        lambda inputs: {"answer": "four"},
        {"correctness": judge_correctness(llm)},
    )
    metric = _run(report)
    assert metric.name == "correctness"
    assert metric.score == 1.0
    assert metric.note == "matches"
    # the reference outputs were formatted into the prompt
    user = llm.requests[0].messages[1].content
    assert "REFERENCE OUTPUTS" in user
    assert "Rubric:" in user


def test_judge_relevance_is_reference_free():
    llm = CapturingLLM('{"score": false, "comment": "evasive"}')
    report = evaluate(
        _dataset(),
        lambda inputs: {"answer": "no idea"},
        {"relevance": judge_relevance(llm)},
    )
    assert _run(report).score == 0.0
    assert "REFERENCE OUTPUTS" not in llm.requests[0].messages[1].content


def test_judge_faithfulness_continuous():
    llm = CapturingLLM('{"score": 0.5, "comment": "partly"}')
    judge = judge_faithfulness(llm, continuous=True)
    report = evaluate(_dataset(), lambda inputs: {"answer": "maybe"}, {"faith": judge})
    assert _run(report).score == 0.5


def test_llm_judge_custom_parse_and_key():
    judge = llm_judge(
        FakeLLM("irrelevant"),
        instructions="rubric",
        key="custom",
        parse=lambda text: (1.0, text.upper()),
    )
    report = evaluate(_dataset(), lambda inputs: {"answer": "x"}, {"c": judge})
    metric = _run(report)
    assert metric.name == "custom"
    assert metric.score == 1.0
    assert metric.note == "IRRELEVANT"


def test_llm_judge_model_goes_to_extra():
    llm = CapturingLLM('{"score": true}')
    judge = llm_judge(llm, instructions="rubric", model="gpt-judge")
    evaluate(_dataset(), lambda inputs: {"answer": "x"}, {"j": judge})
    assert llm.requests[0].extra == {"model": "gpt-judge"}


def test_evaluator_returning_none_is_skipped():
    report = evaluate(
        _dataset(), lambda inputs: {"answer": "x"}, {"skip": lambda ei: None}
    )
    assert report.results[0].skipped == ["skip"]
    assert report.results[0].metrics == []


def test_feedback_clamps_score():
    assert Feedback("k", 2.0).score == 1.0
    assert Feedback("k", -1.0).score == 0.0
    assert Feedback("k", 0.5).to_dict() == {"key": "k", "score": 0.5, "comment": ""}

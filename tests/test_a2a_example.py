"""The A2A example app runs end to end (in-process, no network)."""

import asyncio

from examples.a2a.demo import run_demo


def test_a2a_example_runs_end_to_end():
    result = asyncio.run(run_demo())
    assert result["card_name"] == "upper-agent"
    assert result["skills"] == ["upper"]
    assert result["direct_state"] == "completed"
    assert result["direct_text"] == "HELLO A2A"
    assert result["node_text"] == "RUN VIA NODE"
    assert result["hitl_question"] == "Which format should I use?"
    assert result["hitl_answer"] == "got it: markdown"

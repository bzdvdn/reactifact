import asyncio

from examples.incident_commander.models import Answer, Evidence, RootCauseHypothesis
from examples.incident_commander.pipeline import classify_targets, run
from examples.incident_commander.tools import CALLS
from reactifact import PendingQuestion
from reactifact.verify import VerificationResult


def test_full_scripted_run_wires_all_five_pieces():
    CALLS.clear()
    ctx = asyncio.run(run())

    # branch & merge: the k8s and db forks each investigated independently
    # and both sides' findings ended up in the one merged timeline
    assert CALLS["check_pods"] == [{"namespace": "checkout"}]
    assert CALLS["check_recent_deploys"] == [{}]

    # sub-agent delegation: the DBA specialist's own tool ran, via ask_dba
    assert CALLS["check_db_locks"] == [{}]

    # destructive-tool approval gate: rollback only ran after an approval
    questions = ctx.list_artifacts(PendingQuestion)
    assert len(questions) == 1
    assert questions[0].data.kind == "approve"
    assert questions[0].data.answered is True
    assert CALLS["rollback_deploy"] == [{"deploy_id": "checkout-42"}]

    # both forks' evidence survived the merge into one timeline
    all_evidence = ctx.list_artifacts(Evidence)
    assert len(all_evidence) == 3

    # context builder: the synthesis saw a bounded, non-empty slice of
    # evidence, not necessarily all of it
    hypotheses = ctx.list_artifacts(RootCauseHypothesis)
    assert len(hypotheses) == 1
    assert 0 < hypotheses[0].data.evidence_seen <= len(all_evidence)

    # inline verification: the grounded answer passes
    answers = ctx.list_artifacts(Answer)
    assert len(answers) == 1
    results = ctx.list_artifacts(VerificationResult)
    assert len(results) == 1
    assert results[0].data.passed is True
    assert results[0].data.metrics["provenance_grounded"] == 1.0

    # provenance: the answer is actually linked to its evidence
    linked = ctx.related(answers[0].id, relation="supported_by")
    assert len(linked) == len(all_evidence)


def test_tight_context_budget_still_grounds_the_answer():
    """Even when the synthesis only sees one piece of evidence, the *answer*
    itself (built separately, from all matching Evidence) stays fully
    grounded — the context budget bounds the synthesis, not provenance."""
    CALLS.clear()
    ctx = asyncio.run(run(context_max_tokens=30))

    hypotheses = ctx.list_artifacts(RootCauseHypothesis)
    assert hypotheses[0].data.evidence_seen == 1

    results = ctx.list_artifacts(VerificationResult)
    assert results[0].data.passed is True
    assert results[0].data.metrics["provenance_grounded"] == 1.0


def test_classify_targets_is_deterministic_and_never_dead_ends():
    assert classify_targets("pods CrashLoopBackOff after the deploy") == {"k8s"}
    assert classify_targets("database query timeout, connection pool exhausted") == {
        "db"
    }
    assert classify_targets("pods crashing, team also suspects db timeouts") == {
        "k8s",
        "db",
    }
    # nothing recognizable -> investigate everywhere rather than skip silently (§59)
    assert classify_targets("something is on fire") == {"k8s", "db"}


def test_k8s_only_incident_skips_the_db_fork_entirely():
    CALLS.clear()
    ctx = asyncio.run(
        run(
            incident_text="checkout-api pods stuck in CrashLoopBackOff after the deploy"
        )
    )

    assert CALLS["check_pods"] == [{"namespace": "checkout"}]
    assert "check_db_locks" not in CALLS
    assert "rollback_deploy" in CALLS

    all_evidence = ctx.list_artifacts(Evidence)
    assert len(all_evidence) == 2  # only the k8s fork's findings

    answers = ctx.list_artifacts(Answer)
    linked = ctx.related(answers[0].id, relation="supported_by")
    assert len(linked) == 2

    results = ctx.list_artifacts(VerificationResult)
    assert results[0].data.passed is True

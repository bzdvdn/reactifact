"""forklab agents — one per fork strategy, one for the merge."""

from __future__ import annotations

from reactifact import Agent, Pipeline

from .steps import BreadthInvestigate, DepthInvestigate, Evaluate

#: Runs in a *single* branch: spawn the Strategy first, this reacts.
InvestigateAgent = Agent("strategy", steps=[DepthInvestigate(), BreadthInvestigate()])

#: Runs on the merged context and produces the final Answer.
EvaluateAgent = Agent("evaluator", steps=[Evaluate()])

#: The two reusable definitions; each fork and the merged state run one.
INVESTIGATE = Pipeline(agents=[InvestigateAgent], name="forklab:investigate")
EVALUATE = Pipeline(agents=[EvaluateAgent], name="forklab:evaluate")

"""forklab agents — one per fork strategy, one for the merge."""

from reactifact import Pipeline

from .evaluate import evaluate
from .investigate import investigate

#: Each fork gets its own one-function agent.
InvestigateAgent = investigate

#: Runs on the merged context and produces the final Answer.
EvaluateAgent = evaluate

#: The two reusable definitions; each fork and the merged state run one.
INVESTIGATE = Pipeline(agents=[InvestigateAgent], name="forklab:investigate")
EVALUATE = Pipeline(agents=[EvaluateAgent], name="forklab:evaluate")

__all__ = ["EVALUATE", "INVESTIGATE", "EvaluateAgent", "InvestigateAgent"]

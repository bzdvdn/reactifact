"""final_approval stage — the HITL gate (§60)."""

from __future__ import annotations

from typing import Any

from reactifact import (
    Artifact,
    Context,
    Edit,
    Emit,
    Human,
    LLMProvider,
    PendingQuestion,
    Progress,
    Step,
)
from reactifact.recipes import changed_fields

from ...models import ChatReply, Project, UserMsg
from ...services.geometry import ensure_geometry
from ...services.rollback import rollback_target
from ..common import (
    _APPROVE_RE,
    _BUDGET_COMPLAINT,
    _approval_text,
    _downstream_resets,
    _extract_info,
    _reply,
)


class ApprovalStage(Step):
    """HITL gate (§60): asks the approval question; the answer → assistant or a rebuild."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply, PendingQuestion)

    async def run(
        self,
        context: Context,
        human: Human,
        user: Artifact[UserMsg],
        project: Artifact[Project] | None,
        progress: Progress,
        llm: LLMProvider | None,
    ) -> Any:
        if project is None or project.data.stage != "final_approval":
            return None
        data = project.data
        pending = [p for p in context.pending_questions() if p.data.kind == "approval"]
        if not pending:
            # entering the stage: ask the question immediately, without waiting
            # for a new message
            if user.id != data.handled_msg:
                human.ask(_approval_text(data), kind="approval")
                return (
                    Edit(project, handled_msg=user.id),
                    Emit(
                        _reply(
                            user.id,
                            _approval_text(data),
                            kind="approval",
                        ),
                        id=f"reply:{user.id}",
                    ),
                )
            human.ask(_approval_text(data), kind="approval")
            return None

        # the user answered the approval question
        if user.id == data.handled_msg:
            return None
        question_art = pending[0]
        answer = user.data.text.strip()
        progress("Думаю…")
        human.resume(question_art, answer)
        if _APPROVE_RE.match(answer):
            return (
                Edit(
                    project,
                    approved=True,
                    stage="assistant",
                    handled_msg=user.id,
                ),
                Emit(
                    _reply(
                        user.id,
                        "Отлично! План и смета утверждены. Теперь могу помогать "
                        "по ходу ремонта: этапы, материалы, цены.",
                    ),
                    id=f"reply:{user.id}",
                ),
            )

        # Anything that is not «да» is a request to change/clarify: extract the
        # edits and rebuild. On a budget/expense complaint, rebuild from the
        # "plan" stage (materials define the estimate), not just the estimate.
        new_info = await _extract_info(llm, progress, user.data.text, data.info)
        changed = changed_fields(data.info, new_info)
        target = rollback_target(changed)
        if not changed and _BUDGET_COMPLAINT.search(user.data.text):
            target = "plan"
        updates: dict[str, Any] = _downstream_resets(target)
        updates.update(
            {
                "info": ensure_geometry(new_info),
                "stage": target,
                "handled_msg": "",
            }
        )
        # no separate reply: the rebuild will pass and ApprovalStage will show
        # the updated proposal again (the same user.id gets overwritten by it).
        return Edit(project, **updates)


__all__ = ["ApprovalStage"]

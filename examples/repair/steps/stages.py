"""repair produce — the six pipeline stages (§71).

Each stage is its own Step with a deterministic guard on `Project.stage`:
collect (facts → designs) → design_choice (pick) → plan (LLM + geometry) →
estimate (catalog, no LLM) → final_approval (HITL gate) → assistant (open-ended
post-approval). Routing follows the project's stage field — the runtime/wake
model builds the links from the artifact state, without a manual graph.

Typed inputs: a stage receives the artifacts it declared, plus injected
`context` and (where needed) `human`; changes are returned as `Edit`/`Emit`
instead of low-level `StepCall`/effects plumbing.
"""

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
from reactifact.structured import llm_reply

from ..models import ChatReply, Project, ProjectInfo, UserMsg
from ..services.estimate import build_estimate, qa_budget_warning
from ..services.facts import FACT_LABELS, REQUIRED_FACTS
from ..services.fast import fast_reply
from ..services.geometry import ensure_geometry
from ..services.rollback import rollback_target
from .common import (
    _APPROVE_RE,
    _BUDGET_COMPLAINT,
    _approval_text,
    _assistant_context,
    _conversation_text,
    _downstream_resets,
    _extract_info,
    _parse_pick,
    _project_artifact,
    _wants_more_options,
)
from .design import _make_design_options
from .plan import _make_plan


def _reply(
    context: Context,
    msg_id: str,
    text: str,
    kind: str = "text",
    images: list[str] | None = None,
) -> ChatReply:
    return ChatReply(query_id=msg_id, text=text, kind=kind, images=images or [])


# --- collect stage -----------------------------------------------------------


class CollectStage(Step):
    """Extracts facts; once all the required ones are present — designs and design_choice."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply)

    async def run(
        self,
        context: Context,
        user: Artifact[UserMsg],
        project: Project | None,
    ) -> Any:
        project_art = _project_artifact(context)
        if project_art is None:
            return Project()
        data = project if project is not None else project_art.data
        if data.stage != "collect":
            return None
        if user.id == data.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        fast = fast_reply(user.data.text)
        if fast is not None:
            return (
                Edit(project_art, handled_msg=user.id),
                Emit(_reply(context, user.id, fast), id=f"reply:{user.id}"),
            )

        info = ensure_geometry(await _extract_info(context, user.data.text, data.info))
        missing = [f for f in REQUIRED_FACTS if getattr(info, f) is None]
        if missing:
            labeled = ", ".join(FACT_LABELS[f] for f in missing)
            return (
                Edit(project_art, info=info, handled_msg=user.id),
                Emit(
                    _reply(context, user.id, f"Уточните, пожалуйста: {labeled}."),
                    id=f"reply:{user.id}",
                ),
            )

        options = await _make_design_options(context, info)
        if options is None:
            return (
                Edit(project_art, info=info, handled_msg=user.id),
                Emit(
                    _reply(
                        context,
                        user.id,
                        "Не удалось подобрать варианты дизайна. Попробуйте ещё "
                        "раз или сформулируйте иначе (например, «предложи "
                        "варианты»).",
                    ),
                    id=f"reply:{user.id}",
                ),
            )
        previews = "\n".join(
            f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
        )
        return (
            Edit(
                project_art,
                info=info,
                design_options=options,
                stage="design_choice",
                handled_msg=user.id,
            ),
            Emit(
                _reply(
                    context,
                    user.id,
                    f"Выберите вариант:\n{previews}",
                    images=[o.preview for o in options if o.preview],
                ),
                id=f"reply:{user.id}",
            ),
        )


# --- design_choice stage -------------------------------------------------------


class PickStage(Step):
    """Parses the user's choice → plan."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply)

    async def run(
        self,
        context: Context,
        user: Artifact[UserMsg],
        project: Project | None,
    ) -> Any:
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "design_choice":
            return None
        data = project if project is not None else project_art.data
        if user.id == data.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        text = user.data.text.strip()
        pick = _parse_pick(text, data.design_options)
        if pick is None:
            if _wants_more_options(text):
                return await _regenerate_options(context, project_art, data, user)
            return await _handle_style_change(
                context, project_art, data, user, data.info
            )
        return (
            Edit(
                project_art,
                design_choice=pick.name,
                palette=pick.palette,
                stage="plan",
                handled_msg=user.id,
                plan=[],
                estimate=None,
            ),
            Emit(
                _reply(context, user.id, f"Отлично, «{pick.name}». Составляю план…"),
                id=f"reply:{user.id}",
            ),
        )


# --- plan / estimate / final_approval stages (deterministic steps) ------------


class PlanStage(Step):
    """Plan (LLM + geometry) → estimate."""

    consumes = (Project,)
    produces = (Project,)

    async def run(
        self,
        project: Artifact[Project],
        progress: Progress,
        llm: LLMProvider | None,
    ) -> Any:
        if project.data.stage != "plan" or project.data.plan:
            return None
        steps = await _make_plan(llm, progress, project.data)
        return Edit(project, plan=steps, stage="estimate")


class EstimateStage(Step):
    """Estimate — deterministically, via the catalog (no LLM) → final_approval."""

    consumes = (Project,)
    produces = (Project,)

    async def run(
        self,
        context: Context,
        project: Artifact[Project],
    ) -> Any:
        if project.data.stage != "estimate":
            return None
        if project.data.estimate is not None:
            return None
        catalog = context.resources.get("catalog")
        if catalog is None:
            return None
        context.announce("Считаю смету…", kind="status")
        estimate = build_estimate(project.data.plan, catalog)
        estimate.warnings.extend(qa_budget_warning(estimate, project.data.info.budget))
        return Edit(project, estimate=estimate, stage="final_approval")


class ApprovalStage(Step):
    """HITL gate (§60): asks the approval question; the answer → assistant or a rebuild."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply, PendingQuestion)

    async def run(
        self,
        context: Context,
        human: Human,
        user: Artifact[UserMsg],
        project: Project | None,
    ) -> Any:
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "final_approval":
            return None
        data = project if project is not None else project_art.data
        pending = [p for p in context.pending_questions() if p.data.kind == "approval"]
        if not pending:
            # entering the stage: ask the question immediately, without waiting
            # for a new message
            if user.id != data.handled_msg:
                human.ask(_approval_text(data), kind="approval")
                return (
                    Edit(project_art, handled_msg=user.id),
                    Emit(
                        _reply(
                            context,
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
        context.announce("Думаю…", kind="status")
        human.resume(question_art, answer)
        if _APPROVE_RE.match(answer):
            return (
                Edit(
                    project_art,
                    approved=True,
                    stage="assistant",
                    handled_msg=user.id,
                ),
                Emit(
                    _reply(
                        context,
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
        new_info = await _extract_info(context, user.data.text, data.info)
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
        return Edit(project_art, **updates)


# --- assistant stage ------------------------------------------------------------


class AssistantStage(Step):
    """Open-ended dialogue after approval."""

    consumes = (UserMsg, Project)
    produces = (Project, ChatReply)

    async def run(
        self,
        context: Context,
        user: Artifact[UserMsg],
        project: Project | None,
    ) -> Any:
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "assistant":
            return None
        data = project if project is not None else project_art.data
        if user.id == data.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        parts = [
            p
            for p in (
                _assistant_context(data),
                _conversation_text(context, user.id),
            )
            if p
        ]
        context_text = "\n\n".join(parts)
        prompt = (
            f"{context_text}\n\nВопрос: {user.data.text}"
            if context_text
            else user.data.text
        )
        text = await llm_reply(
            context.resources.llm,
            system=(
                "You are the repair assistant. The project is approved. Answer "
                "briefly and actionably in Russian, based strictly on the plan "
                "and the estimate; do not invent prices that are not in the "
                "estimate."
            ),
            user=prompt,
            attempts=3,
        )
        if not text:
            # Honest fallback (§59): the model was unavailable or its reply did
            # not parse — still answer from the plan, not a canned placeholder.
            text = _assistant_fallback(data)
        return (
            Emit(_reply(context, user.id, text), id=f"reply:{user.id}"),
            Edit(project_art, handled_msg=user.id),
        )


def _assistant_fallback(project: Project) -> str:
    """Deterministic answer from the approved plan (no model needed)."""
    if project.plan:
        first = project.plan[0]
        return (
            f"Комната: {project.info.room_type or '—'}. Начните с этапа "
            f"«{first.name}»: {first.description}. Могу отвечать по этапам, "
            f"материалам и ценам из утверждённой сметы."
        )
    return (
        "План и смета утверждены. Спросите про этапы работ, материалы или "
        "цены из сметы — отвечу по плану."
    )


async def _regenerate_options(
    context: Context,
    project: Artifact[Project],
    data: Project,
    msg: Artifact[UserMsg],
) -> Any:
    """The human rejected the current options — ask for a fresh set."""
    if context.resources.llm is None:
        return (
            Edit(project, handled_msg=msg.id),
            Emit(
                _reply(
                    context,
                    msg.id,
                    "Без модели не могу придумать новые варианты. Выберите один "
                    "из текущих или уточните стиль — тогда обновлю палитру.",
                ),
                id=f"reply:{msg.id}",
            ),
        )
    context.announce("Подбираю другие варианты…", kind="status")
    options = await _make_design_options(
        context, data.info, avoid=[option.name for option in data.design_options]
    )
    if options is None:
        return (
            Edit(project, handled_msg=msg.id),
            Emit(
                _reply(
                    context,
                    msg.id,
                    "Не удалось подобрать другие варианты. Попробуйте ещё раз.",
                ),
                id=f"reply:{msg.id}",
            ),
        )
    previews = "\n".join(
        f"{i + 1}. {option.name} — {option.description}"
        for i, option in enumerate(options)
    )
    return (
        Edit(project, design_options=options, handled_msg=msg.id),
        Emit(
            _reply(
                context,
                msg.id,
                f"Вот другие варианты:\n{previews}",
                images=[option.preview for option in options if option.preview],
            ),
            id=f"reply:{msg.id}",
        ),
    )


async def _handle_style_change(
    context: Context,
    project: Artifact[Project],
    data: Project,
    msg: Artifact[UserMsg],
    current_info: ProjectInfo,
) -> Any:
    """A message at the choice stage without a number — likely a style/palette change.

    Extract updated facts; if something actually changed — regenerate the
    options (like the rollback "style → design_choice" in REPAIR). Otherwise —
    hint to pick a number.
    """
    text_low = msg.data.text.lower()
    if not any(
        keyword in text_low
        for keyword in (
            "стил",
            "дизайн",
            "палитр",
            "давай",
            "цвет",
            "потолок",
            "стен",
            "пол",
        )
    ):
        return _hint(context, msg.id)

    new_info = ensure_geometry(
        await _extract_info(context, msg.data.text, current_info)
    )
    if not changed_fields(current_info, new_info):
        return _hint(context, msg.id)

    context.announce("Обновляю варианты под ваш запрос…", kind="status")
    options = await _make_design_options(context, new_info)
    if options is None:
        return Emit(
            _reply(
                context, msg.id, "Не удалось обновить варианты. Попробуйте ещё раз."
            ),
            id=f"reply:{msg.id}",
        )
    previews = "\n".join(
        f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
    )
    return (
        Edit(
            project,
            info=new_info,
            design_options=options,
            handled_msg=msg.id,
        ),
        Emit(
            _reply(
                context,
                msg.id,
                f"Обновил варианты:\n{previews}",
                images=[o.preview for o in options if o.preview],
            ),
            id=f"reply:{msg.id}",
        ),
    )


def _hint(context: Context, msg_id: str) -> Emit:
    return Emit(
        _reply(context, msg_id, "Выберите номер варианта, например: 1"),
        id=f"reply:{msg_id}",
    )


__all__ = [
    "ApprovalStage",
    "AssistantStage",
    "CollectStage",
    "EstimateStage",
    "PickStage",
    "PlanStage",
]

"""repair produce — the six pipeline stages (§71).

Each stage is its own Produce with a deterministic guard on `Project.stage`:
collect (facts → designs) → design_choice (pick) → plan (LLM + geometry) →
estimate (catalog, no LLM) → final_approval (HITL gate) → assistant (open-ended
post-approval). Routing follows the project's stage field — the runtime/wake
model builds the links from the artifact state, without a manual graph.

Effects-based: produces write `self.effects.create/update/ask` and return None;
the runtime compiles one atomic patch per produce (§24).
"""

from __future__ import annotations

from typing import Any

from reactifact import Context, Produce, ProduceCall
from reactifact.artifacts import Artifact
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
    _latest_user_msg,
    _parse_pick,
    _project_artifact,
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


class CollectStage(Produce[Project]):
    """Extracts facts; once all the required ones are present — designs and design_choice."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if project_art is None:
            self.effects.create(Project())
            return None
        project = project_art.data
        if project.stage != "collect":
            return None
        msg = _latest_user_msg(context)
        if msg is None or msg.id == project.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        fast = fast_reply(msg.data.text)
        if fast is not None:
            self.effects.update(project_art, handled_msg=msg.id)
            self.effects.create(_reply(context, msg.id, fast), id=f"reply:{msg.id}")
            return None

        info = ensure_geometry(
            await _extract_info(context, msg.data.text, project.info)
        )
        missing = [f for f in REQUIRED_FACTS if getattr(info, f) is None]
        if missing:
            labeled = ", ".join(FACT_LABELS[f] for f in missing)
            self.effects.update(project_art, info=info, handled_msg=msg.id)
            self.effects.create(
                _reply(context, msg.id, f"Уточните, пожалуйста: {labeled}."),
                id=f"reply:{msg.id}",
            )
            return None

        options = await _make_design_options(context, info)
        if options is None:
            self.effects.update(project_art, info=info, handled_msg=msg.id)
            self.effects.create(
                _reply(
                    context,
                    msg.id,
                    "Не удалось подобрать варианты дизайна. Попробуйте ещё раз "
                    "или сформулируйте иначе (например, «предложи варианты»).",
                ),
                id=f"reply:{msg.id}",
            )
            return None
        previews = "\n".join(
            f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
        )
        self.effects.update(
            project_art,
            info=info,
            design_options=options,
            stage="design_choice",
            handled_msg=msg.id,
        )
        self.effects.create(
            _reply(
                context,
                msg.id,
                f"Выберите вариант:\n{previews}",
                images=[o.preview for o in options if o.preview],
            ),
            id=f"reply:{msg.id}",
        )
        return None


# --- design_choice stage -------------------------------------------------------


class PickStage(Produce[Project]):
    """Parses the user's choice → plan."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "design_choice":
            return None
        project = project_art.data
        msg = _latest_user_msg(context)
        if msg is None or msg.id == project.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        pick = _parse_pick(msg.data.text.strip(), project.design_options)
        if pick is None:
            return await _handle_style_change(
                self, context, project_art, project, msg, project.info
            )
        self.effects.update(
            project_art,
            design_choice=pick.name,
            palette=pick.palette,
            stage="plan",
            handled_msg=msg.id,
            plan=[],
            estimate=None,
        )
        self.effects.create(
            _reply(context, msg.id, f"Отлично, «{pick.name}». Составляю план…"),
            id=f"reply:{msg.id}",
        )
        return None


# --- plan / estimate / final_approval stages (deterministic steps) ------------


class PlanStage(Produce[Project]):
    """Plan (LLM + geometry) → estimate."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if (
            project_art is None
            or project_art.data.stage != "plan"
            or project_art.data.plan
        ):
            return None
        project = project_art.data
        steps = await _make_plan(context, project)
        self.effects.update(project_art, plan=steps, stage="estimate")
        return None


class EstimateStage(Produce[Project]):
    """Estimate — deterministically, via the catalog (no LLM) → final_approval."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "estimate":
            return None
        project = project_art.data
        if project.estimate is not None:
            return None
        catalog = context.resources.get("catalog")
        if catalog is None:
            return None
        context.announce("Считаю смету…", kind="status")
        estimate = build_estimate(project.plan, catalog)
        estimate.warnings.extend(qa_budget_warning(estimate, project.info.budget))
        self.effects.update(project_art, estimate=estimate, stage="final_approval")
        return None


class ApprovalStage(Produce[Project]):
    """HITL gate (§60): immediately asks the approval question; the answer → assistant or a rebuild."""

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "final_approval":
            return None
        project = project_art.data
        msg = _latest_user_msg(context)

        pending = [p for p in context.pending_questions() if p.data.kind == "approval"]
        if not pending:
            # entering the stage: ask the question immediately, without waiting for a new message
            if msg is not None and msg.id != project.handled_msg:
                self.effects.ask(_approval_text(project), kind="approval")
                self.effects.create(
                    _reply(context, msg.id, _approval_text(project), kind="approval"),
                    id=f"reply:{msg.id}",
                )
                self.effects.update(project_art, handled_msg=msg.id)
                return None
            self.effects.ask(_approval_text(project), kind="approval")
            return None

        # the user answered the approval question
        if msg is None or msg.id == project.handled_msg:
            return None
        question_art = pending[0]
        answer = msg.data.text.strip()
        context.announce("Думаю…", kind="status")
        self.effects.resume(question_art, answer)
        if _APPROVE_RE.match(answer):
            self.effects.update(
                project_art, approved=True, stage="assistant", handled_msg=msg.id
            )
            self.effects.create(
                _reply(
                    context,
                    msg.id,
                    "Отлично! План и смета утверждены. Теперь могу помогать "
                    "по ходу ремонта: этапы, материалы, цены.",
                ),
                id=f"reply:{msg.id}",
            )
            return None

        # Anything that is not «да» is a request to change/clarify: extract the edits
        # and rebuild. On a budget/expense complaint, rebuild from the "plan" stage
        # (materials define the estimate), not just the estimate.
        new_info = await _extract_info(context, msg.data.text, project.info)
        changed = changed_fields(project.info, new_info)
        target = rollback_target(changed)
        if not changed and _BUDGET_COMPLAINT.search(msg.data.text):
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
        # the updated proposal again (the same msg.id gets overwritten by it).
        self.effects.update(project_art, **updates)
        return None


# --- assistant stage ------------------------------------------------------------


class AssistantStage(Produce[Project]):
    """Open-ended dialogue after approval."""

    artifact_type = Project

    async def produce(self, call: ProduceCall) -> None:
        context = call.context
        project_art = _project_artifact(context)
        if project_art is None or project_art.data.stage != "assistant":
            return None
        project = project_art.data
        msg = _latest_user_msg(context)
        if msg is None or msg.id == project.handled_msg:
            return None
        context.announce("Думаю…", kind="status")
        parts = [
            p
            for p in (
                _assistant_context(project),
                _conversation_text(context, msg.id),
            )
            if p
        ]
        context_text = "\n\n".join(parts)
        user = (
            f"{context_text}\n\nВопрос: {msg.data.text}"
            if context_text
            else msg.data.text
        )
        text = await llm_reply(
            context,
            system=(
                "You are the repair assistant. The project is approved. Answer "
                "briefly and actionably in Russian, based strictly on the plan "
                "and the estimate; do not invent prices that are not in the "
                "estimate."
            ),
            user=user,
            attempts=3,
        )
        if not text:
            # Honest fallback (§59): the model was unavailable or its reply did
            # not parse — still answer from the plan, not a canned placeholder.
            text = _assistant_fallback(project)
        self.effects.create(_reply(context, msg.id, text), id=f"reply:{msg.id}")
        self.effects.update(project_art, handled_msg=msg.id)
        return None


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


async def _handle_style_change(
    produce: PickStage,
    context: Context,
    project_art: Artifact[Project],
    project: Project,
    msg: Artifact[UserMsg],
    current_info: ProjectInfo,
) -> None:
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
        return _hint(produce, context, msg.id)

    new_info = ensure_geometry(
        await _extract_info(context, msg.data.text, current_info)
    )
    if not changed_fields(current_info, new_info):
        return _hint(produce, context, msg.id)

    context.announce("Обновляю варианты под ваш запрос…", kind="status")
    options = await _make_design_options(context, new_info)
    if options is None:
        produce.effects.create(
            _reply(
                context, msg.id, "Не удалось обновить варианты. Попробуйте ещё раз."
            ),
            id=f"reply:{msg.id}",
        )
        return None
    previews = "\n".join(
        f"{i + 1}. {o.name} — {o.description}" for i, o in enumerate(options)
    )
    produce.effects.update(
        project_art,
        info=new_info,
        design_options=options,
        handled_msg=msg.id,
    )
    produce.effects.create(
        _reply(
            context,
            msg.id,
            f"Обновил варианты:\n{previews}",
            images=[o.preview for o in options if o.preview],
        ),
        id=f"reply:{msg.id}",
    )
    return None


def _hint(produce: PickStage, context: Context, msg_id: str) -> None:
    produce.effects.create(
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

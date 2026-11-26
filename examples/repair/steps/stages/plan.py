"""plan stage — the plan prompt, its driver, and the Step (§41)."""

from __future__ import annotations

from typing import Any

from reactifact import Artifact, Edit, LLMProvider, Progress, Step
from reactifact.structured import structured_llm

from ...fallbacks import fallback_plan
from ...models import PlanStep, Project, RepairPlan
from ..common import _palette_text

_PLAN_PROMPT = """Ты — Planner. Составь поэтапный план ремонта помещения.

Геометрия уже вычислена: {geometry}
Бюджет: {budget}
Выбранный дизайн: {palette}

ПОРЯДОК ЭТАПОВ — строго такой (пропускай ненужные, но не меняй порядок):
1. Демонтаж и подготовка
2. Черновые работы (стяжка, выравнивание, штукатурка, шпаклёвка)
3. Электромонтаж и разводка
4. Сантехнические работы (если нужны)
5. Отделка стен (покраска или обои)
6. Отделка потолка
7. Укладка пола (подложка, ламинат/плитка/линолеум, плинтус)
8. Финишная отделка и декор

Правила:
- название этапа короткое; описание НЕ повторяет название и добавляет новое;
- УКЛАДЫВАЙСЯ В БЮДЖЕТ {budget}: выбирай доступные материалы и адекватные
  количества, чтобы итоговая смета не выходила за бюджет;
- ПРИВЯЗКА МАТЕРИАЛОВ: штукатурка/шпаклёвка/грунтовка/стяжка/цемент — только в
  «Черновые работы»; кабель/розетки — «Электромонтаж»; плитка/клей — в укладку
  плитки; краска/обои — стены/потолок; ламинат/подложка/плинтус — пол;
- на каждый этап перечислите материалы с количеством через «~» (например,
  «штукатурка ~30 мешков», «подложка ~10 м²»), чтобы потом была точная смета.
  Цены не указывай;
- учитывай выбранный дизайн (стиль, цвета, покрытие пола).

Верни ТОЛЬКО JSON без пояснений:
{{"steps": [{{"name": "", "description": "", "materials": ["..."]}}]}}"""


async def _make_plan(
    llm: LLMProvider | None, progress: Progress, project: Project
) -> list[PlanStep]:
    info = project.info
    palette = _palette_text(project)
    budget = f"{info.budget:,.0f} ₽".replace(",", " ") if info.budget else "не задан"
    geometry = (
        f"площадь пола {info.floor_area} м², стен {info.walls_area} м², "
        f"потолка {info.ceiling_area} м², периметр {info.perimeter} м, "
        f"высота потолков {info.ceiling_height} м"
    )
    progress("Составляю план ремонта…")
    result = await structured_llm(
        llm,
        temperature=0.1,
        schema=RepairPlan,
        user=_PLAN_PROMPT.format(geometry=geometry, palette=palette, budget=budget),
    )
    if result is None or not result.steps:
        return fallback_plan(info)
    return result.steps


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


__all__ = ["PlanStage"]

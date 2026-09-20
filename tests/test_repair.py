import asyncio
from pathlib import Path

from examples.repair.agents import RepairFlow
from examples.repair.models import ChatReply, Project, ProjectInfo, UserMsg
from examples.repair.services import FAST_ABILITIES_TEXT, Catalog
from reactifact import Budget, Context, Runtime, RuntimeResources
from reactifact.providers import (
    ImageProvider,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)


def test_capabilities_answered_without_llm(tmp_path):
    ctx, runtime = build(tmp_path, ScriptedLLM([]))
    query = ctx.create(UserMsg(text="что ты умеешь?", session_id="s"))
    asyncio.run(runtime.arun())

    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id][
        -1
    ].data.text
    assert FAST_ABILITIES_TEXT in reply
    assert not ctx.list_artifacts(Project)[0].data.info.room_type  # never touched LLM


class BoomLLM(LLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("network down")

    async def stream(self, request):
        yield LLMResponse(text="")


def test_llm_failure_falls_back_honestly(tmp_path):
    ctx, runtime = build(tmp_path, BoomLLM())
    query = ctx.create(UserMsg(text="ванная 6 м² бюджет 50к", session_id="s"))
    asyncio.run(runtime.arun())

    # did not crash and no empty reply: honestly asked for the missing facts (§59)
    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id][
        -1
    ].data.text
    assert "Уточните" in reply
    assert ctx.list_artifacts(Project)[0].data.stage == "collect"


def test_missing_fields_asked_in_russian(tmp_path):
    llm = ScriptedLLM(['{"room_type":"детская"}'])
    ctx, runtime = build(tmp_path, llm)
    query = ctx.create(UserMsg(text="детская комната", session_id="s"))
    asyncio.run(runtime.arun())

    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id][
        -1
    ].data.text
    assert "площадь, бюджет" in reply
    # the extracted value was saved into the right field
    assert ctx.list_artifacts(Project)[0].data.info.room_type == "детская"


# A small fixed catalog for a deterministic estimate.


def write_catalog(tmp_path):
    path = tmp_path / "price.csv"
    path.write_text(
        "Наименование,Цена,Ед\n"
        "Штукатурка Ротбанд 30кг,380,₽/мешок\n"
        "Краска люкс интерьер,450,₽/литр\n"
        "Ламинат дуб прованс,890,₽/упаковка\n",
        encoding="utf-8",
    )
    return path


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


def build(tmp_path, llm):
    catalog = Catalog(write_catalog(tmp_path))
    resources = RuntimeResources(llm=llm)
    resources.set("catalog", catalog)
    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[RepairFlow()], budget=Budget(max_runs=200))
    return ctx, runtime


def test_full_pipeline_to_approval(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"ванная","area":6,"budget":50000}',
            '{"options":[{"name":"Светлая","palette":{"wall_color":"белый",'
            '"ceiling_color":"белый","floor_material":"светлый ламинат"},'
            '"description":"лёгкая палитра"}]}',
            '{"steps":[{"name":"стены","description":"отделать",'
            '"materials":["~30 мешков штукатурки Ротбанд"]},'
            '{"name":"пол","description":"уложить",'
            '"materials":["~6 упаковок ламината Дуб"]}]}',
            '{"text":"понял"}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="ванная, 6 м², бюджет 50000", session_id="s"))
    asyncio.run(runtime.arun())
    assert ctx.list_artifacts(Project)[0].data.stage == "design_choice"

    ctx.create(UserMsg(text="1", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "final_approval"
    assert project.design_choice == "Светлая"
    assert project.plan
    assert ctx.has_pending_question()
    assert project.estimate is not None
    assert project.estimate.total is not None and project.estimate.total > 0

    ctx.create(UserMsg(text="да", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "assistant"
    assert project.approved is True
    assert not ctx.has_pending_question()


def test_rollback_on_budget_change(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"кухня","area":8,"budget":40000}',
            '{"options":[{"name":"Светлая","palette":{"floor_material":"светлый ламинат"},'
            '"description":"лёгкая палитра"}]}',
            '{"steps":[{"name":"пол","description":"уложить",'
            '"materials":["~6 упаковок ламината Дуб"]}]}',
            # response to the budget change
            '{"budget":60000}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="кухня, 8 м², бюджет 40000", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="1", session_id="s"))
    asyncio.run(runtime.arun())

    assert ctx.has_pending_question()
    ctx.create(UserMsg(text="нет, бюджет 60000", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.info.budget == 60000
    # rebuild went only from the estimate stage (budget → estimate)
    assert project.plan  # the plan was kept
    assert project.estimate is not None
    assert ctx.has_pending_question()  # waiting for approval again


def test_greeting_and_missing_facts(tmp_path):
    llm = ScriptedLLM([])  # no LLM: extraction finds nothing → we ask for details
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="привет", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "collect"

    # nothing was extracted → ask to clarify the required fields
    ctx.create(UserMsg(text="просто ремонт", session_id="s"))
    asyncio.run(runtime.arun())
    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "collect"


class FakeImage(ImageProvider):
    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, **params):
        self.calls += 1
        return b"\x89PNG\r\n\x1a\nfake-png-bytes"


def test_design_previews_rendered_and_attached(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"ванная","area":5,"budget":50000}',
            '{"options":[{"name":"A","palette":{"wall_color":"белый"},'
            '"description":"палитра A"},{"name":"B",'
            '"palette":{"wall_color":"серый"},"description":"палитра B"}]}',
        ]
    )
    catalog = Catalog(write_catalog(tmp_path))
    images = FakeImage()
    resources = RuntimeResources(llm=llm)
    resources.set("catalog", catalog)
    resources.set("images", images)
    resources.set("images_dir", str(tmp_path / "gen"))

    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[RepairFlow()], budget=Budget(max_runs=200))
    query = ctx.create(UserMsg(text="ванная 5 м² бюджет 50000", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "design_choice"
    assert images.calls == 2
    assert all(
        o.preview.startswith("/assets/generated/design-")
        for o in project.design_options
    )
    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id][
        -1
    ]
    assert len(reply.data.images) == 2
    assert len(list(Path(tmp_path / "gen").glob("*.png"))) == 2


def test_image_prompt_english_deterministic():
    from examples.repair.image_prompt import build_image_prompt
    from examples.repair.models import DesignOption, ProjectInfo

    info = ProjectInfo(room_type="детская", area=10, budget=30000, ceiling_height=2.7)
    option = DesignOption(
        name="Морской",
        palette={
            "wall_color": "голубой",
            "ceiling_color": "белый",
            "floor_material": "ламинат",
        },
        description="спокойно",
    )
    prompt = build_image_prompt(info, option)
    assert "kids room" in prompt
    assert "10.0 m² floor area" in prompt
    assert "2.7 m" in prompt
    assert "light blue" in prompt
    assert "laminate flooring" in prompt
    assert "contemporary scandinavian" in prompt


def test_style_change_regenerates_options(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"детская","area":10,"budget":200000}',
            '{"options":[{"name":"Разные","palette":{"wall_color":"белый"},'
            '"description":"дефолт"}]}',
            '{"style":"морской"}',
            '{"options":[{"name":"Морская волна","palette":{"wall_color":"голубой",'
            '"style":"морской"},"description":"под стиль"}]}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="детская 10 м², бюджет 200000", session_id="s"))
    asyncio.run(runtime.arun())
    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "design_choice"
    assert project.design_options[0].name == "Разные"

    # the user refines the style — options are regenerated with the style in mind
    ctx.create(UserMsg(text="дизайн в морском стиле", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.info.style == "морской"
    assert project.design_options[0].name == "Морская волна"
    assert "морской" in project.design_options[0].palette.get("style", "")


def test_approved_then_assistant_helps(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"ванная","area":6,"budget":50000}',
            '{"options":[{"name":"Светлая","palette":{"floor_material":"светлый ламинат"},'
            '"description":"лёгкая"}]}',
            '{"steps":[{"name":"Пол","description":"уложить ламинат",'
            '"materials":["~6 упаковок ламината Дуб"]}]}',
            '{"text":"Начните с черновых: выровняйте стены и пол."}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="ванная 6 м² бюджет 50000", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="1", session_id="s"))
    asyncio.run(runtime.arun())

    approve = ctx.create(UserMsg(text="да", session_id="s"))
    asyncio.run(runtime.arun())
    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "assistant"
    assert project.approved is True
    # that very «да» was already handled and did not go to the assistant again
    approve_reps = [
        r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == approve.id
    ]
    assert approve_reps and "утверждены" in approve_reps[-1].data.text

    # the next question — the assistant helps with the repair, with context
    ask = ctx.create(UserMsg(text="с чего начать?", session_id="s"))
    asyncio.run(runtime.arun())
    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == ask.id][
        -1
    ].data.text
    assert "выровняйте" in reply.lower()


class SlowImage(ImageProvider):
    """The image "hangs" — but the flow must continue via the timeout."""

    async def generate(self, prompt, **params):
        import asyncio

        await asyncio.sleep(5.0)
        return b"\x89PNG\r\n\x1a\nslow"


def test_slow_image_preview_does_not_hang(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"детская","area":12,"budget":300000}',
            '{"options":[{"name":"Космос","palette":{"wall_color":"тёмно-синий"},'
            '"description":"планетар"}]}',
        ]
    )
    catalog = Catalog(write_catalog(tmp_path))
    resources = RuntimeResources(llm=llm)
    resources.set("catalog", catalog)
    resources.set("images", SlowImage())
    resources.set("images_dir", str(tmp_path / "gen"))
    resources.set("images_timeout", 0.05)
    resources.set(
        "images_retries", 1
    )  # no retries — the test checks there is no "hanging"

    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[RepairFlow()], budget=Budget(max_runs=200))
    import time as _time

    start = _time.monotonic()
    ctx.create(UserMsg(text="детская 12 м² бюджет 300000", session_id="s"))
    asyncio.run(runtime.arun())
    elapsed = _time.monotonic() - start

    project = ctx.list_artifacts(Project)[0].data
    assert project.stage == "design_choice"
    assert elapsed < 4.0  # the slow render did not hang the pipeline
    assert project.design_options[0].preview == ""  # no preview, but the flow is alive


def test_fallback_options_respect_space_style():
    from examples.repair.fallbacks import fallback_options

    options = fallback_options(
        ProjectInfo(room_type="детская", style="космический", area=12, budget=300000)
    )
    assert len(options) == 3
    assert any("косм" in o.name.lower() for o in options)
    assert all(o.palette.get("style") == "космический" for o in options)
    assert any(o.palette.get("wall_color") in ("тёмно-синий", "синий") for o in options)


def test_image_prompt_space_style():
    from examples.repair.image_prompt import build_image_prompt
    from examples.repair.models import DesignOption

    option = DesignOption(
        name="Космическая станция",
        palette={
            "style": "космический",
            "wall_color": "тёмно-синий",
            "ceiling_color": "белый",
            "floor_material": "синий ковролин",
        },
        description="станция",
    )
    prompt = build_image_prompt(ProjectInfo(room_type="детская", area=12), option)
    assert "space themed" in prompt
    assert "galaxy" in prompt or "starry" in prompt


def test_llm_design_failure_is_honest_not_fallback(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"детская","area":12,"budget":300000}',
            "{}",  # the model returned no options
        ]
    )
    ctx, runtime = build(tmp_path, llm)
    query = ctx.create(UserMsg(text="детская 12 м² бюджет 300000", session_id="s"))
    asyncio.run(runtime.arun())

    reply = [r for r in ctx.list_artifacts(ChatReply) if r.data.query_id == query.id][
        -1
    ].data.text
    # NOT the canned «Светлый/Тёплый/Тёмный», but an honest message
    assert "Светлый" not in reply
    assert "Не удалось подобрать варианты" in reply
    assert ctx.list_artifacts(Project)[0].data.stage == "collect"


class FlakyImage(ImageProvider):
    """The first call fails, the second succeeds: the retry must rescue the image."""

    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, **params):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient image failure")
        return b"\x89PNG\r\n\x1a\nok"


def test_image_retry_recovers_preview(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"детская","area":12,"budget":300000}',
            '{"options":[{"name":"Космос","palette":{"style":"космический"},'
            '"description":"планетар"}]}',
        ]
    )
    catalog = Catalog(write_catalog(tmp_path))
    images = FlakyImage()
    resources = RuntimeResources(llm=llm)
    resources.set("catalog", catalog)
    resources.set("images", images)
    resources.set("images_dir", str(tmp_path / "gen"))

    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[RepairFlow()], budget=Budget(max_runs=200))
    ctx.create(UserMsg(text="детская 12 м² бюджет 300000", session_id="s"))
    asyncio.run(runtime.arun())

    assert images.calls == 2  # the retry worked
    assert (
        ctx.list_artifacts(Project)[0]
        .data.design_options[0]
        .preview.startswith("/assets/generated/")
    )


def test_decline_budget_complaint_replans(tmp_path):
    llm = ScriptedLLM(
        [
            '{"room_type":"детская","area":10,"budget":300000}',
            '{"options":[{"name":"Космос","palette":{"style":"космический"},'
            '"description":"планетар"}]}',
            '{"steps":[{"name":"Пол","description":"дорогой вариант",'
            '"materials":["~20 упаковок ламината Дуб"]}]}',
            "{}",  # edit extraction found nothing
            '{"steps":[{"name":"Пол","description":"бюджетный вариант",'
            '"materials":["~6 упаковок ламината Дуб"]}]}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="детская 10 м² бюджет 300000", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="1", session_id="s"))
    asyncio.run(runtime.arun())
    assert ctx.has_pending_question()

    # «измени, ты вышел за бюджет» is neither «да» nor «нет»: the plan gets rebuilt
    first_total = ctx.list_artifacts(Project)[0].data.estimate.total
    ctx.create(UserMsg(text="измени, ты вышел за бюджет", session_id="s"))
    asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.plan[0].description == "бюджетный вариант"  # the plan was rebuilt
    assert project.stage == "final_approval"  # and waits for approval again
    assert ctx.has_pending_question()
    assert project.estimate.total is not None
    assert project.estimate.total < first_total  # it got cheaper


def test_assistant_remembers_conversation(tmp_path):
    """Chat memory: the post-approval assistant sees the previous turns (view, §27)."""

    class RecordingLLM(LLMProvider):
        def __init__(self, responses):
            self.responses = list(responses)
            self.prompts = []

        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.prompts.append(request.messages[-1].content)
            text = self.responses.pop(0) if self.responses else "{}"
            return LLMResponse(text=text)

        async def stream(self, request):
            yield LLMResponse(text="")

    llm = RecordingLLM(
        [
            '{"room_type":"ванная","area":6,"budget":50000}',
            '{"options":[{"name":"Светлая","palette":{"floor_material":"светлый ламинат"},'
            '"description":"лёгкая"}]}',
            '{"steps":[{"name":"Пол","description":"уложить ламинат",'
            '"materials":["~6 упаковок ламината Дуб"]}]}',
            '{"text":"Ламинат Дуб, 6 упаковок."}',
            '{"text":"Укладка займёт день."}',
        ]
    )
    ctx, runtime = build(tmp_path, llm)

    ctx.create(UserMsg(text="ванная 6 м² бюджет 50000", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="1", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="да", session_id="s"))
    asyncio.run(runtime.arun())

    # two questions to the assistant
    ctx.create(UserMsg(text="какой ламинат?", session_id="s"))
    asyncio.run(runtime.arun())
    ctx.create(UserMsg(text="сколько укладка?", session_id="s"))
    asyncio.run(runtime.arun())

    # the assistant's last prompt remembers the first turn (question + answer)
    prompt2 = llm.prompts[-1]
    assert "Разговор:" in prompt2
    assert "какой ламинат" in prompt2
    assert "Ламинат Дуб, 6 упаковок" in prompt2


def test_catalog_find_prefers_cheaper_among_same_stem(tmp_path):
    """The industrial socket must not win over a household one on the same stem (§67)."""
    csv_file = tmp_path / "mini.csv"
    csv_file.write_text(
        "Мешок мусорный,10,₽/шт\n"
        "Розетка стационарная ССИ-145 MAGNUM 125А 3Р+РЕ+N 380В IP67,15958,₽/шт\n"
        "Розетка наружная разборная для плиты с заземлением 32А белая,68.29,₽/шт\n",
        encoding="utf-8",
    )
    from examples.repair.services.catalog import Catalog

    catalog = Catalog(str(csv_file))
    picked = catalog.find("розетки")
    assert picked is not None
    assert picked.price < 15958  # not the industrial one
    top = catalog.search("розетка", top_k=5)
    assert top and top[0].price < 15958


def test_parse_facts_units():
    from examples.repair.services import parse_facts

    assert parse_facts("10 метров, 300 тысяч рублей") == {
        "area": 10.0,
        "budget": 300000.0,
    }
    assert parse_facts("ванная 6 м² бюджет 50к") == {
        "room_type": "ванная",
        "area": 6.0,
        "budget": 50000.0,
    }
    # «потолок 2.7 м» must be the height, not the area
    kitchen = parse_facts("Кухня 12 кв.м, потолок 2.7м, бюджет 150000 рублей")
    assert kitchen["area"] == 12.0
    assert kitchen["ceiling_height"] == 2.7
    assert parse_facts("просто ремонт") == {}


def test_offline_multi_turn_reaches_design_choice(tmp_path):
    """No model: facts are still collected, so the demo does not loop on «площадь»."""
    ctx, runtime = build(tmp_path, None)
    for text in ("детская, в космическом стиле", "10 метров, 300 тысяч рублей"):
        ctx.create(UserMsg(text=text, session_id="s"))
        asyncio.run(runtime.arun())

    project = ctx.list_artifacts(Project)[0].data
    assert project.info.area == 10.0
    assert project.info.budget == 300000.0
    assert project.info.room_type == "детская"
    assert project.info.style == "космический"
    assert project.stage == "design_choice"


def test_model_missed_area_is_filled_from_text(tmp_path):
    """A configured model that leaves `area` null is corrected by the parser."""
    llm = ScriptedLLM(['{"room_type": "детская", "budget": 300000}'])
    ctx, runtime = build(tmp_path, llm)
    ctx.create(UserMsg(text="детская 10 метров, 300 тысяч рублей", session_id="s"))
    asyncio.run(runtime.arun())

    info = ctx.list_artifacts(Project)[0].data.info
    assert info.area == 10.0
    assert info.budget == 300000.0
    assert info.room_type == "детская"

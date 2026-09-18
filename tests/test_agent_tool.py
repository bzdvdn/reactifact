import asyncio

from pydantic import BaseModel
from reactifact import (
    Agent,
    Consume,
    Context,
    Produce,
    ProduceCall,
    Runtime,
    RuntimeResources,
)
from reactifact.agent_tool import AgentAsTool, SubTask
from reactifact.llm_agent import HITLLMAgent, LLMAgent
from reactifact.providers import LLMProvider, LLMRequest, LLMResponse
from reactifact.tool_use import ToolAnswer


class ScriptedLLM(LLMProvider):
    def __init__(self, responses):
        self.responses = list(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text)

    async def stream(self, request):
        yield LLMResponse(text="")


class Question(BaseModel):
    text: str = ""


class MainReport(BaseModel):
    text: str = ""


def sub_agent_factory():
    class SubAgent(LLMAgent):
        system = "Ты специалист-эксперт."
        tools = []
        consumes = [Consume(SubTask)]

    return SubAgent()


def test_agent_as_tool_returns_sub_agent_final_answer():
    sub_llm = ScriptedLLM(['{"type":"answer","text":"42"}'])
    delegate = AgentAsTool(
        name="ask_specialist",
        description="Delegate a sub-question to a specialist agent.",
        agent_factory=sub_agent_factory,
        resources=RuntimeResources(llm=sub_llm),
    )

    main_llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"ask_specialist","args":{"query":"what is the answer?"}}',
            '{"type":"answer","text":"Специалист сказал: 42"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=main_llm))

    class BuildReport(Produce[MainReport]):
        artifact_type = MainReport

        async def produce(self, call: ProduceCall):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            self.effects.create(MainReport(text=a.data.text))

    class MainAgent(LLMAgent):
        system = "Делегируй специалисту при необходимости."
        tools = [delegate]
        consumes = [Consume(Question)]
        produces = [BuildReport()]

    runtime = Runtime(ctx, agents=[MainAgent()])
    ctx.create(Question(text="сколько будет?"))
    asyncio.run(runtime.arun())

    reports = ctx.list_artifacts(MainReport)
    assert len(reports) == 1
    assert reports[0].data.text == "Специалист сказал: 42"


def test_agent_as_tool_isolated_from_parent_history():
    """The sub-agent never sees the parent's own artifacts — only the query."""
    seen_inputs = []

    def sub_factory():
        class RecordingSubAgent(Agent):
            consumes = [Consume(SubTask)]
            produces = []

            async def run(self, event, context):
                from reactifact import Patch

                seen_inputs.append(
                    [type(a.data).__name__ for a in context.list_artifacts()]
                )
                task = context.get(event.artifact_id)
                if task is None:
                    return None
                return Patch().create(ToolAnswer(text=f"echo:{task.data.text}"))

        return RecordingSubAgent()

    delegate = AgentAsTool(
        name="echo",
        description="Echoes the query back through an isolated sub-agent.",
        agent_factory=sub_factory,
        resources=RuntimeResources(),
    )

    main_llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"echo","args":{"query":"hi"}}',
            '{"type":"answer","text":"done"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=main_llm))

    class BuildReport(Produce[MainReport]):
        artifact_type = MainReport

        async def produce(self, call: ProduceCall):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            self.effects.create(MainReport(text=a.data.text))

    class MainAgent(LLMAgent):
        system = "агент"
        tools = [delegate]
        consumes = [Consume(Question)]
        produces = [BuildReport()]

    runtime = Runtime(ctx, agents=[MainAgent()])
    ctx.create(Question(text="parent question"))
    asyncio.run(runtime.arun())

    # the sub-agent's own context only ever contained its SubTask, never the
    # parent's Question/ToolAnswer/Observation artifacts
    assert seen_inputs == [["SubTask"]]
    assert ctx.list_artifacts(MainReport)[0].data.text == "done"


def test_agent_as_tool_reports_unanswered_pending_question_instead_of_empty_text():
    sub_llm = ScriptedLLM(['{"type":"ask","text":"Какой namespace?"}'])

    def hitl_sub_factory():
        class SubAgent(HITLLMAgent):
            system = "агент"
            tools = []
            consumes = [Consume(SubTask)]

        return SubAgent()

    delegate = AgentAsTool(
        name="ask_hitl",
        description="Delegates to a HITL sub-agent (unsupported).",
        agent_factory=hitl_sub_factory,
        resources=RuntimeResources(llm=sub_llm),
    )

    main_llm = ScriptedLLM(
        [
            '{"type":"tool_call","tool":"ask_hitl","args":{"query":"почисти под"}}',
            '{"type":"answer","text":"не получилось делегировать"}',
        ]
    )
    ctx = Context(resources=RuntimeResources(llm=main_llm))

    class BuildReport(Produce[MainReport]):
        artifact_type = MainReport

        async def produce(self, call: ProduceCall):
            a = call.trigger
            if a is None or not isinstance(a.data, ToolAnswer):
                return None
            self.effects.create(MainReport(text=a.data.text))

    class MainAgent(LLMAgent):
        system = "агент"
        tools = [delegate]
        consumes = [Consume(Question)]
        produces = [BuildReport()]

    runtime = Runtime(ctx, agents=[MainAgent()])
    ctx.create(Question(text="почисти под"))
    asyncio.run(runtime.arun())

    # the delegating loop got an explanatory error, not silent empty text,
    # and still reached its own final answer
    assert ctx.list_artifacts(MainReport)[0].data.text == "не получилось делегировать"

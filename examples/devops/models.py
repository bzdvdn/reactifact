from pydantic import BaseModel


class UserMsg(BaseModel):
    text: str
    session_id: str = ""


class ChatReply(BaseModel):
    query_id: str
    text: str
    kind: str = "text"


# Problems the base chat router creates from a message
class K8sProblem(BaseModel):
    text: str
    query_id: str = ""


class GitlabProblem(BaseModel):
    text: str
    query_id: str = ""


class AnsibleProblem(BaseModel):
    text: str
    query_id: str = ""


# Typed tool results. The LLM sees these as JSON; the app can read
# `ToolOutput.data` without parsing a log line.
class PodState(BaseModel):
    name: str
    namespace: str
    status: str
    restarts: int = 0
    last_state: str = ""
    reason: str = ""
    events: list[str] = []


class K8sSnapshot(BaseModel):
    namespace: str
    resource: str
    summary: str
    pods: list[PodState] = []


class MergeRequest(BaseModel):
    iid: str
    title: str
    state: str


class MergeRequests(BaseModel):
    project: str
    query: str
    items: list[MergeRequest] = []


class PipelineState(BaseModel):
    project: str
    pipeline_id: str
    status: str
    stage: str
    job: str


class PlayRecap(BaseModel):
    playbook: str
    role: str
    hosts: str
    ok: int
    changed: int
    failed: int
    unreachable: int = 0


# Reports that each LLM agent builds its own way
class K8sReport(BaseModel):
    query_id: str = ""
    text: str = ""


class GitlabReport(BaseModel):
    query_id: str = ""
    text: str = ""


class AnsibleReport(BaseModel):
    query_id: str = ""
    text: str = ""

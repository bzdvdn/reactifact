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

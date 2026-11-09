from pydantic import BaseModel
from reactifact import Context
from reactifact.artifacts import Artifact


class Person(BaseModel):
    name: str
    age: int
    address: dict = {}


def test_create_artifact():
    person = Person(name="Alice", age=30)
    artifact = Artifact(data=person)
    assert artifact.data.name == "Alice"
    assert artifact.data.age == 30
    assert artifact.version == 0
    assert artifact.history == []


def test_update_artifact():
    person_v1 = Person(name="Alice", age=30)
    context = Context()
    created = context.create(person_v1)

    person_v2 = Person(name="Alice", age=31)
    updated = context.update(created.id, person_v2)
    assert updated is not None

    assert updated.data.age == 31
    assert updated.version == 1
    assert len(updated.history) == 1
    # The old version is preserved in the store
    assert updated.history[0].age == 30
    assert updated.history[0].name == "Alice"


def test_history_is_immutable():
    context = Context()
    created = context.create(Person(name="Bob", age=25))
    context.update(created.id, Person(name="Bob", age=26))
    artifact = context.get(created.id)
    assert artifact is not None

    # Mutating the returned list must not affect the artifact
    history = artifact.history
    history.clear()
    assert len(artifact.history) == 1


def test_artifact_diff():
    context = Context()
    created = context.create(Person(name="Alice", age=30, address={"city": "NY"}))

    context.update(created.id, Person(name="Alice", age=31, address={"city": "LA"}))
    artifact = context.get(created.id)
    assert artifact is not None

    diff = artifact.diff(0, 1)
    assert "age" in diff["changed"]
    assert diff["changed"]["age"]["old"] == 30
    assert diff["changed"]["age"]["new"] == 31
    assert "address" in diff["changed"]
    assert "city" in diff["changed"]["address"]["changed"]
    assert diff["changed"]["address"]["changed"]["city"]["old"] == "NY"
    assert diff["changed"]["address"]["changed"]["city"]["new"] == "LA"

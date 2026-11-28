"""Missing-input errors should teach the fix, not just name the parameter."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from reactifact import Context, Runtime, step


class A(BaseModel):
    value: int


class B(BaseModel):
    value: int


class C(BaseModel):
    value: int


@step(produces=B, reads=A)
def make_b(a: A) -> B:
    return B(value=a.value + 1)


@step(produces=C, reads=(A, B))
def make_c(a: A, b: B) -> C:
    return C(value=a.value + b.value)


def test_missing_input_lists_the_producer_and_the_wakes_hint():
    context = Context()
    context.create(A(value=1))
    with pytest.raises(TypeError) as error:
        Runtime(context, steps=[make_b, make_c]).run()
    message = str(error.value)
    assert "Missing input" in message
    assert "make_b" in message
    assert "read-only" in message
    assert "Consume(T)" in message
    assert "parameter 'b'" in message


def test_missing_input_says_when_nothing_produces_it():
    @step(produces=C, reads=(A, B))
    def lonely(a: A, b: B) -> C:
        return C(value=a.value + b.value)

    context = Context()
    context.create(A(value=1))
    with pytest.raises(TypeError) as error:
        Runtime(context, steps=[lonely], agents=[]).run()
    message = str(error.value)
    assert "No step in this runtime produces it" in message

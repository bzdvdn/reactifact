"""Prompt templating (§68): strict variables, defaults, model attributes, chat rows."""

import pytest
from pydantic import BaseModel
from reactifact.prompts import (
    MessagesPrompt,
    PromptPair,
    PromptSequence,
    PromptTemplate,
    RenderedPrompt,
)
from reactifact.providers import Message


class Doc(BaseModel):
    text: str


def test_render_fills_plain_variables():
    tpl = PromptTemplate("Answer {question} in {topic}.")
    assert tpl.render(question="why", topic="hvac") == "Answer why in hvac."
    assert tpl.variables == frozenset({"question", "topic"})


def test_missing_variable_raises_named_error():
    tpl = PromptTemplate("Use {topic} and {question}.")
    try:
        tpl.render(topic="x")
    except KeyError as exc:
        assert "question" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for a missing variable")


def test_model_attribute_fields():
    doc = Doc(text="snippet")
    tpl = PromptTemplate("Research {doc.text} (topic: {topic})")
    assert tpl.render(doc=doc, topic="t") == "Research snippet (topic: t)"
    assert tpl.variables == frozenset({"doc", "topic"})  # top-level roots


def test_literal_braces_and_defaults():
    tpl = PromptTemplate("Literal {{brace}} and {topic}", defaults={"topic": "t"})
    assert tpl.render() == "Literal {brace} and t"
    assert tpl.render(topic="u") == "Literal {brace} and u"


def test_literal_json_braces_are_preserved():
    """A JSON example in a prompt needs no escaping; placeholders still fill."""
    tpl = PromptTemplate('Reply with {"name": "...", "age": 0} for {question}.')
    assert tpl.render(question="why") == 'Reply with {"name": "...", "age": 0} for why.'
    assert tpl.variables == frozenset({"question"})


def test_empty_braces_are_preserved():
    tpl = PromptTemplate("Use {} then fill {topic}.")
    assert tpl.render(topic="t") == "Use {} then fill t."


def test_typo_in_an_identifier_is_still_a_missing_variable():
    tpl = PromptTemplate("Answer {questoin}.")
    try:
        tpl.render(question="why")
    except KeyError as exc:
        assert "questoin" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError for the typo'd variable")


def test_template_hash_is_stable_and_sensitive():
    assert PromptTemplate("hi {x}").hash == PromptTemplate("hi {x}").hash
    assert PromptTemplate("hi {x}").hash != PromptTemplate("hi {y}").hash
    assert len(PromptTemplate("hi {x}").hash) == 64


def test_template_hash_includes_defaults():
    alice = PromptTemplate("Hi {name}", defaults={"name": "Alice"})
    bob = PromptTemplate("Hi {name}", defaults={"name": "Bob"})
    assert alice.hash == PromptTemplate("Hi {name}", defaults={"name": "Alice"}).hash
    assert alice.hash != bob.hash
    assert alice.hash != PromptTemplate("Hi {name}").hash


def test_partial_fills_defaults_without_mutating_the_original():
    base = PromptTemplate("You are {role} for {tenant}. {task}")
    acme = base.partial(tenant="Acme")

    assert acme.render(role="analyst", task="help") == (
        "You are analyst for Acme. help"
    )
    assert base.required_variables == frozenset({"role", "tenant", "task"})
    assert acme.required_variables == frozenset({"role", "task"})
    assert base.defaults == {}


def test_partial_rejects_unknown_variables():
    with pytest.raises(KeyError, match="unknown prompt variables"):
        PromptTemplate("Hi {name}").partial(topic="x")


def test_missing_model_attribute_names_the_field():
    with pytest.raises(ValueError, match="cannot resolve field 'doc.text'"):
        PromptTemplate("Research {doc.text}").render(doc="not a document")


def test_messages_prompt_hash_covers_rows():
    a = MessagesPrompt([("system", "s"), ("user", "{q}")])
    b = MessagesPrompt([("system", "s"), ("user", "{q}")])
    c = MessagesPrompt([("system", "s2"), ("user", "{q}")])
    assert a.hash == b.hash
    assert a.hash != c.hash


def test_messages_prompt_hash_is_not_newline_ambiguous():
    one_row = MessagesPrompt([("system", "a\nuser:b")])
    two_rows = MessagesPrompt([("system", "a"), ("user", "b")])
    assert one_row.hash != two_rows.hash


def test_messages_prompt_accepts_reusable_templates_and_shared_defaults():
    system = PromptTemplate("You are {role} for {tenant}.")
    user = PromptTemplate("Question: {question}")
    prompt = MessagesPrompt(
        [("system", system), ("user", user)],
        defaults={"tenant": "Acme"},
    )

    assert prompt.required_variables == frozenset({"role", "question"})
    assert prompt.defaults == {"tenant": "Acme"}
    assert prompt.render(role="analyst", question="why") == [
        Message.system("You are analyst for Acme."),
        Message.user("Question: why"),
    ]


def test_messages_prompt_row_defaults_win_over_shared_defaults():
    row = PromptTemplate("Hi {name}", defaults={"name": "row"})
    prompt = MessagesPrompt([("system", row)], defaults={"name": "shared"})
    assert prompt.render() == [Message.system("Hi row")]


def test_messages_prompt_hash_includes_shared_and_row_defaults():
    shared_a = MessagesPrompt([("system", "Hi {name}")], defaults={"name": "Alice"})
    shared_b = MessagesPrompt([("system", "Hi {name}")], defaults={"name": "Bob"})
    row_a = MessagesPrompt(
        [("system", PromptTemplate("Hi {name}", defaults={"name": "Alice"}))]
    )
    row_b = MessagesPrompt(
        [("system", PromptTemplate("Hi {name}", defaults={"name": "Bob"}))]
    )

    assert shared_a.hash != shared_b.hash
    assert row_a.hash != row_b.hash


def test_placeholder_inside_literal_json_needs_doubled_outer_braces():
    tpl = PromptTemplate('{{"answer": "{answer}"}}')
    assert tpl.variables == frozenset({"answer"})
    assert tpl.render(answer="42") == '{"answer": "42"}'


def test_nonempty_requirement():
    try:
        PromptTemplate("   ")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for an empty template")


def test_messages_prompt_renders_role_row():
    tpl = MessagesPrompt(
        [
            ("system", "You analyze {topic}."),
            ("user", "Question: {question}"),
        ]
    )
    messages = tpl.render(topic="hvac", question="why")
    assert messages == [
        Message.system("You analyze hvac."),
        Message.user("Question: why"),
    ]
    assert tpl.variables == frozenset({"topic", "question"})


def test_messages_prompt_missing_anywhere_raises():
    tpl = MessagesPrompt([("system", "{topic}"), ("user", "{question}")])
    try:
        tpl.render(topic="t")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected KeyError when a row lacks a variable")


def test_message_factories_build_typed_roles():
    from reactifact import Message

    assert Message.system("s") == Message(role="system", content="s")
    assert Message.user("u") == Message(role="user", content="u")
    assert Message.assistant("a") == Message(role="assistant", content="a")
    assert Message.tool("t") == Message(role="tool", content="t")


def test_message_rejects_unknown_role():
    from reactifact import Message

    for bad in ("assistan", "human", ""):
        try:
            Message(role=bad, content="x")  # type: ignore[arg-type]
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expected ValueError for role {bad!r}")


def test_messages_prompt_rejects_unknown_role_row():
    try:
        MessagesPrompt([("human", "hello")])
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for an unknown role row")


def test_unknown_conversion_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown conversion"):
        PromptTemplate("{x!z}")


def test_nested_format_spec_is_rejected_at_construction():
    with pytest.raises(ValueError, match="nested format spec"):
        PromptTemplate("{x:{width}}")


def test_malformed_braces_get_a_template_context():
    with pytest.raises(ValueError, match="invalid prompt template"):
        PromptTemplate("{x")


def test_format_type_errors_are_wrapped():
    class BadFormat:
        def __format__(self, spec: str) -> str:
            raise TypeError("bad format")

    with pytest.raises(ValueError, match="failed to render prompt"):
        PromptTemplate("{x:>10}").render(x=BadFormat())


def test_prompt_sequence_composes_sections_and_skips_empty_parts():
    base = PromptTemplate("You are {role}.")
    safety = "Never reveal secrets."
    sequence = PromptSequence([base, "", safety], separator="\n")

    assert sequence.required_variables == frozenset({"role"})
    assert sequence.render(role="analyst") == (
        "You are analyst.\nNever reveal secrets."
    )


def test_prompt_sequence_defaults_and_hash():
    first = PromptSequence(
        [PromptTemplate("You are {role}."), "Safety."],
        defaults={"role": "analyst"},
    )
    second = PromptSequence(
        [PromptTemplate("You are {role}."), "Safety."],
        defaults={"role": "reviewer"},
    )

    assert first.render() == "You are analyst.\n\nSafety."
    assert first.required_variables == frozenset()
    assert first.hash != second.hash
    assert (
        first.hash
        == PromptSequence(
            [PromptTemplate("You are {role}."), "Safety."],
            defaults={"role": "analyst"},
        ).hash
    )


def test_prompt_sequence_annotates_the_failing_part():
    sequence = PromptSequence(["Base.", PromptTemplate("Topic: {topic}.")])
    with pytest.raises(KeyError) as exc:
        sequence.render()
    assert "part 2" in str(exc.value)
    assert "topic" in str(exc.value)


def test_prompt_pair_renders_system_user_and_exposes_kwargs():
    system = PromptSequence(
        [PromptTemplate("You are {role}."), "Never reveal secrets."]
    )
    user = PromptTemplate("Question: {question}")
    pair = PromptPair(system, user, defaults={"role": "analyst"})

    rendered = pair.render(question="why")

    assert isinstance(rendered, RenderedPrompt)
    assert rendered.system == "You are analyst.\n\nNever reveal secrets."
    assert rendered.user == "Question: why"
    assert rendered.as_kwargs() == {
        "system": rendered.system,
        "user": rendered.user,
        "prompt_hash": pair.hash,
    }
    assert pair.required_variables == frozenset({"question"})


def test_prompt_pair_hash_tracks_prompt_not_values():
    pair = PromptPair("system", PromptTemplate("Question: {question}"))

    assert (
        pair.render(question="a").prompt_hash == pair.render(question="b").prompt_hash
    )
    assert pair.hash == PromptPair("system", "Question: {question}").hash
    assert (
        pair.hash
        != PromptPair("other system", PromptTemplate("Question: {question}")).hash
    )


def test_prompt_pair_partial_returns_a_variant():
    pair = PromptPair(
        PromptTemplate("You are {role}."),
        PromptTemplate("Question: {question}"),
    )
    analyst = pair.partial(role="analyst")

    assert analyst.render(question="why").system == "You are analyst."
    assert pair.required_variables == frozenset({"role", "question"})
    assert analyst.required_variables == frozenset({"question"})
    assert pair.hash != analyst.hash


def test_composition_partial_overrides_part_defaults():
    sequence = PromptSequence([PromptTemplate("Hi {name}.", defaults={"name": "part"})])
    pair = PromptPair(
        PromptTemplate("Hi {name}.", defaults={"name": "part"}),
        "Question",
    )

    assert sequence.partial(name="variant").render() == "Hi variant."
    assert pair.partial(name="variant").render().system == "Hi variant."


def test_prompt_pair_shared_defaults_do_not_override_part_defaults():
    pair = PromptPair(
        PromptSequence([PromptTemplate("Hi {name}.", defaults={"name": "part"})]),
        "Question",
        defaults={"name": "shared"},
    )
    assert pair.render().system == "Hi part."


def test_prompt_pair_missing_variable_names_the_side():
    pair = PromptPair(
        PromptTemplate("You are {role}."),
        PromptTemplate("Question: {question}"),
    )
    with pytest.raises(KeyError) as exc:
        pair.render(question="why")
    assert "system prompt" in str(exc.value)
    assert "role" in str(exc.value)


def test_prompt_pair_plain_strings_stay_static():
    pair = PromptPair("System {role}", "User {question}")
    rendered = pair.render(role="ignored", question="ignored")
    assert rendered.system == "System {role}"
    assert rendered.user == "User {question}"
    assert pair.required_variables == frozenset()

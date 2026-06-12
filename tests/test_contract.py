# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The step contract (converted salvage groundwork): every distinct validation
failure fails loud at load time. The full loader slice is 0.0.3."""

import pytest

from generic_ml_workflow.core.contract import (
    InputSpec,
    Lifespan,
    OutputKind,
    OutputSpec,
    StepNature,
    StepSpec,
    Workflow,
)


def _out(name="doc", lifespan=Lifespan.DURABLE, kind=OutputKind.FILE):
    return OutputSpec(name=name, lifespan=lifespan, kind=kind, filename=f"{name}.md")


def _shot(id="analyze", **kw):
    kw.setdefault("cap", "analyst")
    kw.setdefault("outputs", (_out(),))
    return StepSpec(id=id, nature=StepNature.INTERPRETABLE, **kw)


def _exe(id="fetch", **kw):
    kw.setdefault("entrypoint", "fetch_page")
    kw.setdefault("outputs", (_out("page"),))
    return StepSpec(id=id, nature=StepNature.EXECUTABLE, **kw)


def test_input_must_have_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one source"):
        InputSpec(name="x")
    with pytest.raises(ValueError, match="exactly one source"):
        InputSpec(name="x", from_literal=True, from_step="a.b")
    InputSpec(name="x", from_literal=True)  # fine
    InputSpec(name="x", from_step="a.b")  # fine


def test_interpretable_step_requires_a_cap():
    with pytest.raises(ValueError, match="must declare a cap"):
        StepSpec(id="s", nature=StepNature.INTERPRETABLE).validate()


def test_executable_step_requires_an_entrypoint():
    with pytest.raises(ValueError, match="must declare an entrypoint"):
        StepSpec(id="s", nature=StepNature.EXECUTABLE).validate()


def test_duplicate_output_names_rejected():
    s = _shot(outputs=(_out("a"), _out("a")))
    with pytest.raises(ValueError, match="duplicate output names"):
        s.validate()


def test_at_most_one_questions_output():
    qs = _out("q1", Lifespan.TRANSPORT, OutputKind.QUESTIONS)
    qs2 = OutputSpec(
        name="q2", lifespan=Lifespan.TRANSPORT, kind=OutputKind.QUESTIONS, filename="q2.json"
    )
    with pytest.raises(ValueError, match="more than one questions output"):
        _shot(outputs=(qs, qs2)).validate()


def test_workflow_rejects_empty_and_duplicate_steps():
    with pytest.raises(ValueError, match="has no steps"):
        Workflow(name="w", steps=()).validate()
    with pytest.raises(ValueError, match="repeats step id"):
        Workflow(name="w", steps=(_exe("a"), _exe("a"))).validate()


def test_workflow_wiring_must_reference_an_earlier_step():
    wired = _shot("analyze", inputs=(InputSpec(name="page", from_step="fetch.page"),))
    Workflow(name="w", steps=(_exe("fetch"), wired)).validate()  # fine: earlier
    with pytest.raises(ValueError, match="unknown step"):
        Workflow(name="w", steps=(wired,)).validate()
    with pytest.raises(ValueError, match="does not run earlier"):
        Workflow(name="w", steps=(wired, _exe("fetch"))).validate()


def test_helpers_durable_and_questions():
    qs = _out("questions", Lifespan.TRANSPORT, OutputKind.QUESTIONS)
    s = _shot(outputs=(_out("doc"), qs))
    assert [o.name for o in s.durable_outputs()] == ["doc"]
    assert s.questions_output().name == "questions"

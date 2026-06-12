# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The ports-and-bindings contract (DESIGN.md SS7): local step checks, and the
workflow's deduced-correctness pass (errors + the dead-branch warning)."""

import pytest

from generic_ml_workflow.core.contract import (
    Binding,
    InputPort,
    InputType,
    Lifespan,
    OutputKind,
    OutputPort,
    Requirement,
    StepNature,
    StepSpec,
    Workflow,
    WorkflowError,
)


def _out(name="doc", lifespan=Lifespan.DURABLE, kind=OutputKind.FILE):
    return OutputPort(name=name, lifespan=lifespan, kind=kind, filename=f"{name}.md")


def _art(name):
    return InputPort(name=name, requirement=Requirement.ARTIFACT)


def _shot(id="analyze", **kw):
    kw.setdefault("cap", "analyst")
    kw.setdefault("outputs", (_out(),))
    return StepSpec(id=id, nature=StepNature.INTERPRETABLE, **kw)


def _exe(id="fetch", **kw):
    kw.setdefault("entrypoint", "fetch_page")
    kw.setdefault("outputs", (_out("page"),))
    return StepSpec(id=id, nature=StepNature.EXECUTABLE, **kw)


def _wf(steps, bindings=(), name="w", input_type=InputType.FREESTYLE):
    return Workflow(name=name, input_type=input_type, steps=tuple(steps), bindings=tuple(bindings))


# --- local step validity -----------------------------------------------------


def test_interpretable_step_requires_a_cap():
    with pytest.raises(WorkflowError, match="must declare a cap"):
        StepSpec(id="s", nature=StepNature.INTERPRETABLE).validate()


def test_executable_step_requires_an_entrypoint():
    with pytest.raises(WorkflowError, match="must declare an entrypoint"):
        StepSpec(id="s", nature=StepNature.EXECUTABLE).validate()


def test_duplicate_output_names_rejected():
    with pytest.raises(WorkflowError, match="duplicate output names"):
        _shot(outputs=(_out("a"), _out("a"))).validate()


def test_duplicate_input_ports_rejected():
    with pytest.raises(WorkflowError, match="duplicate input port"):
        _shot(inputs=(_art("x"), _art("x"))).validate()


def test_at_most_one_questions_output():
    q1 = _out("q1", Lifespan.TRANSPORT, OutputKind.QUESTIONS)
    q2 = _out("q2", Lifespan.TRANSPORT, OutputKind.QUESTIONS)
    with pytest.raises(WorkflowError, match="more than one questions output"):
        _shot(outputs=(q1, q2)).validate()


# --- workflow-level deduced correctness --------------------------------------


def test_empty_workflow_is_an_error():
    assert "has no steps" in _wf([]).validate().errors[0]


def test_a_valid_two_hop_chain_passes_clean():
    steps = [
        _exe("fetch", outputs=(_out("page"),)),
        _shot("summarize", inputs=(_art("text"),), outputs=(_out("summary"),)),
    ]
    # summarize consumes fetch's product; summary is terminal (unconsumed -> warning)
    binds = [Binding("summarize", "text", "page")]
    result = _wf(steps, binds).validate()
    assert result.ok  # no errors
    assert any("summary" in w for w in result.warnings)  # terminal deliverable lint


def test_unbound_required_port_is_an_error():
    steps = [_shot("summarize", inputs=(_art("text"),), outputs=(_out("summary"),))]
    result = _wf(steps).validate()  # no binding for 'text'
    assert not result.ok
    assert any("nothing is bound to it" in e for e in result.errors)


def test_binding_to_a_product_nothing_contributes_is_an_error():
    steps = [_shot("summarize", inputs=(_art("text"),), outputs=(_out("summary"),))]
    binds = [Binding("summarize", "text", "ghost")]
    result = _wf(steps, binds).validate()
    assert any("nothing contributes" in e for e in result.errors)


def test_binding_to_a_later_product_is_an_error():
    # summarize (step 1) consumes a product only produced by fetch (step 2) -- ordering
    steps = [
        _shot("summarize", inputs=(_art("text"),), outputs=(_out("summary"),)),
        _exe("fetch", outputs=(_out("page"),)),
    ]
    binds = [Binding("summarize", "text", "page")]
    result = _wf(steps, binds).validate()
    assert any("nothing contributes before it" in e for e in result.errors)


def test_two_products_under_one_name_is_an_error():
    steps = [
        _exe("a", outputs=(_out("page"),)),
        _exe("b", outputs=(_out("page"),)),
    ]
    result = _wf(steps).validate()
    assert any("product names are unique" in e for e in result.errors)


def test_an_update_uses_a_new_name_and_is_fine():
    steps = [
        _exe("fetch", outputs=(_out("page"),)),
        _exe("enrich", inputs=(_art("p"),), outputs=(_out("page_enriched"),)),
        _shot("use", inputs=(_art("e"),), outputs=(_out("final"),)),
    ]
    binds = [
        Binding("enrich", "p", "page"),
        Binding("use", "e", "page_enriched"),
    ]
    assert _wf(steps, binds).validate().ok


def test_dead_branch_lint_warns_on_a_forgotten_rebind():
    # enrich produces page_enriched, but 'use' still consumes the old 'page' --
    # page_enriched hangs unconsumed: the classic insertion bug, surfaced.
    steps = [
        _exe("fetch", outputs=(_out("page"),)),
        _exe("enrich", inputs=(_art("p"),), outputs=(_out("page_enriched"),)),
        _shot("use", inputs=(_art("e"),), outputs=(_out("final"),)),
    ]
    binds = [
        Binding("enrich", "p", "page"),
        Binding("use", "e", "page"),  # forgot to rebind to page_enriched
    ]
    result = _wf(steps, binds).validate()
    assert result.ok  # it's a warning, not an error
    assert any("page_enriched" in w and "never consumed" in w for w in result.warnings)


def test_duplicate_binding_for_one_port_is_an_error():
    steps = [
        _exe("fetch", outputs=(_out("page"),)),
        _shot("use", inputs=(_art("t"),), outputs=(_out("final"),)),
    ]
    binds = [Binding("use", "t", "page"), Binding("use", "t", "page")]
    assert any("bound more than once" in e for e in _wf(steps, binds).validate().errors)


# --- computed requirements (for the run interview, slice 0.0.5) ---------------


def test_run_inputs_are_the_deduped_union():
    steps = [
        StepSpec(
            id="a",
            nature=StepNature.EXECUTABLE,
            entrypoint="e",
            inputs=(InputPort("url", Requirement.RUN_INPUT),),
            outputs=(_out("x"),),
        ),
        StepSpec(
            id="b",
            nature=StepNature.EXECUTABLE,
            entrypoint="e",
            inputs=(
                InputPort("url", Requirement.RUN_INPUT),  # same name -> deduped
                InputPort("depth", Requirement.RUN_INPUT),
            ),
            outputs=(_out("y"),),
        ),
    ]
    wf = _wf(steps)
    assert wf.run_inputs() == ("url", "depth")


def test_credential_roles_collected():
    step = StepSpec(
        id="fetch",
        nature=StepNature.EXECUTABLE,
        entrypoint="e",
        inputs=(InputPort("token", Requirement.CREDENTIAL),),
        outputs=(_out("x"),),
    )
    assert _wf([step]).credential_roles() == ("token",)

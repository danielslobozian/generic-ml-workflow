# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The typed, self-describing event vocabulary (DESIGN.md SS11)."""

import pytest

from generic_ml_workflow.core import eventtypes as et


def test_every_type_has_a_registered_bean():
    # the module-level assert guards this; confirm the mapping is total here too
    assert {t.value for t in et.EventType} == set(et.event_types())
    for t in et.EventType:
        assert issubclass(et.bean_for(t), et.Payload)


def test_payload_round_trips_through_json():
    a = et.ArtifactCreated(name="page", path="/runs/x/page.html", sha256="ab" * 32)
    again = et.parse_payload(et.EventType.ARTIFACT_CREATED, a.to_json())
    assert again == a


def test_stamp_payload_carries_references_not_objects():
    s = et.WorkflowExecutionStarted(
        workflow_name="feature",
        input_type="url",
        commit="abc123",
        branch="main",
        engine_version="0.0.4.dev0",
    )
    d = s.to_json()
    assert d["workflow_name"] == "feature" and d["commit"] == "abc123"
    # no embedded definition object -- references and scalars only
    assert set(d) <= {
        "workflow_name",
        "input_type",
        "commit",
        "branch",
        "engine_version",
        "job_id",
        "mode",
    }


def test_parse_rejects_unknown_field():
    with pytest.raises(ValueError, match="unexpected payload fields"):
        et.parse_payload("artifact.created", {"name": "x", "path": "/p", "bogus": 1})


def test_parse_rejects_missing_required_field():
    with pytest.raises(ValueError, match="malformed payload"):
        et.parse_payload("artifact.created", {"name": "x"})  # path missing


def test_bean_for_unknown_type_raises():
    with pytest.raises(ValueError):  # EventType("nope") fails first
        et.bean_for("nope.nope")


def test_describe_lists_fields_with_required_flag():
    desc = et.describe(et.EventType.RUN_INPUT_PROVIDED)
    assert desc["event_type"] == "run_input.provided"
    names = {f["name"]: f for f in desc["payload"]}
    assert names["name"]["required"] and names["value"]["required"]


def test_describe_marks_optional_fields():
    desc = et.describe("step.failed")
    names = {f["name"]: f for f in desc["payload"]}
    assert names["step_name"]["required"]
    assert not names["reason"]["required"]  # has a default
    assert not names["attempt"]["required"]


def test_event_types_is_the_closed_set():
    assert "artifact.created" in et.event_types()
    assert len(et.event_types()) == len(et.EventType)


def test_tier_overridden_registered_and_round_trips():
    from generic_ml_workflow.core import eventtypes as et

    bean = et.bean_for(et.EventType.TIER_OVERRIDDEN)
    p = bean(step_name="summarize", from_tier="medium", to_tier="high")
    again = et.parse_payload(et.EventType.TIER_OVERRIDDEN, p.to_json())
    assert again.step_name == "summarize" and again.from_tier == "medium"
    assert again.to_tier == "high"
    assert "tier.overridden" in et.event_types()


def test_step_completed_carries_usage_and_round_trips():
    from generic_ml_workflow.core import eventtypes as _et

    p = _et.StepCompleted(step_name="s", input_tokens=10, output_tokens=5, cost_usd=0.01)
    again = _et.StepCompleted.from_json(p.to_json())
    assert again.input_tokens == 10 and again.output_tokens == 5 and again.cost_usd == 0.01
    assert again.cache_read_tokens is None  # unset stays None, never zero


def test_step_completed_defaults_usage_to_none():
    from generic_ml_workflow.core import eventtypes as _et

    p = _et.StepCompleted(step_name="s")
    assert p.input_tokens is None and p.cost_usd is None


def test_probe_recorded_round_trips_and_is_registered():
    from generic_ml_workflow.core import eventtypes as _et

    p = _et.ProbeRecorded(client="claude", model="opus", effort="high", ok=False, error="nope")
    again = _et.ProbeRecorded.from_json(p.to_json())
    assert again.client == "claude" and again.ok is False and again.error == "nope"
    assert _et.EventType.PROBE_RECORDED in _et._REGISTRY

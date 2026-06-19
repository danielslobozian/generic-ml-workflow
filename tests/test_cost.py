# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The cost projection (slice 3 of 0.0.10): fold step.completed usage into per-step
rows and an execution total, keeping unknown distinct from zero."""

from types import SimpleNamespace

from generic_ml_workflow.core import cost
from generic_ml_workflow.core import eventtypes as et
from generic_ml_workflow.core.usage import Usage


def _completed(name, **usage):
    payload = et.StepCompleted(step_name=name, **usage)
    return SimpleNamespace(event_type=payload.event_type, payload=payload)


def test_project_aggregates_per_step_and_total():
    events = [
        _completed("a", input_tokens=100, output_tokens=20, cost_usd=0.01),
        _completed("b", input_tokens=50, output_tokens=10, cache_read_tokens=5, cost_usd=0.005),
    ]
    report = cost.project(events)
    assert [s.step_name for s in report.steps] == ["a", "b"]
    assert report.total.input_tokens == 150
    assert report.total.output_tokens == 30
    assert report.total.cache_read_tokens == 5  # only b reported it
    assert abs(report.total.cost_usd - 0.015) < 1e-9


def test_project_all_unknown_total_stays_none_not_zero():
    report = cost.project([_completed("a"), _completed("b")])
    assert report.total.input_tokens is None
    assert report.total.cost_usd is None
    assert len(report.steps) == 2  # the steps are still listed


def test_project_ignores_non_completed_events():
    events = [
        SimpleNamespace(event_type=et.EventType.STEP_STARTED, payload=None),
        _completed("a", input_tokens=7),
    ]
    report = cost.project(events)
    assert len(report.steps) == 1 and report.total.input_tokens == 7


def test_render_usage_unknown_vs_known():
    assert cost.render_usage(Usage()) == "usage unknown"
    line = cost.render_usage(Usage(input_tokens=10, output_tokens=4, cost_usd=0.002))
    assert "10" in line and "4" in line and "$0.0020" in line

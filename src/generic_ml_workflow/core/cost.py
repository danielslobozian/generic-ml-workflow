# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""cost.py -- the cost projection.

A projection (DESIGN.md SS11): a *rebuildable* view folded from the event log,
never a second source of truth. ``project`` reads an execution's ``step.completed``
events and aggregates the normalized usage each carries into per-step rows and an
execution total. Re-run it over the same events and you get the same answer; the
log is authoritative.

Tokens are the unit. Aggregation keeps "unknown" distinct from "zero": a field of
the total is the sum of the steps that reported it, or ``None`` if *no* step did --
so an execution of clients that never reported, say, a cost stays "unknown", not
"$0.00". Per-job totals are not here yet; jobs become persistent in 0.0.12 and the
same projection will then fold across a job's executions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from generic_ml_workflow.core.eventtypes import EventType
from generic_ml_workflow.core.usage import Usage


@dataclass(frozen=True)
class StepCost:
    """One step's recorded usage (as it sits on its step.completed event)."""

    step_name: str
    usage: Usage


@dataclass(frozen=True)
class ExecutionCost:
    """An execution's per-step usage rows plus their total."""

    steps: tuple[StepCost, ...]
    total: Usage


def _sum_field(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum the values that are known; ``None`` if none are -- unknown stays unknown
    rather than collapsing to zero."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _sum_usages(usages: Sequence[Usage]) -> Usage:
    return Usage(
        input_tokens=_sum_field(u.input_tokens for u in usages),
        output_tokens=_sum_field(u.output_tokens for u in usages),
        cache_read_tokens=_sum_field(u.cache_read_tokens for u in usages),
        cache_write_tokens=_sum_field(u.cache_write_tokens for u in usages),
        reasoning_tokens=_sum_field(u.reasoning_tokens for u in usages),
        cost_usd=_sum_field(u.cost_usd for u in usages),
    )


def project(events: Iterable) -> ExecutionCost:
    """Fold a step.completed stream into per-step usage rows and their total. Only
    completion events contribute; anything else is ignored."""
    steps: list[StepCost] = []
    for event in events:
        if event.event_type is not EventType.STEP_COMPLETED:
            continue
        payload = event.payload
        steps.append(
            StepCost(
                step_name=payload.step_name,
                usage=Usage(
                    input_tokens=payload.input_tokens,
                    output_tokens=payload.output_tokens,
                    cache_read_tokens=payload.cache_read_tokens,
                    cache_write_tokens=payload.cache_write_tokens,
                    reasoning_tokens=payload.reasoning_tokens,
                    cost_usd=payload.cost_usd,
                ),
            )
        )
    total = _sum_usages([s.usage for s in steps])
    return ExecutionCost(steps=tuple(steps), total=total)


def render_usage(usage: Usage) -> str:
    """One-line human rendering, tokens first; an advisory cost only if reported.
    'usage unknown' when nothing was reported (distinct from a real zero)."""
    if usage.total_tokens is None and usage.cost_usd is None and usage.cache_read_tokens is None:
        return "usage unknown"
    parts = [f"in {usage.input_tokens or 0} / out {usage.output_tokens or 0} tok"]
    if usage.cache_read_tokens:
        parts.append(f"cache-read {usage.cache_read_tokens}")
    if usage.reasoning_tokens:
        parts.append(f"reasoning {usage.reasoning_tokens}")
    if usage.cost_usd is not None:
        parts.append(f"~${usage.cost_usd:.4f}")
    return ", ".join(parts)

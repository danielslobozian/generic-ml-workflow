# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""usage.py -- the normalized per-call usage the engine reads back from gmlcache.

The engine runs no model call itself (invariant 3) and parses no client output: it
reads usage from gmlcache's ``run --json`` envelope, where gmlcache has already
normalized every client's accounting into one shape. This module holds that shape
as a value object plus the one parser that lifts it out of the envelope.

Tokens, never dollars, are the load-bearing unit (a pinned advisory cost may ride
along, but the engine bills in tokens). Every field is optional: a client that does
not report a number leaves it ``None``, and an envelope with no usage at all yields
``None`` rather than a zeroed record, so "we don't know" stays distinct from "zero".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Usage:
    """One call's normalized usage. Tokens are the unit; ``cost_usd`` is an advisory
    number some clients report, carried but not authoritative."""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cost_usd: Optional[float] = None

    @property
    def total_tokens(self) -> Optional[int]:
        """Input + output, when both are known; ``None`` otherwise. Cache-read and
        cache-write are *components of* input accounting reported separately by some
        clients, so they are not added again here."""
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) else None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def usage_from_envelope(envelope: dict) -> Optional[Usage]:
    """Lift normalized usage out of a gmlcache ``run --json`` envelope.

    Returns ``None`` when the envelope carries no usage (``"usage"`` absent or
    null) -- unknown usage stays unknown, it is never invented as zero. Each field
    is read defensively so an unexpected shape degrades to ``None`` per field rather
    than raising: the cost view must never break a run.
    """
    if not isinstance(envelope, dict):
        return None
    block = envelope.get("usage")
    if not isinstance(block, dict):
        return None
    return Usage(
        input_tokens=_as_int(block.get("input_tokens")),
        output_tokens=_as_int(block.get("output_tokens")),
        cache_read_tokens=_as_int(block.get("cache_read_tokens")),
        cache_write_tokens=_as_int(block.get("cache_write_tokens")),
        reasoning_tokens=_as_int(block.get("reasoning_tokens")),
        cost_usd=_as_float(block.get("cost_usd")),
    )

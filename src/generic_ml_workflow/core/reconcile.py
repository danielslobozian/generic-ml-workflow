# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""reconcile.py -- anticipate a workflow's failure before it runs.

The user's ``[tiers]`` mapping names a concrete ``(client, model, effort)`` per
tier. Whether those clients are installed and those models still exist is *client
knowledge* the engine does not hold -- it is relayed from gmlcache (``doctor`` for
presence, ``models`` for the per-client model lists; see :mod:`core.detect`).

This module is the pure comparison between *what the config asked for* and *what
gmlcache reports is actually there*. It performs no I/O and invents nothing, so
it is unit-tested directly against frozen fixtures.

Two failures are worth catching in advance, and only as far as the cache can
actually see:

  * **missing client** -- a configured tier names a client gmlcache does not
    report as present. Always checkable from the ``doctor`` data we already have.
  * **stale model** -- a configured tier names a model the client no longer
    lists. Checkable *only* when gmlcache could enumerate that client's models
    (``supported`` with a list). When it could not, we do **not** warn: an
    un-listable client is "cannot verify", never "model is gone". The run stays
    the truth.

Nothing here gates anything. The issues are advisory, exactly like detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from generic_ml_workflow.core.contract import Tier
from generic_ml_workflow.core.detect import ClientStatus, ModelListing
from generic_ml_workflow.core.shotrunner import Resolution


class IssueKind(Enum):
    """The kind of mismatch found. Advisory only."""

    MISSING_CLIENT = "missing_client"
    STALE_MODEL = "stale_model"


@dataclass(frozen=True)
class TierIssue:
    """One advisory finding about a configured tier. ``message`` is ready to show."""

    kind: IssueKind
    tier: Tier
    client: str
    model: str
    message: str


def reconcile(
    tiers: dict[Tier, Resolution],
    clients: tuple[ClientStatus, ...],
    listings: tuple[ModelListing, ...] | None,
) -> tuple[TierIssue, ...]:
    """Compare configured tiers against what gmlcache reports.

    ``clients`` is the ``doctor`` relay (always available at startup).
    ``listings`` is the ``models`` relay, or ``None`` when it was not fetched (or
    could not be) -- in which case the stale-model check is skipped entirely.

    Returns advisory issues in tier order (high, medium, low). Empty means every
    configured tier looks reachable *as far as the cache can see*.
    """
    present = {c.name for c in clients if c.present}
    by_client = {m.name: m for m in (listings or ())}

    issues: list[TierIssue] = []
    for tier in Tier:  # stable, meaningful order
        res = tiers.get(tier)
        if res is None:
            continue
        if res.client not in present:
            issues.append(
                TierIssue(
                    kind=IssueKind.MISSING_CLIENT,
                    tier=tier,
                    client=res.client,
                    model=res.model,
                    message=(
                        f"tier '{tier.value}' needs client '{res.client}', which gmlcache "
                        f"does not report as installed -- this tier will fail when run."
                    ),
                )
            )
            continue
        # Client is present. Stale-model check only when we actually have a list.
        if listings is None:
            continue
        listing = by_client.get(res.client)
        if listing is None or not listing.supported or listing.models is None:
            continue  # cannot verify this client's models -> stay silent
        known = {m.id for m in listing.models} | {m.name for m in listing.models}
        if res.model not in known:
            issues.append(
                TierIssue(
                    kind=IssueKind.STALE_MODEL,
                    tier=tier,
                    client=res.client,
                    model=res.model,
                    message=(
                        f"tier '{tier.value}' names model '{res.model}' for '{res.client}', "
                        f"which the client no longer lists -- this tier may fail when run."
                    ),
                )
            )
    return tuple(issues)

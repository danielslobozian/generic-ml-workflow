# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Pure tier reconciliation: configured tiers vs. what gmlcache reports.

No I/O -- the function is exercised directly against hand-built detection and
listing values, the same way the parse steps are tested against frozen fixtures.
"""

from generic_ml_workflow.core import reconcile
from generic_ml_workflow.core.contract import Tier
from generic_ml_workflow.core.detect import ClientStatus, ModelInfo, ModelListing
from generic_ml_workflow.core.reconcile import IssueKind
from generic_ml_workflow.core.shotrunner import Resolution

CLAUDE_PRESENT = ClientStatus(name="claude", present=True, version="1.0")
CODEX_ABSENT = ClientStatus(name="codex", present=False, detail="not on PATH")
CURSOR_PRESENT = ClientStatus(name="cursor", present=True)


def _claude_models(*ids):
    return ModelListing(
        name="claude",
        present=True,
        supported=True,
        models=tuple(ModelInfo(id=i, name=i) for i in ids),
    )


def test_no_tiers_no_issues():
    assert reconcile.reconcile({}, (CLAUDE_PRESENT,), None) == ()


def test_missing_client_is_flagged():
    tiers = {Tier.LOW: Resolution(client="codex", model="gpt-5.5", effort=None)}
    (issue,) = reconcile.reconcile(tiers, (CLAUDE_PRESENT, CODEX_ABSENT), None)
    assert issue.kind is IssueKind.MISSING_CLIENT
    assert issue.tier is Tier.LOW and issue.client == "codex"
    assert "codex" in issue.message and "fail" in issue.message


def test_present_client_no_listings_is_clean():
    # Free startup check: client present, no model list fetched -> nothing to warn.
    tiers = {Tier.HIGH: Resolution(client="claude", model="opus", effort="high")}
    assert reconcile.reconcile(tiers, (CLAUDE_PRESENT,), None) == ()


def test_stale_model_is_flagged_when_listed():
    tiers = {Tier.MEDIUM: Resolution(client="claude", model="sonnet-9", effort=None)}
    listings = (_claude_models("opus", "sonnet", "haiku"),)
    (issue,) = reconcile.reconcile(tiers, (CLAUDE_PRESENT,), listings)
    assert issue.kind is IssueKind.STALE_MODEL
    assert issue.model == "sonnet-9" and issue.tier is Tier.MEDIUM


def test_known_model_is_clean():
    tiers = {Tier.MEDIUM: Resolution(client="claude", model="sonnet", effort=None)}
    listings = (_claude_models("opus", "sonnet", "haiku"),)
    assert reconcile.reconcile(tiers, (CLAUDE_PRESENT,), listings) == ()


def test_model_matched_by_human_name_too():
    tiers = {Tier.HIGH: Resolution(client="claude", model="Claude Opus", effort=None)}
    listings = (
        ModelListing(
            name="claude",
            present=True,
            supported=True,
            models=(ModelInfo(id="opus", name="Claude Opus"),),
        ),
    )
    assert reconcile.reconcile(tiers, (CLAUDE_PRESENT,), listings) == ()


def test_unsupported_listing_cannot_verify_so_no_warning():
    # Client present but no listing mechanism -> we must NOT claim the model is gone.
    tiers = {Tier.HIGH: Resolution(client="claude", model="whatever", effort=None)}
    listings = (ModelListing(name="claude", present=True, supported=False, reason="no list cmd"),)
    assert reconcile.reconcile(tiers, (CLAUDE_PRESENT,), listings) == ()


def test_missing_listing_entry_cannot_verify():
    # Client present, but no listing object for it at all -> cannot verify, stay silent.
    listings = (_claude_models("opus"),)  # a listing for claude, none for cursor
    tiers = {Tier.HIGH: Resolution(client="cursor", model="composer-2.5", effort=None)}
    assert reconcile.reconcile(tiers, (CURSOR_PRESENT,), listings) == ()


def test_missing_client_skips_model_check():
    # An absent client is reported once (missing), never also as a stale model.
    tiers = {Tier.LOW: Resolution(client="codex", model="gpt-5.5", effort=None)}
    listings = (_claude_models("opus"),)
    (issue,) = reconcile.reconcile(tiers, (CLAUDE_PRESENT, CODEX_ABSENT), listings)
    assert issue.kind is IssueKind.MISSING_CLIENT


def test_issues_in_tier_order():
    tiers = {
        Tier.LOW: Resolution(client="codex", model="x", effort=None),
        Tier.HIGH: Resolution(client="codex", model="y", effort=None),
    }
    issues = reconcile.reconcile(tiers, (CODEX_ABSENT,), None)
    assert [i.tier for i in issues] == [Tier.HIGH, Tier.LOW]

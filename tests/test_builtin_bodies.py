# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Host-pinning is the security heart of the built-in fetch: a path may only ever
name a resource under the configured base_url, never another host and never a climb
above the base."""

import pytest

from generic_ml_workflow.core import builtin_bodies as bb


def test_pin_url_joins_a_relative_path_under_the_base():
    assert (
        bb.pin_url("https://acme.test/api/v1", "issues/42") == "https://acme.test/api/v1/issues/42"
    )


def test_pin_url_handles_a_bare_host_base():
    assert bb.pin_url("https://acme.test", "things") == "https://acme.test/things"


def test_pin_url_rejects_an_absolute_url():
    with pytest.raises(bb.BuiltinError, match="relative"):
        bb.pin_url("https://acme.test/api", "https://evil.test/steal")


def test_pin_url_rejects_a_climb_above_the_base():
    with pytest.raises(bb.BuiltinError, match="climbs|escapes"):
        bb.pin_url("https://acme.test/api/v1", "../../secret")


def test_pin_url_rejects_a_nonhttp_base():
    with pytest.raises(bb.BuiltinError, match="absolute http"):
        bb.pin_url("ftp://acme.test/x", "y")

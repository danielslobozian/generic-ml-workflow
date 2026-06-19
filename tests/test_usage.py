# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The normalized usage value object and its parser (slice 1 of cost)."""

from generic_ml_workflow.core.usage import usage_from_envelope


def test_usage_from_full_envelope():
    env = {
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 2,
            "cache_write_tokens": 1,
            "reasoning_tokens": 4,
            "cost_usd": 0.01,
        }
    }
    u = usage_from_envelope(env)
    assert (u.input_tokens, u.output_tokens, u.cache_read_tokens) == (10, 5, 2)
    assert u.cache_write_tokens == 1 and u.reasoning_tokens == 4 and u.cost_usd == 0.01
    assert u.total_tokens == 15


def test_usage_absent_yields_none_not_zero():
    assert usage_from_envelope({"status": "recorded"}) is None
    assert usage_from_envelope({"usage": None}) is None
    assert usage_from_envelope("not a dict") is None


def test_usage_partial_fields_degrade_per_field():
    u = usage_from_envelope({"usage": {"input_tokens": 8}})
    assert u.input_tokens == 8
    assert u.output_tokens is None and u.cost_usd is None
    assert u.total_tokens == 8  # input known, output missing -> counted as 0 in the sum


def test_usage_ignores_bad_types():
    u = usage_from_envelope({"usage": {"input_tokens": "lots", "cost_usd": True}})
    assert u.input_tokens is None  # a string is not a token count
    assert u.cost_usd is None  # a bool is not a cost
    assert u.total_tokens is None  # nothing known -> unknown, not zero

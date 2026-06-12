# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Banner rendering: plain snapshot, fallback, and the no-crash guarantee."""

from generic_ml_workflow.repl import banner


def test_render_plain_contains_name_and_version():
    out = banner.render("panel", "9.9.9", color=False)
    assert "generic-ml-workflow" in out
    assert "9.9.9" in out
    assert banner.TAGLINE.split(";")[0] in out


def test_unknown_style_falls_back_to_default():
    out = banner.render("definitely-not-a-style", "1.2.3", color=False)
    assert "generic-ml-workflow" in out and "1.2.3" in out


def test_every_registered_style_renders():
    for name in banner.names():
        out = banner.render(name, "0.0.1", color=False)
        assert "generic-ml-workflow" in out

# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The config resolver: precedence, source tracking, the documented template,
and in-place single-key updates."""

import tomllib
from pathlib import Path

import pytest

from generic_ml_workflow.core import config


def _write(tmp_path, body) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- precedence: session > env > file > default ------------------------------


def test_defaults_when_file_absent(tmp_path):
    s = config.load(tmp_path / "nope.toml", env={})
    assert s.config_file is None
    assert set(s.sources.values()) == {"default"}
    assert s.banner == "panel"


def test_file_beats_default(tmp_path):
    p = _write(tmp_path, '[paths]\nflows = "/somewhere/flows"\n')
    s = config.load(p, env={})
    assert s.flows_dir == Path("/somewhere/flows")
    assert s.sources["flows_dir"] == "config"
    assert s.sources["state_dir"] == "default"  # untouched keys keep defaults


def test_env_beats_file(tmp_path):
    p = _write(tmp_path, '[paths]\nflows = "/from-file"\n')
    s = config.load(p, env={"GMLWORKFLOW_FLOWS": "/from-env"})
    assert s.flows_dir == Path("/from-env")
    assert s.sources["flows_dir"] == "env"


def test_session_beats_env(tmp_path):
    p = _write(tmp_path, '[ui]\nbanner = "panel"\n')
    s = config.load(p, env={"GMLWORKFLOW_BANNER": "minimal"}, session={"banner": "panel"})
    assert s.banner == "panel"
    assert s.sources["banner"] == "session"


def test_tilde_expansion(tmp_path):
    p = _write(tmp_path, '[paths]\nflows = "~/my-flows"\n')
    s = config.load(p, env={})
    assert s.flows_dir == Path("~/my-flows").expanduser()
    assert s.flows_dir.is_absolute()


# --- failure modes ------------------------------------------------------------


def test_unparseable_file_raises(tmp_path):
    p = _write(tmp_path, "this is = not [ toml\n")
    with pytest.raises(config.ConfigError, match="not valid TOML"):
        config.load(p, env={})


def test_wrong_value_shape_raises(tmp_path):
    p = _write(tmp_path, "[paths]\nflows = 42\n")
    with pytest.raises(config.ConfigError, match="must be a non-empty string"):
        config.load(p, env={})


def test_unknown_sections_and_keys_are_kept_not_rejected(tmp_path):
    p = _write(tmp_path, '[future]\nshiny = "thing"\n\n[paths]\nextra = "kept"\n')
    s = config.load(p, env={})  # no error; known settings fall to defaults
    assert s.sources["flows_dir"] == "default"


# --- the written form ----------------------------------------------------------


def test_initial_config_text_is_valid_toml_and_round_trips(tmp_path):
    text = config.initial_config_text(
        Path("/a/flows"), Path("/a/state"), Path("/a/ws"), banner="minimal"
    )
    doc = tomllib.loads(text)
    assert doc["paths"]["flows"] == "/a/flows"
    assert doc["ui"]["banner"] == "minimal"
    p = tmp_path / "cfg.toml"
    config.write_initial_config(p, text)
    s = config.load(p, env={})
    assert s.workspace_dir == Path("/a/ws") and s.banner == "minimal"
    assert "Precedence" in text  # the documentation lives in the file


def test_set_value_updates_in_place_preserving_comments(tmp_path):
    text = config.initial_config_text(Path("/a/f"), Path("/a/s"), Path("/a/w"))
    p = tmp_path / "cfg.toml"
    config.write_initial_config(p, text)
    before = p.read_text(encoding="utf-8")
    config.set_value(p, "banner", "minimal")
    after = p.read_text(encoding="utf-8")
    assert 'banner = "minimal"' in after
    assert after.count("#") == before.count("#")  # every comment survived
    assert tomllib.loads(after)["paths"]["flows"] == "/a/f"


def test_set_value_appends_missing_key_and_section(tmp_path):
    p = _write(tmp_path, '[paths]\nflows = "/x"\n')
    config.set_value(p, "banner", "minimal")  # [ui] section absent -> appended
    doc = tomllib.loads(p.read_text(encoding="utf-8"))
    assert doc["ui"]["banner"] == "minimal" and doc["paths"]["flows"] == "/x"
    config.set_value(p, "banner", "panel")  # now present -> replaced
    assert tomllib.loads(p.read_text(encoding="utf-8"))["ui"]["banner"] == "panel"

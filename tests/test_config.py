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


# --- [tiers]: the user's bridge from abstract tier -> concrete client/model ---

from generic_ml_workflow.core.contract import Tier  # noqa: E402
from generic_ml_workflow.core.shotrunner import Resolution  # noqa: E402


def test_tiers_absent_section_is_empty(tmp_path):
    cfg = _write(tmp_path, '[paths]\nflows = "/f"\n')
    assert config.load_tiers(cfg) == {}


def test_tiers_absent_file_is_empty(tmp_path):
    assert config.load_tiers(tmp_path / "nope.toml") == {}


def test_tiers_parsed_with_and_without_effort(tmp_path):
    cfg = _write(
        tmp_path,
        "[tiers.high]\n"
        'client = "claude"\nmodel = "sonnet"\neffort = "high"\n\n'
        "[tiers.low]\n"
        'client = "cursor"\nmodel = "composer-2.5"\n',
    )
    tiers = config.load_tiers(cfg)
    assert tiers[Tier.HIGH] == Resolution(client="claude", model="sonnet", effort="high")
    # effort omitted -> None (the client's own default)
    assert tiers[Tier.LOW] == Resolution(client="cursor", model="composer-2.5", effort=None)
    assert Tier.MEDIUM not in tiers  # only configured tiers appear


def test_tiers_blank_effort_becomes_none(tmp_path):
    cfg = _write(tmp_path, '[tiers.medium]\nclient = "codex"\nmodel = "gpt-5.5"\neffort = ""\n')
    assert config.load_tiers(cfg)[Tier.MEDIUM].effort is None


def test_tiers_unknown_name_is_ignored(tmp_path):
    cfg = _write(tmp_path, '[tiers.turbo]\nclient = "claude"\nmodel = "sonnet"\n')
    assert config.load_tiers(cfg) == {}  # forward-compat: unknown tier names skipped


@pytest.mark.parametrize(
    "body",
    [
        '[tiers.high]\nmodel = "sonnet"\n',  # missing client
        '[tiers.high]\nclient = "claude"\n',  # missing model
        '[tiers.high]\nclient = ""\nmodel = "sonnet"\n',  # empty client
        '[tiers.high]\nclient = "claude"\nmodel = "sonnet"\neffort = 3\n',  # non-string effort
    ],
)
def test_tiers_malformed_table_raises(tmp_path, body):
    with pytest.raises(config.ConfigError):
        config.load_tiers(_write(tmp_path, body))


def test_initial_config_documents_tiers_unseeded(tmp_path):
    text = config.initial_config_text(Path("/f"), Path("/s"), Path("/w"))
    assert "[tiers." in text
    # documented but commented: nothing is seeded, so it parses to no tiers
    cfg = _write(tmp_path, text)
    assert config.load_tiers(cfg) == {}


def test_load_providers_merges_config_and_credential_planes(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[providers.issue_tracker.jira]\nbase_url = "https://acme.test"\ntls = true\n',
        encoding="utf-8",
    )
    creds = tmp_path / "credentials.toml"
    creds.write_text('[providers.issue_tracker.jira]\ntoken = "secret-xyz"\n', encoding="utf-8")

    instances, kinds = config.load_providers(cfg, creds)
    assert kinds == {"issue_tracker"}
    inst = instances["issue_tracker"]
    assert inst["base_url"] == "https://acme.test"
    assert inst["tls"] is True
    assert inst["token"] == "secret-xyz"


def test_load_providers_empty_when_nothing_configured(tmp_path):
    instances, kinds = config.load_providers(tmp_path / "none.toml", tmp_path / "none-creds.toml")
    assert instances == {}
    assert kinds == set()


def test_load_providers_binding_selects_the_alias(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[providers.issue_tracker.acme]\nbase_url = "https://acme"\n'
        '[providers.issue_tracker.other]\nbase_url = "https://other"\n',
        encoding="utf-8",
    )
    creds = tmp_path / "credentials.toml"
    creds.write_text(
        '[providers.issue_tracker.acme]\ntoken = "a"\n'
        '[providers.issue_tracker.other]\ntoken = "o"\n',
        encoding="utf-8",
    )
    inst, _ = config.load_providers(cfg, creds, bindings={"issue_tracker": "other"})
    assert inst["issue_tracker"]["base_url"] == "https://other"
    assert inst["issue_tracker"]["token"] == "o"


def test_load_providers_binding_to_unknown_alias_raises(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[providers.issue_tracker.acme]\nbase_url = "x"\n', encoding="utf-8")
    with pytest.raises(config.ConfigError, match="no such instance"):
        config.load_providers(cfg, None, bindings={"issue_tracker": "ghost"})

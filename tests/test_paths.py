# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Locations: the one fixed path and the no-side-effect rule."""

from pathlib import Path

from generic_ml_workflow.core import paths


def test_env_var_overrides_the_config_path(monkeypatch, tmp_path):
    target = tmp_path / "elsewhere" / "config.toml"
    monkeypatch.setenv("GMLWORKFLOW_CONFIG", str(target))
    assert paths.config_path() == target


def test_default_config_path_is_os_standard(monkeypatch):
    monkeypatch.delenv("GMLWORKFLOW_CONFIG", raising=False)
    p = paths.config_path()
    assert p.name == "config.toml"
    assert "gmlworkflow" in str(p)


def test_resolve_never_creates_anything(tmp_path, monkeypatch):
    """Resolving locations is side-effect free; only ensure_runtime writes."""
    before = set(tmp_path.iterdir())
    pp = paths.Paths(
        flows_dir=tmp_path / "flows",
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "ws",
    )
    _ = (pp.db_path, pp.log_dir)
    assert set(tmp_path.iterdir()) == before


def test_ensure_runtime_creates_runtime_dirs_but_never_flows(tmp_path):
    pp = paths.Paths(
        flows_dir=tmp_path / "flows",
        state_dir=tmp_path / "state",
        workspace_dir=tmp_path / "ws",
    )
    pp.ensure_runtime()
    assert pp.state_dir.is_dir() and pp.log_dir.is_dir() and pp.workspace_dir.is_dir()
    assert not (tmp_path / "flows").exists()  # authored content; never created by a run
    assert isinstance(pp.db_path, Path)

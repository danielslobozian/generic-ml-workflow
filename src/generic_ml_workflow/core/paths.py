# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Locations resolver -- the app is location-blind.

Launching from any folder is identical: the app never reads the current directory,
never creates anything in it, never resolves a relative path against it. Only ONE
location is fixed: the config file, at the OS-standard per-user config path
(``~/.config/gmlworkflow/config.toml`` on Linux; platform equivalents elsewhere),
overridable by exactly one environment variable, ``GMLWORKFLOW_CONFIG``. Every
*other* path -- state, flows, workspace -- is a setting inside that config.

This module only *resolves* locations; it never creates them. Directory creation
happens solely through :meth:`Paths.ensure_runtime` or the first-run interview --
both explicit, user-answered flows. Resolving stays free of side effects (design
invariant 15: no auto-written files beyond the answered-for config).

The :class:`Paths` defaults below are what the interview's "standard OS folders"
choice writes; with a config present, every root comes from the config instead
(see ``core.config``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

_APP = "gmlworkflow"


def config_path() -> Path:
    """The one fixed location: the config file. ``GMLWORKFLOW_CONFIG`` overrides it."""
    env = os.environ.get("GMLWORKFLOW_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path(platformdirs.user_config_dir(_APP)) / "config.toml"


def _default_data_root() -> Path:
    return Path(platformdirs.user_data_dir(_APP))


def _default_state_root() -> Path:
    return Path(platformdirs.user_state_dir(_APP))


@dataclass(frozen=True)
class Paths:
    """The resolved locations the app works with.

    ``flows_dir`` is the user's meta-code (their own folder, expected to be a git
    repo -- never created or managed by the app). ``state_dir`` holds runtime
    truth (the event database, logs); ``workspace_dir`` is the per-run working
    area. Resolving a location never creates it; only :meth:`ensure_runtime`
    writes, and only for the runtime dirs the app itself owns.
    """

    flows_dir: Path = field(default_factory=lambda: _default_data_root() / "flows")
    state_dir: Path = field(default_factory=_default_state_root)
    workspace_dir: Path = field(default_factory=lambda: _default_data_root() / "workspace")

    @property
    def db_path(self) -> Path:
        return self.state_dir / "gmlworkflow.db"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    def ensure_runtime(self) -> "Paths":
        """Create the runtime dirs the app writes to. The flows dir is authored
        content -- the user's repo -- and is deliberately NOT created here."""
        for p in (self.state_dir, self.log_dir, self.workspace_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self

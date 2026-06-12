# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""config.py -- the one file the app reads, and the one file it ever writes unasked-for-content
(and even that one only after the first-run interview has asked, §3 of the design).

The config lives at the OS-standard path (``paths.config_path()``), overridable by
exactly one environment variable, ``GMLWORKFLOW_CONFIG``. Every other location --
flows, state, workspace -- is a *setting inside* that config, resolved here with
the family's precedence (mirroring gmlcache):

    session override  >  environment  >  config file  >  default

"Session" is the REPL-side equivalent of a flag (this app has no argument
surface): a value changed at the prompt for the current session, e.g. ``/banner``.
The resolver tracks WHERE each effective value came from, so ``/status`` can show
every setting with its source -- the same honesty gmlcache's ``status`` has.

Reading uses stdlib ``tomllib`` (py>=3.11). Writing happens in exactly two
places: the first-run interview writes the whole documented template
(:func:`initial_config_text`), and an explicit user action may update one key in
place (:func:`set_value`) -- comments and unknown keys are preserved, never
rewritten. Unknown sections and keys are kept, not rejected: a newer config must
not break an older engine. A file that cannot be parsed, or a value of the wrong
shape, raises :class:`ConfigError` with the path and the reason.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from generic_ml_workflow.core import paths as paths_mod

# setting name -> (toml section, toml key, env var)
_SPEC: dict[str, tuple[str, str, str]] = {
    "flows_dir": ("paths", "flows", "GMLWORKFLOW_FLOWS"),
    "state_dir": ("paths", "state", "GMLWORKFLOW_STATE"),
    "workspace_dir": ("paths", "workspace", "GMLWORKFLOW_WORKSPACE"),
    "banner": ("ui", "banner", "GMLWORKFLOW_BANNER"),
}

_PATH_SETTINGS = ("flows_dir", "state_dir", "workspace_dir")


class ConfigError(Exception):
    """The config file exists but cannot be honored. Fail loud, never guess."""


@dataclass(frozen=True)
class Settings:
    """The effective settings plus, for each, where its value came from
    (``session`` / ``env`` / ``config`` / ``default``)."""

    flows_dir: Path
    state_dir: Path
    workspace_dir: Path
    banner: str
    sources: dict[str, str]
    config_file: Path | None  # the file that was loaded, None when absent

    def as_paths(self) -> paths_mod.Paths:
        return paths_mod.Paths(
            flows_dir=self.flows_dir,
            state_dir=self.state_dir,
            workspace_dir=self.workspace_dir,
        )


def _defaults() -> dict[str, object]:
    base = paths_mod.Paths()
    return {
        "flows_dir": base.flows_dir,
        "state_dir": base.state_dir,
        "workspace_dir": base.workspace_dir,
        "banner": "panel",
    }


def _read_file(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: not valid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from exc


def load(
    config_file: Path | None = None,
    *,
    session: dict[str, object] | None = None,
    env: dict[str, str] | None = None,
) -> Settings:
    """Resolve the effective settings.

    ``config_file`` defaults to :func:`paths.config_path` (which honors
    ``GMLWORKFLOW_CONFIG``); ``env`` defaults to ``os.environ``; ``session``
    carries any values the user changed at the prompt this session.
    """
    cfg_path = config_file if config_file is not None else paths_mod.config_path()
    env = os.environ if env is None else env
    session = session or {}

    doc: dict = {}
    loaded: Path | None = None
    if cfg_path.is_file():
        doc = _read_file(cfg_path)
        loaded = cfg_path

    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    for name, (section, key, env_var) in _SPEC.items():
        if name in session:
            values[name], sources[name] = session[name], "session"
            continue
        if env.get(env_var):
            values[name], sources[name] = env[env_var], "env"
            continue
        sect = doc.get(section)
        if isinstance(sect, dict) and key in sect:
            raw = sect[key]
            if not isinstance(raw, str) or not raw.strip():
                raise ConfigError(
                    f"{cfg_path}: [{section}] {key} must be a non-empty string, got {raw!r}"
                )
            values[name], sources[name] = raw, "config"
            continue
        values[name], sources[name] = _defaults()[name], "default"

    for name in _PATH_SETTINGS:
        values[name] = Path(str(values[name])).expanduser()

    return Settings(
        flows_dir=values["flows_dir"],
        state_dir=values["state_dir"],
        workspace_dir=values["workspace_dir"],
        banner=str(values["banner"]),
        sources=sources,
        config_file=loaded,
    )


# --- the written form -------------------------------------------------------


def initial_config_text(flows: Path, state: Path, workspace: Path, banner: str = "panel") -> str:
    """The documented config the first-run interview writes: seeded values plus
    the allowed values and the precedence, in comments. This text is the only
    config this app ever generates."""
    return f"""\
# generic-ml-workflow configuration.
#
# This file was written by the first-run interview, with your answers seeded
# below. Edit freely; the app only ever reads it (one explicit action -- the
# /banner verb -- may update the [ui] banner line in place).
#
# Precedence for every setting:
#   session override  >  environment variable  >  this file  >  built-in default
#
# The location of THIS file is the only fixed path the app knows; override it
# with the GMLWORKFLOW_CONFIG environment variable.

[paths]
# Where your workflow definitions (meta-code) live. Yours, ideally a git repo;
# the app never creates or modifies it.            env: GMLWORKFLOW_FLOWS
flows = "{flows.as_posix()}"

# Runtime truth: the event database and logs.       env: GMLWORKFLOW_STATE
state = "{state.as_posix()}"

# The per-run working area.                          env: GMLWORKFLOW_WORKSPACE
workspace = "{workspace.as_posix()}"

[ui]
# Startup banner style. Allowed: panel, minimal.     env: GMLWORKFLOW_BANNER
banner = "{banner}"
"""


def write_initial_config(cfg_path: Path, text: str) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text, encoding="utf-8")


def set_value(cfg_path: Path, setting: str, value: str) -> None:
    """Update one setting's line in place, preserving everything else byte-for-byte.

    Used only for explicit user actions (e.g. ``/banner minimal``). If the key
    line is missing, it is appended to its section; if the section is missing,
    both are appended at the end.
    """
    section, key, _ = _SPEC[setting]
    text = cfg_path.read_text(encoding="utf-8")
    line = f'{key} = "{value}"'
    in_section = False
    out: list[str] = []
    replaced = False
    section_seen = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("["):
            if in_section and not replaced:
                out.append(line)  # key was missing from its section: add before leaving
                replaced = True
            in_section = stripped == f"[{section}]"
            section_seen = section_seen or in_section
        elif in_section and not replaced and re.match(rf"^\s*{re.escape(key)}\s*=", raw):
            out.append(line)
            replaced = True
            continue
        out.append(raw)
    if not replaced:
        if not section_seen:
            out.extend(["", f"[{section}]"])
        out.append(line)
    cfg_path.write_text("\n".join(out) + "\n", encoding="utf-8")

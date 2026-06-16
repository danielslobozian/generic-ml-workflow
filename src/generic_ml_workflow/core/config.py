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
import sys
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from generic_ml_workflow.core import paths as paths_mod
from generic_ml_workflow.core.contract import Tier
from generic_ml_workflow.core.shotrunner import Resolution

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


def load_tiers(config_file: Path | None = None) -> dict[Tier, Resolution]:
    """Read the optional ``[tiers]`` section: ``tier -> {client, model, effort?}``.

    The engine's abstract tiers (high/medium/low) are bridged to a concrete
    ``(client, model, effort)`` here. The mapping is the **user's**, because the
    clients share no tier nomenclature -- claude/codex/cursor each expose only
    their own model names, never a high/medium/low scale -- so nothing is seeded.
    An absent ``[tiers]`` section yields an empty map, and a shot step then stops
    honestly with a clear message. Detection-driven seeding (asking gmlcache what
    is installed) is a later slice; here the user supplies the bridge explicitly.

    Each configured tier table must give a non-empty ``client`` and ``model``;
    ``effort`` is optional (omitted -> the client's own default). Unknown tier
    names are ignored (forward-compatibility). A malformed table raises
    :class:`ConfigError` with the path and the reason.
    """
    cfg_path = config_file if config_file is not None else paths_mod.config_path()
    if not cfg_path.is_file():
        return {}
    doc = _read_file(cfg_path)
    raw = doc.get("tiers")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{cfg_path}: [tiers] must be a table")

    by_name = {t.value: t for t in Tier}
    resolutions: dict[Tier, Resolution] = {}
    for name, table in raw.items():
        if name not in by_name:
            continue  # forward-compat: ignore tier names this engine doesn't know
        if not isinstance(table, dict):
            raise ConfigError(f"{cfg_path}: [tiers.{name}] must be a table")
        client = table.get("client")
        model = table.get("model")
        effort = table.get("effort")
        if not isinstance(client, str) or not client.strip():
            raise ConfigError(f"{cfg_path}: [tiers.{name}] needs a non-empty 'client'")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"{cfg_path}: [tiers.{name}] needs a non-empty 'model'")
        if effort is not None and not isinstance(effort, str):
            raise ConfigError(f"{cfg_path}: [tiers.{name}] 'effort' must be a string when set")
        eff = effort if (isinstance(effort, str) and effort.strip()) else None
        resolutions[by_name[name]] = Resolution(
            client=client.strip(), model=model.strip(), effort=eff
        )
    return resolutions


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
# Your workflow definitions (meta-code) live here. On first run the app creates
# this folder and initializes it as a git repo, then drives git over it (history,
# time-travel); the contents are yours. Point it at an existing git repo and the
# app leaves that repo's history untouched.   env: GMLWORKFLOW_FLOWS
flows = "{flows.as_posix()}"

# Runtime truth: the event database and logs.       env: GMLWORKFLOW_STATE
state = "{state.as_posix()}"

# The per-run working area.                          env: GMLWORKFLOW_WORKSPACE
workspace = "{workspace.as_posix()}"

[ui]
# Startup banner style. Allowed: panel, minimal.     env: GMLWORKFLOW_BANNER
banner = "{banner}"

# [tiers] -- map a step's abstract tier to a CONCRETE client + model.
#
# A workflow never names a vendor: each step asks for "high" / "medium" / "low".
# This is where YOU bridge that tier to a client you actually have installed.
# There is no shared tier nomenclature across clients (claude has opus/sonnet/
# haiku, codex has gpt-5.x, cursor has composer-*), so nothing is seeded -- fill
# in only the tiers your workflows use. A shot whose tier is left blank stops
# with a clear message rather than guessing. (Detection-assisted defaults, asking
# gmlcache what is installed, arrive in a later release.) There is no env layer
# for tiers. Uncomment and edit to your installed clients/models:
#
# [tiers.high]
# client = "claude"      # one of: claude, codex, cursor
# model  = "sonnet"      # a model that client/account can actually reach
# effort = ""            # optional; omit to use the client's own default
#
# [tiers.medium]
# client = "codex"
# model  = "gpt-5.5"
#
# [tiers.low]
# client = "cursor"
# model  = "composer-2.5"
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


def _ensure_private(path: Path) -> None:
    """Refuse to read a secrets file that others can see. On POSIX the credentials
    file must be mode 600 (owner-only); any group/other permission bit is rejected
    rather than read, so a token can't sit in a world-readable file. A no-op on
    Windows, which has no POSIX permission bits."""
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(
            f"{path}: credentials file is readable by group/other (mode {mode:03o}); "
            "run 'chmod 600' on it -- secrets must be owner-only"
        )


def load_providers(
    config_file: Path | None = None,
    credentials_file: Path | None = None,
    bindings: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, object]], set[str]]:
    """Read configured provider instances: the **config plane** from the main config's
    ``[providers.<kind>.<alias>]`` tables, the **credential plane** (tokens) from a
    separate ``credentials.toml`` under the same path. Returns ``(instances, kinds)``
    where ``instances`` maps each provider kind to its resolved instance (config keys
    merged with the credential keys) and ``kinds`` is the set of kinds that have at
    least one configured instance (what warm-up checks). The credential file is read
    separately and its values are never written back anywhere.

    Resolution per kind: ``bindings`` (``{kind: alias}``, typically a workflow's
    choices) selects the instance; an unbound kind falls back to the alias named
    ``default`` if present, else the single configured alias. A binding naming an
    alias with no configured instance raises :class:`ConfigError`."""
    bindings = bindings or {}
    cfg_path = config_file if config_file is not None else paths_mod.config_path()
    config_doc = _read_file(cfg_path) if cfg_path.is_file() else {}
    creds_doc: dict = {}
    if credentials_file and credentials_file.is_file():
        _ensure_private(credentials_file)
        creds_doc = _read_file(credentials_file)

    def kinds_of(doc: dict) -> dict:
        raw = doc.get("providers")
        return raw if isinstance(raw, dict) else {}

    config_providers = kinds_of(config_doc)
    creds_providers = kinds_of(creds_doc)

    instances: dict[str, dict[str, object]] = {}
    kinds: set[str] = set()
    all_kinds = set(config_providers) | set(creds_providers)
    for kind in all_kinds:
        cfg_aliases = config_providers.get(kind, {})
        cred_aliases = creds_providers.get(kind, {})
        if not isinstance(cfg_aliases, dict) or not isinstance(cred_aliases, dict):
            raise ConfigError(f"[providers.{kind}] must be a table of named instances")
        alias_names = set(cfg_aliases) | set(cred_aliases)
        if not alias_names:
            continue
        bound = bindings.get(kind)
        if bound is not None:
            if bound not in alias_names:
                raise ConfigError(
                    f"workflow binds provider '{kind}' to instance '{bound}', "
                    "but no such instance is configured"
                )
            chosen = bound
        elif "default" in alias_names:
            chosen = "default"
        else:
            chosen = sorted(alias_names)[0]
        merged: dict[str, object] = {}
        for src in (cfg_aliases.get(chosen, {}), cred_aliases.get(chosen, {})):
            if isinstance(src, dict):
                merged.update(src)
        if merged:
            instances[kind] = merged
            kinds.add(kind)
    return instances, kinds

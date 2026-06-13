# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""detect.py -- the detection gate, by relay.

This engine carries zero client knowledge: which ML coding clients exist, where
their executables live, and how to probe them is entirely the business of its
sibling tool, `generic-ml-cache` (`gmlcache`), invoked as a subprocess. Startup
asks ``gmlcache doctor --json`` what is installed and merely presents the answer.

The relay is advisory and graceful on every path:

  * gmlcache absent          -> a clear, friendly status; the app still opens.
  * gmlcache errors/times out-> same, with the detail captured.
  * malformed output         -> same; never a crash at the prompt.

Detection never chooses or gates anything ("the list is advisory; the run is the
truth"). The parse step is a pure function so it is unit-tested directly against
frozen fixtures of gmlcache's output -- no live binary needed in CI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientStatus:
    """One client, as gmlcache reported it. Purely informational."""

    name: str
    present: bool
    executable: str | None = None
    version: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Detection:
    """The outcome of one startup detection pass."""

    gmlcache_present: bool
    gmlcache_detail: str | None = None  # why absent / what went wrong, when not present
    clients: tuple[ClientStatus, ...] = ()


def parse_doctor_output(text: str) -> tuple[ClientStatus, ...]:
    """Parse ``gmlcache doctor --json`` output into client statuses.

    Pure and strict-but-forgiving: the document must be a JSON list of objects;
    unknown keys are ignored, missing optional keys default to None. Anything
    else raises ``ValueError`` (the caller turns that into an advisory detail).
    """
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("doctor output is not a JSON list")
    statuses = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            raise ValueError("doctor output entry is not a client object")
        statuses.append(
            ClientStatus(
                name=str(item["name"]),
                present=bool(item.get("present", False)),
                executable=item.get("executable"),
                version=item.get("version"),
                detail=item.get("detail"),
            )
        )
    return tuple(statuses)


@dataclass(frozen=True)
class ModelInfo:
    """One model a client relayed, as gmlcache reported it. ``id`` is the string
    a caller passes as the model; ``name`` is the client's human label."""

    id: str
    name: str
    default: bool = False
    current: bool = False


@dataclass(frozen=True)
class ModelListing:
    """What gmlcache could learn about one client's models -- three honest
    outcomes, never a guess (mirrors gmlcache's own ``ModelListing``):

      * client absent          -> ``present=False`` (``supported`` meaningless);
      * present, no listing     -> ``supported=False`` with a ``reason``;
      * present and listed      -> ``supported=True`` and ``models`` populated.

    ``models is None`` means "no list was obtained" -- the model drift check must
    treat that as "cannot verify", never as "model is gone".
    """

    name: str
    present: bool
    supported: bool
    models: tuple[ModelInfo, ...] | None = None
    reason: str | None = None


def parse_models_output(text: str) -> tuple[ModelListing, ...]:
    """Parse ``gmlcache models --json`` output into per-client listings.

    Pure and strict-but-forgiving, exactly like :func:`parse_doctor_output`: a
    JSON list of objects; unknown keys ignored, missing optional keys default.
    Anything else raises ``ValueError`` (the caller turns that into "cannot
    verify", i.e. ``None`` -- never a false drift warning).
    """
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("models output is not a JSON list")
    listings = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            raise ValueError("models output entry is not a client object")
        raw_models = item.get("models")
        models: tuple[ModelInfo, ...] | None = None
        if isinstance(raw_models, list):
            models = tuple(
                ModelInfo(
                    id=str(m.get("id", "")),
                    name=str(m.get("name", "")),
                    default=bool(m.get("default", False)),
                    current=bool(m.get("current", False)),
                )
                for m in raw_models
                if isinstance(m, dict)
            )
        listings.append(
            ModelListing(
                name=str(item["name"]),
                present=bool(item.get("present", False)),
                supported=bool(item.get("supported", False)),
                models=models,
                reason=item.get("reason"),
            )
        )
    return tuple(listings)


def discover_models(
    executable: str = "gmlcache", timeout: float = 30.0
) -> tuple[ModelListing, ...] | None:
    """Relay ``gmlcache models --json`` -- what models each client reports.

    Never raises. Returns ``None`` whenever the list cannot be obtained at all
    (gmlcache absent, errored, or unreadable): "the cache cannot retrieve it, so
    we do not validate against it." A returned tuple is per-client honest -- a
    client with ``supported=False`` is its own "cannot verify" for that client.
    """
    path = shutil.which(executable)
    if path is None:
        return None
    try:
        proc = subprocess.run(
            [path, "models", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 -- any launch failure -> "cannot verify"
        return None
    if proc.returncode != 0:
        return None
    try:
        return parse_models_output(proc.stdout)
    except ValueError:
        return None


def discover(executable: str = "gmlcache", timeout: float = 15.0) -> Detection:
    """Run the relay: is gmlcache installed, and what clients does it see?

    Never raises -- every failure mode comes back as an advisory ``Detection``.
    """
    path = shutil.which(executable)
    if path is None:
        return Detection(
            gmlcache_present=False,
            gmlcache_detail=f"'{executable}' was not found on PATH",
        )
    try:
        proc = subprocess.run(
            [path, "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 -- any launch failure is just "unavailable"
        return Detection(gmlcache_present=False, gmlcache_detail=f"doctor call failed: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        return Detection(gmlcache_present=True, gmlcache_detail=f"doctor failed: {detail}")
    try:
        clients = parse_doctor_output(proc.stdout)
    except ValueError as exc:
        return Detection(gmlcache_present=True, gmlcache_detail=f"unreadable doctor output: {exc}")
    return Detection(gmlcache_present=True, clients=clients)


# --- gmlcache version advisory ------------------------------------------------
#
# The engine drives gmlcache in the way the 0.0.7 release established (the cache
# owns its store; the engine passes no --store / --output-dir). Against an older
# gmlcache that contract does not hold, so the launch warns -- advisory only,
# never blocking, and silent whenever the version cannot be read.

MINIMUM_GMLCACHE_VERSION = (0, 0, 7)

_VERSION_NUMBER_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version_tuple(version_text: str) -> tuple[int, int, int] | None:
    """Pull the first ``X.Y.Z`` out of a version line (``gmlcache 0.0.7.dev0`` ->
    ``(0, 0, 7)``). Returns None when there is no version number to read, which the
    caller treats as 'cannot verify' -- never as 'too old'."""
    version_match = _VERSION_NUMBER_PATTERN.search(version_text)
    if version_match is None:
        return None
    major, minor, patch = version_match.groups()
    return (int(major), int(minor), int(patch))


def discover_gmlcache_version(executable: str = "gmlcache", timeout: float = 15.0) -> str | None:
    """Relay ``gmlcache --version`` and return its line (e.g. ``gmlcache 0.0.7``).
    Returns None on any failure -- 'cannot verify', never a crash or false warning."""
    resolved_path = shutil.which(executable)
    if resolved_path is None:
        return None
    try:
        completed_process = subprocess.run(
            [resolved_path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 -- any launch failure is just 'cannot verify'
        return None
    if completed_process.returncode != 0:
        return None
    version_line = completed_process.stdout.strip()
    return version_line or None


def gmlcache_version_is_outdated(
    version_line: str, minimum_version: tuple[int, int, int] = MINIMUM_GMLCACHE_VERSION
) -> bool | None:
    """True when the reported gmlcache version is older than the minimum the engine
    relies on, False when it meets it, None when the version could not be read (so
    the caller stays silent rather than warn on a guess)."""
    reported_version = parse_version_tuple(version_line)
    if reported_version is None:
        return None
    return reported_version < minimum_version

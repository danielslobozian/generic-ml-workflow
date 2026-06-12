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

# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""stamp.py -- read the meta-code stamp for an execution (DESIGN.md SS13).

Every execution records the flows-repo **commit** and **branch** it ran against,
plus the engine version, from the very first run -- because an append-only history
cannot be back-filled. Resuming old work reads the stamp and can offer to check
that commit out, so a run resumes against the system as it was.

This module only *reads* git state (HEAD commit, branch); it never writes. If the
flows folder is not a git repo, or git can't answer, the stamp is recorded
honestly as ``unversioned`` rather than failing -- though in normal operation the
interview git-inits the flows folder, so a stamp is expected.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from generic_ml_workflow import __version__


@dataclass(frozen=True)
class Stamp:
    """What an execution records about the world it ran in."""

    commit: str | None  # HEAD commit sha, or None if unversioned
    branch: str | None  # current branch, or None (detached / unversioned)
    engine_version: str

    @property
    def versioned(self) -> bool:
        return self.commit is not None


def _git(flows_dir: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(flows_dir), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:  # noqa: BLE001 -- git absent/unusable -> unversioned
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def read_stamp(flows_dir: Path) -> Stamp:
    """Read the current commit + branch of the flows repo. Never raises."""
    commit = _git(flows_dir, "rev-parse", "HEAD")
    branch = None
    if commit is not None:
        b = _git(flows_dir, "rev-parse", "--abbrev-ref", "HEAD")
        branch = b if b and b != "HEAD" else None  # HEAD == detached
    return Stamp(commit=commit, branch=branch, engine_version=__version__)

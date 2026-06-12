# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Execution stamping: read the flows repo's commit + branch (DESIGN.md SS13)."""

import subprocess


from generic_ml_workflow import __version__
from generic_ml_workflow.core import stamp


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)


def _init_repo(d):
    d.mkdir(parents=True, exist_ok=True)
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@example.com")
    _git(d, "config", "user.name", "t")
    (d / "flow.yaml").write_text("name: x\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")


def test_unversioned_when_not_a_repo(tmp_path):
    s = stamp.read_stamp(tmp_path)
    assert not s.versioned
    assert s.commit is None and s.branch is None
    assert s.engine_version == __version__


def test_reads_commit_and_branch(tmp_path):
    repo = tmp_path / "flows"
    _init_repo(repo)
    s = stamp.read_stamp(repo)
    assert s.versioned
    assert len(s.commit) == 40  # full sha
    assert s.branch in ("main", "master")  # default branch name varies by git config


def test_detached_head_has_no_branch(tmp_path):
    repo = tmp_path / "flows"
    _init_repo(repo)
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    _git(repo, "checkout", "-q", sha)  # detach
    s = stamp.read_stamp(repo)
    assert s.versioned and s.commit == sha
    assert s.branch is None


def test_engine_version_always_present(tmp_path):
    assert stamp.read_stamp(tmp_path).engine_version == __version__

# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The mandatory dependency check (git + gmlcache).

The probe seam (shutil.which / subprocess.run) is patched so these tests run on
any machine, including CI runners that have git but not gmlcache.
"""

import subprocess

import pytest

from generic_ml_workflow.core import deps


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr="")


def _fake_probe(present_map, versions=None):
    """Build a (which, run) pair faking a given set of installed executables."""
    versions = versions or {}

    def which(exe):
        return f"/fake/{exe}" if present_map.get(exe) else None

    def run(argv, **kw):
        exe = argv[0].rsplit("/", 1)[-1]
        return _completed(stdout=versions.get(exe, f"{exe} 1.0.0"))

    return which, run


def test_all_present_is_satisfied(monkeypatch):
    which, run = _fake_probe(
        {"git": True, "gmlcache": True}, {"git": "git version 2.43.0", "gmlcache": "gmlcache 0.0.5"}
    )
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    report = deps.check()
    assert report.satisfied
    assert report.missing == ()
    names = {s.name: s for s in report.statuses}
    assert names["git"].version == "git version 2.43.0"
    assert names["gmlcache"].version == "gmlcache 0.0.5"


def test_missing_gmlcache_is_unsatisfied(monkeypatch):
    which, run = _fake_probe({"git": True, "gmlcache": False})
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    report = deps.check()
    assert not report.satisfied
    assert [s.name for s in report.missing] == ["gmlcache"]
    assert "generic-ml-cache" in report.missing[0].remedy


def test_missing_git_is_unsatisfied(monkeypatch):
    which, run = _fake_probe({"git": False, "gmlcache": True})
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    report = deps.check()
    assert [s.name for s in report.missing] == ["git"]
    assert "git-scm.com" in report.missing[0].remedy


def test_nonzero_version_exit_counts_as_absent(monkeypatch):
    monkeypatch.setattr(deps.shutil, "which", lambda exe: f"/fake/{exe}")
    monkeypatch.setattr(deps.subprocess, "run", lambda argv, **kw: _completed(returncode=1))
    report = deps.check()
    assert not report.satisfied and len(report.missing) == 2


def test_launch_exception_counts_as_absent(monkeypatch):
    def boom(argv, **kw):
        raise OSError("cannot exec")

    monkeypatch.setattr(deps.shutil, "which", lambda exe: f"/fake/{exe}")
    monkeypatch.setattr(deps.subprocess, "run", boom)
    assert not deps.check().satisfied


def test_require_passes_when_satisfied(monkeypatch):
    which, run = _fake_probe({"git": True, "gmlcache": True})
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    report = deps.require()  # must not raise
    assert report.satisfied


def test_require_raises_with_a_helpful_message(monkeypatch):
    which, run = _fake_probe({"git": True, "gmlcache": False})
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    with pytest.raises(deps.DependencyError) as exc:
        deps.require()
    msg = str(exc.value)
    assert "cannot start" in msg
    assert "gmlcache" in msg and "generic-ml-cache" in msg
    assert "\u2717 git" not in msg  # only gmlcache is reported missing, not git


def test_format_missing_lists_each_missing_with_remedy(monkeypatch):
    which, run = _fake_probe({"git": False, "gmlcache": False})
    monkeypatch.setattr(deps.shutil, "which", which)
    monkeypatch.setattr(deps.subprocess, "run", run)
    text = deps.format_missing(deps.check())
    assert "git" in text and "gmlcache" in text
    assert text.count("\u2717") == 2  # one cross per missing dep

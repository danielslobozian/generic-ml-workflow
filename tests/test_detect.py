# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The gmlcache detection relay: frozen-fixture parsing + every graceful path.

The parse step is pure and tested directly against a frozen fixture of
``gmlcache doctor --json`` output. The subprocess seam is covered by patching
``subprocess.run`` (cross-platform) plus one real-subprocess test on POSIX.
"""

import json
import subprocess
import sys

import pytest

from generic_ml_workflow.core import detect

# A frozen fixture of `gmlcache doctor --json` output (gmlcache 0.0.5 shape:
# a JSON list of client-status objects). The relay must keep parsing this shape.
DOCTOR_FIXTURE = json.dumps(
    [
        {
            "name": "claude",
            "present": True,
            "executable": "/usr/local/bin/claude",
            "version": "1.0.35 (Claude Code)",
            "detail": None,
        },
        {
            "name": "codex",
            "present": False,
            "executable": None,
            "version": None,
            "detail": "codex executable not found on PATH",
        },
        {
            "name": "cursor",
            "present": True,
            "executable": "/usr/local/bin/cursor-agent",
            "version": None,
            "detail": "no version output",
        },
    ]
)


# --- the pure parse ---


def test_parse_doctor_fixture():
    statuses = detect.parse_doctor_output(DOCTOR_FIXTURE)
    assert [s.name for s in statuses] == ["claude", "codex", "cursor"]
    claude, codex, cursor = statuses
    assert claude.present and claude.version.startswith("1.0.35")
    assert not codex.present and "not found" in codex.detail
    assert cursor.present and cursor.version is None


def test_parse_ignores_unknown_keys():
    doc = json.dumps([{"name": "claude", "present": True, "future_field": 42}])
    (s,) = detect.parse_doctor_output(doc)
    assert s.name == "claude" and s.present and s.executable is None


def test_parse_empty_list_is_valid():
    assert detect.parse_doctor_output("[]") == ()


@pytest.mark.parametrize("bad", ['{"name": "claude"}', "[42]", '[{"present": true}]'])
def test_parse_rejects_wrong_shapes(bad):
    with pytest.raises(ValueError):
        detect.parse_doctor_output(bad)


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        detect.parse_doctor_output("plain text, not json")


# --- the subprocess seam, patched (cross-platform) ---


def _completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=["gmlcache"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_discover_gmlcache_absent():
    """The absent path: a binary that exists nowhere yields a graceful advisory
    Detection, never an exception."""
    d = detect.discover(executable="gmlcache-definitely-not-installed-xyz")
    assert not d.gmlcache_present
    assert "not found on PATH" in d.gmlcache_detail
    assert d.clients == ()


def test_discover_happy_path(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", lambda _: "/fake/bin/gmlcache")
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: _completed(stdout=DOCTOR_FIXTURE))
    d = detect.discover()
    assert d.gmlcache_present and d.gmlcache_detail is None
    assert [c.name for c in d.clients] == ["claude", "codex", "cursor"]


def test_discover_doctor_failure_is_advisory(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", lambda _: "/fake/bin/gmlcache")
    monkeypatch.setattr(
        detect.subprocess, "run", lambda *a, **k: _completed(stderr="boom", returncode=3)
    )
    d = detect.discover()
    assert d.gmlcache_present  # the tool exists; its answer failed
    assert "boom" in d.gmlcache_detail
    assert d.clients == ()


def test_discover_malformed_output_is_advisory(monkeypatch):
    monkeypatch.setattr(detect.shutil, "which", lambda _: "/fake/bin/gmlcache")
    monkeypatch.setattr(detect.subprocess, "run", lambda *a, **k: _completed(stdout="not json"))
    d = detect.discover()
    assert d.gmlcache_present
    assert "unreadable doctor output" in d.gmlcache_detail


def test_discover_launch_exception_is_advisory(monkeypatch):
    def explode(*a, **k):
        raise OSError("cannot exec")

    monkeypatch.setattr(detect.shutil, "which", lambda _: "/fake/bin/gmlcache")
    monkeypatch.setattr(detect.subprocess, "run", explode)
    d = detect.discover()
    assert not d.gmlcache_present
    assert "doctor call failed" in d.gmlcache_detail


# --- one real subprocess, POSIX only ---


@pytest.mark.skipif(sys.platform == "win32", reason="sh-script fake; POSIX only")
def test_discover_relays_real_subprocess(tmp_path, monkeypatch):
    import os

    fake = tmp_path / "gmlcache"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' '" + DOCTOR_FIXTURE + "'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    d = detect.discover()
    assert d.gmlcache_present and d.gmlcache_detail is None
    assert [c.name for c in d.clients] == ["claude", "codex", "cursor"]

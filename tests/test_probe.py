# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Rung 2 of the validation ladder: the recorded probe (slice 1 -- runner + verdict)."""

import subprocess

from generic_ml_workflow.core import eventtypes as et
from generic_ml_workflow.core import probe
from generic_ml_workflow.core.shotrunner import Resolution


def _proc(returncode=0, stdout="READY", stderr=""):
    return subprocess.CompletedProcess(
        args=["gmlcache"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_probe_success_yields_ok_verdict(tmp_path):
    verdict = probe.run_probe(
        Resolution("claude", "sonnet", "high"), tmp_path / "p", _runner=lambda argv, **kw: _proc()
    )
    assert verdict.event_type is et.EventType.PROBE_RECORDED
    assert verdict.ok and verdict.error is None
    assert (verdict.client, verdict.model, verdict.effort) == ("claude", "sonnet", "high")


def test_probe_failure_keeps_client_error_verbatim(tmp_path):
    msg = "Error: model 'sonnet-9' is not available for this account"
    verdict = probe.run_probe(
        Resolution("claude", "sonnet-9"),
        tmp_path / "p",
        _runner=lambda argv, **kw: _proc(returncode=1, stdout="", stderr=msg),
    )
    assert not verdict.ok
    assert verdict.error == msg  # verbatim, not paraphrased
    assert verdict.effort is None


def test_probe_nonzero_without_stderr_falls_back_to_exit(tmp_path):
    verdict = probe.run_probe(
        Resolution("cursor", "auto"),
        tmp_path / "p",
        _runner=lambda argv, **kw: _proc(returncode=7, stdout="", stderr=""),
    )
    assert not verdict.ok and verdict.error == "exit 7"


def test_probe_seam_failure_becomes_a_failed_verdict_not_an_exception(tmp_path):
    def boom(argv, **kw):
        raise FileNotFoundError("gmlcache")

    verdict = probe.run_probe(Resolution("codex", "gpt"), tmp_path / "p", _runner=boom)
    assert not verdict.ok and verdict.error  # the seam reason rides along; nothing escapes


def test_stream_key_is_stable_and_per_triple():
    assert probe.probe_stream_key("claude", "opus", "high") == "probe:claude/opus/high"
    assert probe.probe_stream_key("claude", "opus", "high") != probe.probe_stream_key(
        "claude", "opus", "low"
    )
    assert probe.probe_stream_key("claude", "opus", None) == "probe:claude/opus/-"

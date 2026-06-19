# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The gmlcache seam (shot runner): argv construction (pure) and the run path
with an injected runner (no real gmlcache needed). DESIGN.md SS8/SS9."""

import json
import subprocess
from pathlib import Path

import pytest

from generic_ml_workflow.core.contract import (
    Lifespan,
    OutputKind,
    OutputPort,
    StepNature,
    StepSpec,
)
from generic_ml_workflow.core.envelope import Envelope
from generic_ml_workflow.core.shotrunner import (
    Resolution,
    ShotError,
    build_argv,
    run_shot,
)


def _shot(filename="summary.md"):
    return StepSpec(
        id="summarize",
        nature=StepNature.INTERPRETABLE,
        cap="summarizer",
        outputs=(OutputPort("summary", Lifespan.DURABLE, OutputKind.FILE, filename),),
    )


def _env():
    return Envelope(context="You are a summarizer.", prompt="Summarize.", files=("/in/text.txt",))


# --- argv (pure) -------------------------------------------------------------


def test_argv_has_client_model_files_mode(tmp_path):
    argv = build_argv(
        _env(),
        Resolution("claude", "sonnet", "high"),
        tmp_path,
        mode="cache",
    )
    assert argv[:2] == ["gmlcache", "run"]
    assert "--client" in argv and "claude" in argv
    assert "--model" in argv and "sonnet" in argv
    assert "--effort" in argv and "high" in argv
    assert "--input-file" in argv and "/in/text.txt" in argv
    assert "--mode" in argv and "cache" in argv
    assert "--store" not in argv and "--output-dir" not in argv


def test_argv_omits_effort_when_absent(tmp_path):
    argv = build_argv(_env(), Resolution("claude", "sonnet"), tmp_path)
    assert "--effort" not in argv


def test_argv_repeats_input_file_per_file(tmp_path):
    env = Envelope(context="c", prompt="p", files=("/a.txt", "/b.txt"))
    argv = build_argv(env, Resolution("codex", "gpt"), tmp_path)
    assert argv.count("--input-file") == 2


# --- run (injected runner) ---------------------------------------------------


def _fake_proc(returncode=0, stdout="ok", stderr=""):
    return subprocess.CompletedProcess(
        args=["gmlcache"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_run_collects_declared_output(tmp_path):
    spec = _shot()

    def fake_runner(argv, **kw):
        # simulate gmlcache writing the declared output into the run folder
        out_dir = Path(kw["cwd"])
        (out_dir / "summary.md").write_text("a summary", encoding="utf-8")
        return _fake_proc()

    result = run_shot(
        spec,
        _env(),
        Resolution("claude", "sonnet"),
        tmp_path / "run",
        _runner=fake_runner,
    )
    assert result.ok
    assert result.outputs[0].name == "summary"
    assert result.outputs[0].path.read_text() == "a summary"
    assert len(result.outputs[0].sha256) == 64


def test_run_writes_context_and_prompt_files(tmp_path):
    seen = {}

    def fake_runner(argv, **kw):
        run = Path(kw["cwd"])
        seen["context"] = (run / "_context.txt").read_text()
        seen["prompt"] = (run / "_prompt.txt").read_text()
        (run / "summary.md").write_text("s", encoding="utf-8")
        return _fake_proc()

    run_shot(
        _shot(),
        _env(),
        Resolution("claude", "m"),
        tmp_path / "run",
        _runner=fake_runner,
    )
    assert seen["context"] == "You are a summarizer."
    assert seen["prompt"] == "Summarize."


def test_run_missing_output_is_an_error(tmp_path):
    def fake_runner(argv, **kw):
        return _fake_proc()  # produces nothing

    with pytest.raises(ShotError, match="did not produce it"):
        run_shot(
            _shot(),
            _env(),
            Resolution("claude", "m"),
            tmp_path / "run",
            _runner=fake_runner,
        )


def test_run_surfaces_gmlcache_error_via_nonzero_exit(tmp_path):
    def fake_runner(argv, **kw):
        return _fake_proc(returncode=1, stdout="", stderr="gmlc: offline miss")

    result = run_shot(
        _shot(),
        _env(),
        Resolution("claude", "m"),
        tmp_path / "run",
        _runner=fake_runner,
    )
    assert not result.ok and result.exit_code == 1
    assert "offline miss" in result.stderr
    assert result.outputs == ()  # no collection on failure


def test_run_rejects_non_shot_step(tmp_path):
    exe = StepSpec(id="x", nature=StepNature.EXECUTABLE, entrypoint="true")
    with pytest.raises(ShotError, match="not an interpretable"):
        run_shot(exe, _env(), Resolution("c", "m"), tmp_path / "run")


def test_run_missing_gmlcache_binary_is_clear(tmp_path):
    def boom(argv, **kw):
        raise FileNotFoundError("gmlcache")

    with pytest.raises(ShotError, match="gmlcache not found"):
        run_shot(
            _shot(),
            _env(),
            Resolution("c", "m"),
            tmp_path / "run",
            _runner=boom,
        )


# --- the --json envelope: answer + normalized usage (slice 1) ----------------


def test_argv_requests_json_envelope(tmp_path):
    argv = build_argv(_env(), Resolution("claude", "sonnet"), tmp_path)
    assert "--json" in argv


def test_run_lifts_answer_and_usage_from_json_envelope(tmp_path):
    envelope = {
        "status": "recorded",
        "cached": False,
        "exit": 0,
        "client": "claude",
        "model": "sonnet",
        "effort": "",
        "files": 1,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "cache_read_tokens": 3,
            "cache_write_tokens": 0,
            "reasoning_tokens": None,
            "cost_usd": 0.002,
        },
        "stdout": "the answer",
    }

    def fake_runner(argv, **kw):
        (Path(kw["cwd"]) / "summary.md").write_text("s", encoding="utf-8")
        return _fake_proc(stdout=json.dumps(envelope))

    result = run_shot(
        _shot(), _env(), Resolution("claude", "sonnet"), tmp_path / "run", _runner=fake_runner
    )
    assert result.ok
    assert result.stdout == "the answer"  # answer lifted from the envelope, not the raw JSON
    assert result.usage is not None
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.usage.cache_read_tokens == 3
    assert result.usage.cost_usd == 0.002
    assert result.usage.total_tokens == 19


def test_run_degrades_when_stdout_is_not_an_envelope(tmp_path):
    def fake_runner(argv, **kw):
        (Path(kw["cwd"]) / "summary.md").write_text("s", encoding="utf-8")
        return _fake_proc(stdout="plain non-json answer")

    result = run_shot(
        _shot(), _env(), Resolution("claude", "m"), tmp_path / "run", _runner=fake_runner
    )
    assert result.ok
    assert result.stdout == "plain non-json answer"  # raw answer kept
    assert result.usage is None  # usage unknown, never invented

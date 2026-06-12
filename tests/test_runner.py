# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The executable-step runner: isolation, input materialization, output
collection, and honest failure (DESIGN.md SS5)."""

import sys
from pathlib import Path

import pytest

from generic_ml_workflow.core.contract import (
    InputPort,
    Lifespan,
    OutputKind,
    OutputPort,
    Requirement,
    StepNature,
    StepSpec,
)
from generic_ml_workflow.core.runner import RunnerError, run_executable


def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _out(name, filename):
    return OutputPort(name=name, lifespan=Lifespan.DURABLE, kind=OutputKind.FILE, filename=filename)


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_runs_and_collects_declared_output(tmp_path):
    script = _script(tmp_path, "make.sh", "echo hello > page.html\n")
    spec = StepSpec(
        id="fetch",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        outputs=(_out("page", "page.html"),),
    )
    result = run_executable(spec, tmp_path / "run", {})
    assert result.ok and result.exit_code == 0
    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.name == "page" and out.path.read_text().strip() == "hello"
    assert len(out.sha256) == 64
    assert result.duration_seconds >= 0


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_materializes_scalar_input(tmp_path):
    # the step reads a file named after its input port, echoes it into the output
    script = _script(tmp_path, "use.sh", "cat url > echoed.txt\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        inputs=(InputPort("url", Requirement.RUN_INPUT),),
        outputs=(_out("echoed", "echoed.txt"),),
    )
    result = run_executable(spec, tmp_path / "run", {"url": "http://example.com"})
    assert result.ok
    assert result.outputs[0].path.read_text().strip() == "http://example.com"


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_materializes_file_input(tmp_path):
    src = _script(tmp_path, "source.txt", "file-content-here")
    script = _script(tmp_path, "copy.sh", "cp source out.txt\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        inputs=(InputPort("source", Requirement.ARTIFACT),),
        outputs=(_out("o", "out.txt"),),
    )
    result = run_executable(spec, tmp_path / "run", {"source": Path(src)})
    assert result.ok
    assert result.outputs[0].path.read_text() == "file-content-here"


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_isolation_run_folder_starts_empty(tmp_path):
    # a stray file in a pre-existing run folder is wiped before the run
    run = tmp_path / "run"
    run.mkdir()
    (run / "stray.txt").write_text("leftover", encoding="utf-8")
    script = _script(tmp_path, "ls.sh", "ls > listing.txt\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        outputs=(_out("listing", "listing.txt"),),
    )
    result = run_executable(spec, run, {})
    assert "stray.txt" not in result.outputs[0].path.read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_missing_declared_output_is_a_failure(tmp_path):
    script = _script(tmp_path, "nope.sh", "echo doing nothing\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        outputs=(_out("expected", "expected.txt"),),
    )
    with pytest.raises(RunnerError, match="was not produced"):
        run_executable(spec, tmp_path / "run", {})


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_nonzero_exit_is_reported_not_raised(tmp_path):
    script = _script(tmp_path, "fail.sh", "exit 3\n")
    spec = StepSpec(id="s", nature=StepNature.EXECUTABLE, entrypoint=str(script))
    result = run_executable(spec, tmp_path / "run", {})
    assert not result.ok and result.exit_code == 3
    assert result.outputs == ()  # no outputs collected on failure


def test_python_entrypoint(tmp_path):
    script = _script(tmp_path, "make.py", "open('out.txt','w').write('py')\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        outputs=(_out("o", "out.txt"),),
    )
    result = run_executable(spec, tmp_path / "run", {})
    assert result.ok and result.outputs[0].path.read_text() == "py"


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_command_line_entrypoint(tmp_path):
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint="echo cmd > made.txt",
        outputs=(_out("o", "made.txt"),),
    )
    result = run_executable(spec, tmp_path / "run", {})
    assert result.ok and result.outputs[0].path.read_text().strip() == "cmd"


def test_rejects_non_executable_step(tmp_path):
    spec = StepSpec(id="s", nature=StepNature.INTERPRETABLE, cap="x")
    with pytest.raises(RunnerError, match="not executable"):
        run_executable(spec, tmp_path / "run", {})


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_identical_content_same_sha(tmp_path):
    script = _script(tmp_path, "m.sh", "printf abc > a.txt\n")
    spec = StepSpec(
        id="s",
        nature=StepNature.EXECUTABLE,
        entrypoint=str(script),
        outputs=(_out("a", "a.txt"),),
    )
    r1 = run_executable(spec, tmp_path / "r1", {})
    r2 = run_executable(spec, tmp_path / "r2", {})
    assert r1.outputs[0].sha256 == r2.outputs[0].sha256

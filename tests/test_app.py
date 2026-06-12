# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The launch wrapper (app.main): the edge that gates on dependencies before the
workspace is ever built."""

import pytest

from generic_ml_workflow.core import deps
from generic_ml_workflow.repl import app


def test_version_flag_prints_and_returns(monkeypatch, capsys):
    monkeypatch.setattr(app.sys, "argv", ["gmlworkflow", "--version"])
    app.main()
    assert "gmlworkflow" in capsys.readouterr().out


def test_arguments_are_rejected(monkeypatch, capsys):
    monkeypatch.setattr(app.sys, "argv", ["gmlworkflow", "run", "something"])
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 2
    assert "takes no arguments" in capsys.readouterr().out


def test_missing_dependency_blocks_launch(monkeypatch, capsys):
    """The whole point of the slice: a missing mandatory dep stops the app before
    the workspace opens, with a non-zero exit and a helpful message."""
    monkeypatch.setattr(app.sys, "argv", ["gmlworkflow"])

    def fake_require():
        raise deps.DependencyError("generic-ml-workflow cannot start: gmlcache -- not found")

    monkeypatch.setattr(app.deps, "require", fake_require)
    # if the gate failed to stop us, this would explode (no real workspace wanted):
    monkeypatch.setattr(app, "Repl", _ExplodingRepl)
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 1
    assert "cannot start" in capsys.readouterr().err


def test_satisfied_dependencies_build_the_workspace(monkeypatch):
    monkeypatch.setattr(app.sys, "argv", ["gmlworkflow"])
    monkeypatch.setattr(app.deps, "require", lambda: None)
    built = {}
    monkeypatch.setattr(app, "Repl", lambda: _RecordingRepl(built))
    app.main()
    assert built.get("ran") is True


class _ExplodingRepl:
    def run(self):
        raise AssertionError("workspace must not open when a dependency is missing")


class _RecordingRepl:
    def __init__(self, sink):
        self._sink = sink

    def run(self):
        self._sink["ran"] = True

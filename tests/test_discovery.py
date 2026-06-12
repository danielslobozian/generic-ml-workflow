# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Workflow discovery in the flows folder: finds .yaml/.yml, sorts, reports
broken files without throwing."""

import shutil
from pathlib import Path

from generic_ml_workflow.core import discovery

DATA = Path(__file__).parent / "data"


def test_empty_or_absent_flows_dir(tmp_path):
    assert discovery.discover_workflows(tmp_path / "nope") == []
    assert discovery.discover_workflows(tmp_path) == []


def test_finds_and_loads_definitions(tmp_path):
    shutil.copy(DATA / "demo.yaml", tmp_path / "demo.yaml")
    found = discovery.discover_workflows(tmp_path)
    assert len(found) == 1
    assert found[0].name == "demo"
    assert found[0].workflow is not None and found[0].error is None


def test_broken_file_is_reported_not_thrown(tmp_path):
    (tmp_path / "broken.yaml").write_text("steps: [unclosed\n", encoding="utf-8")
    found = discovery.discover_workflows(tmp_path)
    assert len(found) == 1
    assert found[0].workflow is None
    assert "not valid YAML" in found[0].error
    assert found[0].name == "broken"  # falls back to the stem


def test_ignores_non_yaml(tmp_path):
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    assert discovery.discover_workflows(tmp_path) == []

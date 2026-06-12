# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The YAML loader: the demo fixture loads + validates, and every malformed
field surfaces a precise WorkflowError. The demo is TEST DATA (tests/data/),
never shipped in the wheel."""

from pathlib import Path

import pytest

from generic_ml_workflow.core.contract import InputType, StepNature, Tier
from generic_ml_workflow.core.loader import load_workflow
from generic_ml_workflow.core.contract import WorkflowError

DATA = Path(__file__).parent / "data"


def _write(tmp_path, body, name="wf.yaml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_demo_fixture_loads_and_validates_clean():
    wf = load_workflow(DATA / "demo.yaml")
    assert wf.name == "demo"
    assert wf.input_type is InputType.URL
    assert [s.id for s in wf.steps] == ["fetch", "extract", "summarize"]
    fetch, extract, summarize = wf.steps
    assert fetch.nature is StepNature.EXECUTABLE and fetch.tier is Tier.LOW
    assert summarize.nature is StepNature.INTERPRETABLE and summarize.cap == "summarizer"
    assert wf.run_inputs() == ("url",)
    result = wf.validate()
    assert result.ok, result.errors
    # 'summary' is the terminal deliverable -> a single dead-branch warning is fine
    assert all("summary" in w for w in result.warnings)


def test_name_defaults_to_file_stem(tmp_path):
    p = _write(
        tmp_path,
        "input_type: file\nsteps:\n  - {id: s, nature: executable, entrypoint: e}\n",
        name="stemmed.yaml",
    )
    assert load_workflow(p).name == "stemmed"


def test_non_mapping_rejected(tmp_path):
    p = _write(tmp_path, "- a\n- b\n")
    with pytest.raises(WorkflowError, match="must be a mapping"):
        load_workflow(p)


def test_invalid_yaml_rejected(tmp_path):
    p = _write(tmp_path, "steps: [unclosed\n")
    with pytest.raises(WorkflowError, match="not valid YAML"):
        load_workflow(p)


def test_unknown_input_type_rejected(tmp_path):
    p = _write(tmp_path, "input_type: hologram\nsteps: []\n")
    with pytest.raises(WorkflowError, match="unknown input_type"):
        load_workflow(p)


def test_unknown_nature_rejected(tmp_path):
    p = _write(tmp_path, "steps:\n  - {id: s, nature: hybrid, entrypoint: e}\n")
    with pytest.raises(WorkflowError, match="invalid nature"):
        load_workflow(p)


def test_unknown_tier_rejected(tmp_path):
    p = _write(tmp_path, "steps:\n  - {id: s, nature: executable, entrypoint: e, tier: turbo}\n")
    with pytest.raises(WorkflowError, match="invalid tier"):
        load_workflow(p)


def test_missing_required_field_rejected(tmp_path):
    p = _write(tmp_path, "steps:\n  - {nature: executable, entrypoint: e}\n")  # no id
    with pytest.raises(WorkflowError, match="missing required field 'id'"):
        load_workflow(p)


def test_input_requirement_kind_parsed(tmp_path):
    p = _write(
        tmp_path,
        "steps:\n"
        "  - id: s\n    nature: executable\n    entrypoint: e\n"
        "    inputs:\n      - {name: url, require: run_input}\n",
    )
    wf = load_workflow(p)
    assert wf.run_inputs() == ("url",)


def test_unknown_requirement_kind_rejected(tmp_path):
    p = _write(
        tmp_path,
        "steps:\n  - id: s\n    nature: executable\n    entrypoint: e\n"
        "    inputs:\n      - {name: url, require: telepathy}\n",
    )
    with pytest.raises(WorkflowError, match="invalid require"):
        load_workflow(p)


def test_bindings_parsed(tmp_path):
    p = _write(
        tmp_path,
        "steps:\n"
        "  - id: a\n    nature: executable\n    entrypoint: e\n"
        "    outputs:\n      - {name: page, lifespan: durable, kind: file, filename: p.html}\n"
        "  - id: b\n    nature: interpretable\n    cap: c\n"
        "    inputs:\n      - {name: src, require: artifact}\n"
        "    outputs:\n      - {name: out, lifespan: durable, kind: file, filename: o.md}\n"
        "bindings:\n  - {step: b, port: src, product: page}\n",
    )
    wf = load_workflow(p)
    assert wf.validate().ok
    assert wf.bindings[0].step_id == "b" and wf.bindings[0].product == "page"

# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The YAML loader (converted salvage groundwork): happy path + the violations it
must surface. Neutral fixtures only; the bundled demo arrives with 0.0.3."""

import pytest

from generic_ml_workflow.core.contract import StepNature, Tier
from generic_ml_workflow.core.loader import load_workflow

GOOD = """
name: demo
steps:
  - id: fetch
    nature: executable
    entrypoint: fetch_page
    tier: low
    inputs:
      - {name: url, from: literal}
    outputs:
      - {name: page, lifespan: durable, kind: file, filename: page.html}
  - id: analyze
    nature: interpretable
    cap: analyst
    inputs:
      - {name: page, from: fetch.page}
    outputs:
      - {name: report, lifespan: durable, kind: file, filename: report.md}
"""


def test_load_happy_path(tmp_path):
    p = tmp_path / "demo.yaml"
    p.write_text(GOOD, encoding="utf-8")
    wf = load_workflow(p)
    assert wf.name == "demo"
    fetch, analyze = wf.steps
    assert fetch.nature is StepNature.EXECUTABLE and fetch.tier is Tier.LOW
    assert analyze.nature is StepNature.INTERPRETABLE and analyze.tier is Tier.MEDIUM  # default
    assert analyze.inputs[0].from_step == "fetch.page"


def test_name_defaults_to_the_file_stem(tmp_path):
    p = tmp_path / "stemmed.yaml"
    p.write_text("steps:\n  - {id: s, nature: executable, entrypoint: e}\n", encoding="utf-8")
    assert load_workflow(p).name == "stemmed"


def test_non_mapping_document_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a mapping"):
        load_workflow(p)


def test_bad_wiring_surfaces_the_contract_error(tmp_path):
    p = tmp_path / "wired.yaml"
    p.write_text(
        """
name: broken
steps:
  - id: analyze
    nature: interpretable
    cap: analyst
    inputs:
      - {name: page, from: fetch.page}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown step"):
        load_workflow(p)


def test_unknown_nature_and_tier_rejected(tmp_path):
    p = tmp_path / "n.yaml"
    p.write_text("steps:\n  - {id: s, nature: hybrid, entrypoint: e}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_workflow(p)
    p.write_text(
        "steps:\n  - {id: s, nature: executable, entrypoint: e, tier: turbo}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_workflow(p)

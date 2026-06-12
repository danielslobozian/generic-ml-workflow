# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Version is single-sourced from package metadata; pyproject holds the only number."""

from importlib.metadata import version as pkg_version
from pathlib import Path

import generic_ml_workflow


def test_version_matches_metadata():
    assert generic_ml_workflow.__version__ == pkg_version("generic-ml-workflow")


def test_no_hardcoded_version_in_package():
    """The pyproject `version` is the only hardcoded number: no source module may
    carry a literal version assignment."""
    src = Path(__file__).parent.parent / "src" / "generic_ml_workflow"
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("__version__") and '"0+unknown"' not in line:
                # the only permitted assignments are the metadata lookup + its fallback
                assert "version(" in line or "0+unknown" in line, (
                    f"{py.name} hardcodes a version: {line.strip()}"
                )

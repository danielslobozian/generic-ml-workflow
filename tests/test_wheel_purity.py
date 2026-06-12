# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The wheel ships engine CODE ONLY -- never workflow definitions, configuration,
or other data (DESIGN.md invariant). Bundling meta-code would violate the
engine/meta-code boundary. This builds a wheel and asserts every payload entry is
a .py module (the only non-code allowed is the standard .dist-info metadata).

Skipped automatically if the 'build' package isn't available (e.g. a minimal
environment); CI installs the dev extras, so it runs there.
"""

import glob
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent


def test_built_wheel_contains_only_python_modules(tmp_path):
    pytest.importorskip("build", reason="the 'build' package is required for this check")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stderr}"
    wheels = glob.glob(str(tmp_path / "*.whl"))
    assert wheels, "no wheel was produced"
    names = zipfile.ZipFile(wheels[0]).namelist()
    offenders = [
        n
        for n in names
        if not n.endswith("/")  # directory entries
        and not n.endswith(".py")
        and not n.split("/", 1)[0].endswith(".dist-info")  # packaging metadata is fine
    ]
    assert not offenders, (
        "the wheel must ship code only -- found non-code payload:\n  " + "\n  ".join(offenders)
    )

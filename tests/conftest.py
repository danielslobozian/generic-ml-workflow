# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Test isolation: no test ever reads the developer's real config or environment.

Every test gets GMLWORKFLOW_CONFIG pointed at a fresh, valid, tmp config and the
per-setting env vars cleared. Tests that need the no-config (interview) path or a
broken config override the env / pass config_file explicitly.
"""

import pytest


@pytest.fixture(autouse=True)
def config_isolation(tmp_path, monkeypatch):
    cfg = tmp_path / "isolated-config.toml"
    cfg.write_text(
        f"""
[paths]
flows = "{(tmp_path / "flows").as_posix()}"
state = "{(tmp_path / "state").as_posix()}"
workspace = "{(tmp_path / "workspace").as_posix()}"

[ui]
banner = "panel"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("GMLWORKFLOW_CONFIG", str(cfg))
    for var in (
        "GMLWORKFLOW_FLOWS",
        "GMLWORKFLOW_STATE",
        "GMLWORKFLOW_WORKSPACE",
        "GMLWORKFLOW_BANNER",
    ):
        monkeypatch.delenv(var, raising=False)
    return cfg

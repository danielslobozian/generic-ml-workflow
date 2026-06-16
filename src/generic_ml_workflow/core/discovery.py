# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""discovery.py -- find workflow definitions in the user's flows folder.

The flows folder is the user's meta-code (their git repo). Definitions are YAML
files at its top level. Discovery only *finds and names* them; loading/validation
is the loader/contract's job. A definition that fails to load is still listed,
with the load error captured, so ``/list`` never hides a broken file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from generic_ml_workflow.core.contract import ProviderSpec, Workflow, WorkflowError
from generic_ml_workflow.core.loader import load_provider, load_workflow


@dataclass(frozen=True)
class Discovered:
    """One definition file found in the flows folder."""

    path: Path
    workflow: Workflow | None  # None if it failed to load
    error: str | None = None  # the load error, when it failed

    @property
    def name(self) -> str:
        return self.workflow.name if self.workflow is not None else self.path.stem


def discover_workflows(flows_dir: Path) -> list[Discovered]:
    """List the workflow definitions in ``flows_dir`` (top level only), sorted by
    filename. Never raises -- a broken file is reported, not thrown."""
    if not flows_dir.is_dir():
        return []
    found: list[Discovered] = []
    for path in sorted(flows_dir.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml") or not path.is_file():
            continue
        try:
            wf = load_workflow(path)
            found.append(Discovered(path=path, workflow=wf))
        except WorkflowError as exc:
            found.append(Discovered(path=path, workflow=None, error=str(exc)))
    return found


def discover_providers(flows_dir: Path) -> dict[str, ProviderSpec]:
    """Load provider descriptions from ``flows_dir/providers`` (one YAML per
    provider), keyed by kind. Meta-code, alongside the workflows. Missing folder ->
    empty map. A malformed description raises ``WorkflowError`` named with the file
    (unlike workflow discovery, a provider schema must be sound to be trusted for
    validation), and a duplicate kind is rejected."""
    providers_dir = flows_dir / "providers"
    if not providers_dir.is_dir():
        return {}
    specs: dict[str, ProviderSpec] = {}
    for path in sorted(providers_dir.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml") or not path.is_file():
            continue
        spec = load_provider(path)
        if spec.kind in specs:
            raise WorkflowError(f"{path.name}: provider kind '{spec.kind}' is already defined")
        specs[spec.kind] = spec
    return specs

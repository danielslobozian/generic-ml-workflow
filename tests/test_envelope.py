# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The shot request envelope and its purity rule (DESIGN.md SS8)."""

import pytest

from generic_ml_workflow.core.envelope import (
    Envelope,
    PurityError,
    build_envelope,
    check_purity,
)


def test_clean_context_builds():
    env = build_envelope(
        context="You are a careful summarizer. Methodology: read, then condense.",
        prompt="Summarize the attached text.",
        files=("/runs/x/page_text.txt",),
    )
    assert isinstance(env, Envelope)
    assert env.prompt.startswith("Summarize")
    assert env.files == ("/runs/x/page_text.txt",)


def test_timestamp_in_context_is_rejected():
    with pytest.raises(PurityError, match="timestamp"):
        build_envelope(context="Run at 2026-06-12T19:00 for the job.", prompt="go")


def test_absolute_path_in_context_is_rejected():
    with pytest.raises(PurityError, match="absolute path"):
        build_envelope(context="Read the file at /home/user/data/input.txt first.", prompt="go")


def test_windows_path_in_context_is_rejected():
    with pytest.raises(PurityError, match="absolute path"):
        check_purity("See C:\\Users\\me\\file.txt for details.")


def test_execution_id_in_context_is_rejected():
    with pytest.raises(PurityError, match="execution/session id"):
        check_purity("execution_id: 4e29b669 -- consider this run.")


def test_prompt_and_files_may_carry_run_specific_material():
    # only the CONTEXT must be pure; the prompt/files are the run-specific parts
    env = build_envelope(
        context="You are a summarizer.",
        prompt="Summarize the page fetched at 2026-06-12T19:00 from /tmp/page.html",
        files=("/runs/abc/execution_id_notes.txt",),
    )
    assert "2026" in env.prompt  # allowed in the prompt


def test_clean_context_with_relative_mentions_passes():
    # relative references and ordinary prose are fine
    check_purity("Read source_text and produce a concise summary in markdown.")

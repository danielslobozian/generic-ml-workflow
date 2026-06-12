# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The REPL shell, driven through the scripted-input harness (injected I/O --
prompt_toolkit is never constructed on this path)."""

from generic_ml_workflow import __version__
from generic_ml_workflow.core.detect import ClientStatus, Detection
from generic_ml_workflow.repl.shell import Repl


def drive(lines, detection):
    """Run the REPL over a scripted list of input lines; return all output."""
    script = iter(lines)
    out: list[str] = []

    def read(prompt: str) -> str | None:
        return next(script, None)  # exhausted -> EOF

    repl = Repl(read=read, write=out.append, discover=lambda: detection)
    repl.run()
    return "\n".join(out)


PRESENT = Detection(
    gmlcache_present=True,
    clients=(
        ClientStatus(name="claude", present=True, version="1.0.35"),
        ClientStatus(name="codex", present=False, detail="not on PATH"),
    ),
)
ABSENT = Detection(gmlcache_present=False, gmlcache_detail="'gmlcache' was not found on PATH")


def test_banner_snapshot():
    """The banner shows the name and the version, plain (no colour) when captured."""
    out = drive(["/quit"], PRESENT)
    assert "generic-ml-workflow" in out
    assert __version__ in out
    assert "\x1b[" not in out  # no ANSI codes on the injected-I/O path


def test_detection_rendering_present():
    out = drive(["/quit"], PRESENT)
    assert "claude" in out and "1.0.35" in out
    assert "codex" in out and "not found" in out


def test_gmlcache_absent_is_graceful_and_advisory():
    out = drive(["/quit"], ABSENT)
    assert "gmlcache not found" in out
    assert "pip install generic-ml-cache" in out
    assert "the workspace still opens" in out
    assert "bye." in out  # the loop ran and exited cleanly


def test_help_lists_the_closed_verb_set():
    out = drive(["/help", "/quit"], PRESENT)
    for verb in ("/run", "/list", "/validate", "/replay", "/status", "/cost", "/help", "/quit"):
        assert verb in out
    assert "/exit" not in out  # alias shown once, as /quit


def test_stub_verbs_answer_not_yet_with_the_slice():
    out = drive(["/run", "/quit"], PRESENT)
    assert "not yet" in out and "0.0.5" in out and "ROADMAP" in out


def test_every_stub_names_its_slice():
    for verb, slice_id in [
        ("/list", "0.0.3"),
        ("/validate", "0.0.3"),
        ("/replay", "0.0.4"),
        ("/status", "0.0.2"),
        ("/cost", "0.0.10"),
        ("/export", "0.1.2"),
        ("/companion", "0.3.1"),
    ]:
        out = drive([verb, "/quit"], PRESENT)
        assert "not yet" in out and slice_id in out, f"{verb} should point at {slice_id}"


def test_unknown_command():
    out = drive(["/frobnicate", "/quit"], PRESENT)
    assert "unknown command" in out and "/help" in out


def test_bare_verbs_accepted_leniently():
    out = drive(["help", "quit"], PRESENT)
    assert "commands:" in out and "bye." in out


def test_quit_and_eof_both_leave():
    assert "bye." in drive(["/quit"], PRESENT)
    assert "bye." in drive([], PRESENT)  # immediate EOF


def test_clients_reruns_detection():
    calls = []

    def discover():
        calls.append(1)
        return PRESENT

    script = iter(["/clients", "/quit"])
    out: list[str] = []
    Repl(read=lambda p: next(script, None), write=out.append, discover=discover).run()
    assert len(calls) == 2  # startup + /clients


def test_completions_pure_logic():
    repl = Repl(read=lambda p: None, write=lambda s: None, discover=lambda: PRESENT)
    assert "/help" in repl._completions("/he", 0)
    assert "help" in repl._completions("he", 0)
    assert "/exit" not in repl._completions("/", 0)  # alias hidden
    assert repl._completions("/banner pa", 8) == ["panel"]
    assert repl._completion_meta("/quit") == "leave the workspace"


def test_banner_verb_switches_and_previews():
    out = drive(["/banner", "/banner minimal", "/banner nope", "/quit"], PRESENT)
    assert "available: panel, minimal" in out
    assert "banner -> minimal" in out
    assert "no such banner" in out

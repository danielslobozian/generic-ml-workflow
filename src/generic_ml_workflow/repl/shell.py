# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""shell.py -- the work surface, as a bounded REPL.

Launching ``gmlworkflow`` lands you HERE, not in a help dump: there is no
argument-driven human usage model, by design. The shell shows a banner, performs
the ONE startup step the design allows -- asking gmlcache what clients exist
(detection, not selection; advisory, never gating) -- and then waits on a closed
verb set.

Three commitments from the design are made literal here:

  * The verb set is CLOSED. Adding a verb is one row in ``_verbs``; that table is
    the whole complexity budget. In this slice (0.0.1) most verbs are honest
    stubs that answer "not yet" and point at the roadmap.
  * Commands wear a leading slash (``/help``, ``/quit``), matching the convention
    of the clients themselves and reserving bare input for the future
    natural-language routing channel (roadmap 0.3.0). The bare form is accepted
    too for now (lenient), and tightens to slash-only once that channel exists.
  * Startup is cheap and token-free: one ``gmlcache doctor --json`` relay, no
    model calls, nothing domain-specific.

Input layer: prompt_toolkit (not stdlib readline). The choice is deliberate --
readline is GNU on most Linux, libedit on macOS and some Linux builds, and
near-absent on Windows, each with different completion behaviour. prompt_toolkit
renders its own line editor and completion menu, identically across OSes, with a
live as-you-type menu and a description column per command. It activates ONLY on
the real-stdin path: when I/O is injected (tests) or stdin is not a TTY (pipes,
CI), the loop reads via the plain callable and prompt_toolkit is never
constructed, so the loop stays driveable and assertable without a terminal. The
completion *logic* (``_completions``) is a pure function so it is unit-tested
directly. History is in-memory only for now: the app writes no files until the
first-run interview (0.0.2) has asked where they should live.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import CompleteStyle

from generic_ml_workflow import __version__
from generic_ml_workflow.core import detect
from generic_ml_workflow.repl import banner

ROADMAP_URL = "https://github.com/danielslobozian/generic-ml-workflow/blob/main/docs/ROADMAP.md"


@dataclass
class _Verb:
    handler: Callable[["Repl", list[str]], bool]  # returns: keep the loop running?
    usage: str
    help: str


def _stub(slice_id: str, what: str) -> Callable[["Repl", list[str]], bool]:
    """A verb that exists but honestly does not work yet."""

    def handler(repl: "Repl", args: list[str]) -> bool:
        repl._write(f"not yet -- {what} arrives with slice {slice_id}. see the roadmap:")
        repl._write(f"  {ROADMAP_URL}")
        return True

    return handler


class _CommandCompleter(Completer):
    """Bridges prompt_toolkit to the REPL's pure completion logic. It asks the Repl
    for candidate values (``_completions``) and a one-line description per value
    (``_completion_meta``), so the menu shows commands one-per-line with a meta
    column -- the same ``command -> what it does`` shape as ``/help``."""

    def __init__(self, repl: "Repl"):
        self._repl = repl

    def get_completions(self, document, complete_event):
        line = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        begidx = len(line) - len(word)
        for value in self._repl._completions(line, begidx):
            yield Completion(
                value,
                start_position=-len(word),
                display=value,
                display_meta=self._repl._completion_meta(value),
            )


class Repl:
    def __init__(
        self,
        read: Callable[[str], str | None] | None = None,
        write: Callable[[str], None] | None = None,
        discover: Callable[[], detect.Detection] | None = None,
    ):
        self._read = read or _default_read
        self._write = write or print
        self._rich_input = read is None  # prompt_toolkit only on the real stdin path
        self._discover = discover or detect.discover
        self.detection: detect.Detection | None = None
        self._banner_style = banner.DEFAULT
        self._verbs: dict[str, _Verb] = {
            "clients": _Verb(
                Repl._do_clients, "/clients", "re-run detection and list what gmlcache sees"
            ),
            "list": _Verb(
                _stub("0.0.3", "listing workflow definitions"),
                "/list",
                "list the workflow definitions found",
            ),
            "validate": _Verb(
                _stub("0.0.3", "validating a workflow definition"),
                "/validate <flow>",
                "validate a workflow definition",
            ),
            "run": _Verb(
                _stub("0.0.5", "running a workflow"),
                "/run [<workflow>]",
                "run a workflow (the run interview)",
            ),
            "replay": _Verb(
                _stub("0.0.4", "replaying an execution's story"),
                "/replay <execution>",
                "reconstruct an execution from the event log",
            ),
            "status": _Verb(
                _stub("0.0.2", "showing effective settings"),
                "/status",
                "each effective setting, with its source",
            ),
            "cost": _Verb(
                _stub("0.0.10", "the cost view"),
                "/cost",
                "spend in tokens/usage, per step / execution / job",
            ),
            "export": _Verb(
                _stub("0.1.2", "exporting a job's documents"),
                "/export",
                "render a job's documents out of the app",
            ),
            "companion": _Verb(
                _stub("0.3.1", "the companion surface"),
                "/companion",
                "show/hide the companion chat",
            ),
            "banner": _Verb(
                Repl._do_banner, "/banner [name|list]", "switch or preview the startup banner"
            ),
            "help": _Verb(Repl._do_help, "/help", "show the available commands"),
            "quit": _Verb(Repl._do_quit, "/quit", "leave the workspace"),
            "exit": _Verb(Repl._do_quit, "/exit", "leave the workspace"),
        }

    # --- lifecycle ---
    def run(self) -> None:
        self._banner()
        self._startup_detection()
        reader = self._build_reader()
        keep = True
        while keep:
            try:
                line = reader(self._prompt())
            except KeyboardInterrupt:
                self._write("")  # Ctrl-C: drop the half-typed line, keep the shell
                continue
            if line is None:  # EOF (Ctrl-D)
                break
            keep = self._dispatch(line)
        self._write("bye.")

    def _build_reader(self) -> Callable[[str], str | None]:
        """The real terminal gets prompt_toolkit (live menu + cross-OS line editing).
        Injected I/O (tests) or a non-interactive stdin (pipes, CI) gets the plain
        callable -- prompt_toolkit is for interactive terminals only, and this keeps
        piped runs free of its rendering control codes."""
        if not self._rich_input or not sys.stdin.isatty():
            return self._read
        session = PromptSession(
            completer=_CommandCompleter(self),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
        )

        def read(prompt: str) -> str | None:
            try:
                return session.prompt(prompt)
            except EOFError:
                return None

        return read

    # --- prompt ---
    def _prompt(self) -> str:
        return "gmlworkflow> "

    # --- startup pieces ---
    def _banner(self) -> None:
        self._write("")
        self._write(banner.render(self._banner_style, __version__, color=self._use_color()))
        self._write("")

    def _use_color(self) -> bool:
        return self._rich_input and sys.stdin.isatty()

    def _startup_detection(self) -> None:
        self._write("asking gmlcache which clients are installed...")
        self.detection = self._discover()
        self._render_detection()

    def _render_detection(self) -> None:
        d = self.detection
        if d is None:
            return
        if not d.gmlcache_present:
            self._write("  \u00b7 gmlcache not found -- this app executes nothing itself;")
            self._write("    every model call goes through generic-ml-cache.")
            self._write("    install it with: pip install generic-ml-cache")
            self._write(f"    ({d.gmlcache_detail})")
            self._write("")
            self._write("the workspace still opens; '/clients' re-scans once it's installed.")
            self._write("")
            return
        if d.gmlcache_detail:
            self._write(f"  ! gmlcache answered, but: {d.gmlcache_detail}")
        for c in d.clients:
            if c.present:
                self._write(f"  \u2713 {c.name:<8} {c.version or 'version unknown'}")
            else:
                self._write(f"  \u00b7 {c.name:<8} not found")
        if d.clients and not any(c.present for c in d.clients):
            self._write("  (no client installed yet -- detection is advisory, nothing is gated)")
        self._write("")
        self._write("this is slice 0.0.1: the home opens; running workflows is on the roadmap.")
        self._write("'/help' lists the verbs; stubs say which slice brings them to life.")
        self._write("")

    # --- dispatch ---
    def _dispatch(self, line: str) -> bool:
        parts = line.strip().split()
        if not parts:
            return True
        raw, args = parts[0], parts[1:]
        verb_name = raw[1:] if raw.startswith("/") else raw  # lenient: accept /verb or verb
        verb = self._verbs.get(verb_name)
        if verb is None:
            self._write(f"unknown command: {raw!r}. try '/help'.")
            return True
        return verb.handler(self, args)

    # --- verb handlers (return True to keep the loop running) ---
    def _do_clients(self, args: list[str]) -> bool:
        self._startup_detection()
        return True

    def _do_banner(self, args: list[str]) -> bool:
        if not args:
            self._write(f"banner: {self._banner_style}   available: {', '.join(banner.names())}")
            self._write("switch with '/banner <name>', preview all with '/banner list'.")
            return True
        arg = args[0]
        if arg == "list":
            for name in banner.names():
                self._write(("* " if name == self._banner_style else "  ") + name + ":")
                self._write(banner.render(name, __version__, color=self._use_color()))
                self._write("")
            return True
        if arg in banner.names():
            self._banner_style = arg
            self._write(f"banner -> {arg}  (persisting the choice arrives with 0.0.2)")
            self._write(banner.render(self._banner_style, __version__, color=self._use_color()))
            return True
        self._write(f"no such banner {arg!r}. available: {', '.join(banner.names())}")
        return True

    def _do_help(self, args: list[str]) -> bool:
        self._write("commands:")
        for vname, verb in self._verbs.items():
            if vname == "exit":  # alias of quit; show once
                continue
            self._write(f"  {verb.usage:<22} {verb.help}")
        return True

    def _do_quit(self, args: list[str]) -> bool:
        return False

    # --- completion (pure logic; prompt_toolkit and tests both use it) ---
    def _completions(self, line: str, begidx: int) -> list[str]:
        """The candidate values for the word at ``begidx``. First token -> a verb;
        an argument to ``/banner`` -> a style name. Honours a leading slash. Pure:
        no prompt_toolkit, no readline -- directly unit-testable."""
        text = line[begidx:]
        before = line[:begidx].split()
        if not before:  # completing the verb
            slash = text.startswith("/")
            stub = text[1:] if slash else text
            verbs = [v for v in self._verbs if v != "exit" and v.startswith(stub)]
            return [("/" + v) if slash else v for v in verbs]
        head = before[0]
        head = head[1:] if head.startswith("/") else head
        if head == "banner" and len(before) == 1:
            return [n for n in [*banner.names(), "list"] if n.startswith(text)]
        return []

    def _completion_meta(self, value: str) -> str:
        """The one-line description shown beside a completion: a verb's help or a
        banner style. Pure and testable."""
        name = value[1:] if value.startswith("/") else value
        verb = self._verbs.get(name)
        if verb is not None:
            return verb.help
        if name in banner.names():
            return "banner style"
        return ""


def _default_read(prompt: str) -> str | None:
    """Fallback reader (used only if a caller injects no reader but we're not on
    the rich path): plain input(), EOF -> None."""
    try:
        return input(prompt)
    except EOFError:
        return None

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

import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import CompleteStyle

from generic_ml_workflow import __version__
from generic_ml_workflow.core import (
    config,
    contract,
    detect,
    discovery,
    events,
    orchestrator,
    paths,
    reconcile,
    stamp,
    stopping,
)
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
        discover_models: Callable[[], tuple[detect.ModelListing, ...] | None] | None = None,
        discover_gmlcache_version: Callable[[], str | None] | None = None,
        config_file: Path | None = None,
    ):
        self._read = read or _default_read
        self._write = write or print
        self._rich_input = read is None  # prompt_toolkit only on the real stdin path
        self._discover = discover or detect.discover
        self._discover_models = discover_models or detect.discover_models
        self._discover_gmlcache_version = (
            discover_gmlcache_version or detect.discover_gmlcache_version
        )
        self._config_file = config_file  # None -> resolved at startup (env-aware)
        self.detection: detect.Detection | None = None
        self.settings: config.Settings | None = None
        self._session: dict[str, object] = {}  # in-session overrides (the "flag" layer)
        self._banner_style = banner.DEFAULT
        self._workflow_names: tuple[str, ...] = ()  # for /run + /validate tab-completion
        self._active_run: threading.Thread | None = None  # the one background run, if any
        self._active_stop: stopping.StopControl | None = None  # its stop control, if any
        self._verbs: dict[str, _Verb] = {
            "clients": _Verb(
                Repl._do_clients, "/clients", "re-run detection and list what gmlcache sees"
            ),
            "tiers": _Verb(
                Repl._do_tiers,
                "/tiers",
                "check your configured tiers against installed clients/models",
            ),
            "list": _Verb(
                Repl._do_list,
                "/list",
                "list the workflow definitions found",
            ),
            "validate": _Verb(
                Repl._do_validate,
                "/validate <flow>",
                "validate a workflow definition",
            ),
            "run": _Verb(
                Repl._do_run,
                "/run [<workflow>] [<step>=<tier> ...]",
                "run a workflow (the run interview); optionally override a step's tier",
            ),
            "stop": _Verb(
                Repl._do_stop,
                "/stop",
                "stop the run in progress (also: press Escape)",
            ),
            "resume": _Verb(
                Repl._do_resume,
                "/resume [<execution>]",
                "continue a stopped run (bare: the most recent)",
            ),
            "replay": _Verb(
                Repl._do_replay,
                "/replay [<execution>]",
                "reconstruct an execution from the event log",
            ),
            "status": _Verb(
                Repl._do_status,
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
        self._startup_config()
        self._banner()
        if self.settings is not None and self.settings.config_file is None:
            self._first_run_interview()
        self._startup_detection()
        self._refresh_workflow_names()
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

        # Escape stops the run in progress. Non-eager on purpose: a lone Escape
        # fires after the escape-sequence flush timeout, so arrow keys and other
        # escape sequences are unaffected. No run in progress -> a harmless no-op.
        keys = KeyBindings()

        @keys.add("escape")
        def _(_event) -> None:
            self._request_stop()

        session = PromptSession(
            completer=_CommandCompleter(self),
            complete_while_typing=True,
            complete_style=CompleteStyle.COLUMN,
            key_bindings=keys,
        )

        def read(prompt: str) -> str | None:
            try:
                # patch_stdout lets the background run's progress (printed from its
                # worker thread) appear *above* the live prompt without corrupting
                # the line the user is typing.
                with patch_stdout():
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

    # --- configuration -------------------------------------------------------
    def _startup_config(self) -> None:
        """Resolve the effective settings. A broken config is reported loudly but
        does not kill the workspace: built-in defaults carry the session and
        nothing is written."""
        try:
            self.settings = config.load(self._config_file, session=self._session)
        except config.ConfigError as exc:
            self._write(f"! config problem: {exc}")
            self._write("  running on built-in defaults this session; nothing will be written.")
            absent = Path("__gmlworkflow_no_config__")  # never a real file
            self.settings = config.load(absent, session=self._session, env={})
            # keep the broken file out of reach: mark as absent-but-do-not-interview
            self._config_broken = True
            return
        self._config_broken = False
        self._banner_style = self.settings.banner

    def _first_run_interview(self) -> None:
        """No config found -> propose, ask, and only then write (the one file the
        app ever creates unasked-for-content -- and it asks first). Skipping (EOF
        or empty abort) writes nothing and the session runs on defaults."""
        if getattr(self, "_config_broken", False):
            return
        cfg_path = self._config_file if self._config_file is not None else paths.config_path()
        defaults = paths.Paths()
        self._write(f"no configuration found at {cfg_path}")
        self._write("first run? let's pick where the app keeps its things:")
        self._write("  1) standard OS folders (recommended)")
        self._write("  2) one single folder for everything")
        self._write("  3) custom paths")
        choice = self._read("choose 1-3 (Enter = 1, Ctrl-D = skip, nothing written): ")
        if choice is None:
            self._write("skipped -- nothing written; built-in defaults carry this session.")
            self._write("")
            return
        choice = choice.strip() or "1"
        if choice == "1":
            flows, state, ws = defaults.flows_dir, defaults.state_dir, defaults.workspace_dir
        elif choice == "2":
            base = self._ask_path("one folder for everything", defaults.flows_dir.parent)
            if base is None:
                self._write("skipped -- nothing written; built-in defaults carry this session.")
                self._write("")
                return
            flows, state, ws = base / "flows", base / "state", base / "workspace"
        elif choice == "3":
            flows = self._ask_path("flows folder (your meta-code)", defaults.flows_dir)
            state = flows and self._ask_path("state folder (event db, logs)", defaults.state_dir)
            ws = state and self._ask_path("workspace folder (run outputs)", defaults.workspace_dir)
            if ws is None:
                self._write("skipped -- nothing written; built-in defaults carry this session.")
                self._write("")
                return
        else:
            self._write(f"{choice!r} is not one of 1-3 -- nothing written; defaults carry this")
            self._write("session, and the interview returns at next launch.")
            self._write("")
            return
        text = config.initial_config_text(flows, state, ws, banner=self._banner_style)
        config.write_initial_config(cfg_path, text)
        for p in (flows, state, ws):
            p.mkdir(parents=True, exist_ok=True)
        self._init_flows_repo(flows)
        self._write(f"wrote {cfg_path} (documented inline; edit anytime) and created the folders.")
        self._write("")
        self._startup_config()  # reload so /status shows the file as the source

    def _ask_path(self, what: str, default: Path) -> Path | None:
        """Ask for an absolute path (~ allowed). Relative answers are re-asked --
        the app is location-blind and resolves nothing against the cwd."""
        for _ in range(3):
            raw = self._read(f"{what} [{default}]: ")
            if raw is None:
                return None
            raw = raw.strip()
            if not raw:
                return default
            p = Path(raw).expanduser()
            if p.is_absolute():
                return p
            self._write("please give an absolute path (the app never resolves against the cwd).")
        return None

    def _init_flows_repo(self, flows: Path) -> None:
        """Initialize the flows folder as a git repo -- the app drives git over the
        user's meta-code (versioning, time travel). git is a verified mandatory
        dependency by the time we get here. An already-initialized repo is left
        untouched: we never re-init or touch existing history."""
        if (flows / ".git").exists():
            self._write(f"  flows folder is already a git repo: {flows}")
            return
        try:
            subprocess.run(
                ["git", "init", "-q", str(flows)],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except Exception as exc:  # noqa: BLE001 -- never let repo init abort the interview
            self._write(f"  ! could not initialize git in {flows}: {exc}")
            return
        gitignore = flows / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "# Runtime artifacts and local files never belong in meta-code.\n"
                "__pycache__/\n*.pyc\n.DS_Store\n",
                encoding="utf-8",
            )
        self._write(f"  initialized a git repo for your workflows: {flows}")

    def _do_list(self, args: list[str]) -> bool:
        flows = self._flows_dir()
        if flows is None:
            return True
        self._refresh_workflow_names()
        found = discovery.discover_workflows(flows)
        if not found:
            self._write(f"no workflow definitions in {flows}")
            self._write("author one there (a .yaml file) -- it's your meta-code, your git repo.")
            return True
        self._write(f"workflows in {flows}:")
        for d in found:
            if d.workflow is not None:
                itype = d.workflow.input_type.value
                self._write(f"  {d.name:<24} <{itype}>   ({d.path.name})")
            else:
                self._write(f"  {d.path.stem:<24} [does not load]   ({d.path.name})")
        return True

    def _do_validate(self, args: list[str]) -> bool:
        if not args:
            self._write("usage: /validate <flow>   (a name or filename in your flows folder)")
            return True
        flows = self._flows_dir()
        if flows is None:
            return True
        target = args[0]
        found = discovery.discover_workflows(flows)
        match = next(
            (
                d
                for d in found
                if d.name == target or d.path.name == target or d.path.stem == target
            ),
            None,
        )
        if match is None:
            self._write(f"no workflow '{target}' in {flows}. try '/list'.")
            return True
        if match.workflow is None:
            self._write(f"'{target}' does not load:")
            self._write(f"  {match.error}")
            return True
        result = match.workflow.validate()
        if result.ok and not result.warnings:
            self._write(f"'{match.name}' is valid -- no errors, no warnings.")
            return True
        if result.errors:
            self._write(f"'{match.name}' has {len(result.errors)} error(s):")
            for e in result.errors:
                self._write(f"  \u2717 {e}")
        if result.warnings:
            self._write(f"'{match.name}' has {len(result.warnings)} warning(s):")
            for w in result.warnings:
                self._write(f"  ! {w}")
        if result.ok:
            self._write("(warnings only -- the workflow is loadable and runnable.)")
        return True

    def _flows_dir(self) -> Path | None:
        """The configured flows folder, or None (with a message) if unresolved."""
        if self.settings is None:
            self._write("settings not resolved (startup did not run).")
            return None
        return self.settings.flows_dir

    def _open_store(self) -> "events.EventStore | None":
        """Open the event store at the configured state dir's db path. The store is
        read-only here (nothing emits run events until /run, slice 0.0.5), so an
        absent database simply has no executions to show."""
        if self.settings is None:
            self._write("settings not resolved (startup did not run).")
            return None
        db = self.settings.state_dir / "gmlworkflow.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        return events.EventStore(db)

    def _do_run(self, args: list[str]) -> bool:
        if self._active_run is not None and self._active_run.is_alive():
            self._write("a run is already in progress -- wait for it to finish.")
            return True
        flows = self._flows_dir()
        if flows is None:
            return True
        self._refresh_workflow_names()
        found = discovery.discover_workflows(flows)
        runnable = [d for d in found if d.workflow is not None]
        if not runnable:
            self._write(f"no runnable workflows in {flows}. author one (a .yaml), then '/run'.")
            return True
        # select the workflow
        if args:
            match = next((d for d in runnable if d.name == args[0] or d.path.stem == args[0]), None)
            if match is None:
                self._write(f"no workflow '{args[0]}'. try '/list'.")
                return True
        elif len(runnable) == 1:
            match = runnable[0]
        else:
            self._write("which workflow?")
            for d in runnable:
                self._write(f"  {d.name} <{d.workflow.input_type.value}>")
            self._write("run one with '/run <name>'.")
            return True
        workflow = match.workflow
        # validate before asking anything
        result = workflow.validate()
        if not result.ok:
            self._write(f"'{workflow.name}' does not validate -- fix it first ('/validate'):")
            for e in result.errors:
                self._write(f"  \u2717 {e}")
            return True
        # run mode flag: `--manual` (checkpoint after every step) | `--questions`
        # (block only when a step asks) | `--auto` (default, straight through).
        rest = args[1:]
        flags = {t for t in rest if t.startswith("--")}
        unknown = flags - {"--manual", "--auto", "--questions"}
        if unknown:
            self._write(f"unknown option(s): {', '.join(sorted(unknown))}. try '/help'.")
            return True
        if "--manual" in flags and "--questions" in flags:
            self._write("--manual and --questions are different run modes; pick one.")
            return True
        if "--manual" in flags:
            mode = orchestrator.RunMode.FULL_MANUAL
        elif "--questions" in flags:
            mode = orchestrator.RunMode.QUESTIONS_ONLY
        else:
            mode = orchestrator.RunMode.FULL_AUTO
        # optional per-step tier overrides: `/run <flow> <step>=<tier> ...`
        overrides = self._parse_tier_overrides(
            workflow, [t for t in rest if not t.startswith("--")]
        )
        if overrides is None:
            return True  # a bad override was reported; don't run with it
        # the computed interview: ask for the union of unsatisfied run-inputs
        run_inputs: dict[str, str] = {}
        for name in workflow.run_inputs():
            answer = self._read(f"  {name}: ")
            if answer is None:
                self._write("cancelled.")
                return True
            run_inputs[name] = answer.strip()
        self._execute_run(workflow, run_inputs, flows, overrides, mode)
        return True

    def _do_stop(self, args: list[str]) -> bool:
        if not self._request_stop():
            self._write("nothing is running.")
        return True

    def _request_stop(self) -> bool:
        """Ask the run in progress to stop. Returns True if there was one to stop.
        Used by both ``/stop`` and the Escape key binding. The engine reads the
        request at the next boundary; a step running right now is torn down at once
        (cascading to the cache, which tears down the client)."""
        if (
            self._active_run is not None
            and self._active_run.is_alive()
            and self._active_stop is not None
        ):
            self._write("stopping the run...")
            self._active_stop.request()
            return True
        return False

    def _parse_tier_overrides(self, workflow, tokens: list[str]) -> dict | None:
        """Parse ``<step>=<tier>`` tokens into ``{step_id: Tier}``, validated against
        the workflow. Returns the map (possibly empty) or ``None`` after reporting a
        problem -- a bad override must stop the run, not be silently dropped."""
        steps_by_id = {s.id: s for s in workflow.steps}
        tiers_by_name = {t.value: t for t in contract.Tier}
        overrides: dict = {}
        for tok in tokens:
            if "=" not in tok:
                self._write(f"  ! bad override '{tok}' -- use '<step>=<tier>' (e.g. analyze=high).")
                return None
            step_id, _, tier_name = tok.partition("=")
            step = steps_by_id.get(step_id)
            if step is None:
                self._write(f"  ! override names step '{step_id}', which isn't in this workflow.")
                return None
            if step.nature is not contract.StepNature.INTERPRETABLE:
                self._write(
                    f"  ! step '{step_id}' isn't a shot -- only shot steps have a tier to override."
                )
                return None
            tier = tiers_by_name.get(tier_name)
            if tier is None:
                self._write(
                    f"  ! '{tier_name}' isn't a tier -- use one of: {', '.join(tiers_by_name)}."
                )
                return None
            overrides[step_id] = tier
        return overrides

    def _execute_run(self, workflow, run_inputs, flows, overrides=None, mode=None) -> None:
        mode = mode or orchestrator.RunMode.FULL_AUTO
        st = stamp.read_stamp(flows)
        if not st.versioned:
            self._write("  (flows folder is unversioned -- recording the run as such)")
        shot_config = self._build_shot_config()
        if overrides:
            for step_id, tier in overrides.items():
                self._write(f"  tier override: {step_id} -> {tier.value}")
        step_count = len(workflow.steps)
        if mode is orchestrator.RunMode.FULL_MANUAL:
            mode_note = " in full-manual -- it pauses after each step ('/resume' to advance)"
        elif mode is orchestrator.RunMode.QUESTIONS_ONLY:
            mode_note = " in questions-only -- it runs through, pausing only when a step asks"
        else:
            mode_note = ""
        if self._rich_input:
            self._write(
                f"running '{workflow.name}' in the background ({step_count} steps){mode_note}; "
                "progress appears as it advances, the prompt stays free."
            )
        else:
            self._write(f"running '{workflow.name}' ({step_count} steps){mode_note}...")

        def do_run(orch, progress, stop) -> None:
            orch.run(
                workflow,
                run_inputs,
                st,
                shot_config=shot_config,
                tier_overrides=overrides,
                mode=mode,
                progress=progress,
                stop=stop,
            )

        self._launch(f"run-{workflow.name}", do_run)

    def _launch(self, label: str, work) -> None:
        """Run ``work(orch, progress, stop)`` on the background worker (real terminal)
        or synchronously (scripted/CI). Owns the worker, the per-run stop control, and
        the event store's whole lifetime -- a SQLite connection is thread-bound, so it
        is opened, used, and closed inside the one worker, never crossing threads."""
        report_progress = self._run_progress_reporter()
        stop = stopping.StopControl()
        self._active_stop = stop

        def worker() -> None:
            store = self._open_store()
            if store is None:
                return
            orch = orchestrator.Orchestrator(store, self.settings.workspace_dir)
            try:
                work(orch, report_progress, stop)
            except orchestrator.OrchestratorError as exc:
                self._write(f"  cannot run: {exc}")
            finally:
                store.close()

        if self._rich_input:
            thread = threading.Thread(target=worker, name=f"gmlworkflow-{label}", daemon=True)
            self._active_run = thread
            thread.start()
        else:
            worker()

    def _do_resume(self, args: list[str]) -> bool:
        if self._active_run is not None and self._active_run.is_alive():
            self._write("a run is already in progress -- wait for it to finish.")
            return True
        flows = self._flows_dir()
        if flows is None:
            return True
        # resolve which execution to resume (bare -> the most recent resumable one)
        store = self._open_store()
        if store is None:
            return True
        try:
            if args:
                row = store.execution(args[0]) or self._find_execution_prefix(store, args[0])
                if row is None:
                    self._write(f"no execution '{args[0]}'. try '/replay' to list them.")
                    return True
            else:
                resumable = [e for e in store.executions() if e["status"] in ("stopped", "running")]
                if not resumable:
                    self._write("no stopped run to resume.")
                    return True
                row = resumable[-1]  # most recent
        finally:
            store.close()

        # find the workflow this execution ran, by name, in the current flows folder
        execution_id = row["execution_id"]
        wanted = row["workflow_name"]
        match = next(
            (d for d in discovery.discover_workflows(flows) if d.name == wanted and d.workflow),
            None,
        )
        if match is None:
            self._write(f"can't resume: workflow '{wanted}' isn't in {flows} anymore.")
            return True
        workflow = match.workflow
        shot_config = self._build_shot_config()
        if self._rich_input:
            self._write(
                f"resuming '{wanted}' ({execution_id[:12]}) in the background; "
                "progress appears as it advances, the prompt stays free."
            )
        else:
            self._write(f"resuming '{wanted}' ({execution_id[:12]})...")

        def do_resume(orch, progress, stop) -> None:
            orch.resume(
                execution_id, workflow, shot_config=shot_config, progress=progress, stop=stop
            )

        self._launch(f"resume-{wanted}", do_resume)
        return True

    def _run_progress_reporter(self) -> Callable[["orchestrator.RunProgress"], None]:
        """Render the engine's advancement notifications onto the prompt as they
        happen. The reporter is the surface's; the engine knows nothing of it beyond
        calling it (DESIGN.md invariant 24). Thin-slice rendering: one line per
        boundary through ``self._write``. The richer live region (a pinned status
        area) is a later ergonomic pass, not the architecture."""
        phase = orchestrator.RunPhase

        def report(progress: "orchestrator.RunProgress") -> None:
            if progress.phase is phase.STEP_STARTED:
                self._write(
                    f"  \u2192 step {progress.step_number}/{progress.step_count}: "
                    f"{progress.step_name}"
                )
            elif progress.phase is phase.STEP_COMPLETED:
                self._write(f"  \u2713 {progress.step_name}")
            elif progress.phase is phase.STEP_FAILED:
                self._write(f"  \u2717 {progress.step_name} failed")
            elif progress.phase is phase.RUN_COMPLETED:
                eid = progress.execution_id[:12]
                self._write(f"done. execution {eid} completed.")
                self._write(f"see the story with '/replay {eid}'.")
            elif progress.phase is phase.RUN_FAILED:
                eid = progress.execution_id[:12]
                self._write(f"  failed: {progress.reason}")
                self._write(f"see '/replay {eid}' for details.")
            elif progress.phase is phase.RUN_STOPPED:
                eid = progress.execution_id[:12]
                where = f" during '{progress.step_name}'" if progress.step_name else ""
                self._write(f"stopped{where}. execution {eid} can be resumed later.")
            elif progress.phase is phase.RUN_PAUSED:
                eid = progress.execution_id[:12]
                self._write(
                    f"paused after '{progress.step_name}' (full-manual). "
                    f"'/resume' to advance, or '/replay {eid}' to inspect."
                )
            elif progress.phase is phase.RUN_BLOCKED:
                eid = progress.execution_id[:12]
                self._write(f"  blocked -- '{progress.step_name}' is asking ({progress.reason}):")
                for q in progress.questions:
                    self._write(f"    \u2022 {q['text']}")
                self._write(f"the run paused; execution {eid} is recorded and resumable.")
            # RUN_STARTED needs no line -- the launch message already announced it.

        return report

    def _build_shot_config(self):
        """Build the shot resolution config from the user's ``[tiers]`` mapping.

        Returns ``None`` when no tiers are configured -- a shot step then stops
        honestly (the engine never fakes a client/model). The cassette store is
        gmlcache's own (config-owned) concern; the engine dictates no location.
        """
        try:
            resolutions = config.load_tiers(self._config_file)
        except config.ConfigError as exc:
            self._write(f"  ! tiers config problem ({exc}); shots will stop until fixed.")
            return None
        if not resolutions:
            return None
        return orchestrator.ShotConfig(resolutions=resolutions)

    def _do_replay(self, args: list[str]) -> bool:
        store = self._open_store()
        if store is None:
            return True
        try:
            if not args:
                execs = store.executions()
                if not execs:
                    self._write("no executions recorded yet.")
                    self._write("run a workflow (slice 0.0.5) and its story will appear here.")
                    return True
                self._write("executions:")
                for e in execs:
                    job = f"  job={e['job_id']}" if e["job_id"] else ""
                    self._write(
                        f"  {e['execution_id'][:12]}  {e['workflow_name']} "
                        f"<{e['input_type']}>  [{e['status']}]{job}"
                    )
                self._write("replay one with '/replay <execution>'.")
                return True
            target = args[0]
            row = store.execution(target) or self._find_execution_prefix(store, target)
            if row is None:
                self._write(f"no execution '{target}'. try '/replay' to list them.")
                return True
            self._render_replay(store, row)
            return True
        finally:
            store.close()

    def _find_execution_prefix(self, store, prefix: str):
        matches = [e for e in store.executions() if e["execution_id"].startswith(prefix)]
        return matches[0] if len(matches) == 1 else None

    def _render_replay(self, store, row) -> None:
        full = store.execution(row["execution_id"]) or row
        self._write(f"execution {full['execution_id']}")
        stamp = f"commit {full['commit']}" if full.get("commit") else "unversioned"
        self._write(
            f"  {full['workflow_name']} <{full['input_type']}>  [{full['status']}]  "
            f"({stamp}, engine {full.get('engine_version')})"
        )
        story = store.replay(full["execution_id"])
        self._write(f"  {len(story)} event(s):")
        for ev in story:
            scope = f" {ev.step_name}" if ev.step_name else ""
            self._write(f"    {ev.occurred_at}  {ev.event_type.value}{scope}")

    def _do_status(self, args: list[str]) -> bool:
        s = self.settings
        if s is None:
            self._write("settings not resolved (startup did not run).")
            return True
        cfg_path = self._config_file if self._config_file is not None else paths.config_path()
        if s.config_file is not None:
            self._write(f"config file: {s.config_file}")
        elif getattr(self, "_config_broken", False):
            self._write(f"config file: {cfg_path}  (present but BROKEN -- defaults in use)")
        else:
            self._write(f"config file: {cfg_path}  (absent -- interview at next launch)")
        rows = [
            ("flows", s.flows_dir, s.sources["flows_dir"]),
            ("state", s.state_dir, s.sources["state_dir"]),
            ("workspace", s.workspace_dir, s.sources["workspace_dir"]),
            ("banner", self._banner_style, s.sources["banner"]),
        ]
        for name, value, source in rows:
            self._write(f"  {name:<10} {str(value):<52} ({source})")
        db = s.state_dir / "gmlworkflow.db"
        present = "present" if db.exists() else "not created yet"
        self._write(f"  {'event log':<10} {str(db):<52} ({present})")
        return True

    def _startup_detection(self) -> None:
        self._write("asking gmlcache which clients are installed...")
        self.detection = self._discover()
        self._render_detection()
        self._reconcile_tiers(fetch_models=False)
        self._warn_if_gmlcache_outdated()

    def _warn_if_gmlcache_outdated(self) -> None:
        """Advisory: if gmlcache is older than the release whose behavior the engine
        relies on, say so once at startup. Never blocks; silent when the version
        cannot be read (the same 'cannot verify, don't guess' rule as detection)."""
        if self.detection is None or not self.detection.gmlcache_present:
            return
        version_line = self._discover_gmlcache_version()
        if version_line is None:
            return
        if detect.gmlcache_version_is_outdated(version_line):
            wanted_version = ".".join(str(part) for part in detect.MINIMUM_GMLCACHE_VERSION)
            self._write(
                f"  ! {version_line} is older than gmlcache {wanted_version}, which this "
                f"engine relies on -- update gmlcache to avoid surprises (advisory, not blocking)."
            )
            self._write("")

    def _render_detection(self) -> None:
        d = self.detection
        if d is None:
            return
        if not d.gmlcache_present:
            # Reaching the workspace means the launch wrapper already verified
            # gmlcache (see core.deps). This branch is a defensive fallback only
            # (e.g. the workspace constructed directly in a test).
            self._write("  ! gmlcache is unexpectedly unavailable -- detection skipped.")
            self._write(f"    ({d.gmlcache_detail})")
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
        self._write("running workflows is still ahead on the roadmap; '/help' lists the verbs,")
        self._write("and each stub says which slice brings it to life.")
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

    def _reconcile_tiers(self, *, fetch_models: bool) -> None:
        """Advisory: do the configured tiers point at clients/models gmlcache can
        actually see? Free at startup (client presence only, from the doctor data
        already in hand); ``/tiers`` adds the model-drift check by fetching the
        model listings. Nothing here gates anything -- the run stays the truth.
        """
        if self.detection is None or not self.detection.gmlcache_present:
            if fetch_models:
                self._write("gmlcache is unavailable -- cannot check tiers right now.")
            return
        try:
            tiers = config.load_tiers(self._config_file)
        except config.ConfigError as exc:
            self._write(f"  ! [tiers] config problem: {exc}")
            return
        if not tiers:
            if fetch_models:
                self._write("no [tiers] configured yet -- map each tier to an installed")
                self._write("client/model in the config file (see the commented template).")
            return

        listings = self._discover_models() if fetch_models else None
        issues = reconcile.reconcile(tiers, self.detection.clients, listings)

        if fetch_models:
            for tier in contract.Tier:
                res = tiers.get(tier)
                if res is None:
                    continue
                eff = f"  effort={res.effort}" if res.effort else ""
                self._write(f"  {tier.value:<7} {res.client}/{res.model}{eff}")
            if listings is None:
                self._write("  (model drift unchecked -- gmlcache could not list models)")
            if not issues:
                self._write("  all configured tiers look reachable.")
                return
            for issue in issues:
                self._write(f"  ! {issue.message}")
            return

        # startup (free check): stay quiet unless something is actually wrong.
        if not issues:
            return
        self._write("tier check (advisory -- nothing is gated):")
        for issue in issues:
            self._write(f"  ! {issue.message}")
        self._write("")

    def _do_tiers(self, args: list[str]) -> bool:
        self._reconcile_tiers(fetch_models=True)
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
            self._session["banner"] = arg
            if self.settings is not None and self.settings.config_file is not None:
                config.set_value(self.settings.config_file, "banner", arg)
                self._write(f"banner -> {arg}  (saved to {self.settings.config_file.name})")
            else:
                self._write(f"banner -> {arg}  (for this session; no config file to save into yet)")
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
    def _refresh_workflow_names(self) -> None:
        """Cache the discoverable workflow names for /run and /validate tab-completion.
        A convenience only -- never fatal; loadable definitions only."""
        if self.settings is None:
            self._workflow_names = ()
            return
        discovered = discovery.discover_workflows(self.settings.flows_dir)
        self._workflow_names = tuple(
            definition.name
            for definition in discovered
            if definition.workflow is not None and definition.name
        )

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
        if head in ("run", "validate") and len(before) == 1:
            return [name for name in self._workflow_names if name.startswith(text)]
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

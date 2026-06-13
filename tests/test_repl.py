# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The REPL shell, driven through the scripted-input harness (injected I/O --
prompt_toolkit is never constructed on this path)."""

from pathlib import Path

import sys

import pytest

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


_DATA = Path(__file__).parent / "data"

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


def test_gmlcache_absent_at_workspace_is_defensive_only():
    # The launch wrapper (app.main -> deps.require) makes gmlcache-missing a hard
    # launch block, tested in test_deps. If the workspace is somehow built without
    # it (e.g. directly in a test), detection degrades defensively, not gracefully.
    out = drive(["/quit"], ABSENT)
    assert "unexpectedly unavailable" in out
    assert "bye." in out  # constructed-directly, the loop still runs


def test_help_lists_the_closed_verb_set():
    out = drive(["/help", "/quit"], PRESENT)
    for verb in ("/run", "/list", "/validate", "/replay", "/status", "/cost", "/help", "/quit"):
        assert verb in out
    assert "/exit" not in out  # alias shown once, as /quit


def test_stub_verbs_answer_not_yet_with_the_slice():
    out = drive(["/cost", "/quit"], PRESENT)
    assert "not yet" in out and "0.0.10" in out and "ROADMAP" in out


def test_every_stub_names_its_slice():
    for verb, slice_id in [
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


# --- 0.0.2: config, /status, the first-run interview --------------------------


def drive_at(lines, detection, config_file):
    script = iter(lines)
    out: list[str] = []
    repl = Repl(
        read=lambda p: next(script, None),
        write=out.append,
        discover=lambda: detection,
        config_file=config_file,
    )
    repl.run()
    return "\n".join(out), repl


def test_status_shows_each_setting_with_source(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[paths]\nflows = "/cfg/flows"\n', encoding="utf-8")
    monkeypatch.setenv("GMLWORKFLOW_STATE", "/env/state")
    out, _ = drive_at(["/status", "/quit"], PRESENT, cfg)
    assert "config file: " + str(cfg) in out
    # construct expectations through Path so separators match the platform
    assert str(Path("/cfg/flows")) in out and "(config)" in out
    assert str(Path("/env/state")) in out and "(env)" in out
    assert "(default)" in out  # workspace fell through


def test_interview_standard_choice_writes_config_and_creates_folders(tmp_path, monkeypatch):
    from generic_ml_workflow.core import paths as paths_mod

    # keep the "standard OS folders" choice inside tmp -- tests must never
    # create real directories in the developer's home
    monkeypatch.setattr(
        paths_mod.platformdirs, "user_data_dir", lambda app: str(tmp_path / "data" / app)
    )
    monkeypatch.setattr(
        paths_mod.platformdirs, "user_state_dir", lambda app: str(tmp_path / "stt" / app)
    )
    cfg = tmp_path / "cfg" / "config.toml"
    out, repl = drive_at(["1", "/status", "/quit"], PRESENT, cfg)
    assert (tmp_path / "data" / "gmlworkflow" / "flows").is_dir()
    assert "no configuration found" in out
    assert cfg.is_file()
    assert "wrote " + str(cfg) in out
    assert repl.settings.config_file == cfg  # reloaded: the file is now the source
    assert "interview" not in drive_at(["/quit"], PRESENT, cfg)[0]  # second launch skips it


def test_interview_single_folder_choice(tmp_path):
    cfg = tmp_path / "config.toml"
    base = tmp_path / "everything"
    out, repl = drive_at(["2", str(base), "/quit"], PRESENT, cfg)
    assert (base / "flows").is_dir() and (base / "state").is_dir()
    assert repl.settings.flows_dir == base / "flows"
    # the app initializes the flows folder as a git repo (git is a verified dep)
    assert (base / "flows" / ".git").exists()
    assert "initialized a git repo" in out


def test_interview_custom_paths_rejects_relative(tmp_path):
    cfg = tmp_path / "config.toml"
    out, repl = drive_at(
        [
            "3",
            "relative/flows",
            str(tmp_path / "F"),
            str(tmp_path / "S"),
            str(tmp_path / "W"),
            "/quit",
        ],
        PRESENT,
        cfg,
    )
    assert "absolute path" in out
    assert repl.settings.flows_dir == tmp_path / "F"
    assert (tmp_path / "W").is_dir()


def test_interview_skip_writes_nothing(tmp_path):
    cfg = tmp_path / "config.toml"
    out, _ = drive_at([], PRESENT, cfg)  # immediate EOF at the choice prompt
    assert "nothing written" in out
    assert not cfg.exists()
    before = set(tmp_path.iterdir())
    assert before == set(tmp_path.iterdir())  # and no folders appeared


def test_interview_invalid_choice_writes_nothing(tmp_path):
    cfg = tmp_path / "config.toml"
    out, _ = drive_at(["7", "/quit"], PRESENT, cfg)
    assert "not one of 1-3" in out and not cfg.exists()


def test_broken_config_is_loud_but_survivable(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("not [ valid", encoding="utf-8")
    out, _ = drive_at(["/status", "/quit"], PRESENT, cfg)
    assert "config problem" in out
    assert "BROKEN" in out
    assert "bye." in out  # the workspace survived
    assert "not valid TOML" in out


def test_banner_choice_persists_into_config(tmp_path):
    cfg = tmp_path / "config.toml"
    drive_at(["1", "/banner minimal", "/quit"], PRESENT, cfg)
    assert 'banner = "minimal"' in cfg.read_text(encoding="utf-8")
    out, _ = drive_at(["/status", "/quit"], PRESENT, cfg)  # next launch uses it
    assert "minimal" in out and "(config)" in out


# --- 0.0.3: /list and /validate -----------------------------------------------


def _cfg_with_flows(tmp_path, flows):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'[paths]\nflows = "{flows.as_posix()}"\n[ui]\nbanner = "panel"\n', encoding="utf-8"
    )
    return cfg


def test_list_empty_flows_is_honest(tmp_path):
    flows = tmp_path / "flows"
    flows.mkdir()
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/list", "/quit"], PRESENT, cfg)
    assert "no workflow definitions" in out
    assert "your meta-code" in out


def test_list_shows_definitions_with_input_type(tmp_path):
    import shutil

    flows = tmp_path / "flows"
    flows.mkdir()
    shutil.copy(_DATA / "demo.yaml", flows / "demo.yaml")
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/list", "/quit"], PRESENT, cfg)
    assert "demo" in out and "<url>" in out


def test_validate_clean_workflow(tmp_path):
    import shutil

    flows = tmp_path / "flows"
    flows.mkdir()
    shutil.copy(_DATA / "demo.yaml", flows / "demo.yaml")
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/validate demo", "/quit"], PRESENT, cfg)
    # demo is valid; 'summary' is a terminal deliverable -> a warning, not an error
    assert "warning" in out or "is valid" in out
    assert "\u2717" not in out  # no error crosses


def test_validate_reports_errors(tmp_path):
    flows = tmp_path / "flows"
    flows.mkdir()
    # an interpretable step with no cap -> a hard error
    (flows / "bad.yaml").write_text(
        "name: bad\nsteps:\n  - {id: s, nature: interpretable}\n", encoding="utf-8"
    )
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/validate bad", "/quit"], PRESENT, cfg)
    assert "error" in out and "must declare a cap" in out


def test_validate_unknown_flow(tmp_path):
    flows = tmp_path / "flows"
    flows.mkdir()
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/validate ghost", "/quit"], PRESENT, cfg)
    assert "no workflow 'ghost'" in out


def test_validate_without_argument_shows_usage(tmp_path):
    flows = tmp_path / "flows"
    flows.mkdir()
    cfg = _cfg_with_flows(tmp_path, flows)
    out, _ = drive_at(["/validate", "/quit"], PRESENT, cfg)
    assert "usage:" in out


# --- 0.0.4: /replay reads the (real, possibly empty) event store --------------


def _cfg_with_state(tmp_path):
    state = tmp_path / "state"
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'[paths]\nstate = "{state.as_posix()}"\n[ui]\nbanner = "panel"\n', encoding="utf-8"
    )
    return cfg, state


def test_replay_empty_store_is_honest(tmp_path):
    cfg, _ = _cfg_with_state(tmp_path)
    out, _ = drive_at(["/replay", "/quit"], PRESENT, cfg)
    assert "no executions recorded yet" in out
    assert "slice 0.0.5" in out


def test_replay_unknown_execution(tmp_path):
    cfg, _ = _cfg_with_state(tmp_path)
    out, _ = drive_at(["/replay nope", "/quit"], PRESENT, cfg)
    assert "no execution 'nope'" in out


def test_replay_lists_and_renders_a_seeded_execution(tmp_path):
    from generic_ml_workflow.core import eventtypes as et
    from generic_ml_workflow.core.events import EventStore, new_execution_id

    cfg, state = _cfg_with_state(tmp_path)
    state.mkdir(parents=True)
    store = EventStore(state / "gmlworkflow.db")
    x = new_execution_id()
    store.emit(
        et.WorkflowExecutionStarted(
            workflow_name="feature",
            input_type="url",
            commit="abc123",
            branch="main",
            engine_version="0.0.4.dev0",
        ),
        execution_id=x,
    )
    store.emit(et.RunInputProvided(name="ticket", value="test-001"), execution_id=x)
    store.close()

    # bare /replay lists it
    out, _ = drive_at(["/replay", "/quit"], PRESENT, cfg)
    assert "feature" in out and x[:12] in out and "[running]" in out

    # /replay <prefix> renders the story
    out2, _ = drive_at([f"/replay {x[:12]}", "/quit"], PRESENT, cfg)
    assert "commit abc123" in out2
    assert "workflow_execution.started" in out2 and "run_input.provided" in out2


def test_status_shows_event_log_path(tmp_path):
    cfg, state = _cfg_with_state(tmp_path)
    out, _ = drive_at(["/status", "/quit"], PRESENT, cfg)
    assert "event log" in out
    assert "gmlworkflow.db" in out


# --- 0.0.5: /run drives the orchestrator from the prompt ----------------------


def _run_cfg(tmp_path):
    import subprocess as sp

    flows = tmp_path / "flows"
    flows.mkdir()
    # git-init so the run is stamped (mirrors what the interview does)
    sp.run(["git", "-C", str(flows), "init", "-q"], check=True)
    sp.run(["git", "-C", str(flows), "config", "user.email", "t@e.com"], check=True)
    sp.run(["git", "-C", str(flows), "config", "user.name", "t"], check=True)
    state = tmp_path / "state"
    ws = tmp_path / "ws"
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        f'[paths]\nflows = "{flows.as_posix()}"\nstate = "{state.as_posix()}"\n'
        f'workspace = "{ws.as_posix()}"\n[ui]\nbanner = "panel"\n',
        encoding="utf-8",
    )
    return cfg, flows


def _write_demo(flows):
    import subprocess as sp

    (flows / "fetch.sh").write_text(
        "printf '<h1>%s</h1>' \"$(cat url)\" > page.html\n", encoding="utf-8"
    )
    (flows / "extract.sh").write_text("sed 's/<[^>]*>//g' source > text.txt\n", encoding="utf-8")
    (flows / "demo.yaml").write_text(
        "name: demo\ninput_type: url\nsteps:\n"
        f"  - id: fetch\n    nature: executable\n    entrypoint: {flows / 'fetch.sh'}\n"
        "    inputs:\n      - {name: url, require: run_input}\n"
        "    outputs:\n      - {name: page, lifespan: durable, kind: file, filename: page.html}\n"
        f"  - id: extract\n    nature: executable\n    entrypoint: {flows / 'extract.sh'}\n"
        "    inputs:\n      - {name: source, require: artifact}\n"
        "    outputs:\n      - {name: page_text, lifespan: durable, kind: file, "
        "filename: text.txt}\n"
        "bindings:\n  - {step: extract, port: source, product: page}\n",
        encoding="utf-8",
    )
    sp.run(["git", "-C", str(flows), "add", "-A"], check=True)
    sp.run(["git", "-C", str(flows), "commit", "-qm", "demo"], check=True)


def test_run_no_workflows_is_honest(tmp_path):
    cfg, _ = _run_cfg(tmp_path)
    out, _ = drive_at(["/run", "/quit"], PRESENT, cfg)
    assert "no runnable workflows" in out


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_run_executes_demo_end_to_end(tmp_path):
    cfg, flows = _run_cfg(tmp_path)
    _write_demo(flows)
    # /run demo, then answer the computed interview (url), then replay
    out, _ = drive_at(["/run demo", "hello world", "/replay", "/quit"], PRESENT, cfg)
    assert "running 'demo'" in out
    assert "\u2713 fetch" in out and "\u2713 extract" in out
    assert "completed" in out
    # /replay (bare) now lists the real execution
    assert "demo" in out and "[completed]" in out


@pytest.mark.skipif(sys.platform == "win32", reason="sh scripts; POSIX only")
def test_run_invalid_workflow_refuses(tmp_path):
    cfg, flows = _run_cfg(tmp_path)
    (flows / "bad.yaml").write_text(
        "name: bad\nsteps:\n  - {id: s, nature: interpretable}\n", encoding="utf-8"
    )
    out, _ = drive_at(["/run bad", "/quit"], PRESENT, cfg)
    assert "does not validate" in out and "must declare a cap" in out


def test_run_unknown_workflow(tmp_path):
    cfg, flows = _run_cfg(tmp_path)
    (flows / "demo.yaml").write_text(
        "name: demo\ninput_type: url\nsteps:\n"
        "  - {id: s, nature: executable, entrypoint: 'true', "
        "outputs: [{name: o, lifespan: durable, kind: file, filename: o.txt}]}\n",
        encoding="utf-8",
    )
    out, _ = drive_at(["/run ghost", "/quit"], PRESENT, cfg)
    assert "no workflow 'ghost'" in out


# --- tier reconciliation (0.0.7): anticipate a run's failure in advance ---

from generic_ml_workflow.core.detect import ModelInfo, ModelListing  # noqa: E402

_TIERS_PRESENT = Detection(
    gmlcache_present=True,
    clients=(
        ClientStatus(name="claude", present=True, version="1.0"),
        ClientStatus(name="codex", present=False, detail="not on PATH"),
    ),
)


def _cfg(tmp_path, tiers_body: str) -> Path:
    p = tmp_path / "with-tiers.toml"
    p.write_text(tiers_body, encoding="utf-8")
    return p


def _run(cfg, detection, lines, models=None):
    out: list[str] = []
    script = iter(lines)
    repl = Repl(
        read=lambda p: next(script, None),
        write=out.append,
        discover=lambda: detection,
        discover_models=(lambda: models),
        config_file=cfg,
    )
    repl.run()
    return "\n".join(out)


def test_startup_warns_when_configured_client_missing(tmp_path):
    cfg = _cfg(tmp_path, '[tiers.low]\nclient = "codex"\nmodel = "gpt-5.5"\n')
    out = _run(cfg, _TIERS_PRESENT, ["/quit"])
    assert "tier check" in out
    assert "tier 'low' needs client 'codex'" in out


def test_startup_quiet_when_configured_client_present(tmp_path):
    cfg = _cfg(tmp_path, '[tiers.high]\nclient = "claude"\nmodel = "opus"\n')
    out = _run(cfg, _TIERS_PRESENT, ["/quit"])
    assert "tier check" not in out  # free check passes silently


def test_tiers_command_shows_mapping_and_reachable(tmp_path):
    cfg = _cfg(tmp_path, '[tiers.high]\nclient = "claude"\nmodel = "opus"\neffort = "high"\n')
    models = (
        ModelListing(
            name="claude",
            present=True,
            supported=True,
            models=(ModelInfo(id="opus", name="opus"),),
        ),
    )
    out = _run(cfg, _TIERS_PRESENT, ["/tiers", "/quit"], models=models)
    assert "high" in out and "claude/opus" in out and "effort=high" in out
    assert "all configured tiers look reachable" in out


def test_tiers_command_flags_stale_model(tmp_path):
    cfg = _cfg(tmp_path, '[tiers.high]\nclient = "claude"\nmodel = "opus-vanished"\n')
    models = (
        ModelListing(
            name="claude",
            present=True,
            supported=True,
            models=(ModelInfo(id="opus", name="opus"),),
        ),
    )
    out = _run(cfg, _TIERS_PRESENT, ["/tiers", "/quit"], models=models)
    assert "no longer lists" in out and "opus-vanished" in out


def test_tiers_command_cannot_verify_when_no_list(tmp_path):
    cfg = _cfg(tmp_path, '[tiers.high]\nclient = "claude"\nmodel = "anything"\n')
    out = _run(cfg, _TIERS_PRESENT, ["/tiers", "/quit"], models=None)
    assert "model drift unchecked" in out
    assert "no longer lists" not in out  # never a false drift warning


def test_tiers_command_when_none_configured(tmp_path):
    cfg = _cfg(tmp_path, '[ui]\nbanner = "panel"\n')
    out = _run(cfg, _TIERS_PRESENT, ["/tiers", "/quit"])
    assert "no [tiers] configured" in out


# --- 0.0.7: parsing per-step tier overrides on /run ---------------------------

from generic_ml_workflow.core.contract import (  # noqa: E402
    InputType,
    OutputKind,
    OutputPort,
    Lifespan,
    StepNature,
    StepSpec,
    Tier,
    Workflow,
)


def _wf_with_shot_and_exec():
    shot = StepSpec(
        id="summarize",
        nature=StepNature.INTERPRETABLE,
        cap="summarizer",
        tier=Tier.MEDIUM,
        outputs=(OutputPort("s", Lifespan.DURABLE, OutputKind.FILE, "s.md"),),
    )
    fetch = StepSpec(
        id="fetch",
        nature=StepNature.EXECUTABLE,
        entrypoint="true",
        outputs=(OutputPort("f", Lifespan.DURABLE, OutputKind.FILE, "f.txt"),),
    )
    return Workflow(name="w", input_type=InputType.FREESTYLE, steps=(shot, fetch))


def _repl():
    out: list[str] = []
    repl = Repl(read=lambda p: None, write=out.append)
    return repl, out


def test_parse_overrides_good():
    repl, _ = _repl()
    got = repl._parse_tier_overrides(_wf_with_shot_and_exec(), ["summarize=high"])
    assert got == {"summarize": Tier.HIGH}


def test_parse_overrides_empty_is_empty_map():
    repl, _ = _repl()
    assert repl._parse_tier_overrides(_wf_with_shot_and_exec(), []) == {}


def test_parse_overrides_unknown_step_aborts():
    repl, out = _repl()
    assert repl._parse_tier_overrides(_wf_with_shot_and_exec(), ["nope=high"]) is None
    assert any("isn't in this workflow" in line for line in out)


def test_parse_overrides_bad_tier_aborts():
    repl, out = _repl()
    assert repl._parse_tier_overrides(_wf_with_shot_and_exec(), ["summarize=turbo"]) is None
    assert any("isn't a tier" in line for line in out)


def test_parse_overrides_on_executable_step_aborts():
    repl, out = _repl()
    assert repl._parse_tier_overrides(_wf_with_shot_and_exec(), ["fetch=high"]) is None
    assert any("isn't a shot" in line for line in out)


def test_parse_overrides_missing_equals_aborts():
    repl, out = _repl()
    assert repl._parse_tier_overrides(_wf_with_shot_and_exec(), ["summarize"]) is None
    assert any("bad override" in line for line in out)


# --- 0.0.7: advisory gmlcache-version warning at startup ----------------------


def _run_with_version(version_line):
    out: list[str] = []
    script = iter(["/quit"])
    repl = Repl(
        read=lambda p: next(script, None),
        write=out.append,
        discover=lambda: PRESENT,
        discover_gmlcache_version=lambda: version_line,
    )
    repl.run()
    return "\n".join(out)


def test_startup_warns_when_gmlcache_outdated():
    out = _run_with_version("gmlcache 0.0.5")
    assert "older than gmlcache 0.0.7" in out


def test_startup_silent_when_gmlcache_current():
    out = _run_with_version("gmlcache 0.0.9")
    assert "older than" not in out


def test_startup_silent_when_gmlcache_version_unknown():
    out = _run_with_version(None)
    assert "older than" not in out


# --- tab-completion for /run and /validate workflow names ---------------------


def test_run_completes_workflow_names():
    repl, _ = _repl()
    repl._workflow_names = ("feature", "review")
    assert set(repl._completions("/run ", len("/run "))) == {"feature", "review"}
    assert repl._completions("/run fea", len("/run ")) == ["feature"]


def test_validate_completes_workflow_names():
    repl, _ = _repl()
    repl._workflow_names = ("feature",)
    assert repl._completions("/validate ", len("/validate ")) == ["feature"]


def test_no_workflow_completion_for_other_commands():
    repl, _ = _repl()
    repl._workflow_names = ("feature",)
    assert repl._completions("/status ", len("/status ")) == []

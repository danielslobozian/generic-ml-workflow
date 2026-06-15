# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""The clean-stop primitive: a stop flag the engine checks, and a supervised child
that a stop tears down mid-flight. POSIX process-group semantics for the kill tests."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from generic_ml_workflow.core.stopping import StopControl, run_supervised


def test_stop_control_flag_is_off_until_requested():
    stop = StopControl()
    assert not stop.requested()
    stop.request()
    assert stop.requested()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group teardown")
def test_run_supervised_returns_a_completed_process(tmp_path):
    done = run_supervised([sys.executable, "-c", "print('hi')"], cwd=tmp_path, timeout=10)
    assert done.returncode == 0
    assert "hi" in done.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group teardown")
def test_request_tears_down_a_watched_child(tmp_path):
    stop = StopControl()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    with stop.watching(child):
        stop.request()  # sets the flag AND kills the registered child
        child.wait(timeout=10)
    assert child.poll() is not None  # torn down, not left running for 30s


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group teardown")
def test_run_supervised_is_cut_short_by_a_stop(tmp_path):
    stop = StopControl()

    def stop_soon() -> None:
        time.sleep(0.5)
        stop.request()

    watcher = threading.Thread(target=stop_soon)
    watcher.start()
    start = time.monotonic()
    done = run_supervised(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout=60,
        stop=stop,
    )
    elapsed = time.monotonic() - start
    watcher.join()
    assert elapsed < 25  # the 30s child was killed, not waited out
    assert done.returncode != 0  # ended by teardown, not a clean exit

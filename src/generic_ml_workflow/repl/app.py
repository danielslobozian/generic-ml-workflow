# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""app.py -- the front door.

``gmlworkflow`` takes no arguments: launch lands you in the REPL workspace, which
is the product (design invariant 1). There is no usage model in which a human
types a workflow name and flags on a shell command line. The single exception is
``--version``, a courtesy to packaging tools and humans checking an install --
informational, not operational.
"""

from __future__ import annotations

import sys

from generic_ml_workflow import __version__
from generic_ml_workflow.repl.shell import Repl


def main() -> None:
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--version", "-V"):
            print(f"gmlworkflow {__version__}")
            return
        print("gmlworkflow takes no arguments: launching it IS the interface.")
        print("run 'gmlworkflow' and you are home; '/help' lists the verbs.")
        raise SystemExit(2)
    Repl().run()


if __name__ == "__main__":
    main()

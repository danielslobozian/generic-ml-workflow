# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""envelope.py -- the shot request envelope and its purity rule (DESIGN.md SS8).

An interpretable step (a shot) is compiled into one ordered structure::

    [ context , prompt , files ]

  * **context** -- the stable, compressed, run-agnostic prefix. No dates, no job
    identifiers, no session ids, no absolute paths. The high-cache-hit part.
  * **prompt** -- the instruction for this step.
  * **files** -- the work-input content (the subject of the work).

**Purity is load-bearing twice** (SS8): it maximizes the client's own prefix cache,
and it is what makes gmlcache cassette keys stable -- a request that is a pure
function of its declared inputs hits the cassette forever. So purity is **enforced
in the builder, not hoped for**: this module refuses run-specific material in the
context block (timestamps, ids, absolute paths) by raising rather than letting an
impure prefix shatter every downstream cassette key.

This module only *builds and checks* the envelope; it does not call anything. The
seam that hands it to gmlcache lives in ``shotrunner``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class PurityError(Exception):
    """The context block carries run-specific material -- it would break cache key
    stability and the client's prefix cache. Fix the builder/inputs, do not run."""


@dataclass(frozen=True)
class Envelope:
    """A shot's request: the run-agnostic context prefix, the step instruction,
    and the work-input file paths (the files are passed to gmlcache as input
    files; only their content is keyed, never their names)."""

    context: str
    prompt: str
    files: tuple[str, ...] = ()


# Heuristic markers of run-specific material that must never enter the context
# prefix. These are conservative: they catch the obvious leaks (an ISO timestamp,
# an absolute path, an explicit id) that would change run to run.
_ISO_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_ABS_PATH = re.compile(r"(?:^|\s)(?:/[\w.\-]+){2,}", re.MULTILINE)  # /a/b... unix abs path
_WIN_ABS_PATH = re.compile(r"[A-Za-z]:\\[\\\w.\-]+")  # C:\a\b
_EXEC_ID = re.compile(r"\bexecution[_-]?id\b\s*[:=]", re.IGNORECASE)


def check_purity(context: str) -> None:
    """Raise PurityError if the context block carries run-specific material.

    Conservative by design: better to flag a real leak than to let an impure
    prefix silently break every cassette key downstream."""
    leaks: list[str] = []
    if _ISO_TIMESTAMP.search(context):
        leaks.append("a timestamp (dates/times change run to run)")
    if _ABS_PATH.search(context) or _WIN_ABS_PATH.search(context):
        leaks.append("an absolute path (machine- and run-specific)")
    if _EXEC_ID.search(context):
        leaks.append("an execution/session id")
    if leaks:
        raise PurityError(
            "the context prefix must be run-agnostic (SS8) but contains:\n  "
            + "\n  ".join(leaks)
            + "\nMove run-specific material into the prompt or files, never the context."
        )


def build_envelope(context: str, prompt: str, files: tuple[str, ...] = ()) -> Envelope:
    """Assemble the envelope, enforcing context purity. The prompt and files may
    legitimately carry run-specific material; the *context* may not."""
    check_purity(context)
    return Envelope(context=context, prompt=prompt, files=tuple(files))

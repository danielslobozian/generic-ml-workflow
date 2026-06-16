# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""builtin_bodies.py -- step bodies the engine ships itself, selected by an
entrypoint of the form ``builtin:<name>`` (DESIGN.md §10).

The only body today is ``fetch``: it reads from a provider instance without the user
writing a script and **without the token ever entering step code** -- the engine
holds the token in-process, makes the call, and writes only the response data out.
The request host is **pinned** to the instance's ``base_url``: a fetch path can name
a resource under that base, never a different host and never a climb above the base
path. That pin is what keeps a mistaken or hostile path from turning a configured
credential into a request to somewhere else.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit, urlunsplit


class BuiltinError(Exception):
    """A built-in body refused or failed -- e.g. a path that escapes the host."""


def is_builtin(entrypoint: str) -> bool:
    return entrypoint.startswith("builtin:")


def builtin_name(entrypoint: str) -> str:
    return entrypoint.split(":", 1)[1]


def pin_url(base_url: str, path: str) -> str:
    """Resolve ``path`` against ``base_url`` and refuse anything that leaves the base.

    The result must keep the base's scheme and host and stay at or below the base
    path. An absolute path (one carrying its own scheme/host) or a ``..`` climb above
    the base raises :class:`BuiltinError`. This is the host-pin: the configured
    credential can only ever be sent to the configured host."""
    base = urlsplit(base_url)
    if base.scheme not in ("http", "https") or not base.netloc:
        raise BuiltinError(f"provider base_url must be an absolute http(s) URL, got {base_url!r}")
    given = urlsplit(path)
    if given.scheme or given.netloc:
        raise BuiltinError(f"fetch path must be relative to base_url, got {path!r}")

    base_for_join = base_url if base_url.endswith("/") else base_url + "/"
    final = urlsplit(urljoin(base_for_join, path.lstrip("/")))
    if (final.scheme, final.netloc) != (base.scheme, base.netloc):
        raise BuiltinError(f"fetch path escapes the provider host: {path!r}")
    base_path = base.path if base.path.endswith("/") else base.path + "/"
    if not (final.path + "/").startswith(base_path):
        raise BuiltinError(f"fetch path climbs above the provider base path: {path!r}")
    return urlunsplit(final)


def _http_get(url: str, token: str | None) -> bytes:
    """Perform the GET. Isolated so tests can substitute it without a live network."""
    request = urllib.request.Request(url)  # noqa: S310 -- scheme pinned to http(s) by pin_url
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read()


def run_fetch(path_value: str, instance: dict[str, object]) -> bytes:
    """Fetch ``path_value`` from the provider ``instance`` (its ``base_url`` and
    ``token``), host-pinned. Returns the response body bytes. Raises
    :class:`BuiltinError` on a bad instance, an escaping path, or a transport error."""
    base_url = instance.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise BuiltinError("the provider instance has no 'base_url' to fetch from")
    token = instance.get("token")
    url = pin_url(base_url, str(path_value))
    try:
        return _http_get(url, str(token) if token else None)
    except urllib.error.URLError as exc:
        raise BuiltinError(f"fetch failed: {exc}") from exc

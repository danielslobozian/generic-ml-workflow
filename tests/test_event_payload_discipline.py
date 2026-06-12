# SPDX-FileCopyrightText: 2026 Daniel Slobozian
# SPDX-License-Identifier: Apache-2.0
"""Invariant 11 guard: event payloads hold pointers, never blobs. No payload bean
may declare a field that smells like inlined content (a body/blob/text-content).
File products enter as a path + sha, never their bytes."""

import dataclasses

from generic_ml_workflow.core import eventtypes as et

# field names that would mean "the content itself lives in the event" -- forbidden.
_BLOB_FIELD_NAMES = {"content", "body", "blob", "bytes", "data", "file_content", "text_content"}


def test_no_payload_bean_inlines_content():
    offenders = []
    for t in et.EventType:
        bean = et.bean_for(t)
        for f in dataclasses.fields(bean):
            if f.name in _BLOB_FIELD_NAMES:
                offenders.append(f"{bean.__name__}.{f.name}")
    assert not offenders, (
        "event payloads must hold pointers (path + sha), never inlined content:\n  "
        + "\n  ".join(offenders)
    )


def test_artifact_payload_is_a_pointer():
    fields = {f.name for f in dataclasses.fields(et.ArtifactCreated)}
    assert "path" in fields and "sha256" in fields
    assert not (fields & _BLOB_FIELD_NAMES)  # never the content

# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Every shared ``ReportDocument`` field reaches every format meant to show it.

The oracle is the document the envelope builds, read through ``to_mapping``,
never a renderer's own helper. A (format, field) pair the document carries but
a format does not yet show is a *strict* xfail naming the open question in the
PR body: showing it needs a schema or output decision this refactor must not
make on its own. Strict means the day a format starts showing the field the
test XPASSes and fails, so the mark is removed in the same change rather than
left as a stale allowance.
"""

from __future__ import annotations

from functools import cache
from typing import Any

import pytest

from abicheck.checker_policy import Verdict
from abicheck.checker_types import DiffResult
from abicheck.frontends.render import render_output
from abicheck.model import AbiSnapshot
from abicheck.report.document_builder import build_report_envelope
from abicheck.report.envelope import RenderOptions

_SNAP = AbiSnapshot(library="libfoo.so.1", version="1.0")

FORMATS = ("json", "sarif", "junit", "markdown", "html")

# Each shared field, read from the document. Values are chosen to be distinctive
# in every format's output, so a substring hit means the field is shown.
SHARED_FIELDS = {
    "library": lambda doc: doc["library"],
    "verdict": lambda doc: doc["verdict"],
    "used_by_app": lambda doc: doc["used_by"][0]["app"],
}

# (format, field) pairs the document carries but the format does not show yet.
KNOWN_MISSING = {
    ("junit", "used_by_app"): (
        "JUnit shows the consumer app count, not its path; a path needs a "
        "schema decision (PR body, open question 1)"
    ),
    ("markdown", "used_by_app"): (
        "Markdown has no consumer section; adding one changes rendered output "
        "(PR body, open question 1)"
    ),
    ("html", "used_by_app"): (
        "HTML's scoped card omits the consumer app; adding it changes rendered "
        "output (PR body, open question 1)"
    ),
}


def _scoped_result() -> DiffResult:
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so.1",
        changes=[],
        verdict=Verdict.BREAKING,
    )
    scoped = {
        "scoped_verdict": Verdict.COMPATIBLE,
        "gate_scope": "used_by",
        "used_by": [{"app": "/bin/myapp"}],
        "scoped_exit_code": 0,
    }
    for name, value in scoped.items():
        setattr(result, name, value)
    return result


@cache
def _document() -> dict[str, object]:
    result = _scoped_result()
    envelope = build_report_envelope(result, _SNAP, _SNAP, options=RenderOptions())
    return envelope.document.to_mapping()


@cache
def _rendered(fmt: str) -> str:
    return render_output(fmt, _scoped_result(), _SNAP, _SNAP)


def _params() -> list[Any]:
    params: list[Any] = []
    for fmt in FORMATS:
        for field in SHARED_FIELDS:
            reason = KNOWN_MISSING.get((fmt, field))
            if reason is None:
                params.append(pytest.param(fmt, field, id=f"{fmt}-{field}"))
            else:
                params.append(
                    pytest.param(
                        fmt,
                        field,
                        id=f"{fmt}-{field}",
                        marks=pytest.mark.xfail(strict=True, reason=reason),
                    )
                )
    return params


@pytest.mark.parametrize(("fmt", "field"), _params())
def test_format_shows_shared_document_field(fmt: str, field: str) -> None:
    value = SHARED_FIELDS[field](_document())
    assert isinstance(value, str) and value
    assert value in _rendered(fmt)

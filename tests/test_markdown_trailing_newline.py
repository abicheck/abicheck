# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""New defect 5: every Markdown report format must end with a trailing
newline (POSIX text-file convention). ``render_markdown_document.
render_markdown_document`` (``--format markdown``, full mode) and
``render_markdown_alternate``'s ``render_leaf_document``/
``render_root_cause_document`` (``--report-mode leaf``/``root-cause``) each
used a bare ``"\\n".join(lines)`` with no trailing newline appended --
``render_markdown.render_review_digest`` (the review-digest format) already
got this right via ``.rstrip() + "\\n"``.

This is a general invariant over the Markdown format, not one pinned
fixture -- exercised here across several representative comparisons (no
changes, additions, breaking, risk) and every report mode, rather than
relying on the golden-file byte comparison alone to notice a regression.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Visibility,
)
from abicheck.report.dispatch_markdown import to_review_digest
from abicheck.reporter import to_markdown


def _fn(name, mangled, ret="void", params=()):
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _no_change_pair():
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[_fn("api", "_Z3apiv")],
    )
    return snap, snap


def _addition_pair():
    old = AbiSnapshot(
        library="libfoo.so", version="1.0", functions=[_fn("api", "_Z3apiv")]
    )
    new = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        functions=[_fn("api", "_Z3apiv"), _fn("api2", "_Z4api2v")],
    )
    return old, new


def _breaking_pair():
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[_fn("api", "_Z3apiv"), _fn("removed", "_Z7removedv")],
    )
    new = AbiSnapshot(
        library="libfoo.so", version="2.0", functions=[_fn("api", "_Z3apiv")]
    )
    return old, new


def _risk_pair():
    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        types=[RecordType(name="S", kind="struct", size_bits=64)],
    )
    new = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        types=[RecordType(name="S", kind="struct", size_bits=128)],
    )
    return old, new


_PAIRS = {
    "no_change": _no_change_pair,
    "addition": _addition_pair,
    "breaking": _breaking_pair,
    "risk": _risk_pair,
}


@pytest.mark.parametrize("pair_name", sorted(_PAIRS))
# ``leaf`` left this sweep with plan slice 7o; ``impact`` keeps it over
# every supported mode rather than shrinking it to two.
@pytest.mark.parametrize("report_mode", ["full", "impact", "root-cause"])
def test_markdown_report_ends_with_trailing_newline(pair_name, report_mode):
    old, new = _PAIRS[pair_name]()
    result = compare(old, new)
    text = to_markdown(result, report_mode=report_mode)
    assert text.endswith("\n"), (pair_name, report_mode, repr(text[-40:]))
    # Not just any trailing whitespace run collapsed to nothing -- exactly
    # one trailing newline, no trailing blank line(s) left over from the
    # `.rstrip() + "\n"` normalization.
    assert not text.endswith("\n\n"), (pair_name, report_mode, repr(text[-40:]))


@pytest.mark.parametrize("pair_name", sorted(_PAIRS))
def test_review_digest_ends_with_trailing_newline(pair_name):
    old, new = _PAIRS[pair_name]()
    result = compare(old, new)
    text = to_review_digest(result)
    assert text.endswith("\n"), (pair_name, repr(text[-40:]))
    assert not text.endswith("\n\n"), (pair_name, repr(text[-40:]))


@pytest.mark.parametrize("show_impact", [False, True])
@pytest.mark.parametrize("demangle", [False, True])
def test_markdown_trailing_newline_survives_options(show_impact, demangle):
    old, new = _breaking_pair()
    result = compare(old, new)
    text = to_markdown(result, show_impact=show_impact, demangle=demangle)
    assert text.endswith("\n"), repr(text[-40:])

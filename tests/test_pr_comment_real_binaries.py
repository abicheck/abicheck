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

"""The PR comment against *real* compiled artifacts, not hand-written JSON.

ADR-072 D1: the comment is a projection of a completed comparison. A
hand-built report dict can only prove the projection is faithful to a shape
the test itself chose -- it cannot catch a field the real producer spells
differently, emits conditionally, or does not emit at all. Both defects this
file guards against were found that way and not by the 151 unit tests that
already existed:

* an added enumerator's ``new_value`` reaches the comment only when the
  comparison actually had header evidence, and
* a real ELF comparison's ``coverage_warnings`` is eight routine
  detector-disablement notes plus, sometimes, one genuine limitation --
  a shape no hand-written fixture had.

Marked ``integration``: needs a working C toolchain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.pr_comment import build_model, should_post
from abicheck.pr_comment_render import render_comment

pytestmark = pytest.mark.integration

_OLD_H = """
#pragma once
enum Color { RED = 0, GREEN = 1, BLUE = 2 };
int foo_init(int x);
extern int foo_default_flags;
"""

_NEW_H = """
#pragma once
enum Color { RED = 0, GREEN = 1, BLUE = 2, PURPLE = 3 };
int foo_init(int x);
int foo_open(int x);
void foo_close(void);
extern int foo_default_flags;
extern int foo_extra;
"""

_OLD_C = """
#include "foo.h"
int foo_default_flags = 1;
int foo_init(int x) { return x; }
"""

_NEW_C = """
#include "foo.h"
int foo_default_flags = 1;
int foo_extra = 2;
int foo_init(int x) { return x; }
int foo_open(int x) { return x + 1; }
void foo_close(void) {}
"""


def _build(tmp_path: Path, name: str, header: str, source: str) -> tuple[Path, Path]:
    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler available")
    side = tmp_path / name
    side.mkdir()
    (side / "foo.h").write_text(header)
    (side / "foo.c").write_text(source)
    lib = side / f"lib{name}.so"
    subprocess.run(
        [cc, "-g", "-shared", "-fPIC", "-o", str(lib), "foo.c"],
        cwd=side,
        check=True,
    )
    return lib, side / "foo.h"


def _compare(tmp_path: Path, *args: str) -> dict:
    out = tmp_path / "report.json"
    proc = subprocess.run(
        # `sys.executable`, not "python": a valid environment does not
        # guarantee a command by that name exists, or that it resolves to
        # the interpreter running pytest (CodeRabbit review).
        [sys.executable, "-m", "abicheck", "compare", *args, "-o", f"json={out}"],
        capture_output=True,
        text=True,
    )
    assert out.exists(), proc.stderr[-2000:]
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def _libs(tmp_path_factory) -> tuple[Path, Path, Path, Path]:
    tmp = tmp_path_factory.mktemp("prcomment_real")
    old_lib, old_h = _build(tmp, "old", _OLD_H, _OLD_C)
    new_lib, new_h = _build(tmp, "new", _NEW_H, _NEW_C)
    return old_lib, old_h, new_lib, new_h


def test_real_binary_plus_public_headers_shows_the_enum_value(_libs, tmp_path) -> None:
    """Acceptance: two functions, one variable and one enumerator added --
    and the enumerator's own value 3 is visible in the comment."""
    old_lib, old_h, new_lib, new_h = _libs
    report = _compare(
        tmp_path,
        str(old_lib),
        str(new_lib),
        "--header",
        f"old={old_h}",
        "--header",
        f"new={new_h}",
    )
    model = build_model(report, path_prefix=str(tmp_path.parent))
    body = render_comment(model, sha="deadbee", detail="standard")

    assert "→ 3" in body, "the added enumerator's value must reach the comment"
    assert model.change_summary is not None
    rows = {r.entity: r for r in model.change_summary.rows}

    # The enumerator is the acceptance case, and its row is platform-stable:
    # an enum is an enum in every container format.
    assert rows["enum"].added == 1

    # Everything else is asserted as the rollup's own *contract* rather than
    # as an expected per-entity count, because the counts are not this
    # renderer's to predict. How many declarations a comparison reports, and
    # which entity each lands under, is a property of the container and
    # toolchain the runner has: an ELF `.dynsym` distinguishes a function
    # export from a data export, a PE export directory does not, and the
    # same three added declarations were observed as 2 functions + 1
    # variable on Linux and as 4 function/variable findings on macOS and
    # Windows alike.
    #
    # Two earlier revisions of this assertion pinned a count (first the
    # Linux 2+1 split, then the Linux total of 3) and each failed CI on a
    # correct result. Per this repository's own "attempted twice, reverted
    # twice" discipline, the third attempt is not a third count: the
    # invariant that actually matters here, and that holds on every
    # platform, is that the rollup counts every finding the report carried
    # exactly once -- nothing dropped, nothing invented. That is the whole
    # promise `report/change_summary.py` makes, and a wrong per-entity split
    # would still be caught by the entity-level unit tests, which own a
    # fixture whose classification they control.
    assert model.change_summary.counted == len(report["changes"])
    assert sum(r.total for r in model.change_summary.rows) == len(report["changes"])

    # Header evidence was available, so nothing claims otherwise.
    assert model.evidence is not None
    assert model.evidence.confidence == report["confidence"]
    assert model.evidence.coverage_warnings == ()


def test_real_binary_without_headers_keeps_its_limitation_visible(
    _libs, tmp_path
) -> None:
    """Acceptance: the same pair with no headers reports reduced confidence
    and says, in the comment, what that cost -- the exact pair of facts a
    production run carried in its JSON and no comment ever showed.

    Named for the *evidence* it withholds, not for a container format: this
    module's fixture builds with whichever compiler the runner has, so the
    artifact is ELF on Linux and PE on a Windows runner. The assertions
    below hold either way because they read the producer's own confidence
    and warnings rather than a platform-specific expectation.
    """
    old_lib, _old_h, new_lib, _new_h = _libs
    report = _compare(tmp_path, str(old_lib), str(new_lib))
    model = build_model(report)
    body = render_comment(model, sha="deadbee", detail="standard")

    assert model.evidence is not None
    assert model.evidence.confidence == report["confidence"] != "high"
    assert model.evidence.coverage_warnings, (
        "a headerless comparison records a material limitation; the routine "
        "detector-disablement notes must not have absorbed it"
    )
    assert f"Confidence: **{report['confidence']}**" in body
    for warning in model.evidence.coverage_warnings:
        assert warning in body
    assert should_post(model, "changes") is True


def test_same_binary_comparison_reports_no_changes(_libs, tmp_path) -> None:
    old_lib, _old_h, _new_lib, _new_h = _libs
    report = _compare(tmp_path, str(old_lib), str(old_lib))
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert "0 breaking" in render_comment(model, sha="deadbee")

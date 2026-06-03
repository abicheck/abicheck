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

"""Detector metamorphic + oracle properties grounded in **real compiled binaries**.

The fast-lane property tests (``test_detector_properties.py``) run on synthetic
``AbiSnapshot`` objects, which bypass the entire DWARF/ELF extraction frontend.
These tests close that gap: they compile real C shared libraries, dump them
through ``abicheck.dumper.dump`` (castxml + DWARF), and assert the same
invariants on snapshots that came out of the real pipeline.

Requires gcc + castxml; Linux-only (gcc emits ELF there). Marked ``integration``
so the default fast lane skips it.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.checker_policy import ChangeKind

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="ELF dump grounding requires Linux (gcc emits Mach-O/PE elsewhere)",
    ),
]


def _build(src: str, hdr: str, tmp: Path, stem: str):
    """Compile *src* to a .so and dump it (with *hdr*) into an AbiSnapshot."""
    from abicheck.dumper import dump

    if subprocess.run(["which", "castxml"], capture_output=True).returncode != 0:
        pytest.skip("castxml not on PATH")
    src_file = tmp / f"{stem}.c"
    src_file.write_text(textwrap.dedent(src).strip(), encoding="utf-8")
    hdr_file = tmp / f"{stem}.h"
    hdr_file.write_text(textwrap.dedent(hdr).strip(), encoding="utf-8")
    so = tmp / f"lib{stem}.so"
    r = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
         "-o", str(so), str(src_file)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        pytest.skip(f"gcc failed: {r.stderr[:200]}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return dump(so, headers=[hdr_file], version=stem, compiler="cc")


_HDR_V1 = """
    struct Widget { int a; };
    int widget_area(struct Widget *w);
    int helper_count(void);
"""
_SRC_V1 = """
    #include "v1.h"
    int widget_area(struct Widget *w) { return w->a; }
    int helper_count(void) { return 1; }
"""

# v2: grow the struct (layout/ABI break) but leave helper_count untouched.
_HDR_V2 = """
    struct Widget { int a; long b; };
    int widget_area(struct Widget *w);
    int helper_count(void);
"""
_SRC_V2 = """
    #include "v2.h"
    int widget_area(struct Widget *w) { return w->a + (int)w->b; }
    int helper_count(void) { return 1; }
"""


@pytest.mark.integration
def test_real_dump_self_compare_is_no_change(tmp_path: Path) -> None:
    """Idempotence on a snapshot produced by the real dumper."""
    snap = _build(_SRC_V1, _HDR_V1, tmp_path, "v1")
    result = compare(snap, snap)
    assert result.verdict == Verdict.NO_CHANGE
    assert result.changes == []


@pytest.mark.integration
def test_real_dump_struct_growth_is_breaking_and_symmetric(tmp_path: Path) -> None:
    """A real source-level struct growth is detected as a size change, is
    breaking, surfaces symmetrically, and does not flag the untouched helper."""
    v1 = _build(_SRC_V1, _HDR_V1, tmp_path, "v1")
    v2 = _build(_SRC_V2, _HDR_V2, tmp_path, "v2")

    fwd = compare(v1, v2, scope_to_public_surface=False)
    emitted = {c.kind for c in fwd.changes}

    assert ChangeKind.TYPE_SIZE_CHANGED in emitted, (
        f"expected TYPE_SIZE_CHANGED, got {sorted(k.name for k in emitted)}"
    )
    assert fwd.verdict in {Verdict.API_BREAK, Verdict.BREAKING}

    # The untouched helper_count must not be reported as changed.
    assert not any("helper_count" in (c.symbol or "") for c in fwd.changes)

    # Direction symmetry on real snapshots.
    rev = compare(v2, v1, scope_to_public_surface=False)
    assert {c.symbol for c in fwd.changes} == {c.symbol for c in rev.changes}


@pytest.mark.integration
def test_real_dump_compare_is_deterministic(tmp_path: Path) -> None:
    v1 = _build(_SRC_V1, _HDR_V1, tmp_path, "v1")
    v2 = _build(_SRC_V2, _HDR_V2, tmp_path, "v2")
    a = [c.kind for c in compare(v1, v2).changes]
    b = [c.kind for c in compare(v1, v2).changes]
    assert a == b

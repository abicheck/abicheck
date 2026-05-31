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

"""Parity: abicheck ``--scope-public-headers`` vs libabigail ``abidiff
--headers-dir1/2`` (ADR-024 §"Validation & testing strategy" §2).

Both tools restrict findings to the public-header ABI surface. These tests
assert the two agree on the headline verdict for the two clear-cut cases:

* a change to an **internal** type (defined outside the public headers and not
  reachable from any public API) is scoped out by both → compatible;
* a change to a **public-header** type reachable from a public API is in-surface
  for both → breaking.

Requires ``abidiff`` (libabigail), ``gcc``, and ``castxml``; marked
``libabigail`` so the default fast suite skips it. abidiff's header-scoping is
version-sensitive, so an abidiff *error* exit (bit 0) skips rather than fails —
we only assert parity when abidiff actually produced a verdict.
"""
from __future__ import annotations

import subprocess
import textwrap
import warnings
from pathlib import Path

import pytest

from tests._libabigail import decode_exit_code
from tests._libabigail import require_tool as _require_tool

pytestmark = pytest.mark.libabigail

_BIT_ERROR = 1


def _compile(src: str, out: Path, *, include: Path | None = None) -> None:
    src_file = out.with_suffix(".c")
    src_file.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    cmd = ["gcc", "-shared", "-fPIC", "-g", "-fvisibility=default",
           "-o", str(out), str(src_file)]
    if include is not None:
        cmd += ["-I", str(include)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"compile failed: {r.stderr[:200]}")


def _abidiff_headers(old: Path, new: Path, hdr_dir: Path) -> str:
    """abidiff scoped to *hdr_dir*; returns the verdict, or skips on tool error."""
    r = subprocess.run(
        ["abidiff", "--no-show-locs",
         "--headers-dir1", str(hdr_dir), "--headers-dir2", str(hdr_dir),
         str(old), str(new)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode & _BIT_ERROR:
        pytest.skip(f"abidiff errored (header-scoping unsupported?): {r.stderr[:200]}")
    return decode_exit_code(r.returncode, zero_verdict="NO_CHANGE")


def _abicheck_scoped(old: Path, new: Path, pub_header: Path) -> str:
    from abicheck.checker import compare
    from abicheck.dumper import dump

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old_snap = dump(old, headers=[pub_header], version="v1", compiler="cc", lang="C")
        new_snap = dump(new, headers=[pub_header], version="v2", compiler="cc", lang="C")
    result = compare(old_snap, new_snap, scope_to_public_surface=True)
    return result.verdict.value


def _is_breaking(verdict: str) -> bool:
    return "BREAK" in verdict.upper()


@pytest.mark.integration
def test_internal_type_change_scoped_out_by_both(tmp_path: Path) -> None:
    """An internal struct's layout change is scoped out by both tools."""
    _require_tool("abidiff")
    _require_tool("gcc")

    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "api.h").write_text("#ifndef API_H\n#define API_H\nint compute(int x);\n#endif\n")

    # struct Cache lives only in the .c (outside public headers) and is touched
    # solely by a static helper — neither tool should treat it as public.
    base = """
        struct Cache {{ int a;{extra} }};
        static int helper(struct Cache *c) {{ return c->a; }}
        int compute(int x) {{ struct Cache c = {{ x }}; return helper(&c); }}
    """
    v1 = tmp_path / "libv1.so"
    v2 = tmp_path / "libv2.so"
    _compile(base.format(extra=""), v1, include=inc)
    _compile(base.format(extra=" int b;"), v2, include=inc)

    ac = _abicheck_scoped(v1, v2, inc / "api.h")
    ad = _abidiff_headers(v1, v2, inc)
    assert not _is_breaking(ac), f"abicheck unexpectedly breaking: {ac}"
    assert not _is_breaking(ad), f"abidiff unexpectedly breaking: {ad}"


@pytest.mark.integration
def test_public_type_change_breaking_for_both(tmp_path: Path) -> None:
    """A public-header struct passed by value changing size breaks for both."""
    _require_tool("abidiff")
    _require_tool("gcc")

    # Separate per-version include dirs so each .so is built (and dumped /
    # scoped) against its own header revision.
    inc1, inc2 = tmp_path / "inc1", tmp_path / "inc2"
    inc1.mkdir()
    inc2.mkdir()

    def _header(dir_: Path, extra: str) -> Path:
        h = dir_ / "api.h"
        h.write_text(
            "#ifndef API_H\n#define API_H\n"
            f"struct Point {{ int x; int y;{extra} }};\n"
            "int dist(struct Point p);\n#endif\n"
        )
        return h

    src = '#include "api.h"\nint dist(struct Point p) { return p.x + p.y; }\n'
    h1 = _header(inc1, "")
    h2 = _header(inc2, " int z;")  # extra member ⇒ size change, by-value param
    v1, v2 = tmp_path / "libv1.so", tmp_path / "libv2.so"
    _compile(src, v1, include=inc1)
    _compile(src, v2, include=inc2)

    from abicheck.checker import compare
    from abicheck.dumper import dump

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old_snap = dump(v1, headers=[h1], version="v1", compiler="cc", lang="C")
        new_snap = dump(v2, headers=[h2], version="v2", compiler="cc", lang="C")
    ac = compare(old_snap, new_snap, scope_to_public_surface=True).verdict.value

    r = subprocess.run(
        ["abidiff", "--no-show-locs",
         "--headers-dir1", str(inc1), "--headers-dir2", str(inc2),
         str(v1), str(v2)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode & _BIT_ERROR:
        pytest.skip(f"abidiff errored: {r.stderr[:200]}")
    ad = decode_exit_code(r.returncode, zero_verdict="NO_CHANGE")

    assert _is_breaking(ac), f"abicheck should be breaking: {ac}"
    assert _is_breaking(ad), f"abidiff should be breaking: {ad}"

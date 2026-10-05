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

"""Call-count complexity gate on a *real* generated C++ library.

The synthetic gate (``test_compare_call_complexity.py``) covers
``compare()`` over hand-built snapshots. This one covers what those cannot:
``dumper.dump()`` itself -- the castxml XML parse, the model build, surface
facts, provenance -- and ``compare()`` over two genuinely dumped snapshots,
with real mangling, template instantiations and colliding short names
(``_cpp_corpus.py``).

Same oracle (``_call_counts.superlinear_call_sites``): no first-party
function's call count may grow faster than ``(size ratio)^1.5`` between the
two library sizes. The castxml/g++ subprocesses are not Python, so they do
not enter the counts; what is measured is all of abicheck's own work around
them. ``integration`` because it needs g++ and castxml.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from _call_counts import profile_call_counts, superlinear_call_sites
from _cpp_corpus import build_library, dump_library, toolchain_available

from abicheck.checker import compare

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not toolchain_available(), reason="needs g++ and castxml"),
]

SMALL, LARGE = 12, 48
_runs = itertools.count()


def _offenders(small: dict[str, int], large: dict[str, int]) -> list[str]:
    return [o.describe() for o in superlinear_call_sites(small, large, LARGE / SMALL)]


def test_dump_call_counts_grow_subquadratically(tmp_path: Path) -> None:
    def counts(n: int) -> dict[str, int]:
        run = next(_runs)
        lib = build_library(tmp_path / f"d{run}_{n}", n, tag=f"r{run}")
        holder = {}
        c = profile_call_counts(
            lambda: holder.setdefault("snap", dump_library(lib, "1.0"))
        )
        # Non-vacuity: the dump really parsed the generated surface.
        assert len(holder["snap"].declarations.types) >= 2 * n
        return c

    offenders = _offenders(counts(SMALL), counts(LARGE))
    assert not offenders, (
        "dump() call counts grew faster than (size ratio)^1.5:\n  "
        + "\n  ".join(offenders[:10])
    )


@pytest.mark.parametrize("change_fraction", [0.1, 1.0])
def test_compare_of_dumped_libraries_grows_subquadratically(
    tmp_path: Path, change_fraction: float
) -> None:
    def counts(n: int) -> dict[str, int]:
        run = next(_runs)
        root = tmp_path / f"c{run}_{n}"
        # Salted per run: corpus names repeat across sizes otherwise, and a
        # process-wide demangle/spelling memo warmed by an earlier run makes
        # the small size look cheaper than it is -- a phantom superlinear
        # site at the large size (seen on macOS, where nothing else warms it).
        tag = f"r{run}"
        old = dump_library(build_library(root / "v1", n, tag=tag), "1.0")
        new = dump_library(
            build_library(
                root / "v2", n, v2=True, change_fraction=change_fraction, tag=tag
            ),
            "2.0",
        )
        holder = {}
        c = profile_call_counts(lambda: holder.setdefault("r", compare(old, new)))
        assert len(holder["r"].changes) >= max(1, round(n * change_fraction))
        return c

    offenders = _offenders(counts(SMALL), counts(LARGE))
    assert not offenders, (
        f"compare() over dumped libraries (change_fraction={change_fraction}) grew faster than (size ratio)^1.5:\n  "
        + "\n  ".join(offenders[:10])
    )


def _macho_spelled(snapshot):
    # Clang on macOS records declarations with the Mach-O global prefix
    # (`__Z...`); the export table's `_Z...` spelling then reaches the
    # per-name demangling paths no earlier batch warms. Reproduced here on
    # an ELF dump so the Linux lane covers what only the macOS lane saw.
    for decl in (*snapshot.declarations.functions, *snapshot.declarations.variables):
        if decl.mangled and decl.mangled.startswith("_Z"):
            decl.mangled = "_" + decl.mangled
    return snapshot


def test_child_processes_per_compare_stay_constant_with_macho_spellings(
    tmp_path: Path,
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from audit_repeated_calls import count_subprocess_spawns

    spawned = {}
    for n in (SMALL, LARGE):
        run = next(_runs)
        root, tag = tmp_path / f"m{run}_{n}", f"r{run}"
        old = _macho_spelled(
            dump_library(build_library(root / "v1", n, tag=tag), "1.0")
        )
        new = _macho_spelled(
            dump_library(
                build_library(root / "v2", n, v2=True, change_fraction=1.0, tag=tag),
                "2.0",
            )
        )
        spawned[n] = count_subprocess_spawns(lambda o=old, w=new: compare(o, w))
    # A demangler batch per consumer at most; forking per name made this
    # grow with n (48 per consumer at n=48 before the batching fix).
    assert spawned[LARGE] <= max(spawned[SMALL], 3), spawned


def test_child_processes_stay_constant_for_a_removals_only_release(
    tmp_path: Path,
) -> None:
    # A release that only removes units: the long-double pairing returns
    # early (it needs added names too), so nothing warms the removed `_Z`
    # names before surface classification's per-finding `demangle()`
    # fallback reaches them -- that pass must batch them itself.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from audit_repeated_calls import count_subprocess_spawns

    spawned = {}
    for n in (SMALL, LARGE):
        run = next(_runs)
        root, tag = tmp_path / f"x{run}_{n}", f"r{run}"
        old = _macho_spelled(
            dump_library(build_library(root / "v1", n, tag=tag), "1.0")
        )
        new = _macho_spelled(
            dump_library(build_library(root / "v2", n // 4, tag=tag), "2.0")
        )
        spawned[n] = count_subprocess_spawns(lambda o=old, w=new: compare(o, w))
    assert spawned[LARGE] <= max(spawned[SMALL], 3), spawned

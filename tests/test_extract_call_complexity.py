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
        lib = build_library(tmp_path / f"d{next(_runs)}_{n}", n)
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
        root = tmp_path / f"c{next(_runs)}_{n}"
        old = dump_library(build_library(root / "v1", n), "1.0")
        new = dump_library(
            build_library(root / "v2", n, v2=True, change_fraction=change_fraction),
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

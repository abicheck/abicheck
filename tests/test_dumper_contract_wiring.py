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

"""ADR-050 Phase 1 — ``dump()`` populating ``AbiSnapshot.contract``.

These run the **live** clang L2 header backend (no castxml requirement, so
they're not gated behind the ``integration`` marker's castxml-present skip
condition — see ``test_clang_header_backend_integration.py``'s module
docstring for the same rationale) over a real compiled ``.so`` and asserts
the ``compute_extraction_contract`` wiring in ``dumper.dump()``:

1. a header-based dump populates ``contract`` with both fingerprints and the
   expected profile/scope fields;
2. ``symbols_only``/``dwarf_only``/no-headers dumps leave ``contract`` as
   ``None`` — the case a naive "gate on headers being non-empty" design
   would get wrong, since ``dwarf_only=True`` still accepts a ``headers``
   argument but ignores it (``from_headers`` stays ``False``);
3. two independent dumps of the same content are deterministic, and feeding
   the resulting pair through ``checker.compare`` does not spuriously raise
   — the regression gate for "the wiring works but the gate now fires on
   every routine compare."
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import Verdict, compare
from abicheck.dumper import dump

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 backend integration test is ELF/Linux-scoped",
)

_HEADER = """
#pragma once
#define FEATURE_X 1

struct Point { int x; int y; };

int add(int a, int b);
void scale(struct Point *p, double factor);
"""

_SOURCE = """
#include "api.h"
int add(int a, int b) { return a + b; }
void scale(struct Point *p, double factor) { p->x = (int)(p->x * factor); }
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def built_lib(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tiny ELF .so + its public header, returning (so_path, header)."""
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def test_header_dump_populates_contract(built_lib: tuple[Path, Path]) -> None:
    so, header = built_lib
    snap = dump(so, [header], compiler="cc", header_backend="clang")

    assert snap.contract is not None
    assert snap.contract.profile_fingerprint.startswith("sha256:")
    assert snap.contract.scope_fingerprint.startswith("sha256:")
    assert snap.contract.profile_fields["compiler_family"] == "clang"
    assert snap.contract.profile_fields["abi_dialect"] in ("gnu", "msvc")
    assert "api.h" in snap.contract.profile_fields["header_sequence"]
    assert "api.h" in snap.contract.scope_fields["headers"]


def test_symbols_only_dump_leaves_contract_none(built_lib: tuple[Path, Path]) -> None:
    so, _header = built_lib
    snap = dump(so, [], compiler="cc", symbols_only=True)

    assert snap.from_headers is False
    assert snap.contract is None


def test_no_headers_dump_leaves_contract_none(built_lib: tuple[Path, Path]) -> None:
    so, _header = built_lib
    snap = dump(so, [], compiler="cc")

    assert snap.from_headers is False
    assert snap.contract is None


def test_dwarf_only_with_headers_supplied_leaves_contract_none(tmp_path: Path) -> None:
    """Pins the fix for the naive "gate on non-empty ``headers``" bug.

    ``dwarf_only=True`` still accepts a ``headers`` argument on the call
    signature but forces a DWARF-derived snapshot and ignores it (warns
    "ignoring provided headers") — ``from_headers`` stays ``False``, and the
    contract must therefore stay ``None`` too, even though ``headers`` was
    non-empty here.

    Needs a real DWARF-carrying ``.so`` (``-g``): without debug info,
    ``dwarf_only`` can't actually engage and ``dump()`` falls back to the
    header path instead (a different, also-tested code path) — see
    ``dumper.py``'s ADR-003 fallback chain.
    """
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the contract-wiring integration test"
        )
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.c"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    with pytest.warns(UserWarning, match="ignoring provided headers"):
        snap = dump(so, [header], compiler="cc", dwarf_only=True)

    assert snap.from_headers is False
    assert snap.contract is None


def test_explicit_language_standard_flows_into_profile_fingerprint(
    built_lib: tuple[Path, Path],
) -> None:
    """Codex review, PR #624 follow-up: two dumps differing only by an
    explicit -std= must not share a profile_fingerprint — the extraction
    context genuinely differs (e.g. __cplusplus-gated declarations)."""
    so, header = built_lib
    snap_17 = dump(
        so, [header], compiler="cc", header_backend="clang", gcc_options="-std=gnu11"
    )
    snap_99 = dump(
        so, [header], compiler="cc", header_backend="clang", gcc_options="-std=gnu99"
    )

    assert snap_17.contract is not None
    assert snap_99.contract is not None
    assert snap_17.contract.profile_fields["language_standard"] == "gnu11"
    assert snap_99.contract.profile_fields["language_standard"] == "gnu99"
    assert snap_17.contract.profile_fingerprint != snap_99.contract.profile_fingerprint
    # Scope is untouched by a profile-only difference.
    assert snap_17.contract.scope_fingerprint == snap_99.contract.scope_fingerprint


def test_contract_is_deterministic_and_gate_does_not_spuriously_raise(
    built_lib: tuple[Path, Path],
) -> None:
    so, header = built_lib

    snap_a = dump(so, [header], version="1.0", compiler="cc", header_backend="clang")
    snap_b = dump(so, [header], version="1.0", compiler="cc", header_backend="clang")

    assert snap_a.contract is not None
    assert snap_b.contract is not None
    assert snap_a.contract.profile_fingerprint == snap_b.contract.profile_fingerprint
    assert snap_a.contract.scope_fingerprint == snap_b.contract.scope_fingerprint

    result = compare(snap_a, snap_b)
    assert result.verdict == Verdict.NO_CHANGE

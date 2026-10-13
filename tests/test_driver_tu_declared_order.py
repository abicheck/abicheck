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

"""Pin the current declared-order behaviour of the aggregate driver TU.

Known gap: ``declared-header-order-is-still-load-bearing-because-the-driver``.
The castxml and clang backends parse every declared header in one aggregate
translation unit, in the caller's declared order, so a header that depends on
an earlier one parses only in that order. These tests state what the aggregate
does today, so that any change to it (for example, canonicalising the order)
has to change them deliberately:

* the aggregate TU text follows the declared order exactly, for every
  permutation of the input (oracle: the expected lines written out from the
  input list, not the builders' own output);
* the same library dumped under the two orders of a dependent pair yields
  different declarations, so declared order is load-bearing for the result;
* the comparison gate refuses a reorder of existing headers on real dumps.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.comparability import check_contracts_comparable
from abicheck.errors import ProfileMismatchError, SnapshotError
from abicheck.extract.castxml_header_compat import castxml_aggregate_text
from abicheck.extract.headers.ast_config import clang_aggregate_text

# Two headers where ``a_user.h`` needs ``Base`` from ``z_base.h``: it parses
# only when ``z_base.h`` comes first in the aggregate.
_BASE_H = (
    "#ifndef Z_BASE_H\n#define Z_BASE_H\ntypedef struct Base { int x; } Base;\n#endif\n"
)
_USER_H = (
    "#ifndef A_USER_H\n#define A_USER_H\n"
    "Base *make_base(void);\nint base_x(const Base *b);\n#endif\n"
)
_LIB_C = (
    "#include <stdlib.h>\n"
    "typedef struct Base { int x; } Base;\n"
    "Base *make_base(void) { Base *b = malloc(sizeof *b); b->x = 7; return b; }\n"
    "int base_x(const Base *b) { return b->x; }\n"
)


def _write_headers(root: Path, names: list[str]) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for name in names:
        p = root / name
        p.write_text(f"// {name}\n")
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# The aggregate TU text follows the declared order (pure, no compiler)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(["c.h", "a.h", "b.h"])))
def test_clang_aggregate_text_is_the_declared_order(tmp_path, order):
    headers = _write_headers(tmp_path, list(order))
    expected = "".join(f'#include "{(tmp_path / name).resolve()}"\n' for name in order)
    assert clang_aggregate_text(headers) == expected


@pytest.mark.parametrize("order", list(itertools.permutations(["c.h", "a.h", "b.h"])))
def test_castxml_aggregate_text_is_the_preamble_then_the_declared_order(
    tmp_path, order
):
    headers = _write_headers(tmp_path, list(order))
    preamble = tmp_path / "preamble.h"
    expected = f'#include "{preamble}"\n' + "".join(
        f'#include "{(tmp_path / name).resolve()}"\n' for name in order
    )
    assert castxml_aggregate_text(headers, preamble) == expected


# ---------------------------------------------------------------------------
# Real dumps: declared order changes what is extracted, and the gate refuses
# ---------------------------------------------------------------------------


def _build_fixture(root: Path) -> Path:
    if shutil.which("gcc") is None:
        pytest.skip("gcc is required to build the declared-order fixture")
    inc = root / "inc"
    inc.mkdir(parents=True)
    (inc / "z_base.h").write_text(_BASE_H)
    (inc / "a_user.h").write_text(_USER_H)
    src = root / "lib.c"
    src.write_text(_LIB_C)
    so = root / "libbase.so"
    proc = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-O0", str(src), "-o", str(so)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"gcc failed:\n{proc.stderr}"
    return so


def _extraction_outcome(so: Path, headers: list[Path], backend: str):
    """What one declared order extracts: its function names, or the loud
    ``SnapshotError`` the aggregate raised. Either is a fact about that order."""
    from abicheck.dumper import dump
    from abicheck.errors import SnapshotError

    try:
        snap = dump(
            so, headers, version="1", compiler="cc", lang="C", header_backend=backend
        )
    except SnapshotError:
        return "SnapshotError"
    return frozenset(f.name for f in snap.declarations.functions)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_declared_order_changes_the_extracted_declarations(tmp_path, backend):
    so = _build_fixture(tmp_path)
    inc = tmp_path / "inc"
    z_first = _extraction_outcome(so, [inc / "z_base.h", inc / "a_user.h"], backend)
    a_first = _extraction_outcome(so, [inc / "a_user.h", inc / "z_base.h"], backend)
    # Same binary, same header set, same content: only the declared order
    # differs, and what is extracted differs with it.
    assert z_first != a_first


def _self_contained_pair(root: Path) -> tuple[Path, list[Path]]:
    so = _build_fixture(root)
    inc = root / "inc"
    (inc / "z_base.h").write_text("typedef struct Base { int x; } Base;\n")
    (inc / "a_user.h").write_text("int base_x(int v);\n")
    return so, [inc / "z_base.h", inc / "a_user.h"]


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_reordering_self_contained_headers_is_comparable_on_real_dumps(
    tmp_path, backend
):
    """ADR-082: both orders prove the same canonical extraction, so a reorder of
    an identical header set is comparable (the carve-out the gap asked for)."""
    from abicheck.dumper import dump

    so, headers = _self_contained_pair(tmp_path)
    old = dump(
        so, headers, version="1", compiler="cc", lang="C", header_backend=backend
    )
    new = dump(
        so,
        list(reversed(headers)),
        version="2",
        compiler="cc",
        lang="C",
        header_backend=backend,
    )
    assert old.contract.header_canonical is not None
    assert old.contract.header_canonical == new.contract.header_canonical
    check_contracts_comparable(old, new)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_reordering_dependent_headers_still_refuses_on_real_dumps(tmp_path, backend):
    """Negative control: the z-first order is unproven (its sorted parse fails),
    so a reorder against it keeps refusing; the refusal is not lifted by sorting."""
    from abicheck.dumper import dump

    so = _build_fixture(tmp_path)
    inc = tmp_path / "inc"
    z_first = [inc / "z_base.h", inc / "a_user.h"]
    old = dump(
        so, z_first, version="1", compiler="cc", lang="C", header_backend=backend
    )
    assert old.contract.header_canonical is None
    try:
        new = dump(
            so,
            list(reversed(z_first)),
            version="2",
            compiler="cc",
            lang="C",
            header_backend=backend,
        )
    except SnapshotError:
        # clang fails loudly on the a_user-first parse before any comparison.
        assert backend == "clang"
        return
    with pytest.raises(ProfileMismatchError, match="header_sequence"):
        check_contracts_comparable(old, new)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_adding_a_header_to_an_unsorted_declared_set_is_still_comparable(
    tmp_path, backend
):
    """Growth is unchanged by the proof: a trailing append to an unsorted declared
    set stays comparable under the existing header_sequence rule."""
    from abicheck.dumper import dump

    so, headers = _self_contained_pair(tmp_path)
    (tmp_path / "inc" / "m_extra.h").write_text("int extra_fn(void);\n")
    old = dump(
        so, headers, version="1", compiler="cc", lang="C", header_backend=backend
    )
    new = dump(
        so,
        [*headers, tmp_path / "inc" / "m_extra.h"],
        version="2",
        compiler="cc",
        lang="C",
        header_backend=backend,
    )
    check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# The witness's own invariants (ADR-082)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("names", list(itertools.permutations(["c.h", "a.h", "b.h"])))
def test_canonical_order_is_sorted_by_path_for_every_declared_permutation(
    tmp_path, names
):
    """Oracle: the expected order is the name-sorted list, not the helper's output."""
    from abicheck.extract.header_order_witness import canonical_header_order

    headers = _write_headers(tmp_path, list(names))
    assert [h.name for h in canonical_header_order(headers)] == sorted(names)


@pytest.mark.integration
def test_a_proven_snapshot_round_trips_its_canonical_sequence(tmp_path):
    """The proof is written with the snapshot and read back; absent means unproven."""
    from abicheck.dumper import dump
    from abicheck.serialization import snapshot_from_dict
    from abicheck.storage.snapshot_encode import snapshot_to_dict

    so, headers = _self_contained_pair(tmp_path)
    snap = dump(so, list(reversed(headers)), version="1", compiler="cc", lang="C")
    proven = snap.contract.header_canonical
    assert proven is not None
    loaded = snapshot_from_dict(snapshot_to_dict(snap))
    assert loaded.contract.header_canonical == proven

    raw = snapshot_to_dict(snap)
    raw["contract"].pop("header_canonical", None)
    assert snapshot_from_dict(raw).contract.header_canonical is None


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (["a.h", "b.h"], ["a.h", "b.h"]),
        ("a.h", None),
        (["a.h", 1], None),
        (None, None),
    ],
)
def test_a_malformed_canonical_proof_loads_as_unproven(raw_value, expected):
    """Only a list of identity strings counts as a proof; anything else is unproven."""
    from abicheck.storage.snapshot_load_normalization import (
        extraction_contract_from_dict,
    )

    contract = extraction_contract_from_dict({"header_canonical": raw_value})
    assert contract is not None
    assert contract.header_canonical == expected

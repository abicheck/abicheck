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

"""ADR-050 D2 — a *versioned public-header inventory* is ABI/API input, not
an incomparable extraction context.

Bug class `comparability.header_inventory_growth_is_not_profile_drift`
(`tests/regressions/manifest.py`). The reported instance was `libpvxs.so.1.5`:
a release that adds `src/pvxs/json.h` to a `--header old=<dir> new=<dir>`
surface failed the gate with

    ... are not comparable: old and new snapshots were extracted under
    different compile contexts (profile_fingerprint mismatch; differing
    fields: header_sequence)

and produced no ABI verdict at all. The mechanism: a `-H <dir>` surface
expands in SORTED order (`header_utils.iter_directory_headers`), so an added
header lands INTERIOR to `profile_fields["header_sequence"]`, and the
carve-out that exists for exactly this case
(`_header_sequence_is_additive_reorder_free`) required a strictly-TRAILING
append.

The class -- not the one reported input -- is: *any* order-preserving
insertion of genuinely-new declared headers is inventory growth and stays
comparable, while *any* reorder or removal of existing headers, and any
genuine extraction-context drift (toolchain, language standard, target,
include-resolution configuration), still hard-fails. The invariant below is
therefore stated over an exhaustively enumerated small domain against an
independent oracle, not against the reported sequence alone.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.comparability import (
    _header_sequence_is_additive_reorder_free,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.comparability_sequences import _is_ordered_subsequence
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot, ExtractionContract


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _snap(contract: ExtractionContract | None) -> AbiSnapshot:
    return AbiSnapshot(library="libpvxs.so.1.5", version="1.5", contract=contract)


# ---------------------------------------------------------------------------
# End-to-end gate: the reported shape, and its siblings
# ---------------------------------------------------------------------------

# Every insertion position for one new header into a 4-header inventory --
# the reported input is only ONE of these (`json.h` between `data.h` and
# `log.h`), and the trailing-only rule passed exactly the last of them.
_PVXS_OLD = ("client.h", "data.h", "log.h", "source.h")


@pytest.mark.parametrize("added", ["aaa.h", "config.h", "json.h", "nt.h", "zzz.h"])
def test_added_public_header_anywhere_in_a_sorted_inventory_stays_comparable(
    tmp_path, added
):
    """The reported bug's class: one added public header, at every position a
    SORTED directory expansion can place it (before all, interior x3, after
    all). Each must complete the comparability check so the added API is
    compared and reported, never rejected as a compile-context mismatch."""
    old_names = list(_PVXS_OLD)
    new_names = sorted([*old_names, added])
    assert new_names != [*old_names, added] or added == "zzz.h", (
        "the sorted expansion must genuinely place most of these INTERIOR; "
        "a case that only ever appends would not exercise the bug"
    )

    old_headers = [
        _write(tmp_path / "v1" / "pvxs" / n, f"int f_{n[0]}(void);\n")
        for n in old_names
    ]
    new_headers = [
        _write(tmp_path / "v2" / "pvxs" / n, f"int f_{n[0]}(void);\n")
        for n in new_names
    ]

    old = _snap(
        compute_extraction_contract(declared_headers=old_headers, l2_frontend_ran=True)
    )
    new = _snap(
        compute_extraction_contract(declared_headers=new_headers, l2_frontend_ran=True)
    )

    # The bug's own precondition: this is genuinely a profile_fingerprint
    # mismatch confined to header_sequence, not a pair that trivially matches.
    assert old.contract.profile_fingerprint != new.contract.profile_fingerprint
    assert (
        old.contract.profile_fields["header_sequence"]
        != new.contract.profile_fields["header_sequence"]
    )
    assert check_contracts_comparable(old, new) is None


def test_added_public_header_via_header_dirs_stays_comparable(tmp_path):
    """The real CLI shape from the report: `--header old=<dir> new=<dir>`
    with a `public_header_dirs` entry per side, plus content changes in two
    surviving headers (`data.h`, `source.h`) riding along -- header identity
    is path-derived, so those must not perturb the sequence at all."""
    old_dir = tmp_path / "old" / "src" / "pvxs"
    new_dir = tmp_path / "new" / "src" / "pvxs"
    old_headers = [
        _write(old_dir / "client.h", "int c(void);\n"),
        _write(old_dir / "data.h", "int d(void);\n"),
        _write(old_dir / "log.h", "int l(void);\n"),
        _write(old_dir / "source.h", "int s(void);\n"),
    ]
    new_headers = [
        _write(new_dir / "client.h", "int c(void);\n"),
        _write(new_dir / "data.h", "int d(void); int d2(void);\n"),  # changed
        _write(new_dir / "json.h", "int j(void);\n"),  # added, interior
        _write(new_dir / "log.h", "int l(void);\n"),
        _write(new_dir / "source.h", "int s(void); int s2(void);\n"),  # changed
    ]
    old = _snap(
        compute_extraction_contract(
            declared_headers=old_headers,
            public_header_dirs=[old_dir],
            l2_frontend_ran=True,
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=new_headers,
            public_header_dirs=[new_dir],
            l2_frontend_ran=True,
        )
    )
    assert check_contracts_comparable(old, new) is None


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("compiler_family", {"compiler_family": "clang"}),
        ("language_standard", {"language_standard": "c++20"}),
        ("target_triple", {"target_triple": "aarch64-unknown-linux-gnu"}),
        ("macro_ops", {"macro_ops": [("D", "PVXS_INTERNAL=1")]}),
    ],
)
def test_genuine_extraction_context_drift_still_rejected_alongside_added_header(
    tmp_path, field, kwargs
):
    """Requirement 3: a real extraction-context mismatch must still hard-fail,
    including when it rides ALONGSIDE the now-waived inventory growth -- the
    carve-out must not become a blanket waiver for a header_sequence-bearing
    mismatch. One case per independently-drifting profile field, not just the
    one the fix happened to be written against."""
    old_headers = [
        _write(tmp_path / "v1" / "pvxs" / n, "int f(void);\n") for n in _PVXS_OLD
    ]
    new_headers = [
        _write(tmp_path / "v2" / "pvxs" / n, "int f(void);\n")
        for n in sorted([*_PVXS_OLD, "json.h"])
    ]
    old = _snap(
        compute_extraction_contract(declared_headers=old_headers, l2_frontend_ran=True)
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=new_headers, l2_frontend_ran=True, **kwargs
        )
    )
    with pytest.raises(ProfileMismatchError) as excinfo:
        check_contracts_comparable(old, new)
    assert field in str(excinfo.value)


def test_reordered_existing_headers_still_rejected_even_with_growth(tmp_path):
    """The invariant the field exists to protect is order of the EXISTING
    headers -- a swap still raises, growth or no growth."""
    old_headers = [
        _write(tmp_path / "v1" / "pvxs" / n, "int f(void);\n")
        for n in ("a.h", "b.h", "c.h")
    ]
    new_names = ("c.h", "b.h", "a.h", "json.h")
    new_headers = [
        _write(tmp_path / "v2" / "pvxs" / n, "int f(void);\n") for n in new_names
    ]
    old = _snap(
        compute_extraction_contract(declared_headers=old_headers, l2_frontend_ran=True)
    )
    new = _snap(
        compute_extraction_contract(declared_headers=new_headers, l2_frontend_ran=True)
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# The invariant, exhaustively, against an independent oracle
# ---------------------------------------------------------------------------

_UNIVERSE = ("a.h", "b.h", "c.h", "d.h", "e.h")
_ALL_NEW = frozenset(_UNIVERSE)


def _oracle_is_insertion_growth(old: tuple[str, ...], new: tuple[str, ...]) -> bool:
    """Independent second derivation of "new is old with entries inserted,
    existing order preserved": enumerate every choice of len(old) positions
    in `new` and ask whether one of them spells `old` exactly.

    Deliberately NOT the implementation's greedy scan -- per AGENTS.md's
    "a matrix test needs an oracle, not just a type check", the expectation
    must not be the same algorithm under test.
    """
    if len(old) > len(new):
        return False
    return any(
        tuple(new[i] for i in positions) == old
        for positions in itertools.combinations(range(len(new)), len(old))
    )


def _permutations_and_subsets():
    """Every (old, new) pair over a 5-name universe where each side is an
    ordered, duplicate-free selection of size 2..4 -- ~40k pairs covering
    growth, shrinkage, reorder, insertion at every position, and disjoint
    sets alike."""
    selections = [
        p
        for size in (2, 3, 4)
        for combo in itertools.combinations(_UNIVERSE, size)
        for p in itertools.permutations(combo)
    ]
    return itertools.product(selections, selections)


def test_carve_out_matches_the_independent_insertion_oracle_exhaustively():
    """The generalized invariant: over an exhaustively enumerated domain, the
    carve-out fires on exactly the order-preserving-insertion pairs and on
    nothing else. A failure names every disagreeing pair at once."""
    disagreements = [
        (old, new)
        for old, new in _permutations_and_subsets()
        if _header_sequence_is_additive_reorder_free(
            json.dumps(list(old)), json.dumps(list(new)), _ALL_NEW
        )
        != _oracle_is_insertion_growth(old, new)
    ]
    assert not disagreements, (
        f"{len(disagreements)} disagreements, e.g. {disagreements[:5]}"
    )


def test_oracle_is_not_vacuous():
    """Vacuity guard on the oracle itself (AGENTS.md): an oracle accidentally
    reduced to a constant would make the sweep above pass while asserting
    nothing."""
    verdicts = {
        _oracle_is_insertion_growth(old, new)
        for old, new in _permutations_and_subsets()
    }
    assert verdicts == {True, False}


def test_carve_out_is_not_vacuous_over_the_same_domain():
    """The same guard on the function under test -- an implementation that
    returned a constant must not survive the sweep."""
    verdicts = {
        _header_sequence_is_additive_reorder_free(
            json.dumps(list(old)), json.dumps(list(new)), _ALL_NEW
        )
        for old, new in _permutations_and_subsets()
    }
    assert verdicts == {True, False}


def test_corroboration_is_still_required_for_every_inserted_entry():
    """Insertion shape alone never suffices: every entry `new` adds must be
    in the scope-confirmed newly-added set. Exhaustive over which single
    inserted entry is withheld from corroboration."""
    old = ("a.h", "c.h", "e.h")
    for inserted in ("b.h", "d.h", "f.h"):
        new = tuple(sorted({*old, inserted}))
        payload = (json.dumps(list(old)), json.dumps(list(new)))
        assert _header_sequence_is_additive_reorder_free(*payload, {inserted})
        assert not _header_sequence_is_additive_reorder_free(*payload, set())
        assert not _header_sequence_is_additive_reorder_free(*payload, None)


# ---------------------------------------------------------------------------
# The extracted merge primitive, as its own contract (AGENTS.md,
# "Primitive-level property tests")
# ---------------------------------------------------------------------------


def test_subsequence_primitive_matches_the_oracle_exhaustively():
    disagreements = [
        (inner, outer)
        for inner, outer in _permutations_and_subsets()
        if _is_ordered_subsequence(list(inner), list(outer))
        != _oracle_is_insertion_growth(inner, outer)
    ]
    assert not disagreements, (
        f"{len(disagreements)} disagreements, e.g. {disagreements[:5]}"
    )


def test_subsequence_primitive_is_reflexive_and_empty_inner_is_trivial():
    for size in (0, 1, 2, 3):
        for combo in itertools.permutations(_UNIVERSE, size):
            assert _is_ordered_subsequence(list(combo), list(combo))
            assert _is_ordered_subsequence([], list(combo))


def test_subsequence_primitive_rejects_every_nontrivial_reversal():
    for size in (2, 3, 4):
        for combo in itertools.permutations(_UNIVERSE, size):
            reversed_combo = list(reversed(combo))
            assert not _is_ordered_subsequence(list(combo), reversed_combo)


def test_subsequence_primitive_is_transitive():
    """a <= b and b <= c implies a <= c -- a contract property independent of
    any header/profile semantics."""
    selections = [
        p
        for size in (1, 2, 3)
        for combo in itertools.combinations(("a", "b", "c", "d"), size)
        for p in itertools.permutations(combo)
    ]
    for a, b, c in itertools.product(selections, repeat=3):
        if _is_ordered_subsequence(list(a), list(b)) and _is_ordered_subsequence(
            list(b), list(c)
        ):
            assert _is_ordered_subsequence(list(a), list(c))

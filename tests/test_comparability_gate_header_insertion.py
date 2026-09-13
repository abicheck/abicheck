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

"""A declared-header addition is ordinary evolution, wherever it sorts.

Bug class: an *incidental* extraction-ordering fact was treated as evidence
about the extraction environment. ``header_sequence`` records declared-header
order because the aggregate driver TU parses headers sequentially, and the
only waiver was a strict trailing append. Directory-discovered headers arrive
sorted, so adding one public header whose name sorts into the middle
(``data.h, log.h`` -> ``data.h, json.h, log.h``) first produced
``profile_fingerprint mismatch; differing fields: header_sequence`` and NO
verdict at all, and then (PR #1274) a verdict whose ``declaration``/``layout``
assurance was marked unverified -- which reads through to
``analysis_assurance.status = "partial"`` and floors the exit code of every
run configured with ``assurance.require_complete``. Both dispositions price
the added header's *sort position*, which no one chose, and neither is
consistent with the same contract comparing an EXISTING header's edited
content -- an identical macro/pragma-leak hazard -- at full assurance, since
a declared header's content is not part of ``profile_fingerprint`` at all.

The invariant these tests state is not "the json.h case now passes". It is:

  *For any* declared-header growth that preserves every existing header's
  relative order and adds only headers the scope fingerprint independently
  confirms as new, the pair is comparable at FULL assurance and the outcome
  does not depend on where the new headers sort -- while *any* growth that
  reorders an existing header, adds an unconfirmed header, or rides
  alongside another unexplained profile field stays a hard refusal.

Both halves are exercised over generated inputs (every insertion position,
every permutation) against an oracle derived independently of the
implementation, not only against the one reported repro.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.comparability_sequences import (
    _header_sequence_is_additive_reorder_free,
    _header_sequence_is_interior_insertion,
    _header_sequence_is_scope_confirmed_growth,
)
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot

_OLD_HEADERS = ("data.h", "log.h", "version.h")


def _headers(root: Path, names) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for name in names:
        p = root / name
        p.write_text(f"// {name}\n")
        out.append(p)
    return sorted(out)


def _snap(root: Path, names, version: str) -> AbiSnapshot:
    """A snapshot whose contract is produced by the REAL
    ``compute_extraction_contract`` over real files -- never hand-typed
    fingerprints, so the gate's own authenticity check is genuinely exercised
    and the profile/scope fields agree the way a real dump's do."""
    headers = _headers(root, names)
    return AbiSnapshot(
        library="libpvxs.so",
        version=version,
        contract=compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=headers,
            public_header_paths=headers,
        ),
    )


# ---------------------------------------------------------------------------
# The reported scenario, through the real contract builder
# ---------------------------------------------------------------------------


def test_middle_sorting_public_header_addition_is_fully_comparable(tmp_path):
    """The reported repro: one added public header sorting into the middle."""
    old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
    new = _snap(tmp_path / "new", (*_OLD_HEADERS, "json.h"), "1.4")

    # The order really is an insertion, not an append -- i.e. this pair is the
    # shape the original trailing-append-only waiver declined.
    assert json.loads(new.contract.profile_fields["header_sequence"]) == [
        "data.h",
        "json.h",
        "log.h",
        "version.h",
    ]
    assert not _header_sequence_is_additive_reorder_free(
        old.contract.profile_fields["header_sequence"],
        new.contract.profile_fields["header_sequence"],
        {"json.h"},
    )

    assert check_contracts_comparable(old, new) is None


def test_an_existing_headers_leaking_edit_is_compared_at_full_assurance(tmp_path):
    """The symmetry that decides how an addition may be priced.

    The hazard an interior insertion carries is that a header parsed after it
    in the aggregate driver TU now sees macros/pragmas it did not see before.
    That hazard is NOT specific to an insertion: an EXISTING declared header
    that gains ``#define LEAK`` and ``#pragma pack(push, 1)`` between two
    versions does the same thing to every header after it -- and this contract
    compares that at full assurance, deliberately, because a declared header's
    content is not part of ``profile_fingerprint`` at all
    (``comparability_fields._header_identities`` keys a header by its
    root-relative path, never its bytes).

    This test states that as an executable fact rather than a claim in a
    docstring: as long as it passes, pricing an ADDED header's sort position
    as reduced assurance is an inconsistency, not a safeguard -- it would make
    assurance depend on the new header's spelling while the strictly larger
    hazard next door goes unpriced. If this test ever fails because header
    content joins the fingerprint, the insertion question genuinely reopens
    and this file's waiver should be revisited with it.
    """
    leaky = "#define LEAK 1\n#pragma pack(push, 1)\nint a(int);\n"
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_headers = _headers(old_root, _OLD_HEADERS)
    new_headers = _headers(new_root, _OLD_HEADERS)
    # The FIRST declared header, so its leak reaches every header after it.
    new_headers[0].write_text(leaky, encoding="utf-8")

    def _snapshot(headers, version):
        return AbiSnapshot(
            library="libpvxs.so",
            version=version,
            contract=compute_extraction_contract(
                l2_frontend_ran=True,
                declared_headers=headers,
                public_header_paths=headers,
                depfile_resolved_paths=headers,
            ),
        )

    old = _snapshot(old_headers, "1.3")
    new = _snapshot(new_headers, "1.4")
    assert old.contract.profile_fingerprint == new.contract.profile_fingerprint
    assert check_contracts_comparable(old, new) is None


def test_trailing_append_keeps_full_assurance(tmp_path):
    """The strictly-safe shape is not degraded by this change: a header that
    sorts AFTER every existing one changes no existing header's parse context,
    so it stays fully comparable with no mismatch descriptor at all."""
    old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
    new = _snap(tmp_path / "new", (*_OLD_HEADERS, "zzz.h"), "1.4")
    assert check_contracts_comparable(old, new) is None


@pytest.mark.parametrize("added", ["aaa.h", "efg.h", "json.h", "mmm.h", "zzz.h"])
def test_any_insertion_position_is_comparable(tmp_path, added):
    """Generalization guard: the outcome must not depend on WHERE the new
    header sorts. Before the fix, exactly one of these five positions
    (``zzz.h``, the trailing one) was comparable at full assurance and the
    other four were refused (and later, bounded to reduced assurance) -- a
    difference with no bearing on whether the library's ABI changed."""
    old = _snap(tmp_path / f"old{added}", _OLD_HEADERS, "1.3")
    new = _snap(tmp_path / f"new{added}", (*_OLD_HEADERS, added), "1.4")
    assert check_contracts_comparable(old, new) is None


def test_reordering_an_existing_header_still_refuses(tmp_path):
    """The safety property the order fact exists for is untouched: when an
    EXISTING header moves relative to another, its own parse context changed
    for reasons nothing in the declared surface explains."""
    old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
    new = AbiSnapshot(
        library="libpvxs.so",
        version="1.4",
        contract=compute_extraction_contract(
            l2_frontend_ran=True,
            # Deliberately NOT sorted: a caller-declared order that swaps two
            # existing headers.
            declared_headers=list(reversed(_headers(tmp_path / "new", _OLD_HEADERS))),
            public_header_paths=_headers(tmp_path / "new", _OLD_HEADERS),
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# Primitive-level property tests for _header_sequence_is_interior_insertion
# (AGENTS.md "Primitive-level property tests"): the contract as invariants,
# decoupled from any one caller, with an oracle that is not the
# implementation's own formula.
# ---------------------------------------------------------------------------


def _seq(entries) -> str:
    return json.dumps(list(entries))


def _is_order_preserving_supersequence(old: list[str], new: list[str]) -> bool:
    """Independent oracle -- a plain two-pointer subsequence walk, deliberately
    NOT the implementation's ``iter``/``in`` idiom."""
    i = 0
    for entry in new:
        if i < len(old) and entry == old[i]:
            i += 1
    return i == len(old)


class TestInteriorInsertionProperties:
    _BASE = ["a.h", "b.h", "c.h", "d.h"]

    def test_every_single_insertion_position_except_trailing_is_accepted(self):
        """Exhaustive over the small domain: inserting one confirmed-new entry
        at every index. Every position but the last is an interior insertion;
        the last is the trailing append the other waiver owns."""
        disagreeing = []
        for pos in range(len(self._BASE) + 1):
            new = [*self._BASE[:pos], "new.h", *self._BASE[pos:]]
            got = _header_sequence_is_interior_insertion(
                _seq(self._BASE), _seq(new), {"new.h"}
            )
            expected = pos != len(self._BASE)
            if got != expected:
                disagreeing.append((pos, got, expected))
        assert not disagreeing

    def test_accepts_every_multi_insertion_placement(self):
        """Two new entries, every distinct placement -- the result must depend
        only on whether existing entries kept their order, never on how many
        entries were added or how they interleave."""
        bad = []
        for positions in itertools.combinations_with_replacement(
            range(len(self._BASE) + 1), 2
        ):
            new = list(self._BASE)
            for offset, pos in enumerate(sorted(positions)):
                new.insert(pos + offset, f"n{offset}.h")
            if new[: len(self._BASE)] == self._BASE:
                continue  # trailing append: the other waiver's shape
            if not _header_sequence_is_interior_insertion(
                _seq(self._BASE), _seq(new), {"n0.h", "n1.h"}
            ):
                bad.append(new)
        assert not bad

    def test_rejects_every_reordering_permutation(self):
        """Adversarial half: no permutation that moves an existing entry is
        ever accepted, with or without an added header."""
        accepted = []
        for perm in itertools.permutations(self._BASE):
            if list(perm) == self._BASE:
                continue
            for extra in ([], ["new.h"]):
                for pos in range(len(perm) + 1):
                    new = [*list(perm)[:pos], *extra, *list(perm)[pos:]]
                    if _header_sequence_is_interior_insertion(
                        _seq(self._BASE), _seq(new), {"new.h"}
                    ):
                        accepted.append(new)
        assert not accepted

    def test_agrees_with_the_independent_subsequence_oracle(self):
        """Over every generated pair, acceptance implies the oracle's own
        order-preserving-supersequence verdict. Stated as an implication
        rather than equality because the predicate additionally requires
        growth, confirmed-new entries and no trailing-append shape -- an
        equality oracle would have to re-derive those, i.e. re-derive the
        implementation."""
        for perm in itertools.permutations(self._BASE):
            for pos in range(len(perm) + 1):
                new = [*list(perm)[:pos], "new.h", *list(perm)[pos:]]
                if _header_sequence_is_interior_insertion(
                    _seq(self._BASE), _seq(new), {"new.h"}
                ):
                    assert _is_order_preserving_supersequence(self._BASE, new)

    def test_oracle_is_not_vacuous(self):
        """Guard on the oracle itself: it must distinguish its two answers, or
        the implication test above asserts nothing."""
        assert _is_order_preserving_supersequence(["a.h", "b.h"], ["a.h", "x.h", "b.h"])
        assert not _is_order_preserving_supersequence(["a.h", "b.h"], ["b.h", "a.h"])

    @pytest.mark.parametrize(
        "old_list,new_list,scope_new",
        [
            # An added entry the declared surface never confirms as new.
            (["a.h", "b.h"], ["a.h", "x.h", "b.h"], set()),
            (["a.h", "b.h"], ["a.h", "x.h", "b.h"], {"other.h"}),
            # No scope evidence at all.
            (["a.h", "b.h"], ["a.h", "x.h", "b.h"], None),
            # Shrinking, not growing.
            (["a.h", "b.h", "c.h"], ["a.h", "c.h"], {"x.h"}),
            # Same length, one entry swapped for another.
            (["a.h", "b.h"], ["a.h", "x.h"], {"x.h"}),
            # A duplicate entry: never a real header_sequence value, so never
            # usable evidence (the trailing-append waiver fails closed the
            # same way).
            (["a.h", "b.h"], ["a.h", "x.h", "b.h", "b.h"], {"x.h"}),
            (["a.h", "a.h"], ["a.h", "a.h", "x.h", "b.h"], {"x.h"}),
            # The single-header sentinel carries no order to reason about.
            (["<single-header>"], ["a.h", "x.h", "b.h"], {"x.h"}),
        ],
    )
    def test_fails_closed_on_unusable_evidence(self, old_list, new_list, scope_new):
        assert not _header_sequence_is_interior_insertion(
            _seq(old_list), _seq(new_list), scope_new
        )

    @pytest.mark.parametrize("old_value,new_value", [(None, "[]"), ("[]", None)])
    def test_declines_when_either_side_is_absent(self, old_value, new_value):
        assert not _header_sequence_is_interior_insertion(old_value, new_value, {"x.h"})

    def test_declines_malformed_json(self):
        assert not _header_sequence_is_interior_insertion(
            "not-json", _seq(["a.h"]), {"a.h"}
        )

    def test_is_disjoint_from_the_trailing_append_waiver(self):
        """The two predicates partition the growth shapes between them: each
        placement is accepted by exactly one, so the composite below is a
        union with no overlap to make the outcome order-dependent."""
        both = []
        for pos in range(len(self._BASE) + 1):
            new = [*self._BASE[:pos], "new.h", *self._BASE[pos:]]
            if _header_sequence_is_interior_insertion(
                _seq(self._BASE), _seq(new), {"new.h"}
            ) and _header_sequence_is_additive_reorder_free(
                _seq(self._BASE), _seq(new), {"new.h"}
            ):
                both.append(new)
        assert not both


# ---------------------------------------------------------------------------
# The composite predicate the carve-out is actually stated in terms of
# ---------------------------------------------------------------------------


class TestScopeConfirmedGrowthProperties:
    """`_header_sequence_is_scope_confirmed_growth` is the union of the two
    placement predicates, and the union is what the gate may act on.

    The oracle here is deliberately independent of the implementation's
    ``or``: growth is acceptable iff every old entry survives in its original
    relative order and every entry the new sequence added is one the declared
    surface confirms as new. Nothing in that statement mentions placement --
    which is the whole point of the change.
    """

    _BASE = ["a.h", "b.h", "c.h", "d.h"]

    @staticmethod
    def _oracle(old: list[str], new: list[str], scope_new: set[str] | None) -> bool:
        if scope_new is None:
            return False
        if len(set(old)) != len(old) or len(set(new)) != len(new):
            return False
        if len(new) <= len(old):
            return False
        if not _is_order_preserving_supersequence(old, new):
            return False
        return set(new) - set(old) <= scope_new and set(old) <= set(new)

    def test_agrees_with_the_oracle_over_every_single_insertion_position(self):
        disagreeing = []
        for pos in range(len(self._BASE) + 1):
            new = [*self._BASE[:pos], "new.h", *self._BASE[pos:]]
            got = _header_sequence_is_scope_confirmed_growth(
                _seq(self._BASE), _seq(new), {"new.h"}
            )
            expected = self._oracle(self._BASE, new, {"new.h"})
            if got != expected:
                disagreeing.append((pos, got, expected))
        assert not disagreeing

    def test_accepts_every_position_including_the_trailing_one(self):
        """The property the whole fix is about: the verdict does not depend on
        where the added header sorts."""
        rejected = [
            pos
            for pos in range(len(self._BASE) + 1)
            if not _header_sequence_is_scope_confirmed_growth(
                _seq(self._BASE),
                _seq([*self._BASE[:pos], "new.h", *self._BASE[pos:]]),
                {"new.h"},
            )
        ]
        assert not rejected

    def test_agrees_with_the_oracle_over_every_permutation_and_placement(self):
        disagreeing = []
        for perm in itertools.permutations(self._BASE):
            for pos in range(len(perm) + 1):
                new = [*list(perm)[:pos], "new.h", *list(perm)[pos:]]
                got = _header_sequence_is_scope_confirmed_growth(
                    _seq(self._BASE), _seq(new), {"new.h"}
                )
                expected = self._oracle(self._BASE, new, {"new.h"})
                if got != expected:
                    disagreeing.append((new, got, expected))
        assert not disagreeing

    def test_the_oracle_is_not_vacuous(self):
        """A constant oracle would make both agreement tests above pass while
        asserting nothing."""
        assert self._oracle(["a.h"], ["a.h", "x.h"], {"x.h"})
        assert not self._oracle(["a.h", "b.h"], ["b.h", "x.h", "a.h"], {"x.h"})

    @pytest.mark.parametrize(
        "old_list,new_list,scope_new",
        [
            (["a.h", "b.h"], ["a.h", "x.h", "b.h"], set()),
            (["a.h", "b.h"], ["a.h", "b.h", "x.h"], set()),
            (["a.h", "b.h"], ["a.h", "x.h", "b.h"], None),
            (["a.h", "b.h", "c.h"], ["a.h", "c.h"], {"x.h"}),
            (["a.h", "b.h"], ["b.h", "a.h", "x.h"], {"x.h"}),
            (["a.h", "b.h"], ["a.h", "x.h", "b.h", "b.h"], {"x.h"}),
            (["<single-header>"], ["a.h", "x.h"], {"x.h"}),
        ],
    )
    def test_fails_closed_on_unusable_evidence(self, old_list, new_list, scope_new):
        assert not _header_sequence_is_scope_confirmed_growth(
            _seq(old_list), _seq(new_list), scope_new
        )


# ---------------------------------------------------------------------------
# checker.compare(): the disposition the whole change is about
# ---------------------------------------------------------------------------


class TestCompareTreatsTheAdditionAsEvolution:
    def _pair(self, tmp_path):
        return (
            _snap(tmp_path / "old", _OLD_HEADERS, "1.3"),
            _snap(tmp_path / "new", (*_OLD_HEADERS, "json.h"), "1.4"),
        )

    def test_a_verdict_is_produced(self, tmp_path):
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        assert compare(old, new).verdict is not None

    def test_no_assurance_reduction_is_recorded(self, tmp_path):
        """`comparability_assurance` is `None` only when no mismatch was found
        at all -- so this asserts the pair is comparable, not merely that the
        reduction is small."""
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        result = compare(old, new)
        assert result.comparability_assurance is None
        assert result.assurance is None

    def test_no_comparability_warning_is_emitted(self, tmp_path):
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        assert not any(
            "header sequence" in w or "profile_fingerprint" in w
            for w in compare(old, new).coverage_warnings
        )

    def test_an_appended_header_is_treated_identically(self, tmp_path):
        """Vacuity/consistency guard: the trailing-append case -- comparable at
        full assurance before this change and after it -- must now be
        indistinguishable from the interior one on every field the insertion
        used to move."""
        from abicheck.checker import compare

        old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
        inserted = compare(
            old, _snap(tmp_path / "mid", (*_OLD_HEADERS, "json.h"), "1.4")
        )
        appended = compare(
            old, _snap(tmp_path / "end", (*_OLD_HEADERS, "zzz.h"), "1.4")
        )
        assert (inserted.comparability_assurance, inserted.assurance) == (
            appended.comparability_assurance,
            appended.assurance,
        )


class TestTheAssuranceRollupReadsComplete:
    """The reported symptom: `analysis_assurance.status = "partial"` with
    "extraction contexts were not provably identical", which floors the exit
    code of a run configured with `assurance.require_complete`."""

    def _assurance(self, tmp_path, new_names):
        from abicheck.analysis_assurance import compute_analysis_assurance
        from abicheck.checker import compare

        old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
        new = _snap(tmp_path / "new", new_names, "1.4")
        return compute_analysis_assurance(compare(old, new), old, new)

    def test_no_comparability_note_is_added(self, tmp_path):
        """Asserted on the NOTE, not on `status`: these fixtures carry no
        binary, so an unchanged pair already reads `partial` on an unrelated
        axis ("public surface could not be resolved") -- `status` alone would
        prove nothing about this change either way."""
        notes = self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).notes
        assert not any("reduced assurance" in n for n in notes)
        assert not any("provably identical" in n for n in notes)

    def test_the_addition_changes_no_note_at_all(self, tmp_path):
        """Stronger than the absence above: the run with the added header must
        contribute no assurance note the identical run without it doesn't."""
        with_addition = self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).notes
        without = self._assurance(tmp_path, _OLD_HEADERS).notes
        assert [n for n in with_addition if n not in without] == []

    def test_the_note_machinery_is_not_vacuous(self, tmp_path):
        """Guard on the two tests above: `bounded_comparability_notes` must
        still produce the note it is there to produce, or their absence
        assertions would pass against a rollup that can no longer report
        anything."""
        from abicheck.analysis_assurance_comparability import (
            bounded_comparability_notes,
        )

        assert any(
            "reduced assurance" in n
            for n in bounded_comparability_notes(
                {"declaration": "unverified", "symbol": "trusted"}
            )
        )


# ---------------------------------------------------------------------------
# What must still refuse (the safety half)
# ---------------------------------------------------------------------------


def _contract_with(tmp_path, names, **profile):
    headers = _headers(tmp_path, names)
    return AbiSnapshot(
        library="libpvxs.so",
        version="1.4",
        contract=compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=headers,
            public_header_paths=headers,
            **profile,
        ),
    )


class TestAnotherDivergingProfileFieldStillRefuses:
    """The carve-out only ever removes `header_sequence` from the working set.

    A genuine compile-context difference riding alongside the addition
    therefore stays unexplained and stays a hard refusal -- the property that
    keeps this waiver from masking an ABI-affecting context mismatch. Every
    field below names one of the classes the reported requirement calls out:
    compiler target, language mode, ABI-affecting flags.
    """

    @pytest.mark.parametrize(
        "profile",
        [
            {"compiler_family": "clang"},
            {"compiler_version": "gcc 13.2.0"},
            {"language_standard": "c++20"},
            {"target_triple": "aarch64-linux-gnu"},
            {"pointer_width": 32},
            {"endianness": "big"},
            {"abi_dialect": "itanium-v2"},
            {"macro_ops": (("D", "NDEBUG"),)},
            {"pass_through_flags": ("-include forced.h",)},
        ],
    )
    @pytest.mark.parametrize("added", ["aaa.h", "json.h", "zzz.h"])
    def test_it_refuses_at_every_insertion_position(self, tmp_path, profile, added):
        old = _contract_with(tmp_path / "old", _OLD_HEADERS)
        new = _contract_with(tmp_path / "new", (*_OLD_HEADERS, added), **profile)
        with pytest.raises(ProfileMismatchError):
            check_contracts_comparable(old, new)

    def test_the_same_pair_without_the_extra_field_is_comparable(self, tmp_path):
        """Vacuity guard: the refusals above must come from the extra profile
        field, not from the header addition the fix is supposed to waive."""
        old = _contract_with(tmp_path / "old", _OLD_HEADERS)
        new = _contract_with(tmp_path / "new", (*_OLD_HEADERS, "json.h"))
        assert check_contracts_comparable(old, new) is None


def test_an_unconfirmed_sequence_entry_still_refuses(tmp_path):
    """A header fed to the L2 frontend on the new side only, while the declared
    public surface is unchanged, is not scope-confirmed growth: the old
    snapshot never parsed its content, so a removal inside it would be
    invisible. The shape looks identical to the waived one."""
    old_headers = _headers(tmp_path / "old", _OLD_HEADERS)
    new_root = tmp_path / "new"
    new_declared = _headers(new_root, (*_OLD_HEADERS, "json.h"))
    old = AbiSnapshot(
        library="libpvxs.so",
        version="1.3",
        contract=compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=old_headers,
            # json.h is already public on the old side -- only the L2 frontend
            # never saw it, so scope_fields["headers"] does not grow.
            public_header_paths=_headers(tmp_path / "old", (*_OLD_HEADERS, "json.h")),
        ),
    )
    new = AbiSnapshot(
        library="libpvxs.so",
        version="1.4",
        contract=compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=new_declared,
            public_header_paths=new_declared,
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)

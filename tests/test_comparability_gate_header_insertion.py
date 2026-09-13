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

"""A declared-header INSERTION bounds a comparison; it never refuses one.

Bug class: an *incidental* extraction-ordering fact was treated as a fatal
comparability contract. ``header_sequence`` records declared-header order
because the aggregate driver TU parses headers sequentially, and the only
waiver was a strict trailing append. Directory-discovered headers arrive
sorted, so adding one public header whose name sorts into the middle
(``data.h, log.h`` -> ``data.h, json.h, log.h``) produced
``profile_fingerprint mismatch; differing fields: header_sequence`` and NO
verdict at all -- from an ordinary public-header addition.

The invariant these tests state is not "the json.h case now passes". It is:

  *For any* declared-header growth that preserves every existing header's
  relative order and adds only headers the scope fingerprint independently
  confirms as new, the pair stays comparable, with the residual risk
  recorded as a dimension-scoped assurance reduction -- while *any* growth
  that reorders an existing header, adds an unconfirmed header, or rides
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
    ComparabilityMismatch,
    check_contracts_comparable,
    compute_extraction_contract,
    dimension_assurance,
)
from abicheck.comparability_sequences import (
    _header_sequence_is_additive_reorder_free,
    _header_sequence_is_interior_insertion,
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


def test_middle_sorting_public_header_addition_is_bounded_not_refused(tmp_path):
    """The reported repro: one added public header sorting into the middle."""
    old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
    new = _snap(tmp_path / "new", (*_OLD_HEADERS, "json.h"), "1.4")

    # The order really is an insertion, not an append -- i.e. this pair is the
    # shape the pre-existing trailing-append waiver declines.
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

    result = check_contracts_comparable(old, new)
    assert isinstance(result, ComparabilityMismatch)
    assert result.fatal is False  # returned, never raised
    assert result.dimensions == frozenset({"declaration", "layout"})
    assert "json.h" in result.reason
    # The symbol dimension -- the binary's own exported-symbol identity -- is
    # untouched by a header insertion and must stay trusted; that conclusion
    # being discarded wholesale was half the cost of the refusal.
    assert dimension_assurance(result)["symbol"] == "trusted"
    assert dimension_assurance(result)["declaration"] == "unverified"


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
    (``zzz.h``, the trailing one) was comparable and the other four refused --
    a difference with no bearing on whether the library's ABI changed."""
    old = _snap(tmp_path / f"old{added}", _OLD_HEADERS, "1.3")
    new = _snap(tmp_path / f"new{added}", (*_OLD_HEADERS, added), "1.4")
    result = check_contracts_comparable(old, new)
    assert result is None or result.fatal is False


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
        """The two predicates must never both accept the same pair: one grants
        full assurance, the other a reduction, and an overlap would make which
        one a pair gets depend on evaluation order."""
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
# checker.compare(): the disposition the whole change is about
# ---------------------------------------------------------------------------


class TestCompareRecordsRatherThanRefuses:
    def _pair(self, tmp_path):
        return (
            _snap(tmp_path / "old", _OLD_HEADERS, "1.3"),
            _snap(tmp_path / "new", (*_OLD_HEADERS, "json.h"), "1.4"),
        )

    def test_a_verdict_is_produced(self, tmp_path):
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        assert compare(old, new).verdict is not None

    def test_assurance_none_is_not_stamped(self, tmp_path):
        """``assurance: "none"`` means "forced through a refusal with
        --diagnostic-comparison, do not trust this". Nothing was forced here,
        so stamping it would misreport a bounded result as an override."""
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        assert compare(old, new).assurance is None

    def test_the_reduction_is_recorded_per_dimension(self, tmp_path):
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        assurance = compare(old, new).comparability_assurance
        assert assurance == {
            "declaration": "unverified",
            "layout": "unverified",
            "runtime": "trusted",
            "source": "trusted",
            "symbol": "trusted",
        }

    def test_the_reason_is_disclosed_not_swallowed(self, tmp_path):
        """ "Record before disposing": a consumer must be able to see WHY
        assurance dropped, not just that it did."""
        from abicheck.checker import compare

        old, new = self._pair(tmp_path)
        warnings = compare(old, new).coverage_warnings
        assert any("inserted into the declared header sequence" in w for w in warnings)

    def test_an_unchanged_pair_records_nothing(self, tmp_path):
        """Vacuity guard on the three assertions above: an identical pair must
        produce no mismatch, no dimension breakdown and no warning, or they
        would pass for reasons unrelated to the insertion."""
        from abicheck.checker import compare

        old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
        same = _snap(tmp_path / "same", _OLD_HEADERS, "1.4")
        result = compare(old, same)
        assert result.comparability_assurance is None
        assert not any(
            "declared header sequence" in w for w in result.coverage_warnings
        )


# ---------------------------------------------------------------------------
# The bounded path's own boundaries (Codex review, PR #1274)
# ---------------------------------------------------------------------------


class TestOnlyAHeaderSequenceDivergenceIsBounded:
    """`unexplained` must be EXACTLY `{"header_sequence"}`.

    `_unexplained_profile_fields` has already removed every field a carve-out
    verified, so anything still in that set is by construction a divergence
    NO carve-out could corroborate. An `include_sequence` surviving there is a
    changed `-I` topology (two include roots swapped, say), and include-search
    order decides which dependency header an `#include` resolves to — a
    different, unbounded hazard from the declared-header insertion this branch
    reasons about, and one nothing here corroborates.

    Tested at the guard itself rather than through a fixture: every other
    precondition is held identically to the bounded case (real contracts from
    the real insertion pair), so `unexplained` is the only variable.
    """

    def _contracts(self, tmp_path):
        old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
        new = _snap(tmp_path / "new", (*_OLD_HEADERS, "json.h"), "1.4")
        return old.contract, new.contract

    def _call(self, tmp_path, unexplained):
        from abicheck.comparability_profile import (
            _declared_header_insertion_mismatch,
        )

        old_contract, new_contract = self._contracts(tmp_path)
        return _declared_header_insertion_mismatch(
            old_contract,
            new_contract,
            unknown_differing=set(),
            differing=set(unexplained),
            unexplained=set(unexplained),
        )

    def test_header_sequence_alone_is_bounded(self, tmp_path):
        """Vacuity guard for the whole class: the accepted set really is
        accepted here, so the rejections below mean something."""
        got = self._call(tmp_path, {"header_sequence"})
        assert got is not None and got.fatal is False

    @pytest.mark.parametrize(
        "unexplained",
        [
            # The reported P1: a header insertion riding alongside an
            # include-topology change no carve-out could explain.
            {"header_sequence", "include_sequence"},
            # An include_sequence-only divergence carries no insertion at all.
            {"include_sequence"},
            # Any other profile field alongside it is a genuine, uncorroborated
            # compile-context difference.
            {"header_sequence", "compiler_family"},
            {"header_sequence", "language_standard"},
            {"header_sequence", "macro_ops"},
            {"header_sequence", "target_triple"},
            {"header_sequence", "include_sequence", "macro_ops"},
            # Nothing unexplained at all is not this function's case.
            set(),
        ],
    )
    def test_anything_else_stays_fatal(self, tmp_path, unexplained):
        assert self._call(tmp_path, unexplained) is None

    def test_an_unrecognized_field_is_never_bounded(self, tmp_path):
        """Fail-closed: a field this build does not recognize, and an
        absent/malformed `profile_fields` (empty `differing`), are the two
        cases `_profile_mismatch_reason` answers first and neither has a
        verified shape to reason from."""
        assert (
            self._call_raw(
                tmp_path, unknown={"future_field"}, differing={"header_sequence"}
            )
            is None
        )
        assert self._call_raw(tmp_path, unknown=set(), differing=set()) is None

    def _call_raw(self, tmp_path, unknown, differing):
        from abicheck.comparability_profile import (
            _declared_header_insertion_mismatch,
        )

        old_contract, new_contract = self._contracts(tmp_path)
        return _declared_header_insertion_mismatch(
            old_contract,
            new_contract,
            unknown_differing=unknown,
            differing=differing,
            unexplained={"header_sequence"},
        )


class TestBoundedMismatchReachesTheAssuranceRollup:
    """`compute_analysis_assurance` must not report `complete` for a run whose
    own `comparability_assurance` says a dimension is unverified.

    It previously special-cased only `assurance == "none"`, so the two
    assurance fields of one report contradicted each other and
    `assurance.require_complete` declined to gate a run that is, by its own
    account, not complete.
    """

    def _assurance(self, tmp_path, new_names):
        from abicheck.analysis_assurance import compute_analysis_assurance
        from abicheck.checker import compare

        old = _snap(tmp_path / "old", _OLD_HEADERS, "1.3")
        new = _snap(tmp_path / "new", new_names, "1.4")
        return compute_analysis_assurance(compare(old, new), old, new)

    def test_an_insertion_reports_partial(self, tmp_path):
        assert self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).status == "partial"

    def test_the_note_names_the_unverified_dimensions(self, tmp_path):
        notes = self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).notes
        assert any(
            "declaration" in n and "layout" in n and "reduced assurance" in n
            for n in notes
        )

    def test_it_is_partial_not_not_comparable(self, tmp_path):
        """A verdict WAS produced and every other axis was genuinely computed;
        only some dimensions carry reduced assurance. Collapsing that to
        `not_comparable` would discard the axes this run did establish."""
        assert self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).status != (
            "not_comparable"
        )

    def test_an_unchanged_pair_carries_no_comparability_note(self, tmp_path):
        """Vacuity guard, and it earned its place: these fixtures carry no
        binary, so an UNCHANGED pair already reads `partial` on an unrelated
        axis ("public surface could not be resolved"). `status == "partial"`
        alone therefore proves nothing about this change — the note is what
        distinguishes the two, so it is the note this class asserts on, and
        the note must be absent when there is no insertion."""
        assurance = self._assurance(tmp_path, _OLD_HEADERS)
        assert not any("reduced assurance" in n for n in assurance.notes)

    def test_the_insertion_is_what_adds_the_note(self, tmp_path):
        """The two runs differ by exactly the inserted header, so the note
        appearing in one and not the other attributes it to the insertion
        rather than to any axis both runs share."""
        with_insertion = self._assurance(tmp_path, (*_OLD_HEADERS, "json.h")).notes
        without = self._assurance(tmp_path, _OLD_HEADERS).notes
        added = [n for n in with_insertion if n not in without]
        assert any("reduced assurance" in n for n in added)

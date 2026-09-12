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

"""The export axis: what ``compare/export_transition.py`` reports, and when.

The detector half of the ``Visibility`` split;
``tests/test_surface_fact_split.py`` keeps the fact model, its accessors and
the legacy bridge. Split out when that file reached the 1200-line cap for a
new test file, along the boundary the code already has: these classes drive
``checker.compare`` and assert *findings*, where its siblings assert what a
fact means.

Bug class: ``evidence.export_presence_as_declaration_presence``
(``tests/regressions/manifest_evidence.py``). These classes carry its
direction and combination axes — a lost export is never a source removal, a
gained one is never dropped, and neither direction is ever rendered out of
unknown evidence.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import Verdict, compare
from abicheck.extract.surface_fact_producers import (
    debug_info_surface_facts,
)
from abicheck.model import (
    AbiSnapshot,
    Fact,
    FactStatus,
    Function,
    Variable,
    in_source_declaration_index,
    surface_fact_summary,
)
from abicheck.model.change_catalog.kinds import ChangeKind

#: Every way a producer can leave one of the three facts. "Unknown" is
#: represented by all four of its real statuses, not just one, so an
#: invariant cannot accidentally hold for ``NOT_COLLECTED`` alone.
_TRUE = Fact.present(True)
_FALSE = Fact.present(False)
_UNKNOWNS = (
    None,
    Fact.not_collected("no headers parsed"),
    Fact.unsupported("producer cannot answer"),
    Fact.failed("extractor errored"),
    Fact.not_applicable(),
)
_STATES = (_TRUE, _FALSE, *_UNKNOWNS)


def _fn(name: str = "foo", mangled: str = "_Z3foov", **facts: object) -> Function:
    return Function(name=name, mangled=mangled, return_type="void", **facts)  # type: ignore[arg-type]


def _snap(version: str, *functions: Function, **kw: object) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=list(functions),
        from_headers=True,
        **kw,  # type: ignore[arg-type]
    )


def _expected_tri(fact: Fact[bool] | None) -> str:
    """The oracle: derived from the fact this test *constructed*, never from
    the module under test's own predicates."""
    if fact is None:
        return "unknown"
    if fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return "true" if fact.value is True else "false"
    return "unknown"


class TestExportLostWhileDeclarationRemains:
    """(1) An export lost while the declaration survives is never reported
    as a source-declaration removal."""

    @pytest.mark.parametrize(
        ("old_exported", "new_exported"),
        [
            (_TRUE, _FALSE),
            (_TRUE, Fact.not_collected("stripped binary")),
            (Fact.partial(True), _FALSE),
        ],
    )
    def test_no_removal_when_the_declaration_is_unchanged(
        self, old_exported: Fact[bool], new_exported: Fact[bool]
    ) -> None:
        declared = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
        }
        old = _snap("1.0", _fn(**declared, binary_exported_fact=old_exported))
        new = _snap("2.0", _fn(**declared, binary_exported_fact=new_exported))
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.FUNC_REMOVED not in kinds
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in kinds
        assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT not in kinds

    def test_the_export_change_is_still_reported_on_its_own_axis(self) -> None:
        """Not reporting a *removal* is not the same as ignoring the change:
        a disappearing export can still break an already-linked consumer."""
        declared = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
        }
        old = _snap("1.0", _fn(**declared, binary_exported_fact=_TRUE))
        new = _snap("2.0", _fn(**declared, binary_exported_fact=_FALSE))
        changes = compare(old, new).changes
        visibility_changes = [
            c for c in changes if c.kind is ChangeKind.FUNC_VISIBILITY_CHANGED
        ]
        assert visibility_changes, [c.kind for c in changes]
        assert visibility_changes[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "false",
        }

    @pytest.mark.parametrize("exported", [_TRUE, _FALSE, *_UNKNOWNS])
    def test_a_real_declaration_removal_is_still_reported(
        self, exported: Fact[bool] | None
    ) -> None:
        """The fix must not silence the finding it is meant to keep honest:
        whatever the export evidence says, a declaration that is gone from
        the new side is still a removal."""
        old = _snap(
            "1.0",
            _fn(
                declared_in_headers_fact=_TRUE,
                in_public_contract_fact=_TRUE,
                binary_exported_fact=exported,
            ),
            _fn(name="keep", mangled="_Z4keepv"),
        )
        new = _snap("2.0", _fn(name="keep", mangled="_Z4keepv"))
        kinds = {c.kind for c in compare(old, new).changes}
        assert kinds & {ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY}


class TestExportLossIsDetectedForEveryDeclarationKind:
    """The export axis is not a function-only axis.

    Registered because the first revision of this split implemented the
    transition detector for functions only: a variable whose export vanished
    while its declaration stayed then matched on both sides and produced *no
    finding at all*, trading one wrong finding (a source removal) for a
    missing one (a real binary break). Codex review, P1.
    """

    @staticmethod
    def _decl(owner: str, exported: Fact[bool] | None) -> Function | Variable:
        """One declared, promised declaration of either kind, whose only
        variable is the export fact."""
        facts: dict[str, object] = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": exported,
        }
        if owner == "variable":
            return Variable(
                name="g_registry",
                mangled="_Z10g_registry",
                type="int",
                **facts,  # type: ignore[arg-type]
            )
        return _fn(**facts)

    @staticmethod
    def _pair(owner: str, old_decl: object, new_decl: object) -> tuple[object, object]:
        old_snap, new_snap = _snap("1.0"), _snap("2.0")
        for snap, decl in ((old_snap, old_decl), (new_snap, new_decl)):
            if owner == "variable":
                snap.variables = [decl]  # type: ignore[list-item]
            else:
                snap.functions = [decl]  # type: ignore[list-item]
        return old_snap, new_snap

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_VISIBILITY_CHANGED),
            ("variable", ChangeKind.VAR_VISIBILITY_CHANGED),
        ],
    )
    def test_a_lost_export_on_a_surviving_declaration_is_reported(
        self, owner: str, kind: ChangeKind
    ) -> None:
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _TRUE), self._decl(owner, _FALSE)
        )
        changes = compare(old_snap, new_snap).changes  # type: ignore[arg-type]
        kinds = {c.kind for c in changes}
        assert kind in kinds, [c.kind for c in changes]
        # And never as a removal: the declaration did not go anywhere.
        assert not kinds & {
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_REMOVED_ELF_ONLY,
            ChangeKind.VAR_REMOVED,
        }
        stamped = [c for c in changes if c.kind is kind]
        assert stamped[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "false",
        }

    @pytest.mark.parametrize("unknown", [f for f in _UNKNOWNS if f is not None])
    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_unknown_new_side_evidence_is_never_an_observed_transition(
        self, owner: str, unknown: Fact[bool]
    ) -> None:
        """ "Exported before, unknown now" is a gap in this run's evidence, not
        an observed transition, and must never be rendered as one."""
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _TRUE), self._decl(owner, unknown)
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
        assert ChangeKind.FUNC_VISIBILITY_CHANGED not in kinds
        assert ChangeKind.VAR_VISIBILITY_CHANGED not in kinds


class TestEvidenceOnTheSideThatHasIt:
    """A declaration with no symbol *by construction* must not be judged by
    its own export fact.

    Three findings from one review round shared this root: once the export
    fact became honest (a `= delete`d function has no symbol, so it is a
    confirmed ``False``), every filter that asked only the *new* side stopped
    admitting exactly the declarations it existed to report.
    """

    @staticmethod
    def _deleted_pair() -> tuple[object, object]:
        old = _snap(
            "1.0",
            _fn(**debug_info_surface_facts(exported=True)),  # type: ignore[arg-type]
        )
        new = _snap(
            "2.0",
            _fn(
                is_deleted=True,
                deleted_from_dwarf=True,
                **debug_info_surface_facts(exported=False),  # type: ignore[arg-type]
            ),
        )
        return old, new

    def test_a_deleted_dwarf_function_is_still_reported_as_deleted(self) -> None:
        old, new = self._deleted_pair()
        kinds = [c.kind for c in compare(old, new).changes]  # type: ignore[arg-type]
        assert ChangeKind.FUNC_DELETED_DWARF in kinds, kinds

    def test_a_deleted_function_is_reported_exactly_once(self) -> None:
        """Not as a deletion *and* a visibility change: the removal path
        defers to the deletion detector, and both must agree on when."""
        old, new = self._deleted_pair()
        kinds = [c.kind for c in compare(old, new).changes]  # type: ignore[arg-type]
        assert kinds.count(ChangeKind.FUNC_DELETED_DWARF) == 1
        assert ChangeKind.FUNC_VISIBILITY_CHANGED not in kinds
        assert ChangeKind.FUNC_REMOVED not in kinds

    def test_the_legacy_path_reports_the_same_single_finding(self) -> None:
        """The oracle for the two assertions above: a pre-split snapshot pair,
        whose behaviour this change must not alter."""
        old = _snap("1.0", _fn())
        new = _snap("2.0", _fn(is_deleted=True, deleted_from_dwarf=True))
        assert [c.kind for c in compare(old, new).changes] == [
            ChangeKind.FUNC_DELETED_DWARF
        ]

    def test_unexported_debug_only_records_stay_out_of_source_indexes(self) -> None:
        """A debug-info-only record with neither header nor export evidence is
        not a source declaration -- admitting it let two unexported internal
        functions report an inline-namespace version bump."""
        unexported = _fn(**debug_info_surface_facts(exported=False))  # type: ignore[arg-type]
        summary = surface_fact_summary(unexported)
        assert summary["declared_in_headers"] == "unknown"
        assert summary["in_public_contract"] == "unknown"
        assert not in_source_declaration_index(unexported)
        # ... while the exported sibling, which legacy admitted, still is.
        assert in_source_declaration_index(
            _fn(**debug_info_surface_facts(exported=True))  # type: ignore[arg-type]
        )

    def test_a_mangling_churn_heuristic_counts_only_confirmed_exports(self) -> None:
        """`GLIBCXX_DUAL_ABI_FLIP_DETECTED` diagnoses a mangled-symbol change
        across two export tables, so a promised-but-unexported declaration has
        no symbol in either table to have churned."""
        promised = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": _FALSE,
        }
        old = _snap(
            "1.0",
            *[
                _fn(
                    name=f"f{i}",
                    mangled=f"_ZNSt7__cxx1112basic_stringE{i}",
                    **promised,  # type: ignore[arg-type]
                )
                for i in range(6)
            ],
        )
        new = _snap(
            "2.0",
            *[
                _fn(
                    name=f"f{i}",
                    mangled=f"_ZNSt12basic_stringE{i}",
                    **promised,  # type: ignore[arg-type]
                )
                for i in range(6)
            ],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.GLIBCXX_DUAL_ABI_FLIP_DETECTED not in kinds


class TestExportGainedIsRecordedNotDropped:
    """The export axis is not a one-way axis.

    Registered because the first revision of this split implemented the
    transition detector for export *loss* only. Before the split, a
    promised-but-unexported declaration failed the old
    ``visibility in (PUBLIC, ELF_ONLY)`` filter outright, so the old side was
    absent from the public index, the pair never matched, and a newly
    exported declaration was reported as ``FUNC_ADDED``. Once the
    declaration keeps its place on both sides the pair *matches* -- and with
    no gain-side detector the run reported nothing at all for a version
    script that newly exports an existing declaration. Codex review, P2.

    An addition that vanishes is what "record before disposing" forbids; a
    silent diff is a worse outcome than the wrong finding this split set out
    to remove.
    """

    @staticmethod
    def _decl(owner: str, exported: Fact[bool] | None) -> Function | Variable:
        facts: dict[str, object] = {
            "declared_in_headers_fact": _TRUE,
            "in_public_contract_fact": _TRUE,
            "binary_exported_fact": exported,
        }
        if owner == "variable":
            return Variable(
                name="g_registry",
                mangled="_Z10g_registry",
                type="int",
                **facts,  # type: ignore[arg-type]
            )
        return _fn(**facts)

    @staticmethod
    def _pair(owner: str, old_decl: object, new_decl: object) -> tuple[object, object]:
        old_snap, new_snap = _snap("1.0"), _snap("2.0")
        for snap, decl in ((old_snap, old_decl), (new_snap, new_decl)):
            if owner == "variable":
                snap.variables = [decl]  # type: ignore[list-item]
            else:
                snap.functions = [decl]  # type: ignore[list-item]
        return old_snap, new_snap

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_EXPORT_ADDED),
            ("variable", ChangeKind.VAR_EXPORT_ADDED),
        ],
    )
    def test_a_gained_export_on_an_existing_declaration_is_reported(
        self, owner: str, kind: ChangeKind
    ) -> None:
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _FALSE), self._decl(owner, _TRUE)
        )
        result = compare(old_snap, new_snap)  # type: ignore[arg-type]
        kinds = {c.kind for c in result.changes}
        assert kind in kinds, [c.kind for c in result.changes]
        # The whole point: the run is not silent.
        assert result.changes
        stamped = [c for c in result.changes if c.kind is kind]
        assert stamped[0].surface_facts == {
            "declared_in_headers": "true",
            "in_public_contract": "true",
            "binary_exported": "true",
        }

    @pytest.mark.parametrize(
        ("owner", "kind"),
        [
            ("function", ChangeKind.FUNC_EXPORT_ADDED),
            ("variable", ChangeKind.VAR_EXPORT_ADDED),
        ],
    )
    def test_a_gained_export_is_compatible_not_breaking(
        self, owner: str, kind: ChangeKind
    ) -> None:
        """Recording the change must not manufacture a break out of good
        news: gaining an export takes nothing away from any consumer."""
        from abicheck.checker_policy import ADDITION_KINDS, COMPATIBLE_KINDS

        assert kind in COMPATIBLE_KINDS
        assert kind in ADDITION_KINDS
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, _FALSE), self._decl(owner, _TRUE)
        )
        result = compare(old_snap, new_snap)  # type: ignore[arg-type]
        assert result.verdict is not Verdict.BREAKING
        assert result.verdict is not Verdict.API_BREAK

    @pytest.mark.parametrize("unknown", [f for f in _UNKNOWNS if f is not None])
    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_unknown_old_side_evidence_is_never_an_observed_gain(
        self, owner: str, unknown: Fact[bool]
    ) -> None:
        """The exact mirror of the loss side's own rule: "unknown before,
        exported now" is a gap in this run's evidence, not an observed
        addition, across every real ``FactStatus`` that spells unknown."""
        old_snap, new_snap = self._pair(
            owner, self._decl(owner, unknown), self._decl(owner, _TRUE)
        )
        kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
        assert not kinds & {
            ChangeKind.FUNC_EXPORT_ADDED,
            ChangeKind.VAR_EXPORT_ADDED,
        }

    @pytest.mark.parametrize("owner", ["function", "variable"])
    def test_the_two_directions_are_exclusive_and_exhaustive(self, owner: str) -> None:
        """Across the full confirmed×confirmed grid, exactly one of the two
        transition findings fires, and only for a real transition.

        The oracle is the pair of booleans this test itself constructed, not
        either detector's own predicate.
        """
        gain = {ChangeKind.FUNC_EXPORT_ADDED, ChangeKind.VAR_EXPORT_ADDED}
        loss = {ChangeKind.FUNC_VISIBILITY_CHANGED, ChangeKind.VAR_VISIBILITY_CHANGED}
        for old_exported, new_exported in itertools.product((True, False), repeat=2):
            old_snap, new_snap = self._pair(
                owner,
                self._decl(owner, Fact.present(old_exported)),
                self._decl(owner, Fact.present(new_exported)),
            )
            kinds = {c.kind for c in compare(old_snap, new_snap).changes}  # type: ignore[arg-type]
            expect_gain = (not old_exported) and new_exported
            expect_loss = old_exported and not new_exported
            assert bool(kinds & gain) is expect_gain, (old_exported, new_exported)
            assert bool(kinds & loss) is expect_loss, (old_exported, new_exported)

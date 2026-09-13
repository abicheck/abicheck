# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``func_added_elf_only``: an addition visible only in the export table.

A header-aware snapshot builds ``function_map`` from the header AST, so an
exported symbol that no public header declares never enters it -- and the
old/new function diff, which reads that map, could not see such a symbol
appear. The same release reported ``Additions (1)`` at ``--depth binary`` and
``Additions (0)`` with ``-H``; the export was reachable only as an
``exported_not_public`` hygiene finding, which is a different claim and
drives neither the addition count nor the MINOR bump.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.compare.undeclared_exports import _diff_undeclared_exports
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function


def _snap(
    exports: list[str],
    *,
    declared: list[str] | None = None,
    elf_only: bool = False,
    with_table: bool = True,
    data_exports: list[str] | None = None,
) -> AbiSnapshot:
    """A snapshot whose export table holds *exports* and whose header-derived
    function map holds *declared* (by default: nothing, the undeclared case).
    """
    elf = (
        ElfMetadata(
            # `machine` is what says the table was *captured*, and the
            # detector keys on it: the symbol list is a plain list that a
            # default/parse-failed `ElfMetadata()` and a real zero-export
            # library both leave empty, so emptiness cannot tell them apart.
            # A real parsed ELF always sets `machine`; a fixture that omits
            # it is not standing in for one.
            machine="x86_64",
            symbols=[
                ElfSymbol(name=n, visibility="default", sym_type="func")
                for n in exports
            ]
            + [
                ElfSymbol(name=n, visibility="default", sym_type="object")
                for n in (data_exports or [])
            ],
        )
        if with_table
        else ElfMetadata()
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(name=n, mangled=n, return_type="int", params=[])
            for n in (declared or [])
        ],
        elf=elf,
        elf_only_mode=elf_only,
    )


class TestGainedUndeclaredExportIsAnAddition:
    def test_new_undeclared_export_is_reported(self) -> None:
        changes = _diff_undeclared_exports(_snap(["keep"]), _snap(["keep", "gained"]))
        assert [(c.kind, c.symbol) for c in changes] == [
            (ChangeKind.FUNC_ADDED_ELF_ONLY, "gained")
        ]

    def test_it_is_classified_as_a_compatible_addition(self) -> None:
        """The point of the kind: it has to reach the addition count and the
        MINOR-bump recommendation, which a RISK-category hygiene finding does
        not."""
        from abicheck.change_registry import REGISTRY
        from abicheck.checker_policy import Verdict

        meta = REGISTRY.get("func_added_elf_only")
        assert meta is not None
        assert meta.default_verdict == Verdict.COMPATIBLE
        assert meta.is_addition

    def test_unchanged_export_sets_report_nothing(self) -> None:
        assert _diff_undeclared_exports(_snap(["a", "b"]), _snap(["a", "b"])) == []

    def test_a_lost_export_is_not_reported(self) -> None:
        """Deliberately one-directional. Asserting a *removal* from
        export-table evidence alone, for a symbol the headers never promised,
        is the kind of unproven finding the vision forbids -- and
        ``exported_not_public``'s own RESOLVED state already shows it."""
        assert _diff_undeclared_exports(_snap(["a", "gone"]), _snap(["a"])) == []


class TestItDoesNotStealTheOrdinaryDiffsWork:
    """Every guard that keeps this from double-reporting what another
    detector already covers. These matter more than the positive case: a
    duplicated addition inflates the count on every ordinary release, which
    is a far more common input than the undeclared-export case itself.
    """

    def test_a_declared_symbol_is_left_to_the_function_diff(self) -> None:
        """It is in ``function_map``, so ``FUNC_ADDED`` covers it."""
        old = _snap(["keep"], declared=["keep"])
        new = _snap(["keep", "gained"], declared=["keep", "gained"])
        assert _diff_undeclared_exports(old, new) == []

    def test_a_symbol_declared_only_on_the_old_side_is_not_an_addition(self) -> None:
        """A declaration that disappeared is a declaration change, not a new
        export. Consulting both sides' maps -- not just the new one -- is what
        stops it arriving here as an addition."""
        old = _snap(["keep"], declared=["keep", "gained"])
        new = _snap(["keep", "gained"])
        assert _diff_undeclared_exports(old, new) == []

    def test_headerless_comparison_is_left_alone(self) -> None:
        """``elf_only_mode`` snapshots already carry export-only records in
        ``function_map`` (``dumper_elf_fallback``), so the ordinary diff
        reports these. Emitting here too would double-report every added
        symbol in every binary-depth run."""
        old = _snap(["keep"], elf_only=True)
        new = _snap(["keep", "gained"], elf_only=True)
        assert _diff_undeclared_exports(old, new) == []

    def test_only_the_new_sides_mode_decides(self) -> None:
        """The guard keys off NEW alone, and that asymmetry is load-bearing.

        An *addition* is a symbol in NEW and not in OLD, so the ordinary diff
        can only name it when it reached NEW's ``function_map`` -- which
        ``dumper_elf_fallback`` does only for a headerless NEW. In the mixed
        shape (ELF-only OLD, header-aware NEW) an undeclared new export is in
        neither map, so suppressing here as well would lose the addition
        entirely with nothing else reporting it. Suppressing on OLD's mode was
        the original defect (Codex review); this states the rule that replaced
        it, in both directions, so neither half can regress alone.
        """
        elf_only_old = _snap(["keep"], elf_only=True)
        header_aware_new = _snap(["keep", "gained"])
        gained = _diff_undeclared_exports(elf_only_old, header_aware_new)
        assert [c.symbol for c in gained] == ["gained"]

        # The mirror: a headerless NEW is the ordinary diff's business, and
        # OLD being header-aware does not change that.
        header_aware_old = _snap(["keep"])
        elf_only_new = _snap(["keep", "gained"], elf_only=True)
        assert _diff_undeclared_exports(header_aware_old, elf_only_new) == []


class TestMissingEvidenceNeverFabricates:
    """ "Not observed" is not "empty" -- treating an uncaptured export table as
    an empty one would report every export on the other side as newly added.
    """

    @pytest.mark.parametrize("side", ["old", "new"])
    def test_an_uncaptured_export_table_reports_nothing(self, side: str) -> None:
        full = _snap(["a", "b", "c"])
        empty = _snap([], with_table=False)
        old, new = (empty, full) if side == "old" else (full, empty)
        assert _diff_undeclared_exports(old, new) == []

    def test_a_missing_elf_section_reports_nothing(self) -> None:
        no_elf = AbiSnapshot(library="libfoo.so", version="1.0")
        assert _diff_undeclared_exports(no_elf, _snap(["a"])) == []
        assert _diff_undeclared_exports(_snap(["a"]), no_elf) == []


class TestIdentityAndDedupRegistration:
    """The registration steps a new ChangeKind has to complete, checked
    rather than assumed.

    PR #753 shipped a kind with three registry entries silently omitted and
    #759 had to add them hours later: a *missing* entry produced no failure
    anywhere. Each of these is one of those entries.
    """

    def test_it_shares_the_addition_dedup_category(self) -> None:
        from abicheck.finding_identity import _EQUIVALENT_CHANGE_CATEGORIES

        assert (
            _EQUIVALENT_CHANGE_CATEGORIES["func_added_elf_only"]
            == _EQUIVALENT_CHANGE_CATEGORIES["func_added"]
        )

    def test_two_different_added_symbols_stay_distinct(self) -> None:
        """The must-not-merge half of the identity claim.

        Mapping ``func_added_elf_only`` into ``func_added``'s equivalence
        category makes "these two collapse" true; it says nothing about
        "these two stay distinct", and an identity that collapsed *every*
        addition would satisfy the first claim completely. Two genuinely
        different added exports must resolve to different identities.
        """
        from abicheck.finding_identity import resolve_change_identity

        a = Change(
            kind=ChangeKind.FUNC_ADDED_ELF_ONLY, symbol="added_a", description=""
        )
        b = Change(
            kind=ChangeKind.FUNC_ADDED_ELF_ONLY, symbol="added_b", description=""
        )
        assert resolve_change_identity(a) != resolve_change_identity(b)

    def test_the_same_symbol_collapses_across_the_two_addition_tiers(self) -> None:
        """And the must-merge half, stated on real identities rather than on
        the category table alone: the same symbol reported by the
        header-aware and export-only tiers is one addition, not two."""
        from abicheck.finding_identity import resolve_change_identity

        header_tier = Change(
            kind=ChangeKind.FUNC_ADDED, symbol="gained", description=""
        )
        export_tier = Change(
            kind=ChangeKind.FUNC_ADDED_ELF_ONLY, symbol="gained", description=""
        )
        assert resolve_change_identity(header_tier) == resolve_change_identity(
            export_tier
        )

    def test_it_is_in_the_cross_detector_dedup_table(self) -> None:
        """The sibling table in ``diff_filtering`` -- a separate map that has
        to agree with the identity one, and the exact shape of "listed in one
        place, missed in another"."""
        import abicheck.diff_filtering as df

        src = df._deduplicate_cross_detector.__doc__ or ""
        assert "FUNC_ADDED_ELF_ONLY" in src

    def test_it_is_treated_as_always_independent(self) -> None:
        from abicheck.diff_filtering import _ALWAYS_INDEPENDENT_KINDS

        assert ChangeKind.FUNC_ADDED_ELF_ONLY in _ALWAYS_INDEPENDENT_KINDS
        # Vacuity guard: its sibling is there too, so this is not asserting a
        # set that happens to contain everything.
        assert ChangeKind.FUNC_ADDED in _ALWAYS_INDEPENDENT_KINDS

    def test_the_detector_is_auto_discovered(self) -> None:
        """``diff_*`` modules are discovered by name, so the module had to be
        named that way for its detector to register at all."""
        from abicheck.detector_registry import registry

        registry.ensure_loaded()
        assert "undeclared_exports" in set(registry.detector_names)


class TestTheDetectorIsActuallyRegistered:
    """Registration is the one failure mode a move makes silent.

    Detector discovery globs ``abicheck.diff_*`` at the top level; this
    detector's owner is ``abicheck/compare/``, so it reaches the registry only
    through ``detector_registry._EXTRA_DETECTOR_MODULES``. Drop that entry and
    nothing raises anywhere -- the module never imports, the decorator never
    runs, and the detector stops producing findings while every direct unit
    test in this file keeps passing, because they all call the function
    themselves. This is the only test here that goes through the registry.
    """

    def test_it_reaches_the_real_registry(self) -> None:
        from abicheck.detector_registry import registry

        registry.ensure_loaded()
        names = set(registry.detector_names)
        assert "undeclared_exports" in names, sorted(names)


class TestACapturedEmptyTableIsNotMissingEvidence:
    """Zero exports observed is evidence; an unparsed table is not.

    Both leave `ElfMetadata.symbols` empty, so a guard keyed on emptiness
    conflates them -- and it conflated them in the direction that loses a
    real finding: a library that genuinely exports nothing had the detector
    switched off, so the first undeclared export it ever gained went
    unreported by anything at all, since the ordinary function diff cannot
    see an undeclared symbol either (Codex review).
    """

    def test_the_first_export_a_library_ever_gains_is_reported(self) -> None:
        old = _snap([])  # captured, and genuinely exports nothing
        new = _snap(["first"])
        assert [c.symbol for c in _diff_undeclared_exports(old, new)] == ["first"]

    def test_an_unparsed_table_still_fabricates_nothing(self) -> None:
        """The other direction, and the reason the guard is not simply
        dropped: an *uncaptured* OLD table would make every export in NEW
        read as gained."""
        old = _snap([], with_table=False)
        new = _snap(["a", "b", "c"])
        assert _diff_undeclared_exports(old, new) == []

    def test_both_sides_empty_and_captured_report_nothing(self) -> None:
        """Vacuity guard: the first assertion above must not be satisfiable
        by a detector that reports on any empty OLD table."""
        assert _diff_undeclared_exports(_snap([]), _snap([])) == []


class TestUndeclaredDataExportsAreAdditionsToo:
    """A data symbol is lost the same way a function is, so it is found the
    same way.

    An undeclared `STT_OBJECT`/`STT_TLS`/`STT_COMMON` export is absent from
    the header-derived `variable_map` exactly as an undeclared function is
    from `function_map`, so `_diff_variables` cannot report `VAR_ADDED` for
    it either -- the addition disappeared entirely rather than being reported
    weakly (Codex review). A function-only detector fixes half a defect and
    leaves the other half looking fixed, which is worse than not having
    fixed either.
    """

    def test_a_gained_undeclared_data_export_is_reported(self) -> None:
        old = _snap([], data_exports=[])
        new = _snap([], data_exports=["g_table"])
        changes = _diff_undeclared_exports(old, new)
        assert [(c.kind, c.symbol) for c in changes] == [
            (ChangeKind.VAR_ADDED_ELF_ONLY, "g_table")
        ]

    def test_functions_and_data_are_both_reported_in_one_run(self) -> None:
        old = _snap([], data_exports=[])
        new = _snap(["fn"], data_exports=["g_table"])
        by_kind = {c.kind: c.symbol for c in _diff_undeclared_exports(old, new)}
        assert by_kind == {
            ChangeKind.FUNC_ADDED_ELF_ONLY: "fn",
            ChangeKind.VAR_ADDED_ELF_ONLY: "g_table",
        }

    def test_a_declared_variable_is_the_ordinary_diffs_business(self) -> None:
        """The negative control that keeps this from double-reporting: a data
        symbol the headers declare belongs to `_diff_variables`."""
        from abicheck.model import Variable

        old = _snap([], data_exports=[])
        new = _snap([], data_exports=["g_declared"])
        new.variables = [Variable(name="g_declared", mangled="g_declared", type="int")]
        new.__dict__.pop("variable_map", None)
        assert _diff_undeclared_exports(old, new) == []

    def test_a_notype_export_is_reported_once(self) -> None:
        """`notype` is in both symbol-type sets, so without a guard a single
        undeclared export of unknown type would be reported twice -- once as
        a function, once as data."""
        old = _snap([])
        new = _snap([])
        new.elf.symbols.append(
            ElfSymbol(name="mystery", visibility="default", sym_type="notype")
        )
        symbols = [c.symbol for c in _diff_undeclared_exports(old, new)]
        assert symbols.count("mystery") == 1


class TestANameThatAlreadyExistedIsNeverAnAddition:
    """A symbol changing ELF type is a modification, not an addition.

    The per-class subtraction could not see it: an export that keeps its name
    and goes `STT_OBJECT` -> `STT_FUNC` is absent from the *function-only*
    `old_exports`, so it read as newly gained and `func_added_elf_only` was
    emitted alongside the `symbol_type_changed` that already describes the
    real change (Codex review; reproduced on real binaries). That corrupts
    the addition count and, through it, `-warn-newsym` and
    `semver.recommend_release`'s MINOR bump.

    Bug class: a set difference taken within one partition when the question
    spans all of them.
    """

    @staticmethod
    def _snapshot(*, exports, declared=()):
        from abicheck.model import AbiSnapshot

        elf = ElfMetadata(
            machine="x86_64",
            symbols=[
                ElfSymbol(name=name, visibility="default", sym_type=sym_type)
                for name, sym_type in exports
            ],
        )
        snap = AbiSnapshot(library="libfoo.so", version="1", elf=elf)
        snap.functions = list(declared)
        return snap

    def _kinds(self, old, new):
        from abicheck.compare.undeclared_exports import _diff_undeclared_exports

        return sorted(c.kind.value for c in _diff_undeclared_exports(old, new))

    @pytest.mark.parametrize(
        ("old_type", "new_type"),
        [
            ("object", "func"),
            ("func", "object"),
            ("tls", "func"),
            ("func", "tls"),
            ("ifunc", "object"),
            # `other` is the bucket an unrecognised `st_info` type lands in.
            # The first fix widened to the *union of the two class sets*,
            # which omits it -- so this transition still reported a false
            # addition one review round later (Codex review). Hence the
            # exhaustive sweep below rather than a hand-listed set.
            ("other", "func"),
            ("other", "object"),
            ("func", "other"),
            ("object", "other"),
        ],
    )
    def test_no_addition_for_any_type_transition(self, old_type, new_type):
        """Every direction, not just the reported one: the bug is symmetric
        in the two symbol classes and a single example would foreclose one
        half of it."""
        old = self._snapshot(exports=[("shape_shifter", old_type)])
        new = self._snapshot(exports=[("shape_shifter", new_type)])
        assert self._kinds(old, new) == []

    def test_no_transition_between_any_two_types_is_an_addition(self):
        """Exhaustive over every ordered pair the parser can produce.

        Three successive fixes answered "did this name exist before?" with a
        hand-listed subset of types -- one class, then the union of two, each
        closing the instance in front of it. The set is now derived from
        ``SymbolType``; this sweep is the executable form of that, so a type
        added to the enum later cannot reintroduce the bug silently.
        """
        from abicheck.elf_symbol_filter import ALL_SYMBOL_TYPES

        offenders = []
        for old_type in sorted(ALL_SYMBOL_TYPES):
            for new_type in sorted(ALL_SYMBOL_TYPES):
                kinds = self._kinds(
                    self._snapshot(exports=[("shape_shifter", old_type)]),
                    self._snapshot(exports=[("shape_shifter", new_type)]),
                )
                if kinds:
                    offenders.append(f"{old_type} -> {new_type}: {kinds}")
        assert not offenders, offenders

    def test_the_sweep_is_not_vacuous(self):
        """The types it sweeps must be the real ones, and more than the two
        class sets -- otherwise the sweep above could pass by covering
        nothing new."""
        from abicheck.elf_symbol_filter import (
            ALL_SYMBOL_TYPES,
            FUNCTION_SYMBOL_TYPES,
            VARIABLE_SYMBOL_TYPES,
        )

        assert "other" in ALL_SYMBOL_TYPES
        assert ALL_SYMBOL_TYPES > (FUNCTION_SYMBOL_TYPES | VARIABLE_SYMBOL_TYPES)

    def test_a_genuinely_new_name_is_still_reported(self):
        """The negative control: a detector that reported nothing would pass
        every assertion above."""
        old = self._snapshot(exports=[("kept", "func")])
        new = self._snapshot(
            exports=[("kept", "func"), ("brand_new", "func"), ("new_data", "object")]
        )
        assert self._kinds(old, new) == ["func_added_elf_only", "var_added_elf_only"]

    def test_a_new_name_is_classified_by_its_new_side_type(self):
        """NEW's own type still decides which kind a genuinely new name gets
        -- the fix narrows what counts as new, not how new names classify."""
        old = self._snapshot(exports=[("kept", "func")])
        new = self._snapshot(exports=[("kept", "func"), ("fresh", "object")])
        assert self._kinds(old, new) == ["var_added_elf_only"]

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
from abicheck.diff_undeclared_exports import _diff_undeclared_exports
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function


def _snap(
    exports: list[str],
    *,
    declared: list[str] | None = None,
    elf_only: bool = False,
    with_table: bool = True,
) -> AbiSnapshot:
    """A snapshot whose export table holds *exports* and whose header-derived
    function map holds *declared* (by default: nothing, the undeclared case).
    """
    elf = (
        ElfMetadata(
            symbols=[
                ElfSymbol(name=n, visibility="default", sym_type="func")
                for n in exports
            ]
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

    def test_one_sided_elf_only_mode_also_suppresses(self) -> None:
        old = _snap(["keep"], elf_only=True)
        new = _snap(["keep", "gained"])
        assert _diff_undeclared_exports(old, new) == []
        assert _diff_undeclared_exports(new, old) == []


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

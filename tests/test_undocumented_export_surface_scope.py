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

"""Binary churn on a *provably* undocumented export is out of surface.

Bug class (``tests/regressions/manifest.py``: ``two-evidence-sources-
misread-as-unknown``): a classifier that treats "absent from source A" as
*unknown* although source B independently proves the subject exists, and
therefore applies the conservative-unknown rule to a case that is not
unknown at all.

Concretely: ``PublicSurface.all_symbols`` was built from modeled
declarations only, so an export-table-only symbol fell outside the
surface's universe and every finding about it was retained. The reported
instance was twenty-one ``exported_object_alignment_reduced`` rows on
Intel MKL internals that no header declares. Two facts are in hand and
they agree -- the export table proves the symbol exists; the resolved
header surface proves nothing public declares it -- which is exactly the
``not-exported`` demotion reason.

The fix is general on purpose (``policy.public_surface_closure.
_seed_undeclared_exports``): it seeds the surface universe, so *every*
binary-level finding about such a symbol is classified, not just the one
``ChangeKind`` that was reported. Special-casing
``exported_object_alignment_reduced`` would have left the sibling kinds
(size, binding, type, visibility churn) noisy in exactly the same way.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot, Function
from abicheck.model.elf_facts import ElfSymbol, SymbolType
from abicheck.model.macho_facts import MachoExport
from abicheck.model.vocabulary import ScopeOrigin
from abicheck.pe_metadata import PeMetadata
from abicheck.surface import classify_change_surface, compute_public_surface

#: The undocumented export's own hygiene finding, and a representative
#: spread of the *other* binary-level kinds that land on the same symbol.
#: Parametrized rather than asserted for one kind, because the whole point
#: of the fix is that it is not kind-specific.
_BINARY_CHURN_KINDS = [
    ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
    ChangeKind.SYMBOL_SIZE_CHANGED,
    ChangeKind.SYMBOL_BINDING_CHANGED,
]


def _public_fn(name: str = "api") -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        params=[],
        origin=ScopeOrigin.PUBLIC_HEADER,
        source_header="/inc/api.h",
    )


def _elf_snapshot(
    *,
    declared: list[Function],
    exports: list[str],
    versioned: dict[str, str] | None = None,
) -> AbiSnapshot:
    versioned = versioned or {}
    symbols = [
        ElfSymbol(
            name=name,
            binding="GLOBAL",
            sym_type=SymbolType.OBJECT,
            size=8,
            version=versioned.get(name),
            is_default=True,
        )
        for name in exports
    ]
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=declared,
        elf=ElfMetadata(symbols=symbols),
        from_headers=True,
    )


def _classify(kind: ChangeKind, symbol: str, old: AbiSnapshot, new: AbiSnapshot):
    return classify_change_surface(
        Change(kind=kind, symbol=symbol, description="x"),
        compute_public_surface(old),
        compute_public_surface(new),
    )


class TestUndocumentedExportIsProvablyOutOfSurface:
    @pytest.mark.parametrize("kind", _BINARY_CHURN_KINDS)
    def test_undocumented_on_both_sides_is_demoted(self, kind: ChangeKind) -> None:
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        in_surface, reason = _classify(kind, "internal_table", snap, snap)
        assert in_surface is False
        assert reason == "not-exported"

    def test_the_leak_finding_itself_stays_visible(self) -> None:
        """The evidence explaining the demotion must not be demoted with it.

        Without this exemption the run would demote the alignment rows *and*
        the one finding that says why, leaving the leak reported nowhere.
        """
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        assert _classify(
            ChangeKind.EXPORTED_NOT_PUBLIC, "internal_table", snap, snap
        ) == (True, None)

    @pytest.mark.parametrize("kind", _BINARY_CHURN_KINDS)
    def test_a_documented_export_is_untouched(self, kind: ChangeKind) -> None:
        """The negative control: demoting everything would satisfy the claim
        above completely."""
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api"])
        assert _classify(kind, "api", snap, snap) == (True, None)

    def test_public_on_either_side_keeps_the_finding(self) -> None:
        """A symbol declared publicly on *one* side only -- newly hidden, or
        newly documented -- is a real public-surface event, not internal
        churn, and the union of both surfaces is what decides it."""
        documented = _elf_snapshot(
            declared=[_public_fn(), _public_fn("internal_table")],
            exports=["api", "internal_table"],
        )
        undocumented = _elf_snapshot(
            declared=[_public_fn()], exports=["api", "internal_table"]
        )
        for old, new in ((documented, undocumented), (undocumented, documented)):
            assert _classify(
                ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
                "internal_table",
                old,
                new,
            ) == (True, None)


class TestRemovalOfAnUndocumentedExportIsNeverDemoted:
    """Undocumented licenses demoting *churn*, never *disappearance*.

    The catalog states the ground truth directly
    (`catalog/cases/case182_accidental_export_removed_still_breaking`):
    "the absence of a header declaration proves it wasn't part of the
    documented contract -- it does not prove nobody depends on it. A
    consumer that obtained the symbol via `dlsym()`, a leaked internal
    header, or a hand-written prototype fails at lookup time once v2
    removes it."

    Seeding without this exemption turned that case from BREAKING (exit 4)
    into a clean exit 0: a real break hidden by a noise filter. It was
    caught by the example matrix, not by this file, because the original
    kind list here covered alignment/size/binding and never removal --
    which is the whole point of stating the axis rather than enumerating
    the kinds that happened to come to mind.
    """

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.FUNC_REMOVED_ELF_ONLY,
            ChangeKind.VAR_REMOVED_ELF_ONLY,
            ChangeKind.FUNC_REMOVED,
            ChangeKind.VAR_REMOVED,
        ],
    )
    def test_removal_survives(self, kind: ChangeKind) -> None:
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        assert _classify(kind, "internal_table", snap, snap) == (True, None)

    @pytest.mark.parametrize("kind", _BINARY_CHURN_KINDS)
    def test_property_churn_on_the_same_symbol_is_still_demoted(
        self, kind: ChangeKind
    ) -> None:
        """The negative control: exempting the whole symbol would undo the
        noise reduction this seeding exists for."""
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        assert _classify(kind, "internal_table", snap, snap) == (
            False,
            "not-exported",
        )

    def test_only_export_table_only_symbols_get_the_exemption(self) -> None:
        """A symbol a header *does* declare (privately) keeps its
        long-standing demotion -- the exemption is scoped to the set this
        seeding actually added, not to every export."""
        private = Function(
            name="priv",
            mangled="priv",
            return_type="int",
            params=[],
            origin=ScopeOrigin.PRIVATE_HEADER,
            source_header="/src/priv.h",
        )
        snap = _elf_snapshot(declared=[_public_fn(), private], exports=["api", "priv"])
        assert "priv" not in compute_public_surface(snap).undeclared_export_symbols
        in_surface, _ = _classify(ChangeKind.FUNC_REMOVED_ELF_ONLY, "priv", snap, snap)
        assert in_surface is False


class TestCompilerEmittedClassArtifactsAreNotUndocumented:
    """A vtable is not an undocumented export -- its class declares it.

    A header backend records the owning class as a ``RecordType``, never as
    a ``Function``/``Variable``, so ``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT`` and
    the Itanium structors are absent from the declaration list for a reason
    that says nothing about the contract. ``exported_not_public`` has always
    exempted them; scoping has to make the same exemption or the two
    mechanisms contradict each other -- and demoting one made an identical
    binary pair report BREAKING without ``-H`` and clean with it, which is
    the exact invariant
    ``tests/test_export_reconciliation_and_obligations.py`` pins.
    """

    @pytest.mark.parametrize(
        "symbol",
        [
            "_ZTVN3foo3BarE",
            "_ZTIN3foo3BarE",
            "_ZTSN3foo3BarE",
            "_ZTTN3foo3BarE",
            "_ZN3foo3BarC1Ev",
            "_ZN3foo3BarD1Ev",
            "??_7Bar@@6B@",
            "??0Bar@@QEAA@XZ",
        ],
    )
    def test_the_artifact_is_not_seeded(self, symbol: str) -> None:
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", symbol])
        assert symbol not in compute_public_surface(snap).all_symbols

    def test_so_a_finding_about_it_is_retained(self) -> None:
        snap = _elf_snapshot(declared=[_public_fn()], exports=["api", "_ZTVN3foo3BarE"])
        assert _classify(ChangeKind.VAR_REMOVED, "_ZTVN3foo3BarE", snap, snap) == (
            True,
            None,
        )

    def test_an_ordinary_mangled_symbol_is_still_seeded(self) -> None:
        """The negative control: exempting every mangled name would satisfy
        the claims above completely."""
        snap = _elf_snapshot(
            declared=[_public_fn()], exports=["api", "_ZN3foo8internalEv"]
        )
        assert "_ZN3foo8internalEv" in compute_public_surface(snap).all_symbols


class TestConservativeRetentionWhereEvidenceIsMissing:
    """Each of these is a case where "no public header declares it" is an
    *absence of evidence*, not evidence of absence."""

    def test_no_header_provenance_retains_everything(self) -> None:
        """Every declaration UNKNOWN -- the snapshot was dumped without a
        public-header set, so there is no resolved header surface to prove
        anything is undocumented."""
        fn = Function(name="api", mangled="api", return_type="int", params=[])
        snap = _elf_snapshot(declared=[fn], exports=["api", "internal_table"])
        surf = compute_public_surface(snap)
        assert surf.has_provenance is False
        assert "internal_table" not in surf.all_symbols

    def test_no_export_table_retains_everything(self) -> None:
        """Nothing proves the symbol exists, so it stays genuinely unknown."""
        snap = AbiSnapshot(
            library="libx.so", version="1", functions=[_public_fn()], from_headers=True
        )
        surf = compute_public_surface(snap)
        assert surf.all_symbols == {"api"}

    def test_an_unresolvable_side_retains_everything(self) -> None:
        """``classify_change_surface``'s own second gate: scoping needs a
        resolvable surface on *both* sides."""
        resolvable = _elf_snapshot(
            declared=[_public_fn()], exports=["api", "internal_table"]
        )
        elf_only = _elf_snapshot(declared=[], exports=["api", "internal_table"])
        assert _classify(
            ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
            "internal_table",
            elf_only,
            resolvable,
        ) == (True, None)


class TestExportSpellingsAcrossPlatforms:
    """The seeded names must be the same projection ``exported_not_public``
    reports against -- a private re-read would let one demote a symbol the
    other never reported."""

    def test_elf_non_default_version_is_not_seeded(self) -> None:
        """Only default/unversioned ELF exports are the obligation set."""
        snap = AbiSnapshot(
            library="libx.so",
            version="1",
            functions=[_public_fn()],
            elf=ElfMetadata(
                symbols=[
                    ElfSymbol(
                        name="api",
                        binding="GLOBAL",
                        sym_type=SymbolType.FUNC,
                        size=0,
                        is_default=True,
                    ),
                    ElfSymbol(
                        name="old_impl",
                        binding="GLOBAL",
                        sym_type=SymbolType.FUNC,
                        size=0,
                        version="V1",
                        is_default=False,
                    ),
                ]
            ),
            from_headers=True,
        )
        assert "old_impl" not in compute_public_surface(snap).all_symbols

    def test_elf_default_versioned_name_is_seeded_unversioned(self) -> None:
        snap = _elf_snapshot(
            declared=[_public_fn()],
            exports=["api", "internal_table"],
            versioned={"internal_table": "V1"},
        )
        assert "internal_table" in compute_public_surface(snap).all_symbols

    def test_macho_names_are_seeded(self) -> None:
        snap = AbiSnapshot(
            library="libx.dylib",
            version="1",
            functions=[_public_fn()],
            macho=MachoMetadata(
                exports=[MachoExport(name="api"), MachoExport(name="internal_table")]
            ),
            from_headers=True,
        )
        assert "internal_table" in compute_public_surface(snap).all_symbols

    def test_pe_exports_are_seeded(self) -> None:
        from abicheck.model.pe_facts import PeExport

        snap = AbiSnapshot(
            library="x.dll",
            version="1",
            functions=[_public_fn()],
            pe=PeMetadata(
                exports=[PeExport(name="api"), PeExport(name="internal_table")]
            ),
            from_headers=True,
        )
        assert "internal_table" in compute_public_surface(snap).all_symbols


class TestThroughTheRealPipeline:
    """The demotion has to show up as a real ledger row, not just a helper's
    return value."""

    @staticmethod
    def _pipeline(force_public=None, scope=True):
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        new = _elf_snapshot(declared=[_public_fn()], exports=["api", "internal_table"])
        changes = [
            Change(
                kind=ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
                symbol="internal_table",
                description="64 -> 8",
            ),
            Change(
                kind=ChangeKind.EXPORTED_NOT_PUBLIC,
                symbol="internal_table",
                description="undocumented export",
            ),
        ]
        return DEFAULT_PIPELINE.run(
            changes,
            old,
            new,
            scope_to_public_surface=scope,
            force_public_symbols=force_public,
        )

    def test_the_ledger_records_the_reason(self) -> None:
        ctx = self._pipeline()
        assert [c.kind for c in ctx.out_of_surface] == [
            ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED
        ]
        assert ctx.out_of_surface[0].surface_exclusion_reason == "not-exported"
        assert ChangeKind.EXPORTED_NOT_PUBLIC in [c.kind for c in ctx.kept]

    def test_scoping_off_retains_everything(self) -> None:
        """``--no-scope-public-headers``: the classifier never runs."""
        ctx = self._pipeline(scope=False)
        assert ctx.out_of_surface == []
        assert len(ctx.kept) == 2

    def test_force_public_retains_everything(self) -> None:
        """``--public-symbol`` is a user guarantee that outranks the
        classification (ADR-024 D6's widening overlay)."""
        ctx = self._pipeline(force_public={"internal_table"})
        assert ctx.out_of_surface == []
        assert len(ctx.kept) == 2


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
)
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
class TestCase182EndToEnd:
    """The catalog case this regressed, run directly.

    `catalog/cases/case182_accidental_export_removed_still_breaking` already
    encodes this, but only the `Full example matrix` CI job runs it -- which
    is why the regression reached CI instead of a local `pytest` run. Built
    from the catalog's own sources so the two cannot drift apart.
    """

    def test_removing_an_accidental_export_is_still_breaking(
        self, tmp_path: Path
    ) -> None:
        case = (
            Path(__file__).resolve().parent.parent
            / "catalog"
            / "cases"
            / "case182_accidental_export_removed_still_breaking"
        )
        if not case.is_dir():  # pragma: no cover - catalog always present
            pytest.skip("catalog case not available")
        for name in ("v1.c", "v2.c", "v1.h", "v2.h"):
            shutil.copy(case / name, tmp_path / name)
        for ver in ("v1", "v2"):
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-g",
                    str(tmp_path / f"{ver}.c"),
                    "-o",
                    str(tmp_path / f"libfoo_{ver}.so"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(tmp_path / "libfoo_v1.so"),
                str(tmp_path / "libfoo_v2.so"),
                "-H",
                str(tmp_path / "v1.h"),
            ],
        )
        # Exit 4 = BREAKING. Public-header scoping is on by default here, so
        # this is precisely the configuration under which the undeclared
        # export's removal must survive.
        assert result.exit_code == 4, result.output
        assert "internal_helper" in result.output

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

"""Unit tests for G38 Phase 4's C-boundary signature-evidence gate
(``abicheck/bundle_signature_evidence.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle import _compute_resolution_graph
from abicheck.bundle_models import BundleSnapshot
from abicheck.bundle_signature_evidence import find_unverified_signature_findings
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility

# ---------------------------------------------------------------------------
# Fixtures (mirrors tests/test_bundle.py's own in-memory-ElfMetadata style)
# ---------------------------------------------------------------------------


def _meta(
    *,
    exports: list[str] = (),
    imports: list[str] = (),
    needed: list[str] = (),
) -> ElfMetadata:
    from abicheck.elf_metadata import ElfImport

    return ElfMetadata(
        soname="",
        needed=list(needed),
        symbols=[ElfSymbol(name=n, visibility="default") for n in exports],
        imports=[ElfImport(name=n) for n in imports],
    )


def _snapshot(libraries: dict[str, ElfMetadata]) -> BundleSnapshot:
    libs = {name: Path(f"/fake/{name}") for name in libraries}
    graph = _compute_resolution_graph(libs, libraries)
    return BundleSnapshot(
        root=Path("/fake"), libraries=libs, metadata=libraries, resolution=graph
    )


def _diff(library: str, *changes: Change) -> DiffResult:
    return DiffResult(
        old_version="old",
        new_version="new",
        library=library,
        changes=list(changes),
        verdict=Verdict.BREAKING,
    )


def _elf_only_fn(symbol: str) -> Function:
    return Function(
        name=symbol, mangled=symbol, return_type="?", visibility=Visibility.ELF_ONLY
    )


def _evidenced_fn(
    symbol: str,
    *,
    return_type: str = "int",
    params: list[Param] | None = None,
    is_variadic: bool | None = False,
    contract_attributes: list[str] | None = (),
) -> Function:
    return Function(
        name=symbol,
        mangled=symbol,
        return_type=return_type,
        params=params or [],
        visibility=Visibility.PUBLIC,
        is_variadic=is_variadic,
        contract_attributes=(
            list(contract_attributes) if contract_attributes is not None else None
        ),
    )


def _snap(
    library: str,
    *,
    functions: list[Function] = (),
    variables: list[Variable] = (),
    elf_only_mode: bool = False,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=list(functions),
        variables=list(variables),
        elf_only_mode=elf_only_mode,
    )


# ---------------------------------------------------------------------------
# find_unverified_signature_findings
# ---------------------------------------------------------------------------


class TestFindUnverifiedSignatureFindings:
    def test_fires_when_both_sides_elf_only(self):
        # Both sides genuinely dumped without headers at all
        # (elf_only_mode=True) -- so ELF_ONLY here means "confirmed
        # dynsym-exported, just no header/DWARF corroboration," per
        # _symbol_was_exported's own contract.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )

        assert len(findings) == 1
        (f,) = findings
        assert f.kind is ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED
        assert f.symbol == "core_fn"
        assert f.consumer_library == "libconsumer.so"
        assert f.provider_library == "libcore.so"
        assert f.affected_libraries == ["libconsumer.so"]
        assert "neither side has" in f.description

    def test_no_finding_when_evidence_sufficient_both_sides(self):
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_fires_when_variadicness_is_unknown_on_either_side(self):
        # Codex review, fresh evidence: diff_symbols._check_variadic_
        # change() itself skips (skip_none=True) whenever either side's
        # is_variadic is unknown (None) -- an older snapshot/dumper that
        # never populated the field is otherwise indistinguishable from
        # one that positively determined "not variadic". Without this
        # module also treating unknown variadicness as insufficient
        # evidence, a real fixed-arity<->variadic transition landing on
        # an unknown side would produce neither a confirmed diff-level
        # finding nor this module's own risk finding -- total silence.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_evidenced_fn("core_fn", is_variadic=None)]
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_evidenced_fn("core_fn", is_variadic=False)]
            )
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert "old side lacks" in findings[0].description

    def test_fires_when_contract_attributes_are_unknown_on_either_side(self):
        # Codex review, fresh evidence: the identical shape as the
        # is_variadic gap above, for Function.contract_attributes
        # (calling-convention attributes like stdcall/ms_abi/vectorcall) --
        # a real tri-state field (list[str] | None). diff_symbols._check_
        # contract_attributes_change() itself skips whenever either side is
        # None, so a real calling-convention transition landing on an
        # unknown side must not read as sufficient evidence here either.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_evidenced_fn("core_fn", contract_attributes=None)],
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_evidenced_fn("core_fn", contract_attributes=[])],
            )
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert "old side lacks" in findings[0].description

    def test_variadic_function_pointer_type_is_not_treated_as_unresolved(self):
        # Codex review, fresh evidence: a real, complete variadic
        # function-pointer parameter type (e.g. a printf-style callback)
        # legitimately spells its own textual type as
        # "void (*)(int, ...)" -- the literal substring "..." appears
        # inside real, fully-resolved evidence here, not as the
        # recursion-depth-cap sentinel. A bare substring check on "..."
        # would misclassify this as insufficient evidence.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        fn = _evidenced_fn(
            "core_fn",
            params=[Param(name="cb", type="void (*)(int, ...)")],
        )
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        new_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_one_side_insufficient(self):
        # Sufficient evidence on new, but ELF-only (dumped without headers)
        # on old -- still unverified, since agreement can't be confirmed
        # either.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        # The description must name which side actually lacks evidence
        # (old, here) rather than overclaiming "neither side" when the new
        # side is in fact fully evidenced.
        assert "old side lacks" in findings[0].description
        assert "neither side" not in findings[0].description

    def test_no_finding_when_new_side_insufficient(self):
        # Mirror of the above with the deficient side flipped: sufficient
        # evidence on old, ELF-only on new.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert "new side lacks" in findings[0].description
        assert "neither side" not in findings[0].description

    def test_no_finding_when_no_consumer(self):
        new = _snapshot({"libcore.so": _meta(exports=["core_fn"])})
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_provider_is_unreachable_from_the_consumer(self):
        # Codex review, fresh evidence: two unrelated libraries can each
        # export a same-named symbol without either being loadable
        # together with a given consumer -- a bare consumers_of(symbol)
        # (name-only, set-wide) would still pair them. libconsumer.so here
        # has *no* DT_NEEDED edge to libcore.so at all (its own `needed`
        # list is empty), so libcore.so's export of core_fn is unreachable
        # from it and must not produce a finding, even though the bare
        # symbol name matches.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"]),  # no `needed`
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_provider_version_does_not_satisfy_the_consumer(self):
        # Codex review, fresh evidence: consumers_of(symbol) matches by
        # bare name only, so a consumer requiring core_fn@V2 previously
        # still paired with a provider_entry whose only definition is
        # core_fn@V1 -- a provider that cannot actually satisfy that
        # consumer at all (a real resolution failure, not a signature-
        # mismatch risk this module exists to flag).
        from abicheck.elf_metadata import ElfImport

        new = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[ElfSymbol(name="core_fn", version="V1", is_default=False)],
                ),
                "libconsumer.so": ElfMetadata(
                    soname="",
                    needed=["libcore.so"],
                    symbols=[],
                    imports=[ElfImport(name="core_fn", version="V2", is_default=False)],
                ),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_symbol_absent_from_old_snapshot(self):
        # A brand-new export (addition), not a same-symbol unverified case.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[])}
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_old_symbol_was_only_a_private_declaration(self):
        # Codex review: a symbol present in old_snap.function_map is not by
        # itself proof the old binary ever exported it -- AbiSnapshot keeps
        # private/internal declarations too. Visibility.HIDDEN on the old
        # side means it was compiled to NOT export, so a newly-exported
        # symbol in `new` that happens to share a name with an old *private*
        # declaration must read as a genuine addition, not a retained,
        # evidence-uncertain symbol -- even though the old declaration's own
        # evidence (deliberately ELF_ONLY here) would otherwise be
        # insufficient.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        hidden_old_fn = Function(
            name="core_fn",
            mangled="core_fn",
            return_type="?",
            visibility=Visibility.HIDDEN,
        )
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[hidden_old_fn])}
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_old_elf_only_was_header_parsed_and_not_dynamic(self):
        # Codex review, second round: on a HEADER-parsed snapshot (the
        # common case -- elf_only_mode=False), Visibility.ELF_ONLY does
        # NOT mean "confirmed exported" the way it does on a pure
        # elf_only_mode=True dump. dumper_castxml.py's/dumper_clang.py's
        # shared _visibility() policy assigns ELF_ONLY to a header-declared
        # symbol present in .symtab (which includes purely internal,
        # static-linkage globals) but ABSENT from .dynsym -- i.e. declared,
        # but never actually dynamically exported. Only Visibility.PUBLIC
        # means "confirmed in .dynsym" on that path. A symbol newly
        # exported in `new` that happens to share a name with such an old,
        # header-declared-but-never-exported symbol must read as a genuine
        # addition, exactly like the private-declaration (HIDDEN) case
        # above -- not as "retained, evidence uncertain".
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        symtab_only_old_fn = Function(
            name="core_fn",
            mangled="core_fn",
            return_type="?",
            visibility=Visibility.ELF_ONLY,
        )
        # elf_only_mode left at its default False -- this snapshot was
        # header-parsed, so ELF_ONLY here means .symtab-but-not-.dynsym.
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[symtab_only_old_fn])}
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_snapshot_missing_for_provider(self):
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        assert find_unverified_signature_findings(new, new, [], {}, {}) == []

    def test_no_finding_when_confirmed_signature_change_present(self):
        # A real, diff-confirmed signature change outranks "couldn't tell
        # either way" -- BUNDLE_INTRA_DEP_SIGNATURE_CHANGED (built by
        # abicheck.bundle itself) is what should fire here, not this kind.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }
        confirmed_diff = [
            _diff(
                "libcore.so",
                Change(
                    kind=ChangeKind.FUNC_PARAMS_CHANGED,
                    symbol="core_fn",
                    description="params changed",
                ),
            )
        ]

        assert (
            find_unverified_signature_findings(
                new, new, confirmed_diff, old_snaps, new_snaps
            )
            == []
        )

    @pytest.mark.parametrize(
        "confirmed_kind",
        [
            ChangeKind.FUNC_VARIADIC_ADDED,
            ChangeKind.FUNC_VARIADIC_REMOVED,
            ChangeKind.CALLING_CONVENTION_CHANGED,
        ],
    )
    def test_no_finding_when_confirmed_change_present_on_a_different_axis(
        self, confirmed_kind
    ):
        # Codex review, fresh evidence: a symbol with a real, diff-confirmed
        # variadicness/calling-convention change (this module's own
        # is_variadic/contract_attributes sufficiency checks' positive
        # counterparts) that *also* carries an unrelated unresolved field
        # (an unresolved parameter type here) must not additionally produce
        # a redundant, contradictory "cannot be confirmed or denied" risk
        # finding alongside the already-proven break.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        fn = _evidenced_fn("core_fn", params=[Param(name="x", type="?")])
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        new_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        confirmed_diff = [
            _diff(
                "libcore.so",
                Change(
                    kind=confirmed_kind,
                    symbol="core_fn",
                    description="signature changed",
                ),
            )
        ]

        assert (
            find_unverified_signature_findings(
                new, new, confirmed_diff, old_snaps, new_snaps
            )
            == []
        )

    def test_no_finding_when_confirmed_change_present_for_a_versioned_library(self):
        # Codex review, fresh evidence: DiffResult.library is always the raw
        # on-disk basename (`path.name`), which for a normally-versioned
        # SONAME (e.g. "libcore.so.1.2.3") differs from the bundle-canonical
        # key ("libcore.so", binary_utils._canonical_library_key) the
        # resolution graph itself keys providers by. The sibling test above
        # uses matching bare names throughout, which coincidentally hid this
        # -- _confirmed_provider_symbols must resolve the basename back to
        # the canonical key via *old*'s own `.libraries` mapping, or a real,
        # confirmed signature-changed finding fails to suppress the
        # unverified duplicate for the overwhelmingly common versioned case.
        old = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        # A real bundle build: build_bundle_snapshot's own `libraries` dict
        # is keyed by the canonical name but points at the real, versioned
        # on-disk path -- mirror that shape directly rather than going
        # through _snapshot()'s uniform fake-path helper.
        old.libraries["libcore.so"] = Path("/fake/libcore.so.1.2.3")
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        # elf_only_mode=True (unlike the sibling test above) so this
        # actually reaches and depends on the confirmed-precedence check --
        # without it, _symbol_was_exported's own ELF_ONLY/elf_only_mode
        # gate already returns False and short-circuits the whole
        # evidence-sufficiency path before the confirmed check's effect
        # (or absence of it) could be observed at all.
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_elf_only_fn("core_fn")], elf_only_mode=True
            )
        }
        # DiffResult.library carries the real, versioned on-disk basename --
        # exactly what a real compare-release run stashes (Path(old_path).name).
        confirmed_diff = [
            _diff(
                "libcore.so.1.2.3",
                Change(
                    kind=ChangeKind.FUNC_PARAMS_CHANGED,
                    symbol="core_fn",
                    description="params changed",
                ),
            )
        ]

        assert (
            find_unverified_signature_findings(
                old, new, confirmed_diff, old_snaps, new_snaps
            )
            == []
        )

    def test_variable_symbol(self):
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_var"]),
                "libconsumer.so": _meta(imports=["core_var"], needed=["libcore.so"]),
            }
        )
        elf_only_var = Variable(
            name="core_var",
            mangled="core_var",
            type="?",
            visibility=Visibility.ELF_ONLY,
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so", variables=[elf_only_var], elf_only_mode=True
            )
        }
        new_snaps = {"libcore.so": _snap("libcore.so", variables=[elf_only_var])}

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert findings[0].symbol == "core_var"

    def test_unresolved_parameter_type_is_insufficient_evidence(self):
        # Full DWARF coverage overall, but one parameter's own type is the
        # unknown-type sentinel -- must still count as insufficient (not
        # merely the return/variable type).
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        fn = _evidenced_fn("core_fn", params=[Param(name="x", type="?")])
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        new_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1

    @pytest.mark.parametrize(
        "unresolved_return_type",
        [
            "? &",  # dwarf_snapshot.py: unresolved reference target
            "? &&",  # dwarf_snapshot.py: unresolved rvalue-reference target
            "?*",  # dumper_castxml.py: unresolved pointer target
            "?&",  # dumper_castxml.py: unresolved reference target
            "?&&",  # dumper_castxml.py: unresolved rvalue-reference target
            "...",  # dwarf_snapshot.py/dwarf_metadata.py/pdb_parser.py:
            # type-resolution recursion depth cap
            "... *",  # pdb_parser.py: pointer wrapping a depth-capped target
            "... &",  # dwarf_snapshot.py: reference wrapping a depth-capped
            # target
            "... &&",  # dwarf_snapshot.py: rvalue-reference wrapping a
            # depth-capped target
            "... * *",  # pointer to pointer, both wrapping a depth-capped
            # target (multi-level nesting)
            "const ...",  # pdb_parser.py: a const-qualified depth-capped
            # target (qualifier wraps as a *prefix*, not a suffix)
            "...[]",  # pdb_parser.py: an array of depth-capped elements
            "...[] *",  # pdb_parser.py: a pointer to an array of
            # depth-capped elements (mixed suffix wrapping)
            "fn(...)",  # dwarf_snapshot.py/pdb_parser.py: the fixed,
            # unconditional subroutine-type placeholder -- never carries
            # real return/parameter types regardless of depth
        ],
    )
    def test_composite_unresolved_return_type_is_insufficient_evidence(
        self, unresolved_return_type
    ):
        # Codex review: a parser doesn't only ever emit the bare "?"
        # sentinel -- when resolution fails partway through a composite
        # type (a reference/pointer to an unresolved target), the wrapping
        # layer still runs and produces a composite marker instead. Any of
        # these must still count as insufficient evidence, exactly like the
        # bare "?" case.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        fn = _evidenced_fn("core_fn", return_type=unresolved_return_type)
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        new_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1

    def test_composite_unresolved_parameter_type_is_insufficient_evidence(self):
        # Same composite-marker case, but on a parameter type rather than
        # the return type -- mirrors
        # test_unresolved_parameter_type_is_insufficient_evidence's own
        # bare-"?" coverage for the composite forms.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        fn = _evidenced_fn("core_fn", params=[Param(name="x", type="?*")])
        old_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}
        new_snaps = {"libcore.so": _snap("libcore.so", functions=[fn])}

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1

    def test_composite_unresolved_variable_type_is_insufficient_evidence(self):
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_var"]),
                "libconsumer.so": _meta(imports=["core_var"], needed=["libcore.so"]),
            }
        )
        var = Variable(
            name="core_var",
            mangled="core_var",
            type="?*",
            visibility=Visibility.PUBLIC,
        )
        old_snaps = {"libcore.so": _snap("libcore.so", variables=[var])}
        new_snaps = {"libcore.so": _snap("libcore.so", variables=[var])}

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1

    def test_one_finding_per_consumer(self):
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "liba.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
                "libb.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_elf_only_fn("core_fn")])
        }

        findings = find_unverified_signature_findings(
            new, new, [], old_snaps, new_snaps
        )
        assert sorted(f.consumer_library for f in findings) == ["liba.so", "libb.so"]

    def test_no_finding_when_symbol_absent_from_either_map_entirely(self):
        # A provider "exports" a symbol per the resolution graph, but its
        # own AbiSnapshot has no matching Function/Variable entry at all
        # (e.g. symbols_only mode never populated it) -- absence of any
        # declaration entry is the weakest evidence state, so this counts
        # as insufficient -- but since the symbol is entirely absent from
        # the OLD snapshot too, this is the "addition" skip case, not a
        # produced finding. Confirms no crash on a genuinely empty snapshot.
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {"libcore.so": _snap("libcore.so")}
        new_snaps = {"libcore.so": _snap("libcore.so")}

        assert (
            find_unverified_signature_findings(new, new, [], old_snaps, new_snaps) == []
        )

    def test_no_finding_when_provider_version_is_a_fresh_export_not_retained(self):
        # Codex review, fresh evidence: a name-only old-side "was it
        # exported" check (_symbol_was_exported) can't tell a genuinely new
        # symbol *version* apart from an unrelated old-side version sharing
        # the same bare name. libcore.so previously exported only
        # core_fn@V1; the new release adds core_fn@V2 (a distinct,
        # brand-new export -- not a retained one) and a consumer requires
        # exactly V2. Pre-fix, this fired an "unverified" finding for V2
        # purely because *some* core_fn (V1) existed in the old snapshot;
        # V2 has no old-side counterpart to be uncertain about at all.
        from abicheck.elf_metadata import ElfImport

        old = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[ElfSymbol(name="core_fn", version="V1", is_default=False)],
                ),
                "libconsumer.so": _meta(needed=["libcore.so"]),
            }
        )
        new = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[
                        ElfSymbol(name="core_fn", version="V1", is_default=False),
                        ElfSymbol(name="core_fn", version="V2", is_default=False),
                    ],
                ),
                "libconsumer.so": ElfMetadata(
                    soname="",
                    needed=["libcore.so"],
                    symbols=[],
                    imports=[ElfImport(name="core_fn", version="V2", is_default=False)],
                ),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        assert (
            find_unverified_signature_findings(old, new, [], old_snaps, new_snaps) == []
        )

    def test_fires_when_provider_version_was_genuinely_retained_from_old(self):
        # Sibling positive control for the fix above: core_fn@V2 already
        # existed on the old side too (not a fresh export), so the
        # "unverified" finding must still fire when evidence is
        # insufficient on either side.
        from abicheck.elf_metadata import ElfImport

        old = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[ElfSymbol(name="core_fn", version="V2", is_default=False)],
                ),
                "libconsumer.so": _meta(needed=["libcore.so"]),
            }
        )
        new = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[ElfSymbol(name="core_fn", version="V2", is_default=False)],
                ),
                "libconsumer.so": ElfMetadata(
                    soname="",
                    needed=["libcore.so"],
                    symbols=[],
                    imports=[ElfImport(name="core_fn", version="V2", is_default=False)],
                ),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so",
                functions=[_elf_only_fn("core_fn")],
                elf_only_mode=True,
            )
        }

        findings = find_unverified_signature_findings(
            old, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert findings[0].symbol == "core_fn"

    @pytest.mark.parametrize(
        "kind",
        [
            ChangeKind.FUNC_NOEXCEPT_ADDED,
            ChangeKind.FUNC_NOEXCEPT_REMOVED,
            ChangeKind.FUNC_EXCEPTION_SPEC_CHANGED,
            ChangeKind.FUNC_REF_QUAL_CHANGED,
            ChangeKind.FUNC_VIRTUAL_ADDED,
            ChangeKind.FUNC_VIRTUAL_REMOVED,
            ChangeKind.CTOR_EXPLICIT_ADDED,
            ChangeKind.CTOR_EXPLICIT_REMOVED,
        ],
    )
    def test_no_finding_when_confirmed_change_present_on_a_wider_axis(self, kind):
        # Codex review, fresh evidence: a real, diff-confirmed change on any
        # of these axes (none of which _symbol_evidence_sufficient itself
        # inspects) must suppress this module's own "cannot be confirmed or
        # denied" finding for the same symbol, even though the symbol also
        # carries an unrelated unresolved field (return_type="?").
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_evidenced_fn("core_fn", return_type="?")]
            )
        }
        new_snaps = {
            "libcore.so": _snap(
                "libcore.so", functions=[_evidenced_fn("core_fn", return_type="?")]
            )
        }
        results = [
            _diff(
                "libcore.so",
                Change(kind=kind, symbol="core_fn", description="confirmed change"),
            )
        ]

        assert (
            find_unverified_signature_findings(new, new, results, old_snaps, new_snaps)
            == []
        )

    def test_fires_when_bare_name_version_collapsed_on_old_side(self):
        # Codex review, fresh evidence: libcore.so retains two live versions
        # of core_fn (V1 and the default V2) -- an ordinary shape for a
        # provider that never broke ABI. AbiSnapshot.function_map keeps only
        # one bare-name entry for "core_fn", which cannot be attributed to
        # either version specifically. A consumer requiring V1 must not be
        # told evidence is sufficient just because the single collapsed
        # model entry happens to look fully evidenced.
        from abicheck.elf_metadata import ElfImport

        old = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[
                        ElfSymbol(name="core_fn", version="V1", is_default=False),
                        ElfSymbol(name="core_fn", version="V2", is_default=True),
                    ],
                ),
                "libconsumer.so": _meta(needed=["libcore.so"]),
            }
        )
        new = _snapshot(
            {
                "libcore.so": ElfMetadata(
                    soname="",
                    needed=[],
                    symbols=[
                        ElfSymbol(name="core_fn", version="V1", is_default=False),
                        ElfSymbol(name="core_fn", version="V2", is_default=True),
                    ],
                ),
                "libconsumer.so": ElfMetadata(
                    soname="",
                    needed=["libcore.so"],
                    symbols=[],
                    imports=[ElfImport(name="core_fn", version="V1", is_default=False)],
                ),
            }
        )
        # Fully evidenced on its face -- but ambiguous which version it
        # actually reflects, since both V1 and V2 collapse onto this one
        # bare-name entry.
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }

        findings = find_unverified_signature_findings(
            old, new, [], old_snaps, new_snaps
        )
        assert len(findings) == 1
        assert findings[0].symbol == "core_fn"
        assert "neither side has" in findings[0].description

    def test_no_finding_when_provider_has_only_one_version_despite_default_flag(self):
        # Sibling negative control: a single version tagged is_default=True
        # is not a collapse -- only one distinct version exists, so the
        # bare-name model entry is unambiguous.
        old = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(needed=["libcore.so"]),
            }
        )
        new = _snapshot(
            {
                "libcore.so": _meta(exports=["core_fn"]),
                "libconsumer.so": _meta(imports=["core_fn"], needed=["libcore.so"]),
            }
        )
        old_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }
        new_snaps = {
            "libcore.so": _snap("libcore.so", functions=[_evidenced_fn("core_fn")])
        }

        assert (
            find_unverified_signature_findings(old, new, [], old_snaps, new_snaps) == []
        )

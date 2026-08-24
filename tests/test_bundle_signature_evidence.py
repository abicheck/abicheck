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
    symbol: str, *, return_type: str = "int", params: list[Param] | None = None
) -> Function:
    return Function(
        name=symbol,
        mangled=symbol,
        return_type=return_type,
        params=params or [],
        visibility=Visibility.PUBLIC,
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

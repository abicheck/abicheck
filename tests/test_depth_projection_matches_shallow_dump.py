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

"""Projecting a snapshot to ``binary`` against dumping at ``binary``.

Bug class ``evidence.depth_projection_diverges_from_shallow_dump``: a
header-built snapshot projected to ``--depth binary`` gave different findings
from a binary-only dump (no ``-H``) of the same library. The projection
cleared the record/enum declarations, so a layout change reached only the raw
debug-layout fallback (``struct_size_changed``, ``struct_field_removed``)
instead of the type detectors a binary-only dump runs (``type_size_changed``,
``type_field_added_compatible``, ``type_field_removed``). It also kept header
attributes on functions (static, cv/ref qualifiers, virtuality, access,
deprecation, calling convention), so header-only findings reached a
``--depth binary`` run. On a binary-only dump the projection was not even the
identity: it dropped every hidden-but-defined function, so a symbol that
became hidden read as ``func_removed`` instead of ``func_visibility_changed``.

Two invariants, over every catalog case gcc can build. The oracle is always
an independent extraction (a dump with no headers), never the projection's
own bookkeeping:

1. Idempotence, exact: projecting two binary-only dumps to ``binary`` gives
   the findings (kind, symbol) of comparing them unprojected.
2. Parity: projecting two header dumps to ``binary`` gives the findings of
   the binary-only dumps, except where a header-built snapshot does not keep
   the evidence the binary-only extractor reads. Each such difference must
   match a rule below that names the missing evidence; anything else fails.
   ``docs/contribute/known-gaps.md`` records the same residuals.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from abicheck.checker import compare
from abicheck.policy.depth_projection import project_pair_to_depth
from tests.test_example_autodiscovery import (
    _ALL_CASES,
    BUILD_INFO_CASES,
    CURRENT_PLATFORM,
    PLATFORMS,
    REQUIRES_FEATURE,
    SOURCE_SMOKE_CASES,
    _build_libs_for_case,
    _find_sources,
    _header_list,
)

if TYPE_CHECKING:
    from abicheck.model.snapshot import AbiSnapshot

#: ``(kind, symbol)`` of one finding.
Finding = tuple[str, str]

_DEBUG_DECLARATIONS = (
    "a binary-only dump reads function/variable signatures, typedefs and "
    "template arguments from debug-info subprogram and variable records; a "
    "header-built snapshot does not persist those records, so its projection "
    "keeps only the export entry"
)
_RECORD_RELATIONS = (
    "the persisted debug layout (DwarfMetadata.structs) carries size, "
    "alignment and field offsets, not bases, vtable slots, access or field "
    "cv-qualifiers, which a binary-only dump reads from the record DIE"
)
_TYPE_SCOPE = (
    "the projection scopes debug layouts by the discarded header's public "
    "names; a binary-only dump scopes them by debug-info reachability from "
    "the exported subprograms, so the two keep different type sets and "
    "classify different exports as internal"
)

_ELF_ONLY_MODE = (
    "the projection runs in elf_only_mode (no surface is declared), where an "
    "export addition/removal is spelled without the _elf_only suffix; the "
    "binary-only dump has a debug-info surface to contrast an undeclared "
    "export with"
)
_DEMANGLED_PAIRING = (
    "export-only declarations are paired with their replacement by demangled "
    "name; a binary-only dump confirms both declarations from debug info and "
    "reports a removal plus an addition"
)
_ORACLE_EXPORT_NAMES = (
    "oracle defect: the projection keeps only exports the export table "
    "confirms, and it declares this symbol on both sides, yet the binary-only "
    "dump reports it added or removed: when debug info yields any function "
    "the dump declares only debug-confirmed functions, by debug name, and "
    "the other exports drop out, so two versions dumped through different "
    "paths disagree (docs/contribute/known-gaps.md)"
)
_ELF_ONLY_INVENTORY = (
    "the projection, like a symbol-only dump, declares every export; a "
    "debug-backed binary-only dump declares only debug-confirmed functions "
    "and judges the other exports through the undeclared-exports detector, "
    "which credits special members and C++ ABI artifacts to their class"
)
_LAYOUT_PRESENCE = (
    "debug info lists the types the compiled code uses, not the types a "
    "library declares, so the projection compares only layouts present on "
    "both sides; the binary-only dump reports a type whose layout one side "
    "lacks as removed or added (absent is not removed)"
)
_SURFACE_SCOPE = (
    "the other run recorded the finding out of the public surface: debug "
    "info gives it the declaring header and owning type the projection's "
    "layout records lack, so one run scopes the finding out and the other "
    "reports it"
)

#: ``(binary-only-dump kind, projected kind) -> reason``: the same change on
#: the same symbol, spelled differently from the evidence each side keeps.
SPELLING_PAIRS: dict[tuple[str, str], str] = {
    ("func_removed", "func_removed_elf_only"): _DEBUG_DECLARATIONS,
    ("func_added_elf_only", "func_added"): _ELF_ONLY_MODE,
    ("var_removed_elf_only", "var_removed"): _ELF_ONLY_MODE,
    ("var_added_elf_only", "var_added"): _ELF_ONLY_MODE,
    ("type_field_added", "type_field_added_compatible"): _RECORD_RELATIONS,
}

#: Symbol add/remove kinds, excused only by what the other run declares:
#: a projected one when the binary-only dump declares the symbol on neither
#: side (:data:`_ELF_ONLY_INVENTORY`), a binary-only-dump one when the
#: projection declares it on both (:data:`_ORACLE_EXPORT_NAMES`).
SYMBOL_PRESENCE_KINDS: frozenset[str] = frozenset(
    {
        "func_added",
        "func_added_elf_only",
        "func_removed",
        "func_removed_elf_only",
        "var_added",
        "var_added_elf_only",
        "var_removed",
        "var_removed_elf_only",
    }
)

#: Projected kinds that pair a removed export with its replacement; each
#: accounts for the binary-only dump's removal of the same symbol and one
#: addition (:data:`_DEMANGLED_PAIRING`).
REPLACEMENT_KINDS: frozenset[str] = frozenset(
    {"func_params_changed", "func_likely_renamed"}
)

#: Kinds a binary-only dump pairs through a debug subprogram's name; each
#: accounts for the projection's removal/addition of exports carrying that
#: name (:data:`_DEBUG_DECLARATIONS`).
DEBUG_NAME_PAIRING_KINDS: frozenset[str] = frozenset({"func_language_linkage_changed"})

#: Kinds whose symbol is a type (or a member of one): excused only when the
#: two runs disagree on whether that type is in scope on some side
#: (:data:`_TYPE_SCOPE`).
TYPE_SCOPED_KINDS: frozenset[str] = frozenset(
    {
        "inline_namespace_version_bumped",
        "serialization_tag_changed",
        "type_field_offset_changed",
        "type_size_changed",
        "used_reserved_field",
    }
)

#: Kinds a binary-only dump derives from a type's layout being present on one
#: side only; excused when it is (:data:`_LAYOUT_PRESENCE`).
LAYOUT_PRESENCE_KINDS: frozenset[str] = frozenset(
    {"type_added", "type_removed", "tag_type_renamed"}
)

#: ``kind -> reason`` a binary-only dump reports and the projection cannot.
ONLY_BINARY_DUMP: dict[str, str] = {
    "func_params_changed": _DEBUG_DECLARATIONS,
    "func_return_changed": _DEBUG_DECLARATIONS,
    "param_pointer_level_changed": _DEBUG_DECLARATIONS,
    "return_pointer_level_changed": _DEBUG_DECLARATIONS,
    "var_type_changed": _DEBUG_DECLARATIONS,
    "var_became_const": _DEBUG_DECLARATIONS,
    "var_lost_const": _DEBUG_DECLARATIONS,
    "typedef_base_changed": _DEBUG_DECLARATIONS,
    "typedef_removed": _DEBUG_DECLARATIONS,
    "integer_model_changed": _DEBUG_DECLARATIONS,
    # Mach-O (dSYM): a variable's kind read from its debug record.
    "cpo_kind_changed": _DEBUG_DECLARATIONS,
    "base_class_offset_changed": _RECORD_RELATIONS,
    "base_class_virtual_changed": _RECORD_RELATIONS,
    "field_access_changed": _RECORD_RELATIONS,
    "field_became_const": _RECORD_RELATIONS,
    "field_became_volatile": _RECORD_RELATIONS,
    "secondary_vtable_group_changed": _RECORD_RELATIONS,
    "type_base_changed": _RECORD_RELATIONS,
    "type_vtable_changed": _RECORD_RELATIONS,
    "vptr_introduced": _RECORD_RELATIONS,
    "internal_type_leaks_via_public_api": _RECORD_RELATIONS,
    # Mach-O: which exports read as internal follows the type scope.
    "visibility_leak": _TYPE_SCOPE,
}

#: ``kind -> reason`` the projection reports and a binary-only dump does not.
ONLY_PROJECTED: dict[str, str] = {
    "visibility_leak": _TYPE_SCOPE,
}


def _findings(old: AbiSnapshot, new: AbiSnapshot) -> set[Finding]:
    return _reported(old, new)[0]


def _reported(old: AbiSnapshot, new: AbiSnapshot) -> tuple[set[Finding], set[Finding]]:
    """The reported findings, and those recorded as out of the public surface."""
    result = compare(old, new)
    return (
        {(c.kind.value, c.symbol) for c in result.changes},
        {(c.kind.value, c.symbol) for c in result.out_of_surface_changes},
    )


def _declared(snap: AbiSnapshot) -> frozenset[str]:
    return frozenset(
        d.mangled for d in (*snap.declarations.functions, *snap.declarations.variables)
    )


def _type_names(snap: AbiSnapshot) -> frozenset[str]:
    return frozenset(
        t.name for t in (*snap.declarations.types, *snap.declarations.enums)
    )


#: Per side (old, new), the type names each run keeps in scope.
TypeScopes = tuple[frozenset[str], frozenset[str]]


def _scope_differs(symbol: str, projected: TypeScopes, oracle: TypeScopes) -> bool:
    """The runs disagree on whether *symbol* (a type, or ``Type::member``)
    is in scope on some side."""
    names = {symbol, symbol.rpartition("::")[0]}
    return any(
        bool(names & p_side) != bool(names & o_side)
        for p_side, o_side in zip(projected, oracle, strict=True)
    )


def _one_sided(symbol: str, types: TypeScopes) -> bool:
    """*symbol* names a type on exactly one side."""
    return (symbol in types[0]) != (symbol in types[1])


def _pair_off(
    only_b: set[Finding], only_p: set[Finding], b_kind: str, p_kind: str
) -> None:
    for kind, sym in sorted(only_b):
        if kind == b_kind and (p_kind, sym) in only_p:
            only_b.discard((kind, sym))
            only_p.discard((p_kind, sym))


@dataclass(frozen=True)
class Run:
    """One comparison's findings and the per-side (old, new) facts the
    residual rules check against."""

    findings: set[Finding]
    out_of_surface: set[Finding]
    declared: tuple[frozenset[str], frozenset[str]]
    types: TypeScopes

    @classmethod
    def of(cls, old: AbiSnapshot, new: AbiSnapshot) -> Run:
        findings, out_of_surface = _reported(old, new)
        return cls(
            findings,
            out_of_surface,
            (_declared(old), _declared(new)),
            (_type_names(old), _type_names(new)),
        )

    def declares_nowhere(self, symbol: str) -> bool:
        return all(symbol not in side for side in self.declared)

    def declares_on_both_sides(self, symbol: str) -> bool:
        return all(symbol in side for side in self.declared)


def _unexplained(projected_run: Run, oracle_run: Run) -> dict[str, list[Finding]]:
    """The divergences no rule in this module accounts for."""
    projected, oracle = projected_run.findings, oracle_run.findings
    only_p, only_b = projected - oracle, oracle - projected
    for b_kind, p_kind in SPELLING_PAIRS:
        _pair_off(only_b, only_p, b_kind, p_kind)
    replacements = 0
    for p_kind in REPLACEMENT_KINDS:
        before = len(only_b)
        _pair_off(only_b, only_p, "func_removed", p_kind)
        replacements += before - len(only_b)
    for kind, sym in sorted(only_b):
        if replacements and kind in {"func_added", "func_added_elf_only"}:
            only_b.discard((kind, sym))
            replacements -= 1
    for kind, name in sorted(only_b):
        if kind in DEBUG_NAME_PAIRING_KINDS:
            only_b.discard((kind, name))
            only_p -= {
                (p_kind, sym)
                for p_kind, sym in only_p
                if p_kind in SYMBOL_PRESENCE_KINDS
                and (sym == name or f"{len(name)}{name}" in sym)
            }
    return {
        "only_binary_dump": sorted(
            f
            for f in only_b
            if _binary_only_reason(f, projected_run, oracle_run) is None
        ),
        "only_projected": sorted(
            f
            for f in only_p
            if _projected_only_reason(f, projected_run, oracle_run) is None
        ),
    }


def _record_field_subject(symbol: str, oracle: Run) -> bool:
    """Whether *symbol* names a record (or ``Record::field``) in the binary-only run.

    A func/var/typedef subject is a mangled export or a typedef name, never a
    record's name, so this separates a record-field finding from the others.
    """
    owner = symbol.rpartition("::")[0]
    return any(symbol in side or owner in side for side in oracle.types)


def _binary_only_reason(finding: Finding, projected: Run, oracle: Run) -> str | None:
    """Why the projection may lack *finding*, or ``None``."""
    kind, sym = finding
    if kind in ONLY_BINARY_DUMP:
        return ONLY_BINARY_DUMP[kind]
    # The re-kinded form of a func/var/typedef retype (ABI-equal, schema v69): same DWARF-declaration evidence; a record field must agree.
    if kind == "abi_equivalent_type_changed" and not _record_field_subject(sym, oracle):
        return _DEBUG_DECLARATIONS
    if finding in projected.out_of_surface:
        return _SURFACE_SCOPE
    if kind in LAYOUT_PRESENCE_KINDS and _one_sided(sym, oracle.types):
        return _LAYOUT_PRESENCE
    if kind in SYMBOL_PRESENCE_KINDS and projected.declares_on_both_sides(sym):
        return _ORACLE_EXPORT_NAMES
    if kind in TYPE_SCOPED_KINDS and _scope_differs(sym, projected.types, oracle.types):
        return _TYPE_SCOPE
    return None


def _projected_only_reason(finding: Finding, projected: Run, oracle: Run) -> str | None:
    """Why the binary-only dump may lack *finding*, or ``None``."""
    kind, sym = finding
    if kind in ONLY_PROJECTED:
        return ONLY_PROJECTED[kind]
    if finding in oracle.out_of_surface:
        return _SURFACE_SCOPE
    if kind in SYMBOL_PRESENCE_KINDS and oracle.declares_nowhere(sym):
        return _ELF_ONLY_INVENTORY
    if kind in TYPE_SCOPED_KINDS and _scope_differs(sym, projected.types, oracle.types):
        return _TYPE_SCOPE
    return None


def _built_case(
    case_name: str, tmp_path: Path, shared_cmake_build_dir: Path | None
) -> tuple[Path, Path, Path | None, Path | None]:
    if CURRENT_PLATFORM not in PLATFORMS.get(case_name, [CURRENT_PLATFORM]):
        pytest.skip(f"{case_name}: not on {CURRENT_PLATFORM}")
    if case_name in BUILD_INFO_CASES or case_name in SOURCE_SMOKE_CASES:
        pytest.skip(f"{case_name}: needs the CLI build/source path")
    if not shutil.which("castxml"):
        pytest.skip("castxml not found in PATH")
    feature = REQUIRES_FEATURE.get(case_name)
    if feature is not None:
        from tests.feature_probe import compiler_supports

        if not compiler_supports(feature):
            pytest.skip(f"{case_name}: compiler lacks {feature!r}")

    import example_catalog  # on sys.path via test_example_autodiscovery

    case_dir = example_catalog.case_dir(case_name)
    v1_src, v2_src, v1_hdr, v2_hdr = _find_sources(case_dir)
    v1_lib, v2_lib = _build_libs_for_case(
        case_name, case_dir, tmp_path, shared_cmake_build_dir, v1_src, v2_src
    )
    return v1_lib, v2_lib, v1_hdr, v2_hdr


@pytest.mark.integration
@pytest.mark.parametrize("case_name", [c for c, _ in _ALL_CASES])
def test_binary_projection_of_binary_only_dump_is_identity(
    case_name: str, tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    from abicheck.dumper import dump

    v1_lib, v2_lib, _, _ = _built_case(case_name, tmp_path, shared_cmake_build_dir)
    old = dump(v1_lib, headers=[], version="v1")
    new = dump(v2_lib, headers=[], version="v2")
    p_old, p_new = project_pair_to_depth(old, new, "binary")
    projected, oracle = _findings(p_old, p_new), _findings(old, new)
    assert projected == oracle, {
        "only_projected": sorted(projected - oracle),
        "only_unprojected": sorted(oracle - projected),
    }


@pytest.mark.integration
@pytest.mark.parametrize("case_name", [c for c, _ in _ALL_CASES])
def test_binary_projection_matches_binary_only_dump(
    case_name: str, tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    from abicheck.dumper import dump

    v1_lib, v2_lib, v1_hdr, v2_hdr = _built_case(
        case_name, tmp_path, shared_cmake_build_dir
    )
    rich_old = dump(v1_lib, headers=_header_list(v1_hdr), version="v1")
    rich_new = dump(v2_lib, headers=_header_list(v2_hdr), version="v2")
    p_old, p_new = project_pair_to_depth(rich_old, rich_new, "binary")
    bin_old = dump(v1_lib, headers=[], version="v1")
    bin_new = dump(v2_lib, headers=[], version="v2")
    unexplained = _unexplained(Run.of(p_old, p_new), Run.of(bin_old, bin_new))
    assert unexplained == {"only_binary_dump": [], "only_projected": []}, unexplained

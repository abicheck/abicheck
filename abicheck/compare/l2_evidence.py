# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Report observations about removed ELF names and changed enumerators."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot, EnumType, Function, Variable
    from ..model.change import Change
    from ..model.elf_facts import ElfImport


from abicheck.model.availability import FactStatus
from abicheck.model.export_index import snapshot_export_names
from abicheck.model.extraction_scope import ownership_of
from abicheck.model.ownership_rules import is_dependency_owner
from abicheck.model.surface_facts import (
    bound_export_spelling,
    is_export_confirmed_absent,
    present_identities,
)
from abicheck.model.vocabulary import AccessLevel

EnumPaths = dict[str, list[list[str]]]


@dataclass(frozen=True)
class EnumExposure:
    fields: EnumPaths
    inline_arguments: EnumPaths
    exported_signatures: EnumPaths


_EXPORT_LOSS_KINDS = frozenset(
    {
        "func_removed",
        "func_removed_elf_only",
        "var_removed",
        "var_removed_elf_only",
        "func_visibility_changed",
        "var_visibility_changed",
        "inline_function_removed",
        "func_deleted_elf_fallback",
        "instantiation_missing_from_binary",
    }
)


def public_enum_arguments(snapshot: AbiSnapshot) -> EnumPaths:
    from ..evidence_depth import resolve_l5_source_graph
    from ..model.graph_identity import decl_node_identity

    paths: EnumPaths = defaultdict(list)
    if not snapshot.elf:
        return paths
    graph = resolve_l5_source_graph(snapshot, snapshot.build_source)
    if not graph:
        return paths
    callers = {
        fn.mangled: fn.name
        for fn in snapshot.declarations.functions
        if fn.is_inline and _public_target(fn)
    }
    for edge in graph.edges:
        if edge.kind != "DECL_REFERENCES_DECL" or edge.role != "enum_argument":
            continue
        caller = decl_node_identity(edge.src)
        member = decl_node_identity(edge.dst)
        if caller is not None and caller in callers and member:
            for callee in (edge.resolved or edge.attrs).get("argument_callees", ()):
                if callee in snapshot.elf.symbol_aliases:
                    paths[member].append([callers[caller], member, callee])
    return paths


def _public_target(decl: object) -> bool:
    owner = ownership_of(decl)
    return bool(owner and owner.owner == "target" and owner.contract == "public")


def _exported_signature_paths(
    snapshot: AbiSnapshot, spellings: dict[str, str], dependency_enums: set[str]
) -> EnumPaths:
    paths: EnumPaths = defaultdict(list)
    if not dependency_enums or not (exports := snapshot_export_names(snapshot)):
        return paths
    for fn in snapshot.declarations.functions:
        if (
            not _public_target(fn)
            or fn.access is AccessLevel.PRIVATE
            or is_export_confirmed_absent(fn)
        ):
            continue
        export = bound_export_spelling(fn) or fn.mangled
        if export not in exports:
            continue
        slots = [("return", fn.return_type, fn.return_type_identities_fact)]
        slots.extend(
            (f"parameter {position}", param.type, param.type_identities_fact)
            for position, param in enumerate(fn.params, 1)
        )
        for slot, spelling, fact in slots:
            identities = present_identities(fact)
            if fact is None or fact.status is not FactStatus.PRESENT:
                # Match a bare spelling only in a global function. Qualified
                # spellings and captured identities also work in namespaces.
                if ("::" in spelling or "::" not in fn.name) and (
                    name := spellings.get(spelling)
                ):
                    identities = (name,)
            for identity in identities:
                if identity in dependency_enums:
                    paths[identity].append([export, slot, identity])
    return paths


def enum_paths(snapshot: AbiSnapshot) -> EnumExposure:
    fields: EnumPaths = defaultdict(list)
    declarations = snapshot.declarations
    spellings = {}
    dependency_enums = set()
    for enum in declarations.enums:
        name = enum.qualified_name or enum.name
        owner = ownership_of(enum)
        if owner and is_dependency_owner(owner.owner):
            dependency_enums.add(name)
        for spelling in (name, f"::{name}", f"enum {name}", f"enum ::{name}"):
            spellings[spelling] = name
    for record in declarations.types:
        if not _public_target(record):
            continue
        for member in record.fields:
            identities = present_identities(member.type_identities_fact)
            if not identities and ("::" in member.type or not record.qualified_name):
                if matched_name := spellings.get(member.type):
                    identities = (matched_name,)
            for identity in identities:
                fields[identity].append(
                    [record.qualified_name or record.name, member.name, identity]
                )
    return EnumExposure(
        fields,
        public_enum_arguments(snapshot),
        _exported_signature_paths(snapshot, spellings, dependency_enums),
    )


def enum_declaration(
    enum: EnumType, member_name: str, paths: EnumExposure
) -> dict[str, object]:
    owner = ownership_of(enum)
    name = enum.qualified_name or enum.name
    return {
        "name": name,
        "header": enum.source_header,
        "location": enum.source_location,
        "owner": owner.owner if owner else "unresolved",
        "public_target": bool(
            owner and owner.owner == "target" and owner.contract == "public"
        ),
        "ownership_rule": owner.rule_id if owner else None,
        "public_field_paths": paths.fields.get(name, []),
        "public_inline_arguments": paths.inline_arguments.get(
            f"{name}::{member_name}", []
        ),
        "public_exported_signatures": paths.exported_signatures.get(name, []),
    }


def attach_removed_exports(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    if (
        old is None
        or new is None
        or old.elf is None
        or new.elf is None
        or old.static_archive
        or new.static_archive
    ):
        return
    from ..model.export_index import ExportTableState, snapshot_export_table_state
    from ..model.owner_recovery import itanium_special_member_owner
    from .export_owner_resolution import declared_special_members

    losses = [
        change for change in result.changes if change.kind.value in _EXPORT_LOSS_KINDS
    ]
    if not losses or snapshot_export_table_state(new) != ExportTableState.READ:
        return
    declarations = {}
    store = old.declarations
    decls: list[Function | Variable] = [
        *store.functions,
        *store.variables,
    ]
    for decl in decls:
        if decl.source_header:
            declarations[bound_export_spelling(decl) or decl.mangled] = (
                decl.source_header
            )
    special_members = declared_special_members(old)
    for change in losses:
        name = change.symbol
        aliases = old.elf.symbol_aliases.get(name)
        if not aliases or name in new.elf.symbol_aliases:
            continue
        header = declarations.get(name)
        owner_header = None
        if header is None and (owner := itanium_special_member_owner(name)):
            candidates = special_members.get((owner.member, owner.qualified_owner), ())
            headers = {c.source_header for c in candidates if c.source_header}
            if len(headers) == 1:
                if owner.owner_carries_template_arguments:
                    owner_header = next(iter(headers))
                else:
                    header = next(iter(headers))
        change.review_evidence = {
            **(change.review_evidence or {}),
            "removed_exports": [
                {
                    "library": old.elf.soname or old.library,
                    "name": name,
                    "version": s.version or None,
                    "default": s.is_default,
                    "binding": s.binding.value,
                }
                for s in aliases
            ],
            "declaration_header": header,
            "owner_template_header": owner_header,
            "client_observations": (change.review_evidence or {}).get(
                "client_observations", []
            ),
            "client_check_status": (change.review_evidence or {}).get(
                "client_check_status", "not_checked"
            ),
        }


def record_client(
    result: DiffResult,
    path: str,
    imports: list[ElfImport] | None,
    role: str = "unspecified",
    link: dict[str, object] | None = None,
) -> None:
    by_name: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    for imp in imports or ():
        by_name[imp.name].append(
            {
                "name": imp.name,
                "version": imp.version or None,
                "binding": imp.binding.value,
                "provider": imp.version_soname or None,
            }
        )
    for change in result.changes:
        evidence: dict[str, Any] = change.review_evidence or {}
        if "removed_exports" in evidence:
            matches = by_name.get(change.symbol, [])
            evidence["client_check_status"] = "see_artifacts"
            evidence["client_observations"].append(
                {
                    "artifact": path,
                    "artifact_role": role,
                    "status": "checked" if imports is not None else "unavailable",
                    "direct_name_matches": matches if imports is not None else None,
                    "match_count": len(matches) if imports is not None else None,
                    "resolution": (link if matches else None)
                    or {"status": "not_tested"},
                }
            )


def enum_value_change(
    old: EnumType,
    new: EnumType,
    member: str,
    old_members: dict[str, int],
    new_members: dict[str, int],
    paths: tuple[EnumExposure, EnumExposure],
) -> Change:
    from ..diff_helpers import make_change
    from ..model.change_catalog.kinds import ChangeKind
    from .enum_sentinel import is_confirmed_enum_sentinel

    kind = (
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED
        if is_confirmed_enum_sentinel(member, old_members, new_members)
        else ChangeKind.ENUM_MEMBER_VALUE_CHANGED
    )
    return make_change(
        kind,
        symbol=f"{old.name}::{member}",
        name=old.name,
        detail=member,
        old_value=str(old_members[member]),
        new_value=str(new_members[member]),
        entity_id=old.entity_id or new.entity_id,
        review_evidence={
            "enum_declarations": {
                "old": enum_declaration(old, member, paths[0]),
                "new": enum_declaration(new, member, paths[1]),
            }
        },
    )

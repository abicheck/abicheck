# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Report observations about removed ELF names and changed enumerators."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot, EnumType, Function, Variable
    from ..model.change import Change
    from ..model.elf_facts import ElfImport


from ..model.extraction_scope import ownership_of
from ..model.surface_facts import bound_export_spelling, present_identities

EnumPaths = dict[str, list[list[str]]]

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
    graph = resolve_l5_source_graph(snapshot, snapshot.build_source)
    if not graph or not snapshot.elf:
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


def enum_paths(snapshot: AbiSnapshot) -> tuple[EnumPaths, EnumPaths]:
    fields: EnumPaths = defaultdict(list)
    for record in snapshot.declarations.types:
        if not _public_target(record):
            continue
        for member in record.fields:
            identities = present_identities(member.type_identities_fact)
            spelling = member.type.removeprefix("enum ")
            if not identities and ("::" in spelling or not record.qualified_name):
                identities = (spelling.removeprefix("::"),)
            for identity in identities:
                fields[identity].append(
                    [record.qualified_name or record.name, member.name, identity]
                )
    return fields, public_enum_arguments(snapshot)


def enum_declaration(
    enum: EnumType, member_name: str, paths: tuple[EnumPaths, EnumPaths]
) -> dict[str, object]:
    owner = ownership_of(enum)
    name = enum.qualified_name or enum.name
    fields, arguments = paths
    return {
        "name": name,
        "header": enum.source_header,
        "location": enum.source_location,
        "owner": owner.owner if owner else "unresolved",
        "public_target": bool(
            owner and owner.owner == "target" and owner.contract == "public"
        ),
        "ownership_rule": owner.rule_id if owner else None,
        "public_field_paths": fields.get(name, []),
        "public_inline_arguments": arguments.get(f"{name}::{member_name}", []),
    }


def attach_removed_exports(
    result: DiffResult, old: AbiSnapshot | None, new: AbiSnapshot
) -> None:
    if (
        old is None
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
    decls: list[Function | Variable] = [
        *old.declarations.functions,
        *old.declarations.variables,
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
    paths: tuple[tuple[EnumPaths, EnumPaths], tuple[EnumPaths, EnumPaths]],
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

# SPDX-License-Identifier: Apache-2.0
"""Experimental native GCC -> abicheck snapshot adapter. Not a registered CLI backend.

The v2 projection covers declarations, template contract tables and vtables;
remaining limitations are persisted in dump provenance. Failure never returns
an empty successful snapshot. A compiler/plugin pair must match exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ....model import (
    EnumMember,
    EnumType,
    Fact,
    FactStatus,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from ....model.abi_fundamental import AbiFundamental, FundamentalEncoding
from ....model.declaration_headers import HeaderAttributedMap
from ....model.identity import (
    entity_id_for_constant,
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_typedef,
    entity_id_for_variable,
)
from ....model.vocabulary import AccessLevel, ParamKind
from ...header_ast_fields import HeaderAstFields
from ...semantic_normalizer import normalize_header_ast
from .acquisition import HeaderScope
from .contracts import (
    attributes,
    clean,
    leaf,
    qualified,
    scope,
    scope_names,
    templates,
    vtable_fact,
)

#: Booleans the C++ plugin emits for every C++ declaration of that row kind
#: it projects (a function without them has no mangled name and is skipped)
#: but the C plugin never emits, because C has no such property: member
#: cv/static, virtual/pure/deleted and linkage specification on functions,
#: template dependence on records. Variable and field cv are emitted by both.
_CXX_ONLY_FLAGS: dict[str, frozenset[str]] = {
    "function_decl": frozenset(
        {
            "const",
            "volatile",
            "static_member",
            "pure_virtual",
            "deleted",
            "virtual",
            "extern_c",
        }
    ),
    "record_type": frozenset({"dependent"}),
    "union_type": frozenset({"dependent"}),
}


def protocol_flag(row: Any, key: str, language: str) -> bool:
    """A schema-v2 boolean, never an absent key read as ``False``.

    Present: its value. Absent: ``False`` only for a C++-only property under
    the C plugin, where it does not apply. Any other absence is a protocol
    violation (a plugin that does not match this reader), raised as
    ``ValueError`` so the backend fails the dump instead of recording a
    confirmed ``False``.
    """
    if key in row:
        value = row[key]
        if not isinstance(value, bool):
            raise ValueError(f"GCC fact {key!r} is not a boolean: {value!r}")
        return value
    if language == "c" and key in _CXX_ONLY_FLAGS.get(row.get("kind", ""), ()):
        return False
    raise ValueError(f"GCC fact row {row.get('kind')!r} lacks required boolean {key!r}")


def identities(value: Any) -> Any:
    return Fact.present((clean(value),)) if value else Fact.not_collected()


def abi(value: Any) -> Any:
    if value is None:
        return None
    return Fact.present(
        AbiFundamental(
            FundamentalEncoding(value["encoding"]),
            value["size"],
            value["alignment"],
            value["lanes"],
        )
    )


def project(
    rows: Any,
    header: Path,
    *,
    language: Any = "c++",
    public_headers: Any = (),
    public_roots: Any = (),
) -> Any:
    rows = [dict(r) for r in rows]
    header = header.resolve()
    policy = HeaderScope(header, public_headers, public_roots)
    defaults, constraints, patterns = templates(rows, policy)
    types = {}
    enums = {}
    functions = {}
    variables = {}
    aliases = {}
    alias_headers: dict[str, str] = {}
    alias_ids = {}
    alias_abis = {}
    constants = {}
    constant_ids = {}
    type_rows = {
        r["type_uid"]: r
        for r in rows
        if r["kind"] in ("record_type", "union_type", "enumeral_type")
    }
    anonymous_aliases = {
        r["type_uid"]: qualified(r)
        for r in rows
        if r["kind"] == "type_decl"
        and (not r.get("implicit_typedef"))
        and (not r.get("artificial"))
        and policy.public(r.get("source"))
    }
    # A function befriended by a class (C++ only): uid -> the befriending class.
    friend_owner: dict[int, str] = {}
    for r in rows:
        if r["kind"] == "friend":
            friend_owner.setdefault(r["uid"], clean(r["owner"]))
    name_rewrites = {}
    for uid, row in type_rows.items():
        if row.get("anonymous") and uid in anonymous_aliases:
            name_rewrites[clean(row["name"])] = anonymous_aliases[uid]
            row["name"] = anonymous_aliases[uid]
            row["leaf_name"] = anonymous_aliases[uid]

    def resolved_identity(value: Any) -> Any:
        return identities(name_rewrites.get(clean(value), value) if value else None)

    by_name = {clean(r["name"]): r for r in type_rows.values()}
    retained = set()
    pending: list[str] = []

    def references(r: Any) -> Any:
        return [
            r.get("identity"),
            r.get("return_identity"),
            *[
                p.get("identity")
                for p in r.get("parameters") or []
                if isinstance(p, dict)
            ],
            *[f.get("identity") for f in r.get("fields", [])],
            *[b.get("type") for b in r.get("bases", [])],
        ]

    for r in rows:
        if policy.public(r.get("source")):
            pending.extend(v for v in references(r) if v)
    while pending:
        name = clean(pending.pop())
        if name in retained or name not in by_name:
            continue
        retained.add(name)
        pending.extend(v for v in references(by_name[name]) if v)
    for r in rows:
        if not policy.public(r.get("source")) and (
            not (
                r["kind"] in ("record_type", "union_type", "enumeral_type")
                and clean(r["name"]) in retained
            )
        ):
            continue
        if (
            r["kind"] in ("record_type", "union_type", "enumeral_type")
            and r.get("anonymous")
            and (r["type_uid"] not in anonymous_aliases)
        ):
            continue
        src = f"{r['source']}:{r.get('line', 0)}"
        origin = policy.origin(r)
        if r["kind"] in ("record_type", "union_type"):
            name = clean(r["name"])
            dependent = protocol_flag(r, "dependent", language)
            fields = []
            for f in r.get("fields", []):
                if protocol_flag(f, "artificial", language) or not f["name"]:
                    continue
                fields.append(
                    TypeField(
                        name=f["name"],
                        type=clean(f["type"]),
                        offset_bits=f["offset_bits"],
                        is_bitfield=f["bitfield"],
                        bitfield_bits=f["size_bits"] if f["bitfield"] else None,
                        access=AccessLevel(f["access"]),
                        is_const=protocol_flag(f, "const", language),
                        is_volatile=protocol_flag(f, "volatile", language),
                        deprecated_fact=Fact.present(f["deprecated"])
                        if "deprecated" in f
                        else Fact.not_collected(),
                        is_mutable_fact=Fact.present(f["mutable"])
                        if "mutable" in f
                        else Fact.not_collected(),
                        default_fact=Fact.present(f["default"])
                        if "default" in f
                        else Fact.not_collected(),
                        type_identities_fact=resolved_identity(f["identity"]),
                        type_abi_fact=abi(f.get("abi")),
                    )
                )
            bases = r.get("bases", [])
            vtable = vtable_fact(r)
            # Base access specifiers; a base with none recognised (or a plugin
            # build older than the field) leaves the whole map unknown.
            access_map = {clean(b["type"]): b.get("access", "") for b in bases}
            base_access_fact = (
                Fact.not_collected()
                if any(
                    v not in ("public", "protected", "private")
                    for v in access_map.values()
                )
                else Fact.present(access_map)
            )
            types[name] = RecordType(
                name=name,
                qualified_name=name if "::" in name else None,
                kind="union"
                if r["kind"] == "union_type"
                else r.get("record_kind", "struct"),
                is_union=r["kind"] == "union_type",
                is_opaque=r.get("complete") is False,
                deprecated=r.get("deprecated"),
                size_bits=None if dependent else r["size_bits"],
                alignment_bits=r["alignment_bits"],
                data_size_bits=r.get("data_size_bits"),
                is_standard_layout=r.get("standard_layout"),
                is_trivially_copyable=r.get("trivially_copyable"),
                is_final=r.get("final"),
                is_abstract=r.get("abstract"),
                is_template_pattern=dependent,
                fields=fields,
                source_location=src,
                source_header=r["source"],
                origin=origin,
                bases_fact=Fact.present(
                    [clean(b["type"]) for b in bases if not b["virtual"]]
                ),
                virtual_bases_fact=Fact.present(
                    [clean(b["type"]) for b in bases if b["virtual"]]
                ),
                base_access_fact=base_access_fact,
                base_offsets={
                    clean(b["type"]): b["offset_bytes"] * 8
                    for b in bases
                    if b["offset_bytes"] is not None
                },
                vtable_fact=vtable,
                # Itanium: a dynamic class's vptr is at offset 0 (CastXML's rule too).
                vptr_offset_bits_fact=Fact.partial(0 if vtable.value else None)
                if vtable.status is FactStatus.PRESENT
                else Fact.not_collected(),
                specialization_declared_fact=Fact.present(r["specialization_declared"])
                if r.get("specialization_declared") is not None
                else Fact.not_collected(),
                template_param_defaults_fact=Fact.present(
                    patterns["::".join((*scope_names(r), leaf(r).split("<")[0]))]
                )
                if dependent
                and "::".join((*scope_names(r), leaf(r).split("<")[0])) in patterns
                else Fact.not_collected(),
                entity_id=entity_id_for_type(scope(r), leaf(r)),
            )
        elif r["kind"] == "enumeral_type":
            name = clean(r["name"])
            if any(m["value"] is None for m in r["members"]):
                continue
            underlying = r.get("underlying_type")
            if not underlying and r["size_bits"] in (8, 16, 32, 64):
                underlying = ("u" if r["unsigned"] else "") + f"int{r['size_bits']}_t"
            enums[name] = EnumType(
                name=name,
                qualified_name=name if "::" in name else None,
                members=[EnumMember(m["name"], m["value"]) for m in r["members"]],
                is_scoped=r.get("scoped"),
                deprecated=r.get("deprecated"),
                underlying_type=underlying or "?",
                source_location=src,
                source_header=r["source"],
                origin=origin,
                entity_id=entity_id_for_enum(scope(r), leaf(r)),
            )
        elif r["kind"] == "function_decl" and not protocol_flag(
            r, "artificial", language
        ):
            mangled = r.get("mangled")
            if not mangled:
                continue
            params = []
            for i, p in enumerate(r["parameters"]):
                if p.get("implicit_this") or p.get("artificial"):
                    continue
                t = clean(p["type"])
                kind = ParamKind(p["param_kind"])
                params.append(
                    Param(
                        name=p.get("name") or f"arg{i}",
                        type=t,
                        kind=kind,
                        default=p.get("default"),
                        pointer_depth=p["pointer_depth"],
                        is_restrict=p["restrict"],
                        type_identities_fact=resolved_identity(p["identity"]),
                        type_abi_fact=abi(p.get("abi")),
                    )
                )
            fnleaf = (
                leaf({"name": r["scope"][-1]["name"], "scope": r["scope"][:-1]})
                if r.get("constructor") or r.get("destructor")
                else r["name"]
            )
            if r.get("destructor"):
                fnleaf = "~" + fnleaf
            fnname = "::".join((*scope_names(r), fnleaf))
            functions[mangled] = Function(
                name=fnname,
                mangled=mangled,
                return_type=clean(r["return_type"]),
                params=params,
                return_type_identities_fact=resolved_identity(r["return_identity"]),
                return_type_abi_fact=abi(r.get("return_abi")),
                is_inline=protocol_flag(r, "inline", language),
                is_hidden_friend=r.get("uid") in friend_owner
                if language == "c++"
                else None,
                hidden_friend_owner=friend_owner.get(r.get("uid")),
                hidden_friend_owner_fact=Fact.present(friend_owner[r["uid"]])
                if r.get("uid") in friend_owner
                else Fact.not_applicable()
                if language == "c++"
                else Fact.not_collected(),
                is_compiler_generated=protocol_flag(r, "artificial", language),
                is_explicit=r.get("explicit")
                if r.get("constructor") or r["name"].startswith("operator ")
                else None,
                contract_attributes=attributes(r),
                deprecated_fact=Fact.present(r.get("deprecated")),
                is_override=r.get("override"),
                is_final=r.get("final"),
                ref_qualifier=r.get("ref_qualifier", ""),
                is_virtual=protocol_flag(r, "virtual", language),
                is_pure_virtual=protocol_flag(r, "pure_virtual", language),
                is_deleted=protocol_flag(r, "deleted", language),
                is_noexcept_fact=Fact.present(r["noexcept"])
                if r.get("noexcept") is not None
                else Fact.not_collected(),
                return_pointer_depth=r.get("return_pointer_depth", 0),
                is_const=protocol_flag(r, "const", language),
                is_volatile=protocol_flag(r, "volatile", language),
                is_static=protocol_flag(r, "static_member", language),
                is_variadic=r.get("variadic"),
                is_extern_c=language == "c" or protocol_flag(r, "extern_c", language),
                vtable_index=r.get("vindex"),
                source_location=src,
                source_header=r["source"],
                origin=origin,
                access=AccessLevel(r["access"]),
                entity_id=entity_id_for_function(
                    scope(r), fnleaf, mangled_name=mangled
                ),
            )
        elif (
            r["kind"] == "var_decl"
            and not protocol_flag(r, "artificial", language)
            and r.get("mangled")
        ):
            name = qualified(r)
            mangled = r["mangled"]
            value = r.get("value_expression")
            variables[mangled] = Variable(
                name=name,
                mangled=mangled,
                type=clean(r["type"]),
                is_const=protocol_flag(r, "const", language),
                value=value,
                deprecated_fact=Fact.present(r.get("deprecated")),
                is_static=not protocol_flag(r, "external_linkage", language),
                alignment_bits=r.get("alignment_bits"),
                access=AccessLevel(r["access"]),
                source_header=r["source"],
                source_location=src,
                origin=origin,
                type_identities_fact=resolved_identity(r["identity"]),
                type_abi_fact=abi(r.get("abi")),
                entity_id=entity_id_for_variable(
                    scope(r),
                    r["name"],
                    mangled_name=mangled,
                    is_extern_c=language == "c",
                ),
            )
            if protocol_flag(r, "const", language) and value is not None:
                constants[name] = value
                constant_ids[name] = entity_id_for_constant(scope(r), r["name"])
        elif (
            r["kind"] == "type_decl"
            and (not r.get("implicit_typedef"))
            and (not r.get("artificial"))
        ):
            name = qualified(r)
            target = clean(r.get("underlying_type", r["type"]))
            target = name_rewrites.get(target, target)
            if name == target:
                continue
            aliases[name] = target
            alias_headers[name] = r.get("source") or ""
            alias_ids[name] = entity_id_for_typedef(scope(r), r["name"])
            if r.get("abi"):
                alias_abis[name] = abi(r["abi"])
    aliases = HeaderAttributedMap(aliases, alias_headers)
    ir = normalize_header_ast(
        types=types.values(),
        enums=enums.values(),
        functions=functions.values(),
        variables=variables.values(),
        constants=constants,
        constant_entity_ids=constant_ids,
        typedefs_qualified=aliases,
        typedef_entity_ids=alias_ids,
        producer="gcc-experimental",
    )
    public_macros = {
        r["name"]: r
        for r in rows
        if r["kind"] == "macro" and policy.public(r.get("source"))
    }
    gaps = (
        "Experimental GCC v2: template overload constraints, anonymous/local declarations, native header graph and macro comparison are incomplete.",
    )
    provenance = {
        "experimental_gcc_schema": 2,
        "compiler": rows[0],
        "limitations": list(gaps),
        "extraction_limitations": list(gaps),
        "public_headers": sorted(str(p) for p in policy.headers),
        "public_roots": [str(p) for p in policy.roots],
        "public_macros": public_macros,
        "variable_storage": {
            qualified(r): {"tls": r.get("tls"), "volatile": r.get("volatile")}
            for r in rows
            if r["kind"] == "var_decl" and policy.public(r.get("source"))
        },
        "vtable_slots": {
            r["name"]: r.get("vtable_initializers")
            for r in rows
            if r["kind"] == "record_type"
            and policy.public(r.get("source"))
            and r.get("vtable_initializers")
        },
    }
    return HeaderAstFields(
        functions=tuple(functions.values()),
        variables=tuple(variables.values()),
        types=tuple(types.values()),
        enums=tuple(enums.values()),
        typedefs=aliases,
        typedefs_qualified=aliases,
        typedef_entity_ids=alias_ids,
        typedef_abi_facts=alias_abis,
        constants=constants,
        constant_entity_ids=constant_ids,
        semantic_ir=ir,
        cxx_template_defaults=defaults if language == "c++" else None,
        cxx_concept_constraints=constraints if language == "c++" else None,
        std_reexports=std_reexports(rows, policy) if language == "c++" else None,
        extraction_provenance=provenance,
    )


def _namespace_path(row: dict[str, Any]) -> tuple[str, ...] | None:
    """A row's enclosing namespace path; ``None`` below a record or an
    anonymous namespace (no re-export can be declared there)."""
    out: list[str] = []
    for part in row.get("scope") or ():
        if part.get("kind") not in ("namespace", "inline_namespace"):
            return None
        out.append(str(part.get("name") or ""))
    return tuple(out)


def std_reexports(rows: list[dict[str, Any]], policy: Any) -> dict[str, str] | None:
    """The public headers' ``using std::X;`` re-exports from the plugin's
    ``using`` rows. GCC records no position for a using-declaration, so the
    declaring namespace's own declarations stand in for its provenance, and
    a re-export in a namespace with none leaves the table ``None``
    (``extract/headers/std_reexports.py``, ``namespace_origin``)."""
    from ..std_reexports import namespace_origin, reexport_entry

    own_files: dict[tuple[str, ...], list[str | None]] = {}
    entries: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    for r in rows:
        path = _namespace_path(r)
        if not path:
            continue
        if r["kind"] != "using":
            own_files.setdefault(path, []).append(r.get("source"))
            continue
        target = "::".join(p for p in (r.get("target_scope"), r.get("name")) if p)
        entry = reexport_entry(path, target)
        if entry is not None:
            entries.setdefault(path, []).append(entry)
    out: dict[str, str] = {}
    for path, found in entries.items():
        origin = namespace_origin(own_files.get(path, ()), policy.public)
        if origin is None:
            return None  # the re-exports' provenance cannot be established
        if origin:
            for entry in found:
                out.setdefault(*entry)
    return dict(sorted(out.items()))

# Copyright 2026 Nikolay Petrov
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

"""Record-entity parsing for the clang backend (ADR-061 D9).

Third entity module split out of ``_ClangAstParser`` proper, after
``enums.py`` and ``functions.py``. Reads the ``_Decl`` lists
``dumper_clang._ClangAstParser._walk`` already categorized (no traversal of
its own) and produces ``RecordType`` model objects, including the vtable
reconstruction walk that recovers a keyword-less virtual override
(``dumper_clang_vtable.build_vtable`` over ``RecordVtableIndex.
base_lookup_index()``).

This is the fullest test of the shared-context design on THIS backend so
far, following castxml's own ``records.py`` slice: unlike ``enums.py``/
``functions.py``, record parsing reads ``RecordVtableIndex`` state that was
put on ``context.py`` specifically because record-entity parsing (not just
function-entity parsing) needs it. Unlike castxml's ``records.py``, nothing
here *mutates* shared context state -- clang's vtable recovery
(``build_vtable``) is a pure function over ``base_lookup_index()``, with no
analogue of castxml's ``vtable_slot_root``/``vtable_slot_extra_roots``
memoization caches.

``access_level``/``clang_deprecated_message``/``source_location``/
``is_record_definition``/``build_vtable`` are NOT *implemented* here even
though record parsing needs all five: the first three are also read by
function/variable/typedef/enum parsing (some still in ``dumper_clang.py``),
so per this package's own "shared across entity kinds" rule they live in
``context.py`` instead (mirroring castxml's ``location.py`` role); the
latter two already live in ``dumper_clang_vtable.py`` under public names
(``is_record_definition``, ``build_vtable``) for the identical reason.
``decl_is_public`` is likewise read by constant parsing (``dumper_clang.py``'s
still-unmigrated ``parse_constants``) as well as record parsing, so it moved
into ``context.py`` alongside this module rather than living only here --
the same "public-ize in place rather than force a one-sided move" treatment
this package's own ``AGENTS.md`` prescribes, applied proactively this slice
per Codex's prior review round on ``is_record_definition``/the clang
attribute helpers.

``record_kind``/``reduce_opaque_kind_set``/``clang_record_type_traits``/
``clang_record_is_abstract``/``field_own_cv_source``/``desugared_qualtype``
were previously private (leading-underscore) names in
``dumper_clang_qualifiers.py`` with exactly one external caller apiece (this
module's own predecessor code in ``dumper_clang.py``) -- public-ized in
place this slice, each keeping its old private spelling as a back-compat
alias, rather than physically moving them (which would risk splitting a
qualifier-spelling helper cluster ``dumper_clang_qualifiers.py``'s own
module docstring documents as interdependent).

``evaluate_bitfield_int``/``field_default_value`` are taken as explicit
parameters rather than imported, the same reason ``enums.py::parse_enums``
takes ``evaluate_int`` and ``functions.py::parse_functions`` takes
``default_value``: the real evaluators
(``dumper_clang._evaluated_int_value``/``dumper_clang._initializer_value``,
the latter built on ``dumper_clang._id_index``) depend on
``dumper_clang_expr.py``, which imports ``diff_cxx_rules`` (classified
``compare``) for ``itanium_scope_components`` -- importing either from here
would give this ``extract``-classified package a real ``extract -> compare``
edge. ``_clang_record_is_final``/``_bitfield_width``/
``_anonymous_member_names``/``_parse_bases``/``_owned_tag_id`` had exactly
one caller apiece (record-entity parsing) and moved here as ordinary
record-only helpers, the same treatment ``functions.py``'s own
``_pointer_depth``/``_return_type``/``_is_noexcept_qualifier``/etc. received.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ....dumper_clang_qualifiers import (
    clang_record_is_abstract,
    clang_record_type_traits,
    desugared_qualtype,
    field_own_cv_source,
    record_kind,
    reduce_opaque_kind_set,
)
from ....dumper_clang_vtable import build_vtable, is_record_definition
from ....model import Fact, RecordType, TypeField
from ....model.identity import entity_id_for_type
from .context import (
    _Decl,
    access_level,
    clang_deprecated_message,
    decl_is_public,
    default_record_access as _default_record_access,
    is_builtin_file,
    qualtype,
    source_location,
)

#: Evaluates a (possibly wrapped) clang constant-expression node to an int,
#: or ``None`` when it isn't one. Matches
#: ``dumper_clang._evaluated_int_value``'s signature exactly -- used here
#: only for a bitfield's width expression.
IntEvaluator = Callable[[dict[str, Any]], "int | None"]

#: Evaluates a field's in-class initializer to its snapshot default value (or
#: ``None`` for an unevaluable one). Matches
#: ``dumper_clang._field_initializer_value``'s signature, bound to that
#: parser's own memoized ``_id_index``, exactly.
FieldDefaultValueEvaluator = Callable[[dict[str, Any]], "str | None"]


def parse_types(
    records: list[_Decl],
    typedefs: list[_Decl],
    *,
    pub_header_segs: list[tuple[str, ...]],
    pub_dir_segs: list[tuple[str, ...]],
    have_public_set: bool,
    base_lookup_index: dict[str, dict[str, Any]],
    evaluate_bitfield_int: IntEvaluator,
    field_default_value: FieldDefaultValueEvaluator,
) -> list[RecordType]:
    anon_names = _anon_typedef_names(typedefs)
    best: dict[str, tuple[_Decl, str]] = {}
    order: list[str] = []
    deprecated: dict[str, str] = {}
    opaque_kind_sets: dict[str, set[str]] = {}  # raw kinds of non-def redecls
    for entry in records:
        node = entry.node
        if is_builtin_file(entry.file):
            continue
        name = str(node.get("name", ""))
        if not name:
            name = anon_names.get(str(node.get("id", "")), "")
            if not name:
                continue  # a truly anonymous record (e.g. an inline union member)
        if name.startswith("__"):
            continue
        identity = "::".join([*entry.scope, name]) if entry.scope else name
        if (msg := clang_deprecated_message(node)) is not None:  # most recent wins
            deprecated[identity] = msg
        if not (node_is_def := is_record_definition(node)):
            opaque_kind_sets.setdefault(identity, set()).add(record_kind(node))
        if (existing := best.get(identity)) is None:
            best[identity] = (entry, name)
            order.append(identity)
            continue
        if is_record_definition(existing[0].node):
            continue
        node_pub = decl_is_public(entry, pub_header_segs, pub_dir_segs, have_public_set)
        if node_is_def or (
            node_pub
            and not decl_is_public(
                existing[0], pub_header_segs, pub_dir_segs, have_public_set
            )
        ):
            best[identity] = (entry, name)
    return [
        _build_record(
            (rec := best[identity])[0],
            base_lookup_index,
            evaluate_bitfield_int,
            field_default_value,
            override_name=rec[1],
            is_opaque=not is_record_definition(rec[0].node),
            dep_msg=deprecated.get(identity),
            override_kind=reduce_opaque_kind_set(opaque_kind_sets.get(identity)),
        )
        for identity in order
    ]


def _anon_typedef_names(typedefs: list[_Decl]) -> dict[str, str]:
    """``{anonymous-record-id: typedef-name}`` from the collected typedefs."""
    out: dict[str, str] = {}
    for entry in typedefs:
        tname = str(entry.node.get("name", ""))
        if not tname:
            continue
        rid = _owned_tag_id(entry.node)
        if rid:
            out.setdefault(rid, tname)
    return out


def _build_record(
    entry: _Decl,
    base_lookup_index: dict[str, dict[str, Any]],
    evaluate_bitfield_int: IntEvaluator,
    field_default_value: FieldDefaultValueEvaluator,
    *,
    override_name: str = "",
    is_opaque: bool = False,
    dep_msg: str | None = None,
    override_kind: str | None = None,
) -> RecordType:
    node = entry.node
    kind = override_kind if is_opaque and override_kind else record_kind(node)
    own_name = override_name or str(node.get("name", ""))
    deprecated = dep_msg if dep_msg is not None else clang_deprecated_message(node)
    if is_opaque:
        # Mirrors dumper_castxml.py's `incomplete="1"` branch.
        return RecordType(
            name=own_name,
            kind=kind,
            qualified_name=(
                "::".join([*entry.scope, own_name]) if entry.scope else None
            ),
            size_bits=None,
            alignment_bits=None,
            fields=[],
            bases=[],
            virtual_bases=[],
            vtable=[],
            vptr_offset_bits=None,
            is_union=kind == "union",
            is_opaque=is_opaque,
            is_final=_clang_record_is_final(node),
            # ADR-063 Phase 5: Fact[bool | None] sibling of is_final --
            # _clang_record_is_final() always returns a real bool, so this
            # is a genuine determination even for an opaque/incomplete type.
            is_final_fact=Fact.present(_clang_record_is_final(node)),
            is_standard_layout=None,
            is_trivially_copyable=None,
            is_template_pattern=entry.in_template,
            has_anonymous_aggregate_fields=False,
            source_location=source_location(entry),
            deprecated=deprecated,
            # Empty lists are the parse's own answer -- matches dumper_castxml.py's opaque-record Fact stance.
            bases_fact=Fact.present([]),
            virtual_bases_fact=Fact.present([]),
            vtable_fact=Fact.present([]),
            vptr_offset_bits_fact=Fact.partial(
                None
            ),  # heuristic field (see below), partial even here
            # ADR-063 Phase 2: resolved from the typed scope path the walk
            # recorded, never reconstructed from `qualified_name` (which
            # cannot say whether a segment was a namespace or a record).
            entity_id=entity_id_for_type(entry.scope_path, own_name),
        )
    fields = _parse_fields(node, evaluate_bitfield_int, field_default_value)
    bases, virtual_bases, _base_access = _parse_bases(node)
    injected = _anonymous_member_names(node)
    is_standard_layout, is_trivially_copyable = clang_record_type_traits(node)
    # G31 Phase C: reconstruct the vtable (and, from it, the same
    # 0-if-polymorphic vptr_offset_bits heuristic castxml already uses)
    # via dumper_clang_vtable's own signature-matching walk -- see that
    # module's docstring for why this can't be a simple `node.get
    # ("virtual")` check the way castxml's real semantic analysis allows.
    # Keyed by the SAME qualname _base_qualnames'/_record_index's own
    # lookups use, not `qualified_name` (which is None for a top-level
    # record) -- an anonymous-record's `override_name` never appears in
    # a `bases` array, so using it here (rather than the node's own bare
    # "") is what lets it still resolve if ever referenced as a base.
    own_qualname = "::".join([*entry.scope, own_name]) if entry.scope else own_name
    vtable = build_vtable(own_qualname, base_lookup_index)
    return RecordType(
        name=own_name,
        kind=kind,
        # Namespace/enclosing-class-qualified spelling, set only when it
        # actually differs from the bare name (mirrors castxml's own
        # RecordType.qualified_name convention) -- without this, ANY
        # namespaced/nested clang-parsed type had qualified_name=None, so
        # a lookup keyed on the tool's own fully-qualified
        # getQualifiedNameAsString() spelling (e.g. "ns::Foo") fell back
        # to the bare "Foo" and never matched (Codex review, G28 Phase 4).
        qualified_name=("::".join([*entry.scope, own_name]) if entry.scope else None),
        # ADR-063 Phase 5 (Codex review): entry.scope is a clean structural
        # fact from clang's own tree-shaped JSON AST -- unlike castxml's
        # string-context-chain walk, there is no cycle/depth-cap case to
        # conflate with a genuine "no enclosing scope" here, so an empty
        # entry.scope is always a real, confirmed determination.
        qualified_name_fact=Fact.present(
            "::".join([*entry.scope, own_name]) if entry.scope else None
        ),
        # clang's JSON AST does not compute layout — size/align/offsets are
        # left None so the layout detectors skip an unknown-vs-unknown
        # comparison (DWARF remains the layout authority on this host).
        size_bits=None,
        alignment_bits=None,
        fields=fields,
        bases=bases,
        virtual_bases=virtual_bases,
        vtable=vtable,
        # Same convention as dumper_castxml.py: polymorphic (non-empty
        # vtable) -> vtable pointer at offset 0 (the Itanium ABI's
        # primary-base-at-offset-0 rule); None (unknown) otherwise. Real
        # multi-inheritance secondary-vtable placement is still not
        # tracked by either backend -- see the G31 plan doc.
        vptr_offset_bits=0 if vtable else None,
        is_union=kind == "union",
        is_opaque=is_opaque,
        is_final=_clang_record_is_final(node),
        # ADR-063 Phase 5: Fact[bool | None] sibling of is_final -- see
        # the opaque branch above for why this is always a real
        # determination, never a placeholder.
        is_final_fact=Fact.present(_clang_record_is_final(node)),
        # G31 Phase C: unlike layout (size/align/offsets), these are
        # semantic type traits clang's AST computes independent of any
        # layout pass, and are genuinely absent from CastXML's own schema
        # (see dumper_castxml.py's own is_standard_layout/
        # is_trivially_copyable comment) — the direct-clang backend is
        # the one place these can actually be populated.
        is_standard_layout=is_standard_layout,
        is_trivially_copyable=is_trivially_copyable,
        is_template_pattern=entry.in_template,
        # True only when *every* field came from the anonymous-aggregate
        # flatten, not merely "at least one did" (Codex review): a mixed
        # record like `struct Foo { union { int i; }; int tag; };` would
        # otherwise report the flag for `tag` too, letting the DWARF
        # layout-backfill exact-match branch trust an unrelated empty
        # DWARF candidate for a field (`tag`) the flag was never meant to
        # vouch for.
        has_anonymous_aggregate_fields=bool(injected)
        and all(f.name in injected for f in fields),
        source_location=source_location(entry),
        deprecated=deprecated,
        # G31 Phase C backend audit -- see clang_record_is_abstract.
        is_abstract=clang_record_is_abstract(node),
        # Stated explicitly -- this parse genuinely resolved these. vptr_offset_bits_fact is `partial`, not `present`: 0-if-vtable-else-None is the Itanium primary-base heuristic, not a real offset read (matches vptr_offset_bits's own PARTIAL row).
        bases_fact=Fact.present(bases),
        virtual_bases_fact=Fact.present(virtual_bases),
        vtable_fact=Fact.present(vtable),
        vptr_offset_bits_fact=Fact.partial(0 if vtable else None),
        # ADR-063 Phase 2 -- see the opaque branch above.
        entity_id=entity_id_for_type(entry.scope_path, own_name),
    )


def _parse_fields(
    node: dict[str, Any],
    evaluate_bitfield_int: IntEvaluator,
    field_default_value: FieldDefaultValueEvaluator,
) -> list[TypeField]:
    # Members injected from an anonymous struct/union are referenced by
    # ``IndirectFieldDecl`` siblings; collect their names so the anonymous
    # record's FieldDecls can be flattened up into this record (and so a
    # typedef'd anonymous record, which has no IndirectFieldDecl, is not).
    injected = _anonymous_member_names(node)
    return _collect_fields(
        node,
        _default_record_access(node),
        injected,
        evaluate_bitfield_int,
        field_default_value,
    )


def _collect_fields(
    node: dict[str, Any],
    running: str,
    injected: set[str],
    evaluate_bitfield_int: IntEvaluator,
    field_default_value: FieldDefaultValueEvaluator,
    *,
    nested: bool = False,
) -> list[TypeField]:
    fields: list[TypeField] = []
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        if kind == "AccessSpecDecl":
            running = child.get("access", running)
            continue
        if kind in ("RecordDecl", "CXXRecordDecl") and not child.get("name"):
            # Anonymous struct/union member: its public members live directly
            # in the enclosing record's namespace, so flatten them here. Keep
            # only the injected names to avoid pulling in a typedef'd
            # anonymous record's fields.
            fields.extend(
                _collect_fields(
                    child,
                    running,
                    injected,
                    evaluate_bitfield_int,
                    field_default_value,
                    nested=True,
                )
            )
            continue
        if kind != "FieldDecl":
            continue
        fname = str(child.get("name", ""))
        if not fname:
            continue
        if nested and fname not in injected:
            # A nested unnamed record contributes only the members that an
            # IndirectFieldDecl injected (anonymous aggregate); a typedef'd
            # anonymous record injects nothing, so its fields are dropped.
            continue
        fields.append(
            _make_field(
                child,
                child.get("access", running),
                evaluate_bitfield_int,
                field_default_value,
            )
        )
    return fields


def _make_field(
    child: dict[str, Any],
    access: str,
    evaluate_bitfield_int: IntEvaluator,
    field_default_value: FieldDefaultValueEvaluator,
) -> TypeField:
    ftype = qualtype(child)
    cv_type = field_own_cv_source(desugared_qualtype(child))
    bits, is_bitfield = _bitfield_width(child, evaluate_bitfield_int)
    return TypeField(
        name=str(child.get("name", "")),
        type=ftype,
        offset_bits=None,
        is_bitfield=is_bitfield,
        bitfield_bits=bits,
        is_const=bool(re.search(r"\bconst\b", cv_type)),
        is_volatile=bool(re.search(r"\bvolatile\b", cv_type)),
        is_mutable=bool(child.get("mutable")),
        access=access_level(access),
        # field_default_value is only actually invoked deep inside the real
        # evaluator's own constant-expr walk, and only for a real
        # referencedDecl -- never for an ordinary literal default or a field
        # with no initializer at all (Codex review, two rounds against the
        # pre-split `dumper_clang._make_field`: passing the CALLED evaluator
        # built the whole-AST id index eagerly for every field regardless;
        # gating only on hasInClassInitializer still left every LITERAL
        # default, e.g. `int timeout = 30;`, paying for it too, since that
        # never reaches the referencedDecl branch either). The
        # hasInClassInitializer ternary itself stays, to skip the call
        # entirely for a field with no initializer.
        default=(
            field_default_value(child) if child.get("hasInClassInitializer") else None
        ),
        deprecated=clang_deprecated_message(child),
    )


def _clang_record_is_final(node: dict[str, Any]) -> bool:
    """Whether a ``CXXRecordDecl`` carries the ``final`` class-virt-specifier.

    Unlike castxml (which exposes ``final`` as a plain XML attribute), clang's
    ``-ast-dump=json`` signals it as a child ``FinalAttr`` node under
    ``"inner"`` rather than a boolean field on the record itself — there is no
    ``node["final"]`` key to read.
    """
    return any(
        isinstance(child, dict) and child.get("kind") == "FinalAttr"
        for child in node.get("inner", []) or []
    )


def _bitfield_width(
    field: dict[str, Any], evaluate_bitfield_int: IntEvaluator
) -> tuple[int | None, bool]:
    """``(width, is_bitfield)`` for a ``FieldDecl`` (width from its inner expr)."""
    if not field.get("isBitfield"):
        return None, False
    for child in field.get("inner", []) or []:
        if isinstance(child, dict):
            return evaluate_bitfield_int(child), True
    return None, True


def _anonymous_member_names(node: dict[str, Any]) -> set[str]:
    """Names injected into *node* from anonymous struct/union members.

    clang emits an ``IndirectFieldDecl`` for every member that an anonymous
    aggregate injects into its enclosing record; their names mark exactly which
    of the anonymous record's fields belong to this record's surface.
    """
    names: set[str] = set()
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "IndirectFieldDecl":
            name = child.get("name")
            if name:
                names.add(str(name))
    return names


def _parse_bases(node: dict[str, Any]) -> tuple[list[str], list[str], dict[str, str]]:
    """Direct base names, virtual base names, and base→access from a record node.

    clang emits base specifiers as a ``bases`` array on the ``CXXRecordDecl``
    definition; each entry carries the base ``type.qualType``, its ``access``,
    and an ``isVirtual`` flag. Absent on a non-polymorphic C ``RecordDecl``.
    """
    bases: list[str] = []
    virtual_bases: list[str] = []
    access: dict[str, str] = {}
    for b in node.get("bases", []) or []:
        if not isinstance(b, dict):
            continue
        type_obj = b.get("type")
        bname = str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
        if not bname:
            continue
        if b.get("isVirtual"):
            virtual_bases.append(bname)
        else:
            bases.append(bname)
        access[bname] = str(b.get("access", "public"))
    return bases, virtual_bases, access


def _owned_tag_id(typedef_node: dict[str, Any]) -> str:
    """The clang id of an anonymous tag a typedef *owns*, or ``""``.

    For ``typedef struct {…} Foo;`` clang nests an ``ElaboratedType`` under the
    ``TypedefDecl`` whose ``ownedTagDecl`` points at the unnamed ``RecordDecl``
    that holds the fields. Returns that record's ``id`` so parse_types can emit
    the otherwise-anonymous record under the typedef name.
    """

    def _scan(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        owned = node.get("ownedTagDecl")
        if isinstance(owned, dict) and isinstance(owned.get("id"), str):
            return str(owned["id"])
        for child in node.get("inner", []) or []:
            found = _scan(child)
            if found:
                return found
        return ""

    return _scan(typedef_node)

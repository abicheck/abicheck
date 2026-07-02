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

"""Source ABI linker (ADR-030 D5).

Folds per-TU :class:`SourceAbiTu` dumps into one per-library
:class:`SourceAbiSurface`, linking source declarations against the library's
exported binary symbols (from L0) and public-header set — the same conceptual
flow as Android's ``header-abi-linker`` (ADR-030 references), without adopting
its unstable intermediate formats.

Linking is cheap relative to parsing, so it is recomputed rather than cached
(ADR-030 D8); only the per-TU dumps are cached.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .source_abi import SourceAbiSurface, SourceAbiTu, SourceEntity

#: C++ Itanium ctor/dtor "ABI clone" tags. The compiler emits *several* object
#: symbols for one source ctor/dtor — ``C1`` (complete), ``C2`` (base), ``C3``
#: (allocating); ``D0`` (deleting), ``D1`` (complete), ``D2`` (base) — plus GCC's
#: non-standard unified ``C4``/``D4`` marker seen in DWARF linkage names. A source
#: extractor sees ONE ``Decl`` and emits one mangled name (usually ``C1``/``D1``,
#: or ``C4``/``D4`` from DWARF). An exact-string match therefore attributes only
#: *one* clone to the declaration and reports the siblings as "exported symbol
#: with no source decl" — a systematic under-count for every class with exported
#: ctors/dtors. Folding the tag to a canonical marker lets the single declaration
#: claim all of its clone symbols (ADR-030 D5 symbol linking).
_CTOR_DTOR_TAGS = frozenset({"C1", "C2", "C3", "C4", "D0", "D1", "D2", "D4"})


def _skip_e_terminated(symbol: str, i: int) -> int:
    """Return the index just past the ``E`` that closes the ``I``/``N`` at *i*.

    Balances nested ``I`` (template-args) and ``N`` (nested-name) productions and
    consumes length-prefixed ``<source-name>`` components *wholesale* — so their
    interior characters (which can include ``I``/``N``/``E``) never miscount.
    Without this, a nested type inside a template argument (e.g. the
    ``NSt7__cxx11…E`` in ``std::vector<std::string>``) would close the balance
    early and the real ctor tag that follows would be missed (Codex review).

    Best-effort: on any unrecognized production it advances one character, so an
    exotic tail (a non-type-template ``L…E`` literal, say) can only cause a
    *missed* fold — never a wrong one, preserving the no-false-fold guarantee.
    """
    n = len(symbol)
    depth = 1  # symbol[i] is the opener
    i += 1
    while i < n and depth:
        c = symbol[i]
        if c == "L":
            # <expr-primary> literal := L <type> <value> E. Its VALUE is raw
            # digits (a non-type template parameter, e.g. `Fixed<3>` → `Li3E`),
            # NOT a length-prefixed source-name — so consume the literal to its
            # own matching E flatly (digits are literal chars here), only
            # balancing any nested I/N/L in its type. Without this the digit
            # would be misread as a length and swallow the trailing ctor tag
            # (Codex review).
            ldepth = 1
            i += 1
            while i < n and ldepth:
                d = symbol[i]
                if d in "INL":
                    ldepth += 1
                elif d == "E":
                    ldepth -= 1
                i += 1
            continue
        if c.isdigit():  # <source-name> — consume by its declared length
            j = i
            while j < n and symbol[j].isdigit():
                j += 1
            i = j + int(symbol[i:j])
            continue
        if c in "IN":
            depth += 1
        elif c == "E":
            depth -= 1
        i += 1
    return i


def _ctor_dtor_canonical(symbol: str) -> str:
    """Fold a genuine Itanium ``<ctor-dtor-name>`` to a single canonical marker.

    ``_ZN3FooC1Ev``/``_ZN3FooC2Ev``/``_ZN3FooC4Ev`` fold to one key (and likewise
    the ``D0``/``D1``/``D2``/``D4`` destructor variants), so a single source
    ctor/dtor declaration claims all of its exported ABI clones.

    Only a *genuine* special name is folded — one that sits at a ``<nested-name>``
    component boundary, right after the class ``<source-name>``. A ``C1``/``D0``
    that is merely the tail of a length-prefixed **ordinary identifier** (a
    function literally named ``AC1`` mangles as ``_ZN1N3AC1Ev``) must NOT fold,
    or two unrelated symbols (``AC1``/``AC2``) would collide and one would be
    dropped from ``symbols_without_decl`` (Codex review). To tell them apart the
    nested name is parsed component-by-component: length-prefixed identifiers are
    consumed *wholesale* (by their declared length), so their interior characters
    — including any ``C1``/``D0`` — never reach the tag test. A no-op for any name
    without a genuine tag, so non-ctor/dtor symbols keep exact-match semantics.
    """
    # A ctor/dtor is always a class member → a nested name. Non-nested symbols
    # (plain ``_Z…``, vtables/typeinfo ``_ZTV``/``_ZTI``, data) have none.
    if not symbol.startswith("_ZN"):
        return symbol
    i, n = 3, len(symbol)
    # Leading CV-/ref-qualifiers on the implicit object parameter.
    while i < n and symbol[i] in "rVKRO":
        i += 1
    # ``boundary`` = we are at a clean prefix-component boundary (the previous
    # token was a fully-consumed source-name / substitution / template-arg list),
    # so a leading ``C``/``D`` here can only be a ctor/dtor special name — never
    # the middle of an identifier.
    boundary = False
    while i < n:
        c = symbol[i]
        if c == "E":  # end of the nested-name
            break
        if c.isdigit():  # <source-name> := <len> <identifier> — consume wholesale
            j = i
            while j < n and symbol[j].isdigit():
                j += 1
            i = j + int(symbol[i:j])
            boundary = True
            continue
        if c == "I":  # <template-args> := I … E (skip the balanced, nested run)
            i = _skip_e_terminated(symbol, i)
            boundary = True
            continue
        if c == "S":  # <substitution> := S_ | S<id>_ | S[abisod]
            if i + 1 < n and symbol[i + 1] in "atbsiod":
                i += 2
            else:
                i += 1
                while i < n and symbol[i] != "_":
                    i += 1
                i += 1  # consume the trailing '_'
            boundary = True
            continue
        if boundary and c in "CD" and symbol[i : i + 2] in _CTOR_DTOR_TAGS:
            return symbol[:i] + c + "@" + symbol[i + 2 :]
        # Unknown production: advance without claiming a boundary, so a later
        # C1/D0 reached only by char-skip is never mistaken for a special name.
        boundary = False
        i += 1
    return symbol


def _build_export_index(exported: set[str]) -> dict[str, list[str]]:
    """Index ctor/dtor canonical forms → the concrete exported clone symbols.

    Only names whose canonical form *differs* (i.e. actual ctor/dtor symbols) are
    indexed, so the map stays small and a non-ctor symbol can never collide into
    it — those still match exactly against ``exported``.
    """
    index: dict[str, list[str]] = {}
    for sym in exported:
        canon = _ctor_dtor_canonical(sym)
        if canon != sym:
            index.setdefault(canon, []).append(sym)
    return index


def _match_export(
    export_sym: str, exported: set[str], ctor_dtor_index: dict[str, list[str]]
) -> tuple[str, list[str]]:
    """Resolve a decl's export name to ``(primary_symbol, all_clone_symbols)``.

    Exact match wins; its ctor/dtor siblings (if any were also exported) are
    folded in so none is orphaned. When there is no exact hit but the name is a
    ctor/dtor, it is matched against the canonical index so a decl mangled as
    ``C1``/``D1`` (or the DWARF ``C4``/``D4``) still claims the ``C2``/``D2``/…
    clones the binary actually exports. Returns ``("", [])`` when nothing matches.
    """
    if not export_sym:
        return "", []
    canon = _ctor_dtor_canonical(export_sym)
    clones = ctor_dtor_index.get(canon)
    if export_sym in exported:
        if clones:
            return export_sym, sorted(set(clones) | {export_sym})
        return export_sym, [export_sym]
    if clones:
        variants = sorted(clones)
        return variants[0], variants
    return "", []


#: Entity kinds routed to each reachable bucket of the linked surface (D5).
_TYPE_KINDS = frozenset({"record", "enum", "typedef", "union"})
_MACRO_KINDS = frozenset({"macro"})
_TEMPLATE_KINDS = frozenset({"template"})
_INLINE_KINDS = frozenset({"inline"})
#: Everything else (function/method/variable/constexpr) is a declaration.

#: Visibility values that put an entity on the public source surface.
_PUBLIC_VISIBILITY = frozenset({"public_header", "generated"})


def _is_public(entity: SourceEntity) -> bool:
    """Whether an entity belongs to the public source surface (D5 roots).

    An entity is public when it is API-relevant and either declared in a public
    (or generated public) header, or its origin marks it as a public header.
    """
    if not entity.api_relevant:
        return False
    if entity.visibility in _PUBLIC_VISIBILITY:
        return True
    loc = entity.source_location
    return bool(loc and loc.origin in ("PUBLIC_HEADER", "GENERATED"))


def link_source_abi(
    tus: Iterable[SourceAbiTu],
    *,
    exported_symbols: Iterable[str] = (),
    library: str = "",
    target_id: str = "",
    forced_public: Iterable[str] = (),
) -> SourceAbiSurface:
    """Link per-TU dumps into one library source ABI surface (ADR-030 D5).

    ``exported_symbols`` are the L0 dynamic exports (mangled names). A public
    source declaration that maps to one of them is shipped; one that does not is
    recorded under ``unmatched.decls_without_symbol`` and mapped to ``""`` so the
    diff can later flag a lost mapping (``source_decl_binary_symbol_mismatch``).
    ``forced_public`` names declarations the policy forces onto the surface even
    without a public-header origin.
    """
    exported = set(exported_symbols)
    forced = set(forced_public)
    surface = SourceAbiSurface(library=library, target_id=target_id)
    surface.roots["exported_symbols"] = sorted(exported)
    surface.roots["forced_public"] = sorted(forced)

    state = _LinkState()
    state.export_index = _build_export_index(exported)
    for tu in tus:
        for header in tu.public_header_roots:
            surface.mappings["public_header_to_target"][header] = (
                tu.target_id or target_id
            )
        for entity in tu.all_entities():
            if not (_is_public(entity) or entity.qualified_name in forced):
                continue
            state.public_decl_ids.append(entity.id)
            _route_entity(entity, surface, state, exported)

    surface.roots["public_header_declarations"] = sorted(set(state.public_decl_ids))
    surface.mappings["source_decl_to_binary_symbol"] = dict(
        sorted(state.decl_to_symbol.items())
    )
    surface.odr_conflicts = state.odr_conflicts
    surface.unmatched["symbols_without_decl"] = sorted(exported - state.matched_symbols)
    surface.unmatched["decls_without_symbol"] = sorted(
        state.identity_to_qname.get(key, key)
        for key, sym in state.decl_to_symbol.items()
        if not sym
    )
    surface.coverage = {
        "reachable_declarations": len(surface.reachable_declarations),
        "reachable_types": len(surface.reachable_types),
        "reachable_macros": len(surface.reachable_macros),
        "reachable_templates": len(surface.reachable_templates),
        "reachable_inline_bodies": len(surface.reachable_inline_bodies),
        "exported_symbols": len(exported),
        "matched_symbols": len(state.matched_symbols),
        "odr_conflicts": len(state.odr_conflicts),
    }
    return surface


def relink_surface_exports(
    surface: SourceAbiSurface, exported_symbols: Iterable[str]
) -> SourceAbiSurface:
    """Re-derive a linked surface's L0-export mapping against a new export set.

    The parallel-baseline ``merge`` flow links the source surface with no binary
    present, so its ``source_decl_to_binary_symbol`` mapping is all-misses and the
    provenance/mapping checks are inert. Given the binary side's exported symbols,
    recompute ``roots['exported_symbols']`` and the decl→symbol mapping in place
    from the already-recorded public declarations — using exactly the same rule
    as :func:`link_source_abi` (``mangled_name or qualified_name`` matched against
    the export set), so the result is identical to what ``dump <binary> --sources``
    would have produced and introduces no new behaviour. Mutates and returns
    *surface*.
    """
    exported = set(exported_symbols)
    surface.roots["exported_symbols"] = sorted(exported)
    export_index = _build_export_index(exported)
    mapping: dict[str, str] = {}
    matched: set[str] = set()
    # identity -> display name, so the recomputed decls_without_symbol carries the
    # same qualified-name labels the original link produced rather than raw keys.
    identity_to_qname: dict[str, str] = {}
    for entity in surface.reachable_declarations:
        key = entity.identity()
        if not key:
            continue
        identity_to_qname[key] = entity.qualified_name or key
        export_sym = entity.mangled_name or entity.qualified_name
        primary, variants = _match_export(export_sym, exported, export_index)
        if primary:
            mapping[key] = primary
            matched.update(variants)
        else:
            mapping.setdefault(key, "")
    surface.mappings["source_decl_to_binary_symbol"] = dict(sorted(mapping.items()))
    surface.unmatched["symbols_without_decl"] = sorted(exported - matched)
    # Recompute decls_without_symbol from the new mapping: declarations that now
    # resolve to an export must drop out of the unmatched list, or the merged
    # surface would serialize contradictory facts (mapping says foo->foo while
    # decls_without_symbol still reports foo as unmatched).
    surface.unmatched["decls_without_symbol"] = sorted(
        identity_to_qname.get(key, key) for key, sym in mapping.items() if not sym
    )
    if isinstance(surface.coverage, dict):
        surface.coverage["exported_symbols"] = len(exported)
        surface.coverage["matched_symbols"] = len(matched)
    return surface


@dataclass
class _LinkState:
    """Mutable accumulators threaded through the per-entity routing helpers."""

    decl_to_symbol: dict[str, str] = field(
        default_factory=dict
    )  # identity -> symbol ("" if none)
    identity_to_qname: dict[str, str] = field(
        default_factory=dict
    )  # identity -> qualified_name
    # (qualified_name, declaring header) -> type_hash, for ODR detection. The
    # declaring header is part of the key because castxml reports a bare type
    # name (namespace lives in the XML `context`), so a::Widget and b::Widget
    # would otherwise collide into a false odr_source_conflict.
    type_by_name: dict[tuple[str, str], str] = field(default_factory=dict)
    odr_conflicts: list[dict[str, str]] = field(default_factory=list)
    public_decl_ids: list[str] = field(default_factory=list)
    matched_symbols: set[str] = field(default_factory=set)
    #: ctor/dtor canonical form -> exported clone symbols (see _build_export_index)
    export_index: dict[str, list[str]] = field(default_factory=dict)


def _route_entity(
    entity: SourceEntity,
    surface: SourceAbiSurface,
    state: _LinkState,
    exported: set[str],
) -> None:
    """Place one public entity into the right reachable bucket of the surface."""
    if entity.kind in _TYPE_KINDS:
        _route_type(entity, surface, state)
    elif entity.kind in _MACRO_KINDS:
        surface.reachable_macros.append(entity)
    elif entity.kind in _TEMPLATE_KINDS:
        surface.reachable_templates.append(entity)
    elif entity.kind in _INLINE_KINDS:
        surface.reachable_inline_bodies.append(entity)
    else:
        _route_declaration(entity, surface, state, exported)


def _route_type(
    entity: SourceEntity, surface: SourceAbiSurface, state: _LinkState
) -> None:
    """Record a type entity and detect same-name/different-hash ODR conflicts (D5)."""
    surface.reachable_types.append(entity)
    if not entity.qualified_name:
        return
    # Typedefs are kept out of the ODR / source_type_to_debug_type path: a common
    # C self-alias `typedef struct Foo Foo;` (and anonymous-struct typedefs) shares
    # its `(qualified_name, header)` with the `record` the same header defines, so
    # routing the typedef here would collide with that record and emit a spurious
    # odr_source_conflict on an unchanged header (Codex review). Typedef target
    # changes are still surfaced by source_diff._diff_typedefs, which keys by
    # entity identity, so dropping them from the ODR/type-mapping path loses no
    # detection.
    if entity.kind == "typedef":
        return
    # Key ODR detection by (name, declaring header) so same-named types in
    # different namespaces/headers (a::Widget vs b::Widget, which castxml emits
    # with the bare name) don't conflate into a false odr_source_conflict. A
    # genuine ODR conflict (one type, two TUs, divergent definitions) shares
    # both name and header, so it still fires.
    header = entity.source_location.path if entity.source_location else ""
    key = (entity.qualified_name, header)
    prev = state.type_by_name.get(key)
    if prev is not None and prev != entity.type_hash:
        state.odr_conflicts.append(
            {
                "qualified_name": entity.qualified_name,
                # The declaring header is part of the conflict's identity (ODR is
                # keyed by (name, header) above), so the diff can tell a new
                # conflict for a same-named type in a *different* header apart
                # from one already present elsewhere.
                "header": header,
                "old_type_hash": prev,
                "new_type_hash": entity.type_hash,
            }
        )
    else:
        state.type_by_name[key] = entity.type_hash
    surface.mappings["source_type_to_debug_type"][entity.qualified_name] = (
        entity.type_hash
    )


def _route_declaration(
    entity: SourceEntity,
    surface: SourceAbiSurface,
    state: _LinkState,
    exported: set[str],
) -> None:
    """Record a declaration and map it to its exported binary symbol (D5).

    Keyed by the entity's stable identity (mangled name when present), not the
    bare qualified name, so C++ overloads sharing one name (f(int) vs f(double))
    keep independent mappings. The exported symbol is the mangled name for C++ or
    the plain qualified name for C / extern "C" decls whose extractor leaves
    mangled_name empty — matching on either avoids false "unmatched" evidence.
    """
    surface.reachable_declarations.append(entity)
    key = entity.identity()
    if not key:
        return
    state.identity_to_qname[key] = entity.qualified_name or key
    export_sym = entity.mangled_name or entity.qualified_name
    primary, variants = _match_export(export_sym, exported, state.export_index)
    if primary:
        state.decl_to_symbol[key] = primary
        state.matched_symbols.update(variants)
    else:
        state.decl_to_symbol.setdefault(key, "")

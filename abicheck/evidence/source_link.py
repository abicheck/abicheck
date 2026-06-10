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

from .source_abi import SourceAbiSurface, SourceAbiTu, SourceEntity

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

    # entity identity -> exported symbol ("" if none)
    decl_to_symbol: dict[str, str] = {}
    # identity -> qualified_name, for readable reports
    identity_to_qname: dict[str, str] = {}
    # qualified_name -> type_hash, for ODR detection
    type_by_name: dict[str, str] = {}
    odr_conflicts: list[dict[str, str]] = []
    public_decl_ids: list[str] = []
    matched_symbols: set[str] = set()

    for tu in tus:
        for header in tu.public_header_roots:
            surface.mappings["public_header_to_target"][header] = (
                tu.target_id or target_id
            )

        for entity in tu.all_entities():
            public = _is_public(entity) or entity.qualified_name in forced
            if not public:
                continue
            public_decl_ids.append(entity.id)

            if entity.kind in _TYPE_KINDS:
                surface.reachable_types.append(entity)
                if entity.qualified_name:
                    prev = type_by_name.get(entity.qualified_name)
                    if prev is not None and prev != entity.type_hash:
                        odr_conflicts.append(
                            {
                                "qualified_name": entity.qualified_name,
                                "old_type_hash": prev,
                                "new_type_hash": entity.type_hash,
                            }
                        )
                    else:
                        type_by_name[entity.qualified_name] = entity.type_hash
                    surface.mappings["source_type_to_debug_type"][
                        entity.qualified_name
                    ] = entity.type_hash
            elif entity.kind in _MACRO_KINDS:
                surface.reachable_macros.append(entity)
            elif entity.kind in _TEMPLATE_KINDS:
                surface.reachable_templates.append(entity)
            elif entity.kind in _INLINE_KINDS:
                surface.reachable_inline_bodies.append(entity)
            else:
                surface.reachable_declarations.append(entity)
                # Map source declarations to exported binary symbols (D5). Key by
                # the entity's stable identity (mangled name when present), not the
                # bare qualified name, so C++ overloads sharing one name (f(int) vs
                # f(double)) keep independent mappings — dropping one exported
                # overload is then visible instead of being hidden by the other.
                key = entity.identity()
                if key:
                    identity_to_qname[key] = entity.qualified_name or key
                    # The exported binary symbol is the mangled name for C++, or
                    # the plain qualified name for C / extern "C" declarations
                    # whose extractor leaves mangled_name empty. Matching on
                    # either avoids false "unmatched" evidence for C libraries.
                    export_sym = entity.mangled_name or entity.qualified_name
                    if export_sym and export_sym in exported:
                        decl_to_symbol[key] = export_sym
                        matched_symbols.add(export_sym)
                    else:
                        decl_to_symbol.setdefault(key, "")

    surface.roots["public_header_declarations"] = sorted(set(public_decl_ids))
    surface.mappings["source_decl_to_binary_symbol"] = dict(
        sorted(decl_to_symbol.items())
    )
    surface.odr_conflicts = odr_conflicts
    surface.unmatched["symbols_without_decl"] = sorted(exported - matched_symbols)
    surface.unmatched["decls_without_symbol"] = sorted(
        identity_to_qname.get(key, key)
        for key, sym in decl_to_symbol.items()
        if not sym
    )
    surface.coverage = {
        "reachable_declarations": len(surface.reachable_declarations),
        "reachable_types": len(surface.reachable_types),
        "reachable_macros": len(surface.reachable_macros),
        "reachable_templates": len(surface.reachable_templates),
        "reachable_inline_bodies": len(surface.reachable_inline_bodies),
        "exported_symbols": len(exported),
        "matched_symbols": len(matched_symbols),
        "odr_conflicts": len(odr_conflicts),
    }
    return surface

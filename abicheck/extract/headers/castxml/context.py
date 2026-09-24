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

"""Shared parser state for the castxml header-AST backend (ADR-061 D9).

Owns exactly the state every castxml entity-parsing module needs to resolve
a node without re-deriving it: the id-to-element map built by one pass over
the XML root, the exported-symbol sets and public-header scoping
visibility/provenance decisions depend on, the tag-grouped element lists
that let each ``parse_*`` entry point iterate only its own node kind, and
the memoization caches the type-resolution helpers in this package share.

This module does not parse a function, record, enum, or template itself —
that is each entity module's job (``enums.py``, ``functions.py``, and
``records.py``; castxml has no separate template-entity module — see
``abicheck/dumper_castxml.py``'s module docstring for why). An entity
module receives a :class:`CastxmlParserContext` instance
explicitly rather than reading instance state off a monolithic parser
class, per D9's "entity modules parse one class of node using shared
context" — they do not independently open input, resolve global
configuration, or create policy findings.

Canonical entry point: construct a :class:`CastxmlParserContext` and call
:meth:`CastxmlParserContext.build_id_map` once, before any entity module
reads it. ``abicheck.dumper_castxml._CastxmlParser`` is this context's one
production caller today; it holds the state below as ``self._ctx`` and
exposes each field as a read-only property of the same old name so its
still-unmigrated methods, and every existing external caller (tests
included) that reads ``parser._id_map``/``parser._type_name_cache``/etc.
directly, keep resolving unchanged.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element

from ....provenance import build_public_set

# castxml tags that represent a callable (free function, method, special
# member, or operator). Shared with ``_CastxmlParser`` via re-export so the
# existing ``_FUNCTION_TAGS`` class attribute keeps its value unchanged.
FUNCTION_TAGS: tuple[str, ...] = (
    "Function",
    "Method",
    "Constructor",
    "Destructor",
    "Converter",
    "OperatorFunction",
    "OperatorMethod",
)


class CastxmlParserContext:
    """Mutable state shared across every castxml entity-parsing module."""

    def __init__(
        self,
        root: Element,
        exported_dynamic: set[str],
        exported_static: set[str],
        public_header_paths: list[str] | None = None,
        public_dir_paths: list[str] | None = None,
        no_binary_evidence: bool = False,
    ) -> None:
        self.root = root
        self.exported_dynamic = exported_dynamic
        self.exported_static = exported_static
        # Workstream F S1 ("Header-only comparison"): true only for the
        # binary-less header-AST dump path, where exported_dynamic/
        # exported_static are deliberately both empty because there is no
        # binary to have exported anything from -- see
        # `extract.headers.castxml.location.visibility`'s own docstring for
        # what this changes. False (the default) for every ordinary binary
        # dump, unchanged.
        self.no_binary_evidence = no_binary_evidence
        # Public-header surface used to scope constant extraction
        # (parse_constants). Seeded from the parsed headers (-H/--header) plus
        # any explicit public-header inputs, and matched with the same
        # provenance segment logic used elsewhere — so constants reached via
        # an umbrella header or a public include dir are kept, while
        # transitively-included system/private-header constants are excluded.
        # Empty → constant extraction is skipped (provenance is opt-in).
        (self.pub_header_segs, self.pub_dir_segs, self.have_public_set) = (
            build_public_set(public_header_paths, public_dir_paths)
        )
        self.id_map: dict[str, Element] = {}
        self.virtual_methods_by_class: dict[str, list[Element]] = {}
        self.source_lines_cache: dict[str, list[str]] = {}
        # Tag-grouped elements populated by the single pass in build_id_map()
        # below, so parse_functions()/parse_types()/etc. don't each re-scan
        # every top-level element themselves.
        self.function_els: list[Element] = []
        self.variable_els: list[Element] = []
        self.record_els: list[Element] = []
        self.enum_els: list[Element] = []
        self.typedef_els: list[Element] = []
        # Per-id memoization for the recursive type-graph resolvers in
        # ``type_resolution.py``; safe since the XML tree is immutable for
        # this context's lifetime.
        self.type_name_cache: dict[str, str] = {}
        self.pointer_depth_cache: dict[str, int] = {}
        # method element id -> canonical vtable-slot key, resolved through any
        # `overrides` chain. Populated lazily by ``_collect_virtual_methods``
        # (still in ``dumper_castxml.py``); see its docstring for why this is
        # needed alongside vtable_index.
        self.vtable_slot_root: dict[str, int | str] = {}
        # method element id -> any ADDITIONAL slot keys beyond the primary one
        # in vtable_slot_root, for a method that itself overrides more than
        # one base slot (non-virtual multiple inheritance). A further-derived
        # override referencing this id by `overrides` must propagate to every
        # one of these, not just the primary -- see _collect_virtual_methods.
        self.vtable_slot_extra_roots: dict[str, list[int | str]] = {}

    def build_id_map(self) -> None:
        """Build the id map, the virtual-method index, and the tag-grouped
        element lists ``parse_functions()``/``parse_types()``/etc. use.

        A declaration local to a function body is left out of every grouped
        list (it stays in ``id_map``, so a signature naming it still
        resolves). castxml emits one whenever an emitted signature refers to
        it -- a deduced ``auto`` return type that is a local alias, enum or
        class (``inline auto f() { struct V {int x;}; return V{}; }``) -- and
        its ``context`` is the function. It has no linkage and cannot be
        named outside that body, and the clang backend never emits one (its
        walk stops at a function). Grouping it would give it a namespace-
        level identity (``scope_path`` has no function segment to offer),
        colliding with a real ``q::V`` and disagreeing with ``SemanticIR``,
        which rejects the whole dump.
        """
        for el in self.root:
            eid = el.get("id")
            if eid:
                self.id_map[eid] = el
        local: dict[str, bool] = {}
        groups = self._groups_by_tag()
        for el in self.root:
            if self._is_function_local(el, local):
                continue
            self._group(el, groups)

    def _group(self, el: Element, groups: dict[str, list[Element]]) -> None:
        """Add *el* to the virtual-method index and its tag's grouped list."""
        tag = el.tag
        if tag in ("Method", "Destructor") and el.get("virtual") == "1":
            ctx = el.get("context")
            if ctx:
                self.virtual_methods_by_class.setdefault(ctx, []).append(el)
        group = groups.get(tag)
        if group is not None:
            group.append(el)

    def _groups_by_tag(self) -> dict[str, list[Element]]:
        groups = {tag: self.function_els for tag in FUNCTION_TAGS}
        groups.update(
            Variable=self.variable_els,
            Struct=self.record_els,
            Class=self.record_els,
            Union=self.record_els,
            Enumeration=self.enum_els,
            Typedef=self.typedef_els,
        )
        return groups

    def _is_function_local(self, el: Element, memo: dict[str, bool]) -> bool:
        """Whether *el*'s ``context`` chain reaches a function-like element.

        Memoized per context id, so the whole pass stays linear in the
        document size. A cycle (malformed input) answers ``False``: that is
        the pre-existing behaviour, and :func:`.scope.scope_path` guards the
        same chain the same way.
        """
        chain: list[str] = []
        ctx_id = el.get("context", "") or ""
        answer = False
        while ctx_id:
            if ctx_id in memo:
                answer = memo[ctx_id]
                break
            if ctx_id in chain:
                break
            parent = self.id_map.get(ctx_id)
            if parent is None:
                break
            chain.append(ctx_id)
            if parent.tag in FUNCTION_TAGS:
                answer = True
                break
            ctx_id = parent.get("context", "") or ""
        for visited in chain:
            memo[visited] = answer
        return answer

    def resolve(self, id_: str) -> Element | None:
        return self.id_map.get(id_)

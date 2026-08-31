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

"""castxml XML → ABI model parser.

Split from ``dumper.py`` to keep that module under the AI-readiness file-size
soft cap. Re-exported from ``abicheck.dumper`` so existing imports of
``_CastxmlParser``, ``_parse_vtable_index``, and ``_vt_sort_key`` from
``abicheck.dumper`` keep working.

The vtable-index, mangled-name, and synthetic-key helpers now live in
``abicheck.extract.headers.castxml.names`` (ADR-061 Phase 5 item 1) and are
re-exported here so every existing import of them from this module keeps
working unchanged. Parser state (the id map, tag-grouped element lists, and
memoization caches) now lives on a shared ``CastxmlParserContext``
(``extract.headers.castxml.context``), with location resolution
(``location.py``), the type-graph walk (``type_resolution.py``), and enum
parsing (``enums.py``) built on top of it as free functions taking that
context explicitly. Function-entity parsing (``functions.py``) is the
second entity module built the same way; ``qualified_name``/
``decl_is_public``/``visibility``/``access_level`` moved into
``location.py`` rather than ``functions.py`` since typedef/variable/
constant parsing (still here) reads them too. Record-entity parsing
(``records.py``), including the vtable/RTTI layout walk, is the third
entity module split out the same way -- ``ctx.vtable_slot_root``/
``ctx.vtable_slot_extra_roots`` already lived on the shared context (put
there during the ``functions.py`` slice, since clang's analogous
``RecordVtableIndex`` needed the same state), so no context-shape change
was needed to move the code that reads and mutates them. This closes
Phase 5 item 1's parser-split work on the castxml backend: there is no
separate ``templates.py`` here, and none is missing -- castxml's XML output
resolves a class-template specialization down to an ordinary ``Struct``/
``Class`` element indistinguishable (at the AST-node level) from a
non-template record, carrying no ``ClassTemplateSpecializationDecl``-shaped
node, no separate specialization-index pass, and no
``RecordType.is_template_pattern`` concept at all (verified: grepping this
module and every module in ``extract/headers/castxml/`` for
"template"/"specialization"/"Specialization" turns up nothing but this
paragraph and an unrelated cross-reference to ``diff_templates.py``, the
compare-layer detector). Every fact a specialization's own record carries
(fields, bases, vtable/RTTI layout) is already produced by the ordinary
record path ``records.py`` owns -- there is no castxml counterpart to
clang's own template-parameter-kind/default/name reconstruction or
specialization-spelling machinery (contrast ``dumper_clang.py``'s module
docstring, and ``extract.headers.clang.templates``, for why clang's JSON
AST needs that reconstruction and castxml's XML does not). Every method
below that has a counterpart in one of those modules is a thin delegating
wrapper, kept for every existing internal and external caller (tests
included) that still reads ``_CastxmlParser``'s private surface directly.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

from . import dumper_castxml_typedefs as _typedefs_helpers
from .dumper_castxml_typedefs import (
    _deprecation_marker as _deprecation_marker,
    _extract_contract_attributes as _extract_contract_attributes,
)
from .extract.headers.castxml import (
    context as _castxml_context,
    enums as _castxml_enums,
    functions as _castxml_functions,
    location as _castxml_location,
    records as _castxml_records,
    scope as _castxml_scope,
    type_resolution as _castxml_type_resolution,
)
from .extract.headers.castxml.names import (
    _SYNTHETIC_DTOR_KEY_PREFIX as _SYNTHETIC_DTOR_KEY_PREFIX,
    SYNTHETIC_CTOR_KEY_PREFIX as SYNTHETIC_CTOR_KEY_PREFIX,
    _mangled_name_is_local_linkage as _mangled_name_is_local_linkage,
    _parse_vtable_index as _parse_vtable_index,
    _ref_qualifier_from_mangled as _ref_qualifier_from_mangled,
    _virtual_method_mangled_name as _virtual_method_mangled_name,
    _vt_sort_key as _vt_sort_key,
    is_synthetic_ctor_key as is_synthetic_ctor_key,
    is_synthetic_dtor_key as is_synthetic_dtor_key,
)
from .model import (
    AccessLevel,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from .model.identity import ScopePath, entity_id_for_variable
from .provenance import header_from_location


class _CastxmlParser:
    """Parse castxml XML into ABI model objects."""

    def __init__(
        self,
        root: Element,
        exported_dynamic: set[str],
        exported_static: set[str],
        public_header_paths: list[str] | None = None,
        public_dir_paths: list[str] | None = None,
    ):
        # All parser state now lives on a shared context object (ADR-061 D9
        # "context.py") so entity-parsing modules under
        # ``extract.headers.castxml`` can read it without depending on this
        # class. Each field below is still reachable as ``self._xxx`` via the
        # read-only properties following this method, so every method in
        # this class not yet migrated to a shared-context module -- and every
        # external caller (tests included) that reads a parser's private
        # state directly -- keeps working unchanged.
        self._ctx = _castxml_context.CastxmlParserContext(
            root,
            exported_dynamic,
            exported_static,
            public_header_paths,
            public_dir_paths,
        )
        self._ctx.build_id_map()

    # ── shared-context state, exposed for methods not yet migrated ─────────

    @property
    def _root(self) -> Element:
        return self._ctx.root

    @property
    def _exported_dynamic(self) -> set[str]:
        return self._ctx.exported_dynamic

    @property
    def _exported_static(self) -> set[str]:
        return self._ctx.exported_static

    @property
    def _pub_header_segs(self) -> Any:
        return self._ctx.pub_header_segs

    @property
    def _pub_dir_segs(self) -> Any:
        return self._ctx.pub_dir_segs

    @property
    def _have_public_set(self) -> bool:
        return self._ctx.have_public_set

    @property
    def _id_map(self) -> dict[str, Element]:
        return self._ctx.id_map

    @property
    def _virtual_methods_by_class(self) -> dict[str, list[Element]]:
        return self._ctx.virtual_methods_by_class

    @property
    def _source_lines_cache(self) -> dict[str, list[str]]:
        return self._ctx.source_lines_cache

    @property
    def _function_els(self) -> list[Element]:
        return self._ctx.function_els

    @property
    def _variable_els(self) -> list[Element]:
        return self._ctx.variable_els

    @property
    def _record_els(self) -> list[Element]:
        return self._ctx.record_els

    @property
    def _enum_els(self) -> list[Element]:
        return self._ctx.enum_els

    @property
    def _typedef_els(self) -> list[Element]:
        return self._ctx.typedef_els

    @property
    def _type_name_cache(self) -> dict[str, str]:
        return self._ctx.type_name_cache

    @property
    def _pointer_depth_cache(self) -> dict[str, int]:
        return self._ctx.pointer_depth_cache

    @property
    def _vtable_slot_root(self) -> dict[str, int | str]:
        return self._ctx.vtable_slot_root

    @property
    def _vtable_slot_extra_roots(self) -> dict[str, list[int | str]]:
        return self._ctx.vtable_slot_extra_roots

    def _resolve(self, id_: str) -> Element | None:
        return self._ctx.resolve(id_)

    def _source_line_has_explicit(
        self,
        loc_el: Element | None,
        declaration_el: Element | None = None,
    ) -> bool | None:
        """Fallback for castxml Converter nodes that omit explicit="1"."""
        return _castxml_location.source_line_has_explicit(
            self._ctx, loc_el, declaration_el
        )

    # ── type-graph resolution, delegated to extract.headers.castxml.type_resolution ──
    # (ADR-061 D9 "type_resolution.py": entity modules and the still-unmigrated
    # methods below share one context object rather than reading `self`.)

    def _type_name(self, id_: str, depth: int = 0) -> str:
        return _castxml_type_resolution.type_name(self._ctx, id_, depth)

    def _type_name_uncached(self, id_: str, depth: int = 0) -> str:
        return _castxml_type_resolution.type_name_uncached(self._ctx, id_, depth)

    def _cv_qualifies_pointer_value(self, type_id: str) -> bool:
        return _castxml_type_resolution.cv_qualifies_pointer_value(self._ctx, type_id)

    def _type_alignment_bits(self, id_: str, depth: int = 0) -> int | None:
        return _castxml_type_resolution.type_alignment_bits(self._ctx, id_, depth)

    def _resolve_cv_restrict(self, id_: str, depth: int = 0) -> tuple[bool, bool, bool]:
        return _castxml_type_resolution.resolve_cv_restrict(self._ctx, id_, depth)

    def _is_global_scope(self, el: Any) -> bool:
        return _castxml_type_resolution.is_global_scope(self._ctx, el)

    def _qualified_type_name(self, el: Any, leaf_name: str | None = None) -> str | None:
        return _castxml_type_resolution.qualified_type_name(self._ctx, el, leaf_name)

    def _pointer_depth(self, id_: str, depth: int = 0) -> int:
        return _castxml_type_resolution.pointer_depth(self._ctx, id_, depth)

    def _pointer_depth_uncached(self, id_: str, depth: int = 0) -> int:
        return _castxml_type_resolution.pointer_depth_uncached(self._ctx, id_, depth)

    @staticmethod
    def _access_level(el: Element) -> AccessLevel:
        """Map castxml 'access' attribute to AccessLevel enum."""
        return _castxml_location.access_level(el)

    def _variable_value_eligible(self, el: Element) -> bool:
        """``init`` eligible for ``Variable.value``? Mirrors
        ``_iter_public_constants`` below (Codex review, 3 rounds): top-level
        constness (not the loose whole-spelling ``is_const``), public
        access, public header provenance if configured -- but not gated on
        ``_have_public_set`` outright, unlike that opt-in-only method.
        """
        if el.get("access") in ("private", "protected"):
            return False
        if self._have_public_set and not self._decl_is_public(el):
            return False
        return self._resolve_cv_restrict(el.get("type", ""))[0]

    def _visibility(self, mangled: str, name: str = "") -> Visibility:
        """Determine visibility based on ELF symbol tables."""
        return _castxml_location.visibility(self._ctx, mangled, name)

    def _ctor_or_dtor_visibility(
        self,
        raw_mangled: str,
        name: str,
        access: AccessLevel,
        is_deleted: bool,
        is_artificial: bool,
    ) -> Visibility:
        """Visibility for a Constructor or Destructor element, with a
        source-access fallback.

        ``_visibility()`` is an ELF-symbol-table lookup: it needs a real
        mangled name to check. When castxml omits the mangled name for a
        user-declared, overloaded constructor (a documented castxml gap —
        see :func:`_function_mangled_name`'s synthesis comment), the ELF
        lookup can never match *any* overload of that constructor — the
        class's bare name never appears as its own exported symbol (Itanium
        mangling always applies to constructors), so every such overload
        would silently classify HIDDEN regardless of whether it is genuinely
        callable from outside the library. That hid both a removed public
        constructor overload (case78: FUNC_REMOVED never fired for
        ``task_arena(attach_mode_t)``) and an added one (case111: FUNC_ADDED
        never fired for the new ``std::function<int()>`` overload) behind
        `_public_functions()`'s PUBLIC/ELF_ONLY filter.

        castxml ALSO omits the mangled name for every ``<Destructor>``
        (never just user-declared/overloaded ones — a class has at most one
        destructor, so there's no overload-collision risk the way there is
        for constructors), so the exact same problem applies there: a
        removed or added virtual destructor would silently classify HIDDEN
        (Phase 2 castxml↔clang parity gate, PR #582 — discovered by
        comparing real castxml/clang dumps of a multiple/virtual-inheritance
        hierarchy: clang correctly reports a base's virtual destructor as
        PUBLIC while castxml reported it HIDDEN).

        Falls back to the real ELF lookup first (it stays authoritative
        whenever it can actually resolve something); only when that lookup
        has no mangled name to work with does a public, non-deleted,
        **user-declared** (``is_artificial`` false) constructor/destructor
        default to PUBLIC — the same "declared public in a public header,
        without contrary evidence" principle already used for source-graph
        public-surface classification
        (:data:`abicheck.buildsource.source_graph_query.PUBLIC_VISIBILITIES`).
        Compiler-generated implicit constructors/destructors (marked
        ``artificial="1"``) are excluded: they have no source declaration of
        their own to compare across versions, so promoting them would treat
        every trivial aggregate's synthesized ctor/dtor as a churny "added"/
        "removed" API surface instead of staying silent like the clang
        header backend already does for them.
        """
        return _castxml_functions.ctor_or_dtor_visibility(
            self._ctx, raw_mangled, name, access, is_deleted, is_artificial
        )

    def _variable_visibility(self, el: Element, mangled: str, name: str) -> Visibility:
        """Visibility for a namespace-scope Variable element, with a
        no-symbol-emitted fallback for genuine customisation point objects.

        A real CPO (``inline constexpr __sort_fn sort{};``) has external
        linkage but, when never ODR-used, the compiler emits **no** symbol
        for it at all — not even a local one — so ``_visibility()``'s ELF
        lookup correctly finds nothing and defaults to HIDDEN. That hid a
        CPO's own kind-changed finding: ``detect_cpo_kind_changed``
        (diff_templates.py) requires ``visibility == PUBLIC`` to consider a
        variable at all (case88).

        Falls back to PUBLIC only when castxml's own attributes rule out
        internal linkage: no ``static="1"`` (an explicit C++ ``static``), no
        anonymous-namespace mangling marker (``_GLOBAL__N_1``), and no
        Itanium local-linkage marker (a namespace-scope ``const``/
        ``constexpr`` variable with no ``extern`` — internal linkage by
        default, mangled with an ``L`` marker rather than exported) — the
        same "declared public, without contrary evidence" principle already
        applied to constructors/destructors (:meth:`_ctor_or_dtor_visibility`).
        """
        vis = self._visibility(mangled, name)
        if vis is not Visibility.HIDDEN:
            return vis
        if (
            el.get("static") == "1"
            or "_GLOBAL__N_1" in mangled
            or _mangled_name_is_local_linkage(mangled)
        ):
            return Visibility.HIDDEN  # genuine internal linkage, not just unexported
        return Visibility.PUBLIC

    def _is_builtin_element(self, el: Element) -> bool:
        """Return True if element originates from a compiler built-in pseudo-file."""
        return _castxml_location.is_builtin_element(self._ctx, el)

    def _build_hidden_friend_ids(self) -> dict[str, str]:
        """Map function ids to the qualified name of their befriending class.

        See :func:`abicheck.extract.headers.castxml.functions.
        build_hidden_friend_ids` (this method's real home since ADR-061
        Phase 5 item 1) for the full account.
        """
        return _castxml_functions.build_hidden_friend_ids(self._ctx)

    # castxml emits non-member operator overloads as <OperatorFunction>
    # (e.g. `bool operator==(const Foo&, const Foo&)` at namespace scope,
    # including hidden friends declared inside a class body). Single source
    # of truth is now `extract.headers.castxml.context.FUNCTION_TAGS`, which
    # `CastxmlParserContext.build_id_map` itself uses; kept as a class
    # attribute of the same name for any external reader of it.
    _FUNCTION_TAGS: tuple[str, ...] = _castxml_context.FUNCTION_TAGS

    def parse_functions(self) -> list[Function]:
        return _castxml_functions.parse_functions(self._ctx)

    def _function_display_name(self, el: Element) -> str:
        """Resolve a function element's display name, synthesizing/normalizing operator forms."""
        return _castxml_functions.function_display_name(self._ctx, el)

    def _ctor_param_identity_type(self, type_id: str) -> str:
        """Type spelling for a synthesized constructor identity key. See
        :func:`~.extract.headers.castxml.functions.ctor_param_identity_type`."""
        return _castxml_functions.ctor_param_identity_type(self._ctx, type_id)

    def _parse_function_params(
        self, el: Element
    ) -> tuple[list[Param], bool, list[str]]:
        """Collect a function element's parameters. See
        :func:`~.extract.headers.castxml.functions.parse_function_params`."""
        return _castxml_functions.parse_function_params(self._ctx, el)

    def _enclosing_class_qualified_name(self, el: Element) -> str:
        """Fully-qualified name of the class/struct/union enclosing a
        Constructor/Destructor element *el*. See
        :func:`~.extract.headers.castxml.functions.enclosing_class_qualified_name`."""
        return _castxml_functions.enclosing_class_qualified_name(self._ctx, el)

    @staticmethod
    def _function_mangled_name(
        el: Element,
        name: str,
        ctor_identity_types: list[str],
        raw_mangled: str,
        qualified_scope: str = "",
    ) -> str:
        """Pick the snapshot key for a function. See
        :func:`~.extract.headers.castxml.functions.function_mangled_name`."""
        return _castxml_functions.function_mangled_name(
            el, name, ctor_identity_types, raw_mangled, qualified_scope
        )

    def _function_source_location(
        self, el: Element
    ) -> tuple[str | None, Element | None]:
        """Resolve a function element's ``file:line`` source location and
        Location element. See
        :func:`~.extract.headers.castxml.functions.function_source_location`."""
        return _castxml_functions.function_source_location(self._ctx, el)

    def _function_is_explicit(self, el: Element, loc_el: Element | None) -> bool | None:
        """Determine the tri-state `explicit` specifier for a function
        element. See
        :func:`~.extract.headers.castxml.functions.function_is_explicit`."""
        return _castxml_functions.function_is_explicit(self._ctx, el, loc_el)

    @staticmethod
    def _function_ref_qualifier(el: Element, mangled: str) -> str:
        """Derive the &/&& ref-qualifier. See
        :func:`~.extract.headers.castxml.functions.function_ref_qualifier`."""
        return _castxml_functions.function_ref_qualifier(el, mangled)

    def _function_exception_spec(self, el: Element) -> str:
        """Render a function element's dynamic exception specification, if
        any. See
        :func:`~.extract.headers.castxml.functions.function_exception_spec`."""
        return _castxml_functions.function_exception_spec(self._ctx, el)

    def _parse_function_element(
        self, el: Element, hidden_friend_owner_by_id: dict[str, str]
    ) -> Function | None:
        """Build a Function from a castxml function-like element, or None if
        filtered. See
        :func:`~.extract.headers.castxml.functions.parse_function_element`."""
        return _castxml_functions.parse_function_element(
            self._ctx, el, hidden_friend_owner_by_id
        )

    def parse_variables(self) -> list[Variable]:
        variables = []
        for el in self._variable_els:
            name = el.get("name", "")
            # C-mode castxml does not emit a mangled attribute for C-linkage variables
            # (C has no name mangling); fall back to plain name as the symbol key,
            # mirroring the same pattern in parse_functions().
            mangled = el.get("mangled", "") or name
            if not mangled:
                continue
            # Real ELF export evidence overrides castxml's language-mode guess
            # — the same "case141" fallback already applied to functions
            # above (_parse_function_element): castxml ALWAYS emits a
            # pseudo-Itanium `mangled` attribute for a Variable too, even
            # when the header is actually a plain C API compiled with a C
            # linkage that never mangles at all (confirmed empirically —
            # Phase 2 castxml↔clang parity gate, PR #582: a `.c`-compiled
            # `extern int g;` got a bogus `_Z1g`-style key from castxml
            # while clang correctly reported the real bare-name export).
            # Restricted to global scope for the same reason as the function
            # override: a namespaced C++ variable's bare leaf could
            # coincidentally match an unrelated global export.
            #
            # Deliberately NOT gated on ``mangled.startswith("_Z")`` (an
            # earlier revision was): that hard-coded the Itanium mangling
            # prefix, so a Windows CI leg's real MSVC-targeting castxml --
            # which decorates a guessed C-linkage variable with its own
            # ``?...@@...`` prefix, never Itanium's ``_Z`` -- silently
            # never matched the condition at all, leaving the bogus
            # MSVC-decorated guess standing even though the real export
            # table already confirmed the bare name (confirmed via a real
            # Windows CI failure, Codex review, PR #943). Nothing else in
            # this condition is ABI-specific: `mangled not in
            # (exported_dynamic|exported_static)` already means "not
            # itself a real observed export" regardless of what guessed
            # prefix produced it, so dropping the prefix check makes this
            # override recognize the identical evidence on every mangling
            # scheme castxml's underlying compiler can guess, not just
            # Itanium's. Mirrors the identical fix to the sibling
            # function-level override in extract.headers.castxml.functions.
            if (
                mangled not in self._exported_dynamic
                and mangled not in self._exported_static
                and name in (self._exported_dynamic | self._exported_static)
                and self._is_global_scope(el)
            ):
                mangled = name
            # Skip compiler built-ins and command-line synthetic declarations
            if self._is_builtin_element(el):
                continue
            type_name = self._type_name(el.get("type", ""))
            # Use castxml structured attribute first; fall back to word-boundary
            # regex on type_name to avoid false positives on names like
            # "constructor_t", "const_iterator", "myconstant".
            is_const = el.get("const") == "1" or bool(
                re.search(r"\bconst\b", type_name)
            )
            vis = self._variable_visibility(el, mangled, name)
            # ADR-063 Phase 2: whether `mangled` is a genuine mangling at
            # all. castxml emits a pseudo-Itanium `mangled` attribute even
            # for a C-linkage variable, and the ELF-export override above
            # rewrites it back to the bare name -- in both cases the symbol
            # IS its bare name at the ABI level, which is exactly what
            # `entity_id_for_variable`'s `is_extern_c` branch encodes.
            # (A genuine C++ variable at *namespace* scope always mangles
            # to a distinct `_ZN...` spelling, and the override above is
            # itself restricted to global scope, so this cannot silently
            # drop a real namespace from a namespaced variable's identity.)
            symbol_is_bare_name = mangled == name
            variables.append(
                Variable(
                    name=name,
                    mangled=mangled,
                    type=type_name,
                    visibility=vis,
                    is_const=is_const,
                    # G31: reuses `_access_level` (already used for `Field`).
                    access=self._access_level(el),
                    # G31: `init`, gated by `_variable_value_eligible`.
                    value=el.get("init") if self._variable_value_eligible(el) else None,
                    source_location=self._source_location(el),
                    # Explicit alignas/aligned override when castxml emits an
                    # ``align`` attribute on the Variable itself; falls back to
                    # the type's own natural (computed) alignment when there is
                    # no explicit override, so a plain scalar/aggregate global —
                    # the common case — still carries real declared-alignment
                    # evidence instead of leaving this None. See
                    # _type_alignment_bits.
                    alignment_bits=self._optional_int_attr(el, "align")
                    or self._type_alignment_bits(el.get("type", "")),
                    # See RecordType.deprecated for the message-text convention.
                    deprecated=_deprecation_marker(el),
                    # ADR-063 Phase 2 -- see symbol_is_bare_name above.
                    entity_id=entity_id_for_variable(
                        self._scope_path(el),
                        name,
                        mangled_name=None if symbol_is_bare_name else mangled,
                        is_extern_c=symbol_is_bare_name,
                    ),
                )
            )
        return variables

    def parse_constants(self) -> dict[str, str]:
        """Extract ``const`` / ``constexpr`` constant *values* declared in the
        provided public headers.

        These have a compile-time initializer (castxml emits ``init="..."``) and
        their value is baked into every consumer that ``#include``s the header —
        so a value change is a real source/ABI compatibility hazard. Yet a
        namespace-scope ``const``/``constexpr`` has internal linkage and emits no
        exported symbol, so it is invisible to DWARF/object comparison; only the
        header (castxml) tier can see it.

        Scoped to the public-header surface via provenance: a constant is kept
        only when its declaring header classifies as ``PUBLIC_HEADER`` (the
        parsed ``-H`` headers, plus any other public-header
        inputs — so constants reached through an umbrella header or a public
        include dir are captured, while transitively-included system/private
        headers are excluded). Returns ``name -> value``; empty when no public
        header set is available (e.g. DWARF/symbols-only mode).
        """
        return {name: init for name, init, _ in self._iter_public_constants()}

    def parse_constant_headers(self) -> dict[str, str]:
        """Map each public constant's qualified name to its declaring header path.

        Same public-header scoping and key qualification as
        :meth:`parse_constants` (they share one filtering pass, so the maps never
        disagree). The L4 source-ABI extractor uses this to mark constants from a
        *generated* public header as ``GENERATED`` — otherwise a constant removed
        from a generated config header produces no L4 finding (the value-change
        case is already covered). The L2 snapshot path does not call this.
        """
        return {name: header for name, _, header in self._iter_public_constants()}

    def _iter_public_constants(self) -> list[tuple[str, str, str]]:
        """Return ``(qualified_name, init_value, declaring_header)`` for every
        public ``const``/``constexpr`` — the single source of truth shared by
        :meth:`parse_constants` and :meth:`parse_constant_headers`.
        """
        if not self._have_public_set:
            return []
        out: list[tuple[str, str, str]] = []
        for el in self._variable_els:
            init = el.get("init")
            if not init:
                continue
            if self._is_builtin_element(el):
                continue
            name = el.get("name", "")
            if not name:
                continue
            # Skip private/protected class-scope members: a consumer cannot
            # name them, so a value change to such an implementation detail is
            # not an API contract change. (Namespace-scope constants carry no
            # `access` attribute, so they pass through as public.)
            if el.get("access") in ("private", "protected"):
                continue
            # Only const / constexpr: the initializer is a baked-in contract.
            # (constexpr implies const, so this captures both.)
            type_name = self._type_name(el.get("type", ""))
            is_const = el.get("const") == "1" or bool(
                re.search(r"\bconst\b", type_name)
            )
            if not is_const:
                continue
            if not self._decl_is_public(el):
                continue
            # Qualify the key with its namespace/class context so that
            # constants sharing an unqualified name in different scopes
            # (``A::kLimit`` vs ``B::kLimit``) don't alias and overwrite each
            # other — which would mask or misreport a CONSTANT_CHANGED.
            out.append(
                (
                    self._qualified_name(el),
                    init,
                    header_from_location(self._source_location(el)) or "",
                )
            )
        return out

    def _qualified_name(self, el: Any) -> str:
        """Namespace/class-qualified name by walking ``context`` (bare name
        for a global; stops at ``"::"``). See
        :func:`~.extract.headers.castxml.location.qualified_name`, this
        primitive's real home since ADR-061 Phase 5 item 1 — read by more
        than one entity kind's parsing, same as ``is_builtin_element``/
        ``source_location``."""
        return _castxml_location.qualified_name(self._ctx, el)

    def _scope_path(self, el: Any) -> ScopePath:
        """*el*'s containing scope as typed ``model.identity`` segments.

        The structural counterpart of :meth:`_qualified_name` (ADR-063
        Phase 2): the identical ``context``-chain walk, keeping each parent's
        own XML tag and ``access`` attribute instead of discarding them into
        a flat ``"::"``-joined string. Purely additive --
        :meth:`_qualified_name` is unchanged and still what every existing
        consumer reads. Feeds `entity_id_for_*` (a runtime-only carrier on
        the parsed declaration, never persisted to a snapshot -- CodeRabbit
        review, PR #943, on the docstring going stale once that wiring
        landed). See :func:`~.extract.headers.castxml.scope.scope_path`.
        """
        return _castxml_scope.scope_path(self._ctx, el)

    def _decl_is_public(self, el: Any) -> bool:
        """True if *el*'s declaring header classifies as a public header.
        See :func:`~.extract.headers.castxml.location.decl_is_public`."""
        return _castxml_location.decl_is_public(self._ctx, el)

    def parse_types(self) -> list[RecordType]:
        return _castxml_records.parse_types(self._ctx)

    def _is_public_record_type(self, el: Any) -> bool:
        return _castxml_records.is_public_record_type(self._ctx, el)

    def _build_record_type(
        self, el: Any, override_name: str | None = None
    ) -> RecordType:
        return _castxml_records.build_record_type(self._ctx, el, override_name)

    def _source_location(self, el: Any) -> str | None:
        """Resolve a declaration's ``file:line`` source location."""
        return _castxml_location.source_location(self._ctx, el)

    def _optional_int_attr(self, el: Any, attr: str) -> int | None:
        return _castxml_location.optional_int_attr(el, attr)

    def _parse_record_fields(self, el: Any) -> list[TypeField]:
        """Parse struct/class/union fields. See
        :func:`~.extract.headers.castxml.records.parse_record_fields`."""
        return _castxml_records.parse_record_fields(self._ctx, el)

    def _expand_anonymous_field(
        self, field_el: Any, _depth: int = 0, _outer_offset: int = 0
    ) -> list[TypeField]:
        """Flatten anonymous struct/union field into the parent's field
        list. See
        :func:`~.extract.headers.castxml.records.expand_anonymous_field`."""
        return _castxml_records.expand_anonymous_field(
            self._ctx, field_el, _depth, _outer_offset
        )

    @staticmethod
    def _parse_bitfield_bits(bits_raw: str | None) -> tuple[int | None, bool]:
        return _castxml_records.parse_bitfield_bits(bits_raw)

    def _build_vtable(self, class_id: str) -> list[str]:
        return _castxml_records.build_vtable(self._ctx, class_id)

    def _collect_virtual_methods(
        self,
        cid: str,
        seen: set[str] | None = None,
    ) -> dict[int | str, tuple[int | None, str]]:
        """Ordered mapping of *canonical vtable-slot key* ->
        ``(vtable_index, mangled)``. See
        :func:`~.extract.headers.castxml.records.collect_virtual_methods`,
        this primitive's real home since ADR-061 Phase 5."""
        return _castxml_records.collect_virtual_methods(self._ctx, cid, seen)

    def _inherited_vtable_slots(
        self, class_el: Any, seen: set[str]
    ) -> dict[int | str, tuple[int | None, str]]:
        """Every base class's slots, in base-declaration order. See
        :func:`~.extract.headers.castxml.records.inherited_vtable_slots`."""
        return _castxml_records.inherited_vtable_slots(self._ctx, class_el, seen)

    def _resolved_override_keys(self, overrides_id: str) -> list[int | str]:
        """Every existing slot key the ``overrides`` attribute resolves to.
        See
        :func:`~.extract.headers.castxml.records.resolved_override_keys`."""
        return _castxml_records.resolved_override_keys(self._ctx, overrides_id)

    def _vtable_slot_key(
        self, method_el: Any, mid: str, mangled_name: str
    ) -> tuple[int | str, list[int | str], int | None]:
        """``(key, extra_keys, vtable_index)`` for one virtual method
        declaration. See
        :func:`~.extract.headers.castxml.records.vtable_slot_key`."""
        return _castxml_records.vtable_slot_key(self._ctx, method_el, mid, mangled_name)

    def parse_enums(self) -> list[EnumType]:
        return _castxml_enums.parse_enums(self._ctx)

    def _underlying_type_name(self, id_: str, depth: int = 0) -> str:
        """Follow typedef chains to the concrete base type name."""
        return _castxml_type_resolution.underlying_type_name(self._ctx, id_, depth)

    def parse_typedefs(self) -> dict[str, str]:
        return _typedefs_helpers.parse_typedefs(
            self._typedef_els, self._is_builtin_element, self._underlying_type_name
        )

    def parse_typedefs_qualified(self) -> dict[str, str]:
        """Same mapping as :meth:`parse_typedefs`, keyed by qualified name
        (see ``AbiSnapshot.typedefs_qualified``'s docstring for why)."""
        return _typedefs_helpers.parse_typedefs_qualified(
            self._typedef_els,
            self._is_builtin_element,
            self._underlying_type_name,
            self._qualified_name,
        )

    def _iter_public_typedefs(self) -> list[tuple[str, str, str]]:
        """``(qualified_name, underlying_type, declaring_header)`` for every
        *public-header* typedef — the provenance-scoped source of truth shared by
        :meth:`parse_public_typedefs` and :meth:`parse_public_typedef_headers`.

        Unlike :meth:`parse_typedefs` (unscoped, used by the L2 snapshot), this is
        filtered to the public surface so the L4 extractor does not pull
        private/system aliases onto the linked source surface (ADR-030 #3).
        """
        if not self._have_public_set:
            return []
        out: list[tuple[str, str, str]] = []
        for el in self._typedef_els:
            name = el.get("name", "")
            if not name:
                continue
            if self._is_builtin_element(el):
                continue
            if el.get("access") in ("private", "protected"):
                continue
            if not self._decl_is_public(el):
                continue
            type_id = el.get("type", "")
            underlying = self._underlying_type_name(type_id) if type_id else "?"
            out.append(
                (
                    self._qualified_name(el),
                    underlying,
                    header_from_location(self._source_location(el)) or "",
                )
            )
        return out

    def parse_public_typedefs(self) -> dict[str, str]:
        """Public-header typedef aliases ``qualified_name → underlying type`` (ADR-030 #3)."""
        return {name: target for name, target, _ in self._iter_public_typedefs()}

    def parse_public_typedef_headers(self) -> dict[str, str]:
        """Public typedef qualified name → declaring header (provenance, ADR-030 #3)."""
        return {name: header for name, _, header in self._iter_public_typedefs()}

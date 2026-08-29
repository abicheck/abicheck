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
context explicitly. Every method below that has a counterpart in one of
those modules is a thin delegating wrapper, kept for every existing
internal and external caller (tests included) that still reads
``_CastxmlParser``'s private surface directly.
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
    location as _castxml_location,
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
    Fact,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Variable,
    Visibility,
)
from .name_classification import strip_anonymous_type_location
from .provenance import classify_origin, header_from_location


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
        raw = el.get("access", "public")
        if raw == "protected":
            return AccessLevel.PROTECTED
        if raw == "private":
            return AccessLevel.PRIVATE
        return AccessLevel.PUBLIC

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
        # Check dynamic symbols (.dynsym) — truly exported
        if mangled and mangled in self._exported_dynamic:
            return Visibility.PUBLIC
        if name and name in self._exported_dynamic:
            return Visibility.PUBLIC
        # Check all symbols (.symtab) — present in ELF but not exported
        if mangled and mangled in self._exported_static:
            return Visibility.ELF_ONLY
        if name and name in self._exported_static:
            return Visibility.ELF_ONLY
        return Visibility.HIDDEN

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
        (:data:`abicheck.buildsource.source_graph.PUBLIC_VISIBILITIES`).
        Compiler-generated implicit constructors/destructors (marked
        ``artificial="1"``) are excluded: they have no source declaration of
        their own to compare across versions, so promoting them would treat
        every trivial aggregate's synthesized ctor/dtor as a churny "added"/
        "removed" API surface instead of staying silent like the clang
        header backend already does for them.
        """
        resolved = self._visibility(raw_mangled, name)
        if raw_mangled:
            return resolved  # a real name was checked — trust a negative too
        if resolved is not Visibility.HIDDEN:
            return resolved  # matched via the bare name (e.g. C linkage)
        if access == AccessLevel.PUBLIC and not is_deleted and not is_artificial:
            return Visibility.PUBLIC
        return Visibility.HIDDEN

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

        castxml emits an in-class ``friend`` declaration as a separate
        ``Function`` / ``Method`` / ``OperatorFunction`` element at namespace
        scope, and records the link from the class via a ``befriending``
        attribute on the ``Class`` / ``Struct`` element — a whitespace-
        separated list of ids. We resolve those ids so we can mark the
        corresponding ``Function`` objects as hidden friends downstream, and
        also record *which class* befriended each one (``hidden_friend_owner``)
        so surface classification can key demotion off the owner's header
        origin instead of unconditionally retaining every hidden-friend finding
        regardless of whether the owner lives in a system/private header.

        The same free function can legitimately be befriended by more than
        one class (e.g. one comparison operator declared as a friend inside
        two distinct types). ``Function.hidden_friend_owner`` holds only a
        single owner, so when ids collide, a public owner always wins and,
        once recorded, is never displaced by a later private/system one —
        never let a public ADL function look privately-owned only because a
        different, non-public befriending class happened to be visited last
        (Codex review).
        """
        owner_by_id: dict[str, str] = {}
        owner_is_public_by_id: dict[str, bool] = {}
        for el in self._record_els:
            if el.tag not in ("Class", "Struct", "Union"):
                continue
            befriending = el.get("befriending", "")
            if not befriending:
                continue
            owner_name = self._qualified_name(el)
            is_public = self._decl_is_public(el)
            for fid in befriending.split():
                if not fid:
                    continue
                if fid not in owner_by_id or (
                    is_public and not owner_is_public_by_id[fid]
                ):
                    owner_by_id[fid] = owner_name
                    owner_is_public_by_id[fid] = is_public
        return owner_by_id

    # castxml emits non-member operator overloads as <OperatorFunction>
    # (e.g. `bool operator==(const Foo&, const Foo&)` at namespace scope,
    # including hidden friends declared inside a class body). Single source
    # of truth is now `extract.headers.castxml.context.FUNCTION_TAGS`, which
    # `CastxmlParserContext.build_id_map` itself uses; kept as a class
    # attribute of the same name for any external reader of it.
    _FUNCTION_TAGS: tuple[str, ...] = _castxml_context.FUNCTION_TAGS

    def parse_functions(self) -> list[Function]:
        funcs: list[Function] = []
        hidden_friend_owner_by_id = self._build_hidden_friend_ids()
        for el in self._function_els:
            func = self._parse_function_element(el, hidden_friend_owner_by_id)
            if func is not None:
                funcs.append(func)
        return funcs

    def _function_display_name(self, el: Element) -> str:
        """Resolve a function element's display name, synthesizing/normalizing operator forms."""
        # castxml emits user-defined conversion operators as <Converter>
        # rather than <Method>. They carry mangled names (unlike
        # constructors), `const`/`virtual`/`explicit` qualifiers, and an
        # implicit empty name (which we synthesize as `operator <T>`).
        name = el.get("name", "")
        if not name and el.tag == "Converter":
            # Synthesize a stable display name for conversion operators.
            ret_id = el.get("returns", "")
            ret_type_for_name = self._type_name(ret_id) if ret_id else "?"
            name = f"operator {ret_type_for_name}"
        if name and el.tag == "Destructor":
            # castxml's <Destructor name="..."> is the bare CLASS name (e.g.
            # "Base1"), identical to its own Constructor's — unlike clang's
            # `-ast-dump=json`, which already names a CXXDestructorDecl
            # "~Base1" (confirmed against a live clang 18 dump; Phase 2
            # parity gate, PR #582). Synthesizing the same "~ClassName" form
            # here both matches clang's convention and gives
            # _function_mangled_name's no-mangled-name fallback (`return
            # name`) a key that can never collide with the class's own
            # constructor/type entries.
            name = f"~{name}"
        # castxml emits operator name as the bare symbol (e.g. "==", "+").
        # Normalize to the canonical "operator==" form for readability and
        # to match how the rest of the pipeline (and human reports)
        # refer to operator overloads.
        if (
            name
            and el.tag in ("OperatorFunction", "OperatorMethod")
            and not name.startswith("operator")
        ):
            name = f"operator{name}"
        return name

    def _ctor_param_identity_type(self, type_id: str) -> str:
        """Type spelling for a synthesized constructor identity key: like
        ``_type_name``, but with at most one OUTERMOST ``CvQualifiedType``
        layer removed.

        A top-level cv-qualifier — one directly wrapping the parameter's own
        type, whether that type is by-value (``volatile int``) or a pointer
        VALUE itself (``int * volatile``, i.e. ``CvQualifiedType`` directly
        wrapping ``PointerType``) — participates in neither real Itanium
        mangling nor overload identity, so it must not change the
        synthesized key either (Codex review, PR #582). A POINTEE-position
        qualifier (``const int *`` — ``PointerType`` wrapping
        ``CvQualifiedType``) is NOT touched: that one genuinely does
        distinguish two overloads and would mangle differently, so it must
        keep contributing to the key. This can't be done by pattern-matching
        the rendered ``_type_name`` string (both cases can render
        identically, e.g. ``"volatile int*"`` for either a volatile pointer
        VALUE or a pointer to volatile int) — only the real XML structure
        tells them apart: only strip when the type id itself resolves
        directly to a ``CvQualifiedType`` element.
        """
        el = self._resolve(type_id)
        if el is not None and el.tag == "CvQualifiedType":
            return self._type_name(el.get("type", ""))
        return self._type_name(type_id)

    def _parse_function_params(
        self, el: Element
    ) -> tuple[list[Param], bool, list[str]]:
        """Collect a function element's parameters, whether it is
        C-variadic, and each parameter's ctor-identity-key type spelling
        (mirrors ``params`` positionally; see ``_ctor_param_identity_type``).
        """
        params: list[Param] = []
        ctor_identity_types: list[str] = []
        is_variadic = False
        for arg in el:
            if arg.tag == "Argument":
                p_name = arg.get("name", "")
                p_type_id = arg.get("type", "")
                p_type = self._type_name(p_type_id)
                p_depth = self._pointer_depth(p_type_id)
                _, _, p_restrict = self._resolve_cv_restrict(p_type_id)
                # castxml emits default="<expr>" on Arguments that carry a
                # default value. Removing/changing a default is a source-API
                # (and silent-behaviour) concern even though the mangled name
                # is unchanged; capture it so the param_defaults detector can
                # fire. Absent attribute → None (no default).
                params.append(
                    Param(
                        name=p_name,
                        type=p_type,
                        pointer_depth=p_depth,
                        default=arg.get("default"),
                        # restrict has no ABI/mangling effect (unlike
                        # const/volatile) — tracked as its own compatible-
                        # classified fact via the dedicated param_restrict
                        # detector rather than folded into `type` (see
                        # _type_name's CvQualifiedType handling above).
                        is_restrict=p_restrict,
                        # CastXML never determines va_list-ness at all -- UNSUPPORTED says so plainly, stronger than the omission bridge's NOT_COLLECTED ("not this time" vs. "never from this producer").
                        is_va_list_fact=Fact.unsupported(),
                    )
                )
                ctor_identity_types.append(self._ctor_param_identity_type(p_type_id))
            elif arg.tag == "Ellipsis":
                # Trailing C ellipsis (...) — the function is variadic.
                is_variadic = True
        return params, is_variadic, ctor_identity_types

    def _enclosing_class_qualified_name(self, el: Element) -> str:
        """Fully-qualified (``ns::Outer::Class``) name of the class/struct/
        union enclosing a Constructor/Destructor element *el*.

        Distinct from calling ``_qualified_name(el)`` directly on *el*: a
        Constructor/Destructor's own bare ``name`` attribute already equals
        the class's own leaf name, so walking from *el* itself would count
        that leaf twice (``Foo::Foo`` instead of ``ns::Foo``). Walking from
        *el*'s ``context`` (the class element) instead starts one level up,
        at the class's own name.
        """
        class_el = self._resolve(el.get("context", ""))
        if class_el is None:
            return el.get("name", "")
        return self._qualified_name(class_el)

    @staticmethod
    def _function_mangled_name(
        el: Element,
        name: str,
        ctor_identity_types: list[str],
        raw_mangled: str,
        qualified_scope: str = "",
    ) -> str:
        """Pick the snapshot key for a function: mangled name, ctor synthesis, or plain name."""
        if raw_mangled:
            return raw_mangled
        if el.tag == "Constructor":
            # CastXML may omit constructor mangled names even for public
            # user-declared overloaded constructors.  Using the bare class
            # name would collapse all overloads in AbiSnapshot.function_map,
            # hiding constructor additions such as case111.  Synthesize a
            # deterministic internal identity from the display name and
            # normalized parameter types; it is intentionally not an ABI
            # symbol, only a stable snapshot key for source-level overloads.
            # ctor_identity_types (not the raw Param.type strings) drops a
            # TOP-LEVEL cv qualifier the same way real Itanium mangling
            # would — see _ctor_param_identity_type's docstring: without it,
            # a layout-neutral declaration change like ``Widget(int)`` ->
            # ``Widget(volatile int)`` (by-value) or ``Widget(int*)`` ->
            # ``Widget(int* volatile)`` (the pointer VALUE itself, not its
            # pointee) produced two different synthetic keys, so the diff
            # engine saw a removed + added constructor instead of the same
            # overload reaching the cv-neutral param comparison (Codex
            # review, PR #582).
            #
            # Use the fully-qualified enclosing class name (falling back to
            # the bare *name* only if it couldn't be resolved), not just the
            # bare class name: two public classes with the same leaf name in
            # different namespaces (``ns1::Foo``/``ns2::Foo``) would
            # otherwise synthesize the identical key, silently colliding in
            # ``AbiSnapshot.function_map`` — one class's constructor
            # additions/removals then went undetected, "first-wins" (Codex
            # review, PR #582). A non-namespaced class's qualified name is
            # just its bare name, so this is a no-op for the common case.
            scope = qualified_scope or name
            param_sig = ",".join(ctor_identity_types)
            return f"{SYNTHETIC_CTOR_KEY_PREFIX}{scope}({param_sig})"
        if el.tag == "Destructor" and qualified_scope:
            # Same namespace-collision reasoning as above, applied to the
            # destructor's synthesized "~ClassName" key: qualify it as
            # "~ns::Class" instead of bare "~Class". The leading "~" is
            # preserved (is_synthetic_dtor_key() checks for it), and a
            # non-namespaced class again collapses to the pre-existing
            # "~Class" form.
            return f"~{qualified_scope}"
        return name  # C functions: use plain name

    def _function_source_location(
        self, el: Element
    ) -> tuple[str | None, Element | None]:
        """Resolve a function element's ``file:line`` source location and Location element."""
        # CastXML may store source location two ways:
        #   1. Directly as ``file``/``line`` attributes on the declaration
        #      element (modern compound-attribute form).
        #   2. As ``location="loc1"`` referencing a separate ``Location``
        #      element in the id map (legacy form).
        # Try direct attrs first, then fall back to the id-map lookup so
        # both formats are supported without losing source_location info.
        file_id = el.get("file", "")
        line = el.get("line", "")
        loc_el: Element | None = None
        if not (file_id and line):
            loc_id = el.get("location", "")
            loc_el = self._id_map.get(loc_id) if loc_id else None
            if loc_el is not None:
                file_id = loc_el.get("file", "")
                line = loc_el.get("line", "")
        file_el = self._id_map.get(file_id) if file_id else None
        fname = file_el.get("name", "") if file_el is not None else ""
        source_loc = f"{fname}:{line}" if fname and line else None
        return source_loc, loc_el

    def _function_is_explicit(self, el: Element, loc_el: Element | None) -> bool | None:
        """Determine the tri-state `explicit` specifier for a function element."""
        # castxml emits explicit="1" on Constructor / Method elements that
        # carry the `explicit` specifier. Tri-state: only Constructor /
        # Method tags can be explicit; for plain Function / Destructor the
        # attribute is conceptually N/A and we leave is_explicit=None so
        # the diff does not produce spurious findings.
        if el.tag in ("Constructor", "Method"):
            return el.get("explicit") == "1"
        if el.tag == "Converter":
            return (
                el.get("explicit") == "1"
                if el.get("explicit") is not None
                else self._source_line_has_explicit(loc_el, el)
            )
        return None

    @staticmethod
    def _function_ref_qualifier(el: Element, mangled: str) -> str:
        """Derive the &/&& ref-qualifier from the refqual attribute or the mangling."""
        # C++ ref-qualifier: newer castxml emits refqual="lvalue"/"rvalue",
        # but released versions (≤0.6.x) omit the attribute entirely, so
        # fall back to the Itanium mangling — the qualifier is encoded as
        # R (&) / O (&&) right after the CV-qualifiers in <nested-name>.
        refqual_raw = el.get("refqual", "")
        return {"lvalue": "&", "rvalue": "&&"}.get(
            refqual_raw, ""
        ) or _ref_qualifier_from_mangled(mangled)

    def _function_exception_spec(self, el: Element) -> str:
        """Render a function element's dynamic exception specification, if any."""
        # Dynamic exception specification: castxml emits throw="" for
        # `throw()` and a space-separated type-id list for `throw(T...)`.
        # Absent attribute = no dynamic spec (captured as ""), keeping the
        # tri-state None for dumpers that cannot know.
        throw_attr = el.get("throw")
        if throw_attr is None:
            return ""
        if not throw_attr.strip():
            return "throw()"
        thrown = ", ".join(self._type_name(tid) for tid in throw_attr.split())
        return f"throw({thrown})"

    def _parse_function_element(
        self, el: Element, hidden_friend_owner_by_id: dict[str, str]
    ) -> Function | None:
        """Build a Function from a castxml function-like element, or None if filtered."""
        name = self._function_display_name(el)
        if not name:
            return None
        # Skip compiler built-ins and command-line synthetic declarations
        if self._is_builtin_element(el):
            return None
        raw_mangled = el.get("mangled", "")
        ret_id = el.get("returns", "")
        ret_type = self._type_name(ret_id) if ret_id else "void"
        ret_ptr_depth = self._pointer_depth(ret_id) if ret_id else 0

        params, is_variadic, ctor_identity_types = self._parse_function_params(el)
        qualified_scope = (
            self._enclosing_class_qualified_name(el)
            if el.tag in ("Constructor", "Destructor")
            else ""
        )
        mangled = self._function_mangled_name(
            el, name, ctor_identity_types, raw_mangled, qualified_scope
        )

        # Real ELF export evidence overrides castxml's language-mode guess:
        # castxml ALWAYS emits a pseudo-Itanium `mangled` attribute, even for
        # a plain C function parsed in ambiguous/C++ mode (confirmed
        # empirically — the "C functions: use plain name" fallback in
        # _function_mangled_name is otherwise dead code, since raw_mangled is
        # never actually empty). When that guessed mangling matches no real
        # exported symbol at all while the function's bare declared name
        # *is* a real export, that's strong, low-false-positive-risk
        # evidence the function actually has C linkage — a genuine C++
        # function's real compiled export would essentially never coincide
        # with its bare unqualified name. Use the bare name as the
        # canonical symbol identity instead (case141).
        #
        # Restricted to global-scope functions (context is the root ``::``
        # namespace): ``name`` is always castxml's bare leaf identifier —
        # for a *namespaced* C++ function (``ns::foo``), the same bare
        # leaf could coincidentally match an unrelated, genuinely-exported
        # plain C ``foo``, which would wrongly rewrite the namespaced
        # function's identity onto that unrelated export instead. A real
        # (possibly extern "C") function this override is meant to recover
        # is always declared at global scope.
        if (
            el.tag == "Function"
            and mangled.startswith("_Z")
            and mangled not in self._exported_dynamic
            and name in self._exported_dynamic
            and self._is_global_scope(el)
        ):
            mangled = name
            is_extern_c_override = True
        else:
            is_extern_c_override = False

        is_virtual = el.get("virtual") == "1"
        noexcept_re = re.search(r"noexcept", el.get("attributes", ""))
        vtable_index = (
            _parse_vtable_index(el.get("vtable_index")) if is_virtual else None
        )

        # Detect extern "C": explicit extern attribute OR no mangled name (C linkage)
        is_extern_c = (
            el.get("extern") == "1"
            or (
                not raw_mangled and el.tag == "Function"
            )  # C functions have no mangled name
            or is_extern_c_override
        )

        source_loc, loc_el = self._function_source_location(el)
        access = self._access_level(el)
        is_deleted = el.get("deleted") == "1"
        visibility = (
            self._ctor_or_dtor_visibility(
                raw_mangled, name, access, is_deleted, el.get("artificial") == "1"
            )
            if el.tag in ("Constructor", "Destructor")
            else self._visibility(raw_mangled, name)
        )

        return Function(
            name=name,
            mangled=mangled,
            return_type=ret_type,
            params=params,
            visibility=visibility,
            is_virtual=is_virtual,
            is_noexcept=bool(noexcept_re),
            is_extern_c=is_extern_c,
            vtable_index=vtable_index,
            source_location=source_loc,
            is_static=el.get("static") == "1",
            is_const=el.get("const") == "1",
            is_volatile=el.get("volatile") == "1",
            is_pure_virtual=el.get("pure_virtual") == "1",
            is_deleted=is_deleted,
            # castxml emits inline="1" for inline functions/methods
            is_inline=el.get("inline") == "1",
            access=access,
            return_pointer_depth=ret_ptr_depth,
            ref_qualifier=self._function_ref_qualifier(el, mangled),
            is_explicit=self._function_is_explicit(el, loc_el),
            # Hidden-friend marker: castxml records the link via the
            # ``befriending`` attribute on the class element. We resolved
            # the referenced ids upfront and check membership here.
            is_hidden_friend=el.get("id", "") in hidden_friend_owner_by_id,
            hidden_friend_owner=hidden_friend_owner_by_id.get(el.get("id", "")),
            is_variadic=is_variadic,
            # Semantic contract / calling-convention attributes, filtered from
            # the compound ``attributes`` string (same channel as noexcept).
            contract_attributes=_extract_contract_attributes(el.get("attributes", "")),
            exception_spec=self._function_exception_spec(el),
            # See _deprecation_marker for why this isn't a plain
            # el.get("deprecation") read.
            deprecated=_deprecation_marker(el),
            # Explicit C++11 `override` specifier: castxml has no dedicated
            # boolean for it (distinct from `overrides`, the id-reference
            # list used for vtable-slot dedup) — the `override` token is
            # embedded in the same compound `attributes` string as
            # `noexcept`/`final`. Only member-function forms that can
            # actually be virtual may carry it; a free function/operator or
            # a constructor never can, so those stay None (not merely
            # False) rather than asserting a fact that's not applicable.
            is_override=(
                bool(re.search(r"\boverride\b", el.get("attributes", "")))
                if el.tag in ("Method", "Destructor", "Converter", "OperatorMethod")
                else None
            ),
            is_compiler_generated=el.get("artificial") == "1",
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
            if (
                mangled.startswith("_Z")
                and mangled not in self._exported_dynamic
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
        for a global; stops at ``"::"``). Segments are stripped via
        `strip_anonymous_type_location`, matching `_qualified_type_name`."""
        parts = [strip_anonymous_type_location(el.get("name", ""))]
        ctx_id = el.get("context", "")
        seen: set[str] = set()
        while ctx_id and ctx_id not in seen:
            seen.add(ctx_id)
            ctx = self._id_map.get(ctx_id)
            if ctx is None:
                break
            cname = strip_anonymous_type_location(ctx.get("name", ""))
            if cname and cname != "::":
                parts.append(cname)
            ctx_id = ctx.get("context", "")
        return "::".join(reversed(parts))

    def _decl_is_public(self, el: Any) -> bool:
        """True if *el*'s declaring header classifies as a public header.

        Uses the shared provenance segment matcher (suffix/basename/public-dir
        containment), so build-prefixed paths and umbrella-included public
        headers match while system/private headers do not.
        """
        sh = header_from_location(self._source_location(el))
        if not sh:
            return False
        return (
            classify_origin(
                sh,
                self._pub_header_segs,
                self._pub_dir_segs,
                have_public_set=self._have_public_set,
            )
            == ScopeOrigin.PUBLIC_HEADER
        )

    def parse_types(self) -> list[RecordType]:
        # Build reverse mapping: struct/union ID → typedef name for anonymous types.
        # This allows us to include `typedef struct { ... } Foo;` where the struct
        # itself is anonymous (name="") but reachable via the typedef.
        typedef_name_for: dict[str, str] = {}
        for el in self._typedef_els:
            td_name = el.get("name", "")
            if not td_name:
                continue
            target_id = el.get("type", "")
            target_el = self._resolve(target_id)
            # Follow through ElaboratedType / CvQualifiedType wrappers
            # that castxml may insert between Typedef and the actual Struct.
            while target_el is not None and target_el.tag in (
                "ElaboratedType",
                "CvQualifiedType",
            ):
                target_id = target_el.get("type", "")
                target_el = self._resolve(target_id)
            if target_el is not None and target_el.tag in ("Struct", "Class", "Union"):
                target_name = target_el.get("name", "")
                if not target_name:
                    # Anonymous struct/union with a typedef alias — record it.
                    # Use the struct's own id as key (may differ from the
                    # Typedef's type attr when ElaboratedType is involved).
                    struct_id = target_el.get("id", "")
                    if struct_id:
                        typedef_name_for[struct_id] = td_name

        types = []
        for el in self._record_els:
            if self._is_public_record_type(el):
                types.append(self._build_record_type(el))
            else:
                # self._record_els is already pre-filtered to Struct/Class/
                # Union (see _build_id_map), so this is every record type
                # _is_public_record_type rejected. Check if it's an
                # anonymous struct reachable via typedef.
                eid = el.get("id", "")
                override_name = typedef_name_for.get(eid)
                if override_name and not self._is_builtin_element(el):
                    types.append(
                        self._build_record_type(el, override_name=override_name)
                    )
        return types

    def _is_public_record_type(self, el: Any) -> bool:
        if el.tag not in ("Struct", "Class", "Union"):
            return False
        name = el.get("name", "")
        if not name or el.get("artificial") == "1":
            return False
        if name.startswith("__"):
            return False
        # Skip compiler built-ins and command-line synthetic types
        if self._is_builtin_element(el):
            return False
        return True

    def _build_record_type(
        self, el: Any, override_name: str | None = None
    ) -> RecordType:
        name = strip_anonymous_type_location(override_name or el.get("name", ""))
        is_opaque = el.get("incomplete") == "1"
        vtable = [] if is_opaque else self._build_vtable(el.get("id", ""))

        def _base_names(*, virtual: bool) -> list[str]:
            return [
                self._type_name(b.get("type", ""))
                for b in el
                if b.tag == "Base" and (b.get("virtual") == "1") == virtual
            ]

        bases = [] if is_opaque else _base_names(virtual=False)
        virtual_bases = [] if is_opaque else _base_names(virtual=True)
        # Polymorphic (non-empty vtable) → vtable pointer at offset 0; None when non-polymorphic so the diff can tell "gained a vptr" apart.
        vptr_offset_bits = 0 if vtable else None
        # Best-effort layout descriptor (layout-closure work): direct (non-virtual) base subobject offsets from each ``<Base offset=...>``; the unit only has to be consistent across snapshots for change detection, and it is.
        base_offsets: dict[str, int] = {}
        if not is_opaque:
            for b in el:
                if b.tag == "Base" and b.get("virtual") != "1":
                    off = self._optional_int_attr(b, "offset")
                    if off is not None:
                        base_offsets[self._type_name(b.get("type", ""))] = off
        # is_standard_layout / is_trivially_copyable / data_size_bits are left None: "not polymorphic and no virtual bases" is not a sound standard-layout signal (a mixed-access class is already non-standard-layout, so the heuristic would flip True→False on gaining a virtual and emit a spurious STANDARD_LAYOUT_LOST), and CastXML doesn't expose the trivially-copyable trait directly (Codex review #345).
        return RecordType(
            name=name,
            kind=el.tag.lower(),
            size_bits=self._optional_int_attr(el, "size"),
            alignment_bits=self._optional_int_attr(el, "align"),
            fields=[] if is_opaque else self._parse_record_fields(el),
            bases=bases,
            virtual_bases=virtual_bases,
            vtable=vtable,
            is_union=el.tag == "Union",
            is_opaque=is_opaque,
            vptr_offset_bits=vptr_offset_bits,
            base_offsets=base_offsets,
            # castxml genuinely resolves these itself (real semantic analysis, not a heuristic reconstruction), opaque or not -- stated explicitly (kept as individual kwargs, not a **record_layout_facts() spread, so scripts/backend_capabilities.py's AST scanner can still see each field named).
            bases_fact=Fact.present(bases),
            virtual_bases_fact=Fact.present(virtual_bases),
            vtable_fact=Fact.present(vtable),
            # 0-if-vtable-else-None is the Itanium primary-base heuristic above, not a real offset read -- partial, not present (Codex review; matches vptr_offset_bits's own PARTIAL row).
            vptr_offset_bits_fact=Fact.partial(vptr_offset_bits),
            qualified_name=self._qualified_type_name(el, leaf_name=name),
            # castxml records the `final` class-key specifier as a `final`
            # token inside the compound ``attributes`` string (e.g.
            # ``attributes="final"``), the same channel used for noexcept.
            # Header mode always knows the answer, so this is a concrete bool
            # (never None on the castxml path); DWARF/symbols mode leaves the
            # model default of None since the binary carries no `final` info.
            is_final=bool(re.search(r"\bfinal\b", el.get("attributes", ""))),
            source_location=self._source_location(el),
            # castxml's `abstract="1"` marks a class/struct with at least one
            # pure virtual function (cannot be instantiated). Header mode
            # always knows the answer for a complete type, matching the
            # `is_final` convention above; left None for an opaque/incomplete
            # record (no member list to have judged it from).
            is_abstract=None if is_opaque else el.get("abstract") == "1",
            # `[[deprecated("msg")]]` -> the message text verbatim; a bare
            # `[[deprecated]]` with no message -> "" (see _deprecation_marker:
            # castxml only emits the `deprecation` XML attribute when there
            # IS a message, so a bare marker must be read from the
            # compound `attributes` string instead); not deprecated -> None.
            deprecated=_deprecation_marker(el),
        )

    def _source_location(self, el: Any) -> str | None:
        """Resolve a declaration's ``file:line`` source location."""
        return _castxml_location.source_location(self._ctx, el)

    def _optional_int_attr(self, el: Any, attr: str) -> int | None:
        return _castxml_location.optional_int_attr(el, attr)

    def _parse_record_fields(self, el: Any) -> list[TypeField]:
        """Parse struct/class/union fields.

        castxml uses two layouts depending on version / output mode:
        - Inline children: ``<Struct><Field .../></Struct>``
        - Members attribute: ``<Struct members="_14 _15 _16 ..."/>`` (IDs resolved via id_map)

        We support both: first scan inline children, then fall back to the
        ``members`` attribute so we never miss fields in either format.
        """
        fields: list[TypeField] = []

        # Collect Field elements: inline children first
        field_elements: list[Any] = [c for c in el if c.tag == "Field"]

        # Fallback: resolve via space-separated "members" attribute
        if not field_elements:
            for mid in el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    field_elements.append(member_el)

        for child in field_elements:
            child_name = child.get("name", "")
            if not child_name:
                # Anonymous struct/union member — flatten its fields into parent
                fields.extend(self._expand_anonymous_field(child))
                continue
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(child.get("bits"))
            field_type_id = child.get("type", "")
            field_type = self._type_name(field_type_id)
            # Resolved from the real XML type chain (following through any
            # Typedef indirection), not a regex over `field_type`: a field
            # declared through a typedef to a cv-qualified type (`typedef
            # const int T; struct S { T x; };`) renders as the bare alias
            # name ("T"), which a spelling-based regex could never see
            # through (Codex review, PR #582).
            field_const, field_volatile, _ = self._resolve_cv_restrict(field_type_id)
            fields.append(
                TypeField(
                    name=child_name,
                    type=field_type,
                    offset_bits=self._optional_int_attr(child, "offset"),
                    is_bitfield=is_bitfield,
                    bitfield_bits=bitfield_bits,
                    is_const=field_const,
                    is_volatile=field_volatile,
                    # castxml's Field element carries its own `mutable="1"`
                    # attribute (fixed xs:int, per castxml.xsd) rather than
                    # deriving it from the referenced type like const/volatile.
                    is_mutable=child.get("mutable") == "1",
                    access=self._access_level(child),
                    # Default member initializer expression, verbatim
                    # (castxml's Field ``init`` attribute — the same channel
                    # already used for Variable/constant initializers).
                    default=child.get("init"),
                    # See RecordType.deprecated for the message-text convention.
                    deprecated=_deprecation_marker(child),
                )
            )
        return fields

    def _expand_anonymous_field(
        self, field_el: Any, _depth: int = 0, _outer_offset: int = 0
    ) -> list[TypeField]:
        """Flatten anonymous struct/union field into the parent's field list.

        In castxml output, anonymous unions/structs inside a struct appear as
        ``Field`` elements with ``name=""`` pointing to a ``Union`` or ``Struct``
        element.  We inline their named fields at the correct offset to prevent
        false ``TYPE_FIELD_REMOVED`` reports when a named field moves into an
        anonymous union (issue #58).

        ``_depth`` guards against malformed/cyclic XML (max nesting: 16).
        ``_outer_offset`` carries the accumulated offset from outer anonymous
        members so doubly-nested fields get correct absolute ``offset_bits``.
        """
        if _depth > 16:
            return []
        type_id = field_el.get("type", "")
        type_el = self._resolve(type_id)
        if type_el is None or type_el.tag not in ("Union", "Struct"):
            return []

        this_offset = _outer_offset + (self._optional_int_attr(field_el, "offset") or 0)
        result: list[TypeField] = []

        # Collect inner Field elements (inline children or members attribute)
        inner_fields: list[Any] = [c for c in type_el if c.tag == "Field"]
        if not inner_fields:
            for mid in type_el.get("members", "").split():
                member_el = self._id_map.get(mid)
                if member_el is not None and member_el.tag == "Field":
                    inner_fields.append(member_el)

        for inner in inner_fields:
            inner_name = inner.get("name", "")
            if not inner_name:
                # Doubly-nested anonymous member — recurse, passing accumulated offset
                result.extend(
                    self._expand_anonymous_field(
                        inner,
                        _depth + 1,
                        _outer_offset=this_offset,
                    )
                )
                continue
            inner_offset = self._optional_int_attr(inner, "offset") or 0
            bitfield_bits, is_bitfield = self._parse_bitfield_bits(inner.get("bits"))
            inner_type_id = inner.get("type", "")
            inner_type = self._type_name(inner_type_id)
            inner_const, inner_volatile, _ = self._resolve_cv_restrict(inner_type_id)
            result.append(
                TypeField(
                    name=inner_name,
                    type=inner_type,
                    offset_bits=this_offset + inner_offset,
                    is_bitfield=is_bitfield,
                    bitfield_bits=bitfield_bits,
                    is_const=inner_const,
                    is_volatile=inner_volatile,
                    is_mutable=inner.get("mutable") == "1",
                    access=self._access_level(inner),
                    # Same channel as the direct-field path in
                    # _parse_record_fields — a field inside an anonymous
                    # struct/union must not lose its initializer/deprecation
                    # just because it was flattened (Codex review, PR #582).
                    default=inner.get("init"),
                    deprecated=_deprecation_marker(inner),
                )
            )
        return result

    @staticmethod
    def _parse_bitfield_bits(bits_raw: str | None) -> tuple[int | None, bool]:
        try:
            bitfield_bits = int(bits_raw) if bits_raw is not None else None
        except ValueError:
            return (None, False)
        return (bitfield_bits, bitfield_bits is not None)

    def _build_vtable(self, class_id: str) -> list[str]:
        slots = self._collect_virtual_methods(class_id)
        ordered = sorted(slots.values(), key=_vt_sort_key)
        return [name for _, name in ordered]

    def _collect_virtual_methods(
        self,
        cid: str,
        seen: set[str] | None = None,
    ) -> dict[int | str, tuple[int | None, str]]:
        """Ordered mapping of *canonical vtable-slot key* -> ``(vtable_index, mangled)``.

        Keyed so a derived override replaces its base's entry **in place**
        rather than appending a duplicate: dict re-assignment to an existing
        key keeps that key's original insertion position (Python dict
        semantics), so a reused slot stays where the base declared it while a
        genuinely new virtual still appends at the end.

        ``vtable_index`` is the preferred slot identity when castxml emits it
        (unchanged from prior behavior). But that attribute is not always
        present — this castxml/Clang build may track no slot indices at all —
        and without it, a same-signature override (which reuses its base's
        slot per the Itanium ABI) has no other signal tying it to the base
        entry it replaces, so it was appended as a spurious extra slot,
        growing the reconstructed vtable by one entry it never actually
        gained (case185's false-positive ``type_vtable_changed``: a
        `Derived::paint(int) override` reusing `Base::paint(int)`'s slot read
        as vtable growth instead of a compatible rename in place).
        castxml's ``overrides`` attribute — the id of the method declaration
        this one overrides — is the fallback signal: resolved (through
        ``_vtable_slot_root``, to survive multi-level override chains where
        ``overrides`` points at an intermediate override rather than the
        slot's original declarer) to the same key the overridden entry was
        stored under, so the override replaces it instead of duplicating it.
        """
        if seen is None:
            seen = set()
        if cid in seen:
            return {}
        seen.add(cid)
        class_el = self._id_map.get(cid)
        if class_el is None:
            return {}

        slots = self._inherited_vtable_slots(class_el, seen)
        for method_el in self._virtual_methods_by_class.get(cid, []):
            mangled_name = _virtual_method_mangled_name(method_el)
            if not mangled_name:
                continue
            mid = method_el.get("id", "")
            key, extra_keys, idx = self._vtable_slot_key(method_el, mid, mangled_name)
            if mid:
                # Record the *actual* slot key (int index or str id) this method
                # landed under, not just a self-reference -- a downstream override
                # in a mixed indexed/unindexed chain (e.g. Base has vtable_index,
                # Mid overrides it losing the index, Derived overrides Mid via
                # `overrides="Mid's id"`) must still resolve back to the int index
                # Base's slot is keyed by, or it would append instead of replace.
                self._vtable_slot_root[mid] = key
                if extra_keys:
                    # This id itself touches more than one slot -- a further-
                    # derived override referencing it by `overrides` must
                    # propagate to all of them.
                    self._vtable_slot_extra_roots[mid] = list(extra_keys)
            slots[key] = (idx, mangled_name)
            for extra_key in extra_keys:
                prev_idx, _ = slots.get(extra_key, (None, ""))
                slots[extra_key] = (prev_idx, mangled_name)

        return slots

    def _inherited_vtable_slots(
        self, class_el: Any, seen: set[str]
    ) -> dict[int | str, tuple[int | None, str]]:
        """Every base class's slots, in base-declaration order."""
        slots: dict[int | str, tuple[int | None, str]] = {}
        for base in class_el:
            if base.tag != "Base":
                continue
            base_type_el = self._resolve(base.get("type", ""))
            if base_type_el is not None:
                slots.update(
                    self._collect_virtual_methods(base_type_el.get("id", ""), seen)
                )
        return slots

    def _resolved_override_keys(self, overrides_id: str) -> list[int | str]:
        """Every existing slot key the ``overrides`` attribute resolves to.

        castxml can list more than one overridden declaration as a
        whitespace-separated id list when a single override simultaneously
        covers more than one base-class branch (e.g. non-virtual multiple
        inheritance -- ``Derived : Base1, Base2`` -- where one final overrider
        satisfies both ``Base1::foo()`` and ``Base2::foo()``). Each resolved id
        is a genuinely distinct position in the object's real vtable-group
        layout (typically an adjusting thunk for all but one), and an exact
        lookup of the raw composite string never matches ``_vtable_slot_root``,
        so every id is resolved separately.

        A resolved id can itself carry extra roots from an earlier multi-slot
        override (a further-derived override referencing an intermediate
        override's id by ``overrides`` must propagate to every slot that
        intermediate one touched, not just its primary), so both
        ``_vtable_slot_root`` and ``_vtable_slot_extra_roots`` are consulted
        per id.
        """
        resolved: list[int | str] = []
        for oid in overrides_id.split():
            candidates: list[int | str] = []
            primary = self._vtable_slot_root.get(oid)
            if primary is not None:
                candidates.append(primary)
            candidates.extend(self._vtable_slot_extra_roots.get(oid, ()))
            for candidate in candidates:
                if candidate not in resolved:
                    resolved.append(candidate)
        return resolved

    def _vtable_slot_key(
        self, method_el: Any, mid: str, mangled_name: str
    ) -> tuple[int | str, list[int | str], int | None]:
        """``(key, extra_keys, vtable_index)`` for one virtual method declaration.

        An override always reuses whatever slot its base declaration landed
        under -- checked BEFORE falling back to this declaration's own
        ``vtable_index``. Preferring a fresh index would miss the reverse mixed-
        index direction: a base that lacks ``vtable_index`` (so its slot is
        keyed by its own string id) but is overridden by a declaration that DOES
        carry an index would otherwise open a new int-keyed slot instead of
        collapsing onto the base's string-keyed one.

        The first resolved slot becomes this entry's own key; every OTHER
        resolved slot keeps its own key and prior sort position (*extra_keys*)
        with only its content updated to this override, rather than collapsing
        them into one entry -- which would under-report the vtable's true size
        -- or leaving them with stale pre-override content.
        """
        idx = _parse_vtable_index(method_el.get("vtable_index"))
        overrides_id = method_el.get("overrides")
        if not overrides_id:
            return (idx if idx is not None else (mid or mangled_name)), [], idx

        resolved_keys = self._resolved_override_keys(overrides_id)
        if resolved_keys:
            key: int | str = resolved_keys[0]
            extra_keys = resolved_keys[1:]
        else:
            key, extra_keys = overrides_id, []
        if isinstance(key, int):
            # Consistently-indexed lineage: adopt the resolved index for
            # sorting when this declaration has none of its own, so
            # _build_vtable's final _vt_sort_key sort places it at the
            # inherited position instead of the unindexed tail (which would
            # silently reorder it past any indexed sibling slot declared after
            # this one, an apparent "vtable reordered" that never happened).
            if idx is None:
                idx = key
        else:
            # Unindexed lineage (key is a string): a fresh vtable_index on THIS
            # declaration has no verified relationship to sibling unindexed
            # slots' true positions (e.g. Base has unindexed foo then bar;
            # Derived overrides bar with its own vtable_index="1" -- that "1"
            # doesn't mean "after foo", it's not comparable to foo's unknown
            # position at all), so it must not be trusted for cross-slot
            # ordering. Discard it and let _vt_sort_key treat this slot as
            # unindexed, preserving its original discovery-order position.
            idx = None
        return key, extra_keys, idx

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

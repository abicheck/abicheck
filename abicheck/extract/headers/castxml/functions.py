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

"""Function-entity parsing for the castxml backend (ADR-061 D9).

Second entity module split out of ``_CastxmlParser`` proper, after
``enums.py``. Reads ``ctx.function_els`` — populated once by
:meth:`~.context.CastxmlParserContext.build_id_map` — plus ``ctx.record_els``
(for hidden-friend resolution) and produces ``Function`` model objects,
using the shared location/type-resolution modules in this package for
everything below the function-entity level. Creates no policy finding and
resolves nothing global; ``dumper_castxml.py`` still owns opening the
castxml document and driving ``build_id_map()``.

``qualified_name``/``decl_is_public``/``visibility``/``access_level`` are
NOT here even though ``parse_functions`` needs all four: each is also read
by variable/constant/typedef parsing (still in ``dumper_castxml.py``), so
per this package's own "shared across entity kinds" rule (see
``location.py``'s docstring) they live in ``location.py`` instead, the same
way ``underlying_type_name`` lives in ``type_resolution.py`` rather than
``enums.py`` because typedef resolution reads it too.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

from ....model import AccessLevel, Fact, Function, Param, Visibility
from ....model.identity import entity_id_for_function
from .context import CastxmlParserContext
from .location import (
    access_level,
    contract_attributes as _extract_contract_attributes,
    decl_is_public,
    deprecation_marker as _deprecation_marker,
    is_builtin_element,
    qualified_name,
    source_line_has_explicit,
    visibility,
)
from .names import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    _parse_vtable_index,
    _ref_qualifier_from_mangled,
)
from .scope import scope_path
from .type_resolution import (
    is_global_scope,
    pointer_depth,
    resolve_cv_restrict,
    type_name,
)


def build_hidden_friend_ids(ctx: CastxmlParserContext) -> dict[str, str]:
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
    for el in ctx.record_els:
        if el.tag not in ("Class", "Struct", "Union"):
            continue
        befriending = el.get("befriending", "")
        if not befriending:
            continue
        owner_name = qualified_name(ctx, el)
        is_public = decl_is_public(ctx, el)
        for fid in befriending.split():
            if not fid:
                continue
            if fid not in owner_by_id or (is_public and not owner_is_public_by_id[fid]):
                owner_by_id[fid] = owner_name
                owner_is_public_by_id[fid] = is_public
    return owner_by_id


def ctor_or_dtor_visibility(
    ctx: CastxmlParserContext,
    raw_mangled: str,
    name: str,
    access: AccessLevel,
    is_deleted: bool,
    is_artificial: bool,
) -> Visibility:
    """Visibility for a Constructor or Destructor element, with a
    source-access fallback.

    ``visibility()`` is an ELF-symbol-table lookup: it needs a real
    mangled name to check. When castxml omits the mangled name for a
    user-declared, overloaded constructor (a documented castxml gap —
    see :func:`function_mangled_name`'s synthesis comment), the ELF
    lookup can never match *any* overload of that constructor — the
    class's bare name never appears as its own exported symbol (Itanium
    mangling always applies to constructors), so every such overload
    would silently classify HIDDEN regardless of whether it is genuinely
    callable from outside the library. That hid both a removed public
    constructor overload (case78: FUNC_REMOVED never fired for
    ``task_arena(attach_mode_t)``) and an added one (case111: FUNC_ADDED
    never fired for the new ``std::function<int()>`` overload) behind
    ``_public_functions()``'s PUBLIC/ELF_ONLY filter.

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
    resolved = visibility(ctx, raw_mangled, name)
    if raw_mangled:
        return resolved  # a real name was checked — trust a negative too
    if resolved is not Visibility.HIDDEN:
        return resolved  # matched via the bare name (e.g. C linkage)
    if access == AccessLevel.PUBLIC and not is_deleted and not is_artificial:
        return Visibility.PUBLIC
    return Visibility.HIDDEN


def function_display_name(ctx: CastxmlParserContext, el: Element) -> str:
    """Resolve a function element's display name, synthesizing/normalizing
    operator forms."""
    # castxml emits user-defined conversion operators as <Converter>
    # rather than <Method>. They carry mangled names (unlike
    # constructors), `const`/`virtual`/`explicit` qualifiers, and an
    # implicit empty name (which we synthesize as `operator <T>`).
    name = el.get("name", "")
    if not name and el.tag == "Converter":
        # Synthesize a stable display name for conversion operators.
        ret_id = el.get("returns", "")
        ret_type_for_name = type_name(ctx, ret_id) if ret_id else "?"
        name = f"operator {ret_type_for_name}"
    if name and el.tag == "Destructor":
        # castxml's <Destructor name="..."> is the bare CLASS name (e.g.
        # "Base1"), identical to its own Constructor's — unlike clang's
        # `-ast-dump=json`, which already names a CXXDestructorDecl
        # "~Base1" (confirmed against a live clang 18 dump; Phase 2
        # parity gate, PR #582). Synthesizing the same "~ClassName" form
        # here both matches clang's convention and gives
        # function_mangled_name's no-mangled-name fallback (`return
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


def ctor_param_identity_type(ctx: CastxmlParserContext, type_id: str) -> str:
    """Type spelling for a synthesized constructor identity key: like
    ``type_name``, but with at most one OUTERMOST ``CvQualifiedType``
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
    the rendered ``type_name`` string (both cases can render
    identically, e.g. ``"volatile int*"`` for either a volatile pointer
    VALUE or a pointer to volatile int) — only the real XML structure
    tells them apart: only strip when the type id itself resolves
    directly to a ``CvQualifiedType`` element.
    """
    el = ctx.resolve(type_id)
    if el is not None and el.tag == "CvQualifiedType":
        return type_name(ctx, el.get("type", ""))
    return type_name(ctx, type_id)


def parse_function_params(
    ctx: CastxmlParserContext, el: Element
) -> tuple[list[Param], bool, list[str]]:
    """Collect a function element's parameters, whether it is
    C-variadic, and each parameter's ctor-identity-key type spelling
    (mirrors ``params`` positionally; see ``ctor_param_identity_type``).
    """
    params: list[Param] = []
    ctor_identity_types: list[str] = []
    is_variadic = False
    for arg in el:
        if arg.tag == "Argument":
            p_name = arg.get("name", "")
            p_type_id = arg.get("type", "")
            p_type = type_name(ctx, p_type_id)
            p_depth = pointer_depth(ctx, p_type_id)
            _, _, p_restrict = resolve_cv_restrict(ctx, p_type_id)
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
                    # type_name's CvQualifiedType handling above).
                    is_restrict=p_restrict,
                    # CastXML never determines va_list-ness at all -- UNSUPPORTED says so plainly, stronger than the omission bridge's NOT_COLLECTED ("not this time" vs. "never from this producer").
                    is_va_list_fact=Fact.unsupported(),
                )
            )
            ctor_identity_types.append(ctor_param_identity_type(ctx, p_type_id))
        elif arg.tag == "Ellipsis":
            # Trailing C ellipsis (...) — the function is variadic.
            is_variadic = True
    return params, is_variadic, ctor_identity_types


def enclosing_class_qualified_name(ctx: CastxmlParserContext, el: Element) -> str:
    """Fully-qualified (``ns::Outer::Class``) name of the class/struct/
    union enclosing a Constructor/Destructor element *el*.

    Distinct from calling ``qualified_name(ctx, el)`` directly on *el*: a
    Constructor/Destructor's own bare ``name`` attribute already equals
    the class's own leaf name, so walking from *el* itself would count
    that leaf twice (``Foo::Foo`` instead of ``ns::Foo``). Walking from
    *el*'s ``context`` (the class element) instead starts one level up,
    at the class's own name.
    """
    class_el = ctx.resolve(el.get("context", ""))
    if class_el is None:
        return el.get("name", "")
    return qualified_name(ctx, class_el)


def function_mangled_name(
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
        # would — see ctor_param_identity_type's docstring: without it,
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


def function_source_location(
    ctx: CastxmlParserContext, el: Element
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
        loc_el = ctx.id_map.get(loc_id) if loc_id else None
        if loc_el is not None:
            file_id = loc_el.get("file", "")
            line = loc_el.get("line", "")
    file_el = ctx.id_map.get(file_id) if file_id else None
    fname = file_el.get("name", "") if file_el is not None else ""
    source_loc = f"{fname}:{line}" if fname and line else None
    return source_loc, loc_el


def function_is_explicit(
    ctx: CastxmlParserContext, el: Element, loc_el: Element | None
) -> bool | None:
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
            else source_line_has_explicit(ctx, loc_el, el)
        )
    return None


def function_ref_qualifier(el: Element, mangled: str) -> str:
    """Derive the &/&& ref-qualifier from the refqual attribute or the mangling."""
    # C++ ref-qualifier: newer castxml emits refqual="lvalue"/"rvalue",
    # but released versions (≤0.6.x) omit the attribute entirely, so
    # fall back to the Itanium mangling — the qualifier is encoded as
    # R (&) / O (&&) right after the CV-qualifiers in <nested-name>.
    refqual_raw = el.get("refqual", "")
    return {"lvalue": "&", "rvalue": "&&"}.get(
        refqual_raw, ""
    ) or _ref_qualifier_from_mangled(mangled)


def function_exception_spec(ctx: CastxmlParserContext, el: Element) -> str:
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
    thrown = ", ".join(type_name(ctx, tid) for tid in throw_attr.split())
    return f"throw({thrown})"


def parse_function_element(
    ctx: CastxmlParserContext, el: Element, hidden_friend_owner_by_id: dict[str, str]
) -> Function | None:
    """Build a Function from a castxml function-like element, or None if filtered."""
    name = function_display_name(ctx, el)
    if not name:
        return None
    # Skip compiler built-ins and command-line synthetic declarations
    if is_builtin_element(ctx, el):
        return None
    raw_mangled = el.get("mangled", "")
    ret_id = el.get("returns", "")
    ret_type = type_name(ctx, ret_id) if ret_id else "void"
    ret_ptr_depth = pointer_depth(ctx, ret_id) if ret_id else 0

    params, is_variadic, ctor_identity_types = parse_function_params(ctx, el)
    qualified_scope = (
        enclosing_class_qualified_name(ctx, el)
        if el.tag in ("Constructor", "Destructor")
        else ""
    )
    mangled = function_mangled_name(
        el, name, ctor_identity_types, raw_mangled, qualified_scope
    )

    # Real ELF export evidence overrides castxml's language-mode guess:
    # castxml ALWAYS emits a pseudo-Itanium `mangled` attribute, even for
    # a plain C function parsed in ambiguous/C++ mode (confirmed
    # empirically — the "C functions: use plain name" fallback in
    # function_mangled_name is otherwise dead code, since raw_mangled is
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
    # Checks BOTH exported_dynamic and exported_static -- a C API observed
    # only through a static archive's own export set (a bare, unmangled
    # symbol) must recover the identical extern-"C" override a
    # dynamically-linked one gets; restricting this to exported_dynamic
    # alone left a static-archive-only C function's guessed C++ mangling
    # standing, disagreeing with both the archive's own observed symbol
    # and the clang producer's extern_c identity for the same declaration
    # (Codex review, PR #943). Mirrors the identical
    # exported_dynamic|exported_static union dumper_castxml.py's own
    # sibling variable-level override already uses.
    #
    # Deliberately NOT gated on ``mangled.startswith("_Z")`` (an earlier
    # revision was): that hard-coded the Itanium mangling prefix, so a
    # Windows CI leg's real MSVC-targeting castxml -- which decorates a
    # guessed C-linkage function with its own ``?...@@...`` prefix, never
    # Itanium's ``_Z`` -- silently never matched the condition at all,
    # leaving the bogus MSVC-decorated guess standing even though the
    # real export table already confirmed the bare name (confirmed via a
    # real Windows CI failure, Codex review, PR #943). Nothing else in
    # this condition is ABI-specific: `mangled not in
    # (exported_dynamic|exported_static)` already means "not itself a
    # real observed export" regardless of what guessed prefix produced
    # it, so dropping the prefix check makes this override recognize the
    # identical evidence on every mangling scheme castxml's underlying
    # compiler can guess, not just Itanium's.
    if (
        el.tag == "Function"
        and mangled not in ctx.exported_dynamic
        and mangled not in ctx.exported_static
        and name in (ctx.exported_dynamic | ctx.exported_static)
        and is_global_scope(ctx, el)
    ):
        mangled = name
        is_extern_c_override = True
    else:
        is_extern_c_override = False

    is_virtual = el.get("virtual") == "1"
    noexcept_re = re.search(r"noexcept", el.get("attributes", ""))
    vtable_index = _parse_vtable_index(el.get("vtable_index")) if is_virtual else None

    # Detect extern "C": explicit extern attribute OR no mangled name (C linkage)
    is_extern_c = (
        el.get("extern") == "1"
        or (
            not raw_mangled and el.tag == "Function"
        )  # C functions have no mangled name
        or is_extern_c_override
    )

    source_loc, loc_el = function_source_location(ctx, el)
    access = access_level(el)
    is_deleted = el.get("deleted") == "1"
    visibility_ = (
        ctor_or_dtor_visibility(
            ctx, raw_mangled, name, access, is_deleted, el.get("artificial") == "1"
        )
        if el.tag in ("Constructor", "Destructor")
        else visibility(ctx, raw_mangled, name)
    )

    # Hoisted so the identity constructor is handed the identical values
    # the model object records, rather than a second, independently
    # recomputed opinion about the same facts (ADR-063 Phase 2).
    is_const = el.get("const") == "1"
    is_volatile = el.get("volatile") == "1"
    ref_qualifier = function_ref_qualifier(el, mangled)
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret_type,
        params=params,
        visibility=visibility_,
        is_virtual=is_virtual,
        is_noexcept=bool(noexcept_re),
        is_extern_c=is_extern_c,
        vtable_index=vtable_index,
        source_location=source_loc,
        is_static=el.get("static") == "1",
        is_const=is_const,
        is_volatile=is_volatile,
        is_pure_virtual=el.get("pure_virtual") == "1",
        is_deleted=is_deleted,
        # castxml emits inline="1" for inline functions/methods
        is_inline=el.get("inline") == "1",
        access=access,
        return_pointer_depth=ret_ptr_depth,
        ref_qualifier=ref_qualifier,
        is_explicit=function_is_explicit(ctx, el, loc_el),
        # Hidden-friend marker: castxml records the link via the
        # ``befriending`` attribute on the class element. We resolved
        # the referenced ids upfront and check membership here.
        is_hidden_friend=el.get("id", "") in hidden_friend_owner_by_id,
        hidden_friend_owner=hidden_friend_owner_by_id.get(el.get("id", "")),
        is_variadic=is_variadic,
        # Semantic contract / calling-convention attributes, filtered from
        # the compound ``attributes`` string (same channel as noexcept).
        contract_attributes=_extract_contract_attributes(el.get("attributes", "")),
        exception_spec=function_exception_spec(ctx, el),
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
        # ADR-063 Phase 2. castxml ALWAYS emits a pseudo-Itanium `mangled`
        # attribute, even for a plain C function (see the extern-"C"
        # override above), so a genuine mangling is only offered when this
        # element's own linkage says it is one -- otherwise the C-linkage
        # case routes through `is_extern_c`, the same order
        # `finding_identity.resolve_function_identity` applies.
        entity_id=entity_id_for_function(
            scope_path(ctx, el),
            name,
            mangled_name=None if is_extern_c else mangled,
            is_extern_c=is_extern_c,
            param_types=tuple(p.type for p in params),
            is_const=is_const,
            is_volatile=is_volatile,
            ref_qualifier=ref_qualifier,
            is_variadic=is_variadic,
        ),
    )


def parse_functions(ctx: CastxmlParserContext) -> list[Function]:
    funcs: list[Function] = []
    hidden_friend_owner_by_id = build_hidden_friend_ids(ctx)
    for el in ctx.function_els:
        func = parse_function_element(ctx, el, hidden_friend_owner_by_id)
        if func is not None:
            funcs.append(func)
    return funcs

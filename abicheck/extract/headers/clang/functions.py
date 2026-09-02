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

"""Function-entity parsing for the clang backend (ADR-061 D9).

Second entity module split out of ``_ClangAstParser`` proper, after
``enums.py``. Reads the ``_Decl`` list ``dumper_clang._ClangAstParser._walk``
already categorized (no traversal of its own) and produces ``Function``
model objects, using ``context.py`` for everything below the function-entity
level — including ``RecordVtableIndex.virtual_mangled_names()``, needed here
to recover a signature-matched virtual override that carries neither a
``virtual`` nor an ``override`` keyword (clang's JSON gives no other signal
for it; see that method's own docstring).

``access_level``/``visibility``/``source_location``/``clang_deprecated_message``
are NOT *implemented* here even though ``parse_functions`` needs all four:
each is also read by variable/constant/typedef/record-field parsing (still
in ``dumper_clang.py``), so per this package's own "shared across entity
kinds" rule (see ``context.py``'s docstring) they live there instead — the
role castxml's separate ``location.py`` plays, folded into ``context.py``
here since clang's cross-entity node-inspection surface is small enough not
to need its own module yet.

``parse_functions`` takes its own default-value evaluator as an explicit
parameter rather than importing one: the real evaluator
(``dumper_clang._initializer_value``, itself built on ``dumper_clang._id_index``)
depends on ``dumper_clang_expr.py``, which imports ``diff_cxx_rules``
(classified ``compare``) for ``itanium_scope_components`` — importing either
from here would give this ``extract``-classified package a real
``extract -> compare`` edge, the identical reasoning ``enums.py`` already
documents for its own ``evaluate_int`` parameter.

Contract-attribute filtering, override/restrict/va-list parameter
classification, and the eligible-override-kind set are read from
``dumper_clang_attributes.py``/``dumper_clang_qualifiers.py`` by their
PUBLIC names (``clang_contract_attributes``, ``clang_method_is_override``,
``clang_param_is_restrict``, ``clang_param_is_va_list``,
``OVERRIDE_ELIGIBLE_KINDS``, imported here under their old private aliases
for a minimal diff against every existing call site in this module) rather
than a still-flat sibling's private surface — those modules made the names
public specifically for this cross-boundary read, each keeping its old
private spelling as a back-compat alias (Codex review, PR #940), the same
treatment ``location.py``'s ``deprecation_marker``/``contract_attributes``
and ``context.py``'s ``is_record_definition`` already received.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ....dumper_clang_attributes import (
    clang_contract_attributes as _clang_contract_attributes,
)
from ....dumper_clang_qualifiers import (
    OVERRIDE_ELIGIBLE_KINDS as _OVERRIDE_ELIGIBLE_KINDS,
    clang_method_is_override as _clang_method_is_override,
    clang_param_is_restrict as _clang_param_is_restrict,
    clang_param_is_va_list as _clang_param_is_va_list,
)
from ....model import Fact, Function, Param
from ....model.identity import (
    canonicalize_type_param_references,
    entity_id_for_function,
)
from ..scope_segments import strip_record_scopes
from .context import (
    _Decl,
    access_level as _access_level,
    clang_deprecated_message as _clang_deprecated_message,
    is_builtin_file,
    is_darwin_target as _is_darwin_target,
    qualtype as _qualtype,
    source_location as _source_location,
    symbol_candidates as _symbol_candidates,
    visibility as _visibility,
)
from .return_type import return_type as _return_type

#: Evaluates a param's default-argument initializer to its snapshot value
#: (or ``None`` for an unevaluable one). Matches
#: ``dumper_clang._initializer_value``'s signature (bound to that parser's
#: own ``_id_index`` memoized id-lookup) exactly.
DefaultValueEvaluator = Callable[[dict[str, Any]], "str | None"]


def _pointer_depth(type_str: str) -> int:
    """Best-effort pointer nesting depth from a written type spelling.

    castxml computes this from the type graph; on the clang path we count
    top-level ``*`` tokens in the ``qualType`` spelling (``const char *`` → 1,
    ``int **`` → 2), ignoring any inside template/array brackets. Stable for the
    pointer-depth-change detector even though it is a spelling heuristic.
    """
    depth = 0
    bracket = 0
    for ch in type_str:
        if ch in "<[(":
            bracket += 1
        elif ch in ">])":
            bracket = max(0, bracket - 1)
        elif ch == "*" and bracket == 0:
            depth += 1
    return depth


def _is_noexcept_qualifier(quals: str) -> bool:
    """Whether a function's trailing qualifiers denote a *non-throwing* spec.

    A bare ``noexcept`` (and ``noexcept(true)`` / ``noexcept(1)``) is
    non-throwing; ``noexcept(false)`` / ``noexcept(0)`` is *throwing* and must
    not be treated as ``noexcept`` — since C++17 the exception specification is
    part of the function type, so conflating the two would hide a real ABI break
    (CodeRabbit review). A dependent ``noexcept(expr)`` keeps its conservative
    "non-throwing" reading (the spelling is all the header AST exposes).
    """
    m = re.search(r"\bnoexcept(?:\s*\(([^)]*)\))?", quals)
    if m is None:
        return False
    expr = m.group(1)
    if expr is None:
        return True
    return expr.strip() not in ("false", "0")


def _clang_exception_spec(quals: str) -> str:
    """The dynamic exception-specification spelling from trailing qualifiers.

    ``""`` when the function has no ``throw(...)`` spec (noexcept is handled
    separately by :func:`_is_noexcept_qualifier`).
    """
    m = re.search(r"\bthrow\s*\(([^)]*)\)", quals)
    if m is None:
        return ""
    inner = ", ".join(p.strip() for p in m.group(1).split(",") if p.strip())
    return f"throw({inner})"


def _function_qualifiers(qualtype: str) -> str:
    """The trailing cv/ref/exception qualifiers after a function's parameter list.

    Returns the substring after the matching ``)`` of the top-level parameter
    list — e.g. ``" const noexcept"`` for ``int (int) const noexcept`` — so the
    caller can detect ``const``/``volatile``/``noexcept`` and the ref-qualifier.
    """
    bracket = 0
    start = -1
    for idx, ch in enumerate(qualtype):
        if ch in "<[":
            bracket += 1
        elif ch in ">]":
            bracket = max(0, bracket - 1)
        elif ch == "(" and bracket == 0 and start == -1:
            start = idx
            bracket += 1
            # consume the parameter-list parentheses
            depth = 1
            j = idx + 1
            while j < len(qualtype) and depth:
                if qualtype[j] == "(":
                    depth += 1
                elif qualtype[j] == ")":
                    depth -= 1
                j += 1
            return qualtype[j:]
    return ""


def _param_has_default(param: dict[str, Any]) -> bool:
    """Whether a ``ParmVarDecl`` carries a default argument.

    clang flags it either with ``"init": "c"`` or by nesting the default-value
    expression as the parameter's lone ``inner`` child.
    """
    if param.get("init"):
        return True
    return any(
        isinstance(c, dict) and not str(c.get("kind", "")).endswith(("Attr", "Comment"))
        for c in param.get("inner", []) or []
    )


def function_template_param_kinds(
    function_template_decl: dict[str, Any],
    enclosing_type_param_names: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """The per-position parameter-KIND signature of a ``FunctionTemplateDecl``'s
    own (uninstantiated) template parameter list, in declaration order.

    Distinguishes ``template<class T> void f();`` from ``template<int N>
    void f();`` -- two distinct, legally-overloaded declarations (real C++:
    explicit-template-argument calls like ``f<5>()`` disambiguate them) that
    otherwise share the identical scope, leaf name, and (empty) ordinary
    parameter list. Neither ever gets a real ``mangledName`` from clang
    (confirmed by direct compilation: the key is absent entirely, not merely
    empty, for an uninstantiated template), so ``entity_id_for_function``'s
    "sig" signature-fallback tuple -- built only from ordinary
    `param_types`/qualifiers -- could not tell them apart either, collapsing
    two real declarations onto one ``EntityId`` (Codex review, PR #943).

    Each entry is ``"type"``/``"type..."`` (a ``TemplateTypeParmDecl``),
    ``"template"``/``"template..."`` (a ``TemplateTemplateParmDecl``), or
    ``"nontype:<type-spelling>"``/``"nontype...:<type-spelling>"`` (a
    ``NonTypeTemplateParmDecl``, keyed on its own declared type so
    ``template<int N>`` and ``template<bool B>`` stay distinguishable too --
    deliberately NOT restricted to
    ``templates._SAFE_NONTYPE_INT_TYPES`` the way
    ``templates._template_param_kinds`` is, since this function only needs a
    stable per-parse discriminator string, never a real evaluated argument
    value the way a specialization match does). The trailing ``"..."``
    reflects the node's own ``isParameterPack`` flag (confirmed by direct
    compilation: clang sets ``isParameterPack: true`` on all three parameter
    node kinds for ``template<class... T>``/``template<int... N>``/
    ``template<template<class> class... TT>``, and omits the key entirely
    otherwise) -- without it, ``template<class T> void f();`` and
    ``template<class... T> void f();`` are two more legal overloads that
    share every other discriminator this function produces and would
    otherwise still collide (Codex review, PR #943, on the first version of
    this function). Stops at the first non-parameter child, mirroring
    ``templates._template_param_kinds``'s identical convention for a
    ``ClassTemplateDecl``'s own list -- the parameter list always precedes
    the pattern's own body in ``inner`` order.

    A non-type parameter's own declared type is canonicalized against the
    PRECEDING type-like (type OR template-template) parameters' names
    before joining into a ``"nontype:"`` entry: ``template<class T, T N>
    void f();`` and ``template<class U, U N> void f();`` are the
    identical declaration under a pure parameter rename, but clang's own
    ``qualType`` for ``N`` spells the dependent type literally as
    ``"T"``/``"U"`` (confirmed by direct compilation), which would
    otherwise fingerprint a non-semantic rename as two different
    overloads (Codex review, PR #943, on the second version of this
    function). A template-TEMPLATE parameter's own name is canonicalized
    the identical way: ``template<template<class> class TT, TT<int>* N>
    void f();`` renamed to ``UU`` produces ``qualType`` ``"TT<int> *"``/
    ``"UU<int> *"`` (confirmed by direct compilation -- a real,
    syntactically valid non-type parameter can depend on a preceding
    template-template parameter's own instantiation, unlike a bare
    reference to the template-template parameter itself, which is not
    legal C++), so the same fix applies to it too (Codex review, PR #943,
    on the fourth version of this function). A NON-TYPE parameter's own
    NAME also joins the same substitution list, once its own spelling is
    canonicalized: a later parameter's dependent spelling can reference an
    earlier non-type parameter too (``decltype(N)`` for a preceding
    ``int N``, confirmed by direct compilation -- Codex review, PR #943,
    on the fifth version of this function). Each earlier parameter's own
    name -- whichever of the three kinds it is -- is replaced by its
    0-based position among all parameters seen so far
    (``"type-param-0"``, ...), via :func:`~abicheck.model.identity.
    canonicalize_type_param_references`'s single combined-alternation
    pass (see that function's own docstring for why a naive per-name
    sequential pass has its own self-inflicted collision this avoids) --
    safe against one name being a substring of another (``T`` inside
    ``TT``) since a word-boundary match never fires mid-identifier, and
    against a compound spelling like ``"T *"``, which still resolves to
    ``"type-param-0 *"``.

    A ``TemplateTemplateParmDecl``'s own entry additionally encodes ITS
    parameter list recursively, e.g. ``"template(type,type)"`` for
    ``template<template<class, class> class TT>``: confirmed by direct
    compilation that clang shapes a ``TemplateTemplateParmDecl``'s ``inner``
    exactly like a top-level parameter list (its own ``TemplateTypeParmDecl``/
    ``NonTypeTemplateParmDecl``/nested ``TemplateTemplateParmDecl`` children),
    so ``template<template<class> class TT>`` and ``template<template<class,
    class> class TT>`` -- two more legal overloads sharing every OTHER
    discriminator this function produces -- collapsed onto one bare
    ``"template"`` entry before this recursion (Codex review, PR #943, on
    the third version of this function). A nested non-type parameter's own
    declared type CAN legally reference an ENCLOSING parameter's name --
    confirmed by direct compilation: ``template<class T, template<T> class
    TT> void f();`` is valid C++, and clang's ``qualType`` for the nested,
    unnamed ``NonTypeTemplateParmDecl`` inside ``TT`` spells its type as the
    literal enclosing name ``"T"`` -- so the recursive descent is seeded
    with every enclosing name already visible, not an empty scope (Codex
    review, PR #943, on the fifth version of this function; a nested
    parameter's OWN names still do not leak back out to an enclosing or
    sibling scope, only inherit inward).

    *enclosing_type_param_names* extends that same seeding one level
    further OUT, to an ENCLOSING CLASS template's own parameter names --
    the identical hazard as the nested-parameter-template case just
    above, just one level higher: ``template<class T> struct A {
    template<T N> void f(); };`` renamed to ``template<class U> struct A
    { template<U N> void f(); };`` is the identical declaration, and
    clang's ``qualType`` for the MEMBER template's own non-type parameter
    ``N`` spells its type literally as the enclosing class template's
    own parameter name, ``"T"``/``"U"`` (confirmed by direct
    compilation) -- the same hazard ``class_template_type_param_names``
    already exists to fix for an ORDINARY member's own parameter type,
    just for a member that is itself a ``FunctionTemplateDecl`` (Codex
    review, PR #943, on a later round). Passed through unchanged to the
    top-level call only -- a nested ``TemplateTemplateParmDecl``'s own
    recursive call already seeds itself from the accumulated
    ``type_param_names`` at that point, which already includes whatever
    was passed in here.
    """
    return _template_param_kinds_from_node(
        function_template_decl, enclosing_type_param_names
    )[0]


def function_template_type_param_names(
    function_template_decl: dict[str, Any],
) -> tuple[str, ...]:
    """The TOP-LEVEL parameter names (all three kinds -- type,
    template-template, and non-type) of a ``FunctionTemplateDecl``'s own
    parameter list, in declaration order.

    Companion to :func:`function_template_param_kinds`, sharing its exact
    walk (:func:`_template_param_kinds_from_node`) -- but for a different
    consumer: ``entity_id_for_function``'s ORDINARY parameter list has the
    identical dependent-rename hazard a non-type template parameter's own
    declared type already gets canonicalized against (see that function's
    own docstring), since an ordinary parameter can equally be typed
    ``T``/``U``, or even ``decltype(N)`` for a non-type parameter ``N``,
    after a pure template-parameter rename (Codex review, PR #943). Only
    the top-level names are returned -- never a nested
    ``TemplateTemplateParmDecl``'s own inner ones, which are invisible
    outside that parameter's own signature and so can never appear in the
    enclosing function's ordinary parameter list.
    """
    return _template_param_kinds_from_node(function_template_decl)[1]


def class_template_type_param_names(
    class_template_decl: dict[str, Any],
) -> tuple[str, ...]:
    """The TOP-LEVEL parameter names of a ``ClassTemplateDecl``'s (or a
    ``ClassTemplatePartialSpecializationDecl``'s) own parameter list, in
    declaration order.

    The class-template sibling of :func:`function_template_type_param_names`
    -- confirmed by direct compilation that both node kinds carry the
    identical shape (their own ``TemplateTypeParmDecl``/
    ``TemplateTemplateParmDecl``/``NonTypeTemplateParmDecl`` children,
    followed by the pattern body), so the same walk applies unchanged.
    Needed for the SAME dependent-rename hazard as the function-template
    case, but one level further out: ``template<class T> struct A { void
    f(T); };`` renamed to ``template<class U> struct A { void f(U); };``
    is the identical declaration, but a member's ordinary parameter type
    spells the ENCLOSING class template's own parameter name literally
    (``"T"``/``"U"``) -- confirmed by direct compilation, since `f` here
    is an ordinary (non-template) member of the class-template pattern,
    never itself a ``FunctionTemplateDecl`` (Codex review, PR #943).
    Threaded by ``dumper_clang.py``'s ``_walk`` alongside (never
    replacing) any FUNCTION-template names already accumulated, so a
    member TEMPLATE nested inside a class template sees both its own and
    the enclosing class's parameter names.
    """
    return _template_param_kinds_from_node(class_template_decl)[1]


def _template_param_kinds_from_node(
    node: dict[str, Any],
    enclosing_type_param_names: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Shared recursive body of :func:`function_template_param_kinds` and
    :func:`function_template_type_param_names` -- returns ``(kinds,
    type_param_names)``.

    Runs identically over a ``FunctionTemplateDecl`` (the top-level call)
    and a ``TemplateTemplateParmDecl`` (the nested, recursive call) --
    confirmed by direct compilation that clang gives the latter's own
    ``inner`` the identical parameter-list shape as the former's, so no
    node-kind-specific branching is needed here. *enclosing_type_param_names*
    seeds the substitution scope for a nested (``TemplateTemplateParmDecl``)
    call with every name already visible from the enclosing list -- a
    nested non-type parameter's own declared type can legally reference one
    (confirmed by direct compilation; see :func:`function_template_param_kinds`'s
    own docstring) -- while the top-level call passes none, since a
    ``FunctionTemplateDecl``'s own list has no enclosing scope of its own.
    Enclosing names are seeded once, ahead of any name this call's own
    parameters accumulate, so a nested parameter never shadows an enclosing
    one's POSITION in the substitution index -- only names visible when the
    nested reference could legally appear are ever candidates.
    """
    kinds: list[str] = []
    type_param_names: list[str] = list(enclosing_type_param_names)
    own_type_param_names: list[str] = []
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        kind = child.get("kind")
        pack = "..." if child.get("isParameterPack") else ""
        if kind == "TemplateTypeParmDecl":
            kinds.append(f"type{pack}")
            name = str(child.get("name") or "")
            type_param_names.append(name)
            own_type_param_names.append(name)
        elif kind == "TemplateTemplateParmDecl":
            nested_kinds, _nested_names = _template_param_kinds_from_node(
                child, tuple(type_param_names)
            )
            kinds.append(f"template{pack}({','.join(nested_kinds)})")
            name = str(child.get("name") or "")
            type_param_names.append(name)
            own_type_param_names.append(name)
        elif kind == "NonTypeTemplateParmDecl":
            type_obj = child.get("type")
            spelling = (
                str(type_obj.get("qualType", "")) if isinstance(type_obj, dict) else ""
            )
            spelling = canonicalize_type_param_references(
                spelling, tuple(type_param_names)
            )
            kinds.append(f"nontype{pack}:{spelling}")
            # A LATER parameter's own dependent spelling can reference this
            # one's name too (e.g. `decltype(N)` for a preceding non-type
            # parameter `N`), so it joins the same substitution list --
            # confirmed by direct compilation (Codex review, PR #943).
            name = str(child.get("name") or "")
            type_param_names.append(name)
            own_type_param_names.append(name)
        else:
            break
    return tuple(kinds), tuple(own_type_param_names)


def parse_functions(
    functions: list[_Decl],
    *,
    exported_dynamic: set[str],
    exported_static: set[str],
    virtual_mangled_names: frozenset[str],
    target_triple: str | None,
    default_value: DefaultValueEvaluator,
) -> list[Function]:
    funcs: list[Function] = []
    for entry in functions:
        node = entry.node
        if is_builtin_file(entry.file):
            continue
        name = str(node.get("name", ""))
        if not name:
            continue
        # The declaration's own, always-unqualified leaf name. `name` itself
        # may be requalified below for a template specialization's member,
        # which is a *display*/owner-matching spelling, not this
        # declaration's leaf identity (ADR-063 Phase 2).
        leaf_name = name
        if entry.scope and "<" in entry.scope[-1]:
            # A method of a concrete class-template specialization
            # (`A<int>::f`) -- unlike an ordinary member, whose name
            # this backend deliberately leaves bare everywhere else
            # (`owner_class_of` recovers its owner from the MANGLED
            # name instead, which works fine there since a plain
            # class's mangled scope component already IS its matching
            # spelling). A specialization's own mangled scope component
            # is the RAW, un-spelled Itanium template-argument encoding
            # (`"AIiE"`, confirmed with a real clang build) -- which
            # never matches `RecordType.bases`'s spelled form
            # (`"A<int>"`, built from clang's own type printer) at all,
            # so `owner_class_of`'s mangled fallback silently failed to
            # recognize an inherited-slot override whose base is a
            # template specialization, producing a false
            # `TYPE_VTABLE_CHANGED` (Codex review, fresh evidence: found
            # while verifying the base-lookup fix end to end -- the
            # vtable itself now resolves correctly, but this SEPARATE
            # owner-matching gap was still reachable once it did).
            # Qualifying the name here lets `owner_class_of`'s
            # PREFERRED (already-qualified-name) branch resolve the
            # SAME spelling `RecordType.bases` records, sidestepping
            # the mismatched mangled fallback entirely -- mirroring
            # what DWARF already does for every member unconditionally
            # (`owner_class_of`'s own docstring).
            name = "::".join((*entry.scope, name))
        qualtype = _qualtype(node)
        # ``raw_mangled`` distinguishes "clang genuinely emitted this
        # mangling" from "clang emitted none, and `mangled` fell back to
        # the bare name" -- ``node.get("mangledName", "")`` alone conflates
        # the two, since an absent key and a present-but-empty one both
        # read as falsy. This matters because the very next `is_extern_c`
        # check below trusts "mangled == name" as a genuine C-linkage
        # signal, which is only true when clang actually said so: verified
        # directly (`clang -x c -Xclang -ast-dump=json`) that a real
        # plain-C `FunctionDecl` DOES carry an explicit `"mangledName"` key
        # equal to `name`, while an uninstantiated C++ function template's
        # `FunctionDecl` carries NO `"mangledName"` key at all -- confirmed
        # by checking key presence, not just value truthiness (Codex/
        # CodeRabbit review, fresh evidence: two uninstantiated template
        # methods named `f` in different namespaces both fell back to the
        # bare name, so the old check wrongly read that fallback collision
        # as C linkage and collapsed both to one `EntityId`).
        raw_mangled = node.get("mangledName")
        mangled = raw_mangled or name
        quals = _function_qualifiers(qualtype)
        ret_type = _return_type(qualtype) or "void"
        params = [
            Param(
                name=str(p.get("name", "")),
                type=_qualtype(p),
                pointer_depth=_pointer_depth(_qualtype(p)),
                # G31 Phase C: castxml was the ONLY producer of this fact (`_resolve_cv_restrict`), so a castxml-vs-clang comparison of unchanged headers reported PARAM_RESTRICT_CHANGED for every restrict-qualified parameter -- the detector compares the two bools directly, with no producer gate to decline on (unlike `deprecated`/`is_scoped` before this phase).
                is_restrict=_clang_param_is_restrict(p),
                # G31 Phase C continued: same shape as `is_restrict` above -- castxml never populated this fact either. See `dumper_clang_qualifiers._clang_param_is_va_list`. is_va_list_fact is `partial`, not `present`: the check only covers x86-64 System V, and conservatively answers `False` -- not "confirmed no" -- on any other target (Codex review; target-scoping residual unchanged, per that function's own docstring).
                is_va_list=(_iv := _clang_param_is_va_list(p)),
                is_va_list_fact=Fact.partial(_iv),
                # Preserve the actual default-argument value (so a changed
                # default fires PARAM_DEFAULT_VALUE_CHANGED); fall back to a
                # bare presence marker when the value can't be evaluated.
                default=(default_value(p) or "default")
                if _param_has_default(p)
                else None,
            )
            for p in node.get("inner", []) or []
            if isinstance(p, dict) and p.get("kind") == "ParmVarDecl"
        ]
        kind = node.get("kind")
        is_explicit: bool | None
        if kind in ("CXXConstructorDecl", "CXXConversionDecl"):
            is_explicit = bool(node.get("explicit"))
        else:
            is_explicit = None
        if "&&" in quals:
            ref_qualifier = "&&"
        elif re.search(r"(?<!&)&(?!&)", quals):
            ref_qualifier = "&"
        else:
            ref_qualifier = ""
        # Hoisted from the ``Function(...)`` call below so the identity
        # constructor is handed the identical values the model object
        # records, rather than a second, independently-recomputed opinion
        # about the same facts (ADR-063 Phase 2). The `mangled == name`
        # heuristic below is gated on `raw_mangled is not None` (see that
        # variable's own comment above) -- without the gate, an
        # uninstantiated template's fallback-to-`name` mangling reads as
        # false C linkage.
        #
        # The plain `raw_mangled == name` case stays UNGATED -- it holds on
        # every platform for a plain-C declaration clang mangles as its own
        # bare name, with no leading-underscore stripping involved at all.
        # The Darwin-gated `symbol_candidates` de-prefixing is a SEPARATE,
        # additional fallback layered on top (Codex review, ADR-063 Phase
        # 6, fifteenth AND sixteenth rounds, fresh evidence each time): a
        # genuinely plain-C compilation unit has no `LinkageSpecDecl` at
        # all (that node only exists in C++'s grammar), so `entry.extern_c`
        # never becomes True for it, and on Mach-O clang's own
        # `mangledName` carries the Darwin linker's leading underscore
        # ("_foo" for source-level "foo") that castxml's "pure" convention
        # never does -- so the bare-equality check alone always missed
        # this case even though castxml correctly recognizes the identical
        # declaration as extern "C". Left unfixed, this function's
        # `entity_id` stayed tagged `("mangled", "_foo")`
        # (`dumper_hybrid.py`'s own Mach-O underscore-stripping rewrite
        # only re-spells the mangled tag's VALUE, not its KIND) while
        # castxml's tags the same declaration `("extern_c",)`, so a hybrid
        # merge's bare-`EntityId` matching never recognized the two as one
        # declaration and retained it twice in `semantic_ir` even though
        # the flat `functions` list (which matches on the bare mangled
        # string, not `EntityId`) already unified it.
        #
        # The Darwin gate on the de-prefixed fallback is NOT optional
        # (sixteenth round, fresh evidence, a real regression an earlier,
        # UNGATED revision of this same fallback introduced): on a
        # NON-Darwin target, a real, explicit `asm("_foo")` label
        # genuinely produces `raw_mangled == "_foo"` while `name ==
        # "foo"` and `entry.extern_c` stays False -- that IS a real,
        # distinct mangled identity (an asm label), not a linker-
        # decoration artifact, and castxml's own resolver keeps it tagged
        # `("mangled", "_foo")` for the identical declaration. Gating the
        # de-prefixed fallback ALONE on Darwin -- rather than the whole
        # check, which would also have broken the plain-equality case
        # above on every non-Darwin platform -- is what fixes this
        # without reintroducing a different regression. `symbol_
        # candidates` itself stays target-agnostic (it is the identical
        # tolerant-match helper `visibility()` already uses for pure
        # export-table membership, where trying the de-prefixed form is
        # always safe); the identity decision built on top of it is what
        # needs the platform gate.
        is_extern_c = (
            entry.extern_c
            or raw_mangled == name
            or (
                raw_mangled is not None
                and _is_darwin_target(target_triple)
                and name in _symbol_candidates(raw_mangled)
            )
        )
        is_const = bool(re.search(r"\bconst\b", quals))
        is_volatile = bool(re.search(r"\bvolatile\b", quals))
        is_variadic = bool(node.get("variadic")) or "..." in qualtype
        funcs.append(
            Function(
                name=name,
                mangled=mangled,
                return_type=ret_type,
                params=params,
                visibility=_visibility(
                    exported_dynamic,
                    exported_static,
                    str(node.get("mangledName", "")),
                    name,
                ),
                # bool(node.get("virtual")) alone misses a signature-
                # matched override with neither `virtual` nor `override`
                # written -- clang's JSON gives no direct signal for that
                # case at all (see dumper_clang_vtable.py's own
                # docstring). `virtual_mangled_names` recovers it from
                # the reconstructed vtables, which already do this
                # matching; only ever widens False -> True.
                #
                # Restricted to actual member-function kinds (Codex
                # review, fresh evidence): an uninstantiated template
                # method carries no `mangledName` at all, so
                # `_collect_virtual_slots` falls back to its bare,
                # unmangled `name` as the slot's "mangled" identity (e.g.
                # `"f"`). A free `extern "C"` function sharing that same
                # bare name mangles to the identical string by design (C
                # linkage), so the plain `mangled in
                # virtual_mangled_names` membership test above matched an
                # unrelated global FunctionDecl purely by name collision --
                # confirmed with a real clang dump of `template<class T>
                # struct A { virtual void f(); }; extern "C" void f();`,
                # where both `f`s share the identical unmangled fallback
                # string. Only a CXXMethodDecl/CXXConstructorDecl/
                # CXXDestructorDecl/CXXConversionDecl can be virtual at all
                # in C++, and `_collect_virtual_slots` only ever walks
                # those same member kinds when building
                # `virtual_mangled_names` -- a bare `FunctionDecl` (never a
                # class member) can never legitimately appear in that set,
                # so excluding it here closes the collision without
                # narrowing any real member-override case.
                is_virtual=bool(node.get("virtual"))
                or (kind != "FunctionDecl" and mangled in virtual_mangled_names),
                is_noexcept=_is_noexcept_qualifier(quals),
                # An ``extern "C"`` linkage spec is authoritative; fall back
                # to the mangled==name heuristic for a plain C-mode parse
                # (no LinkageSpecDecl, but C-linkage names equal their symbol).
                is_extern_c=is_extern_c,
                vtable_index=None,
                source_location=_source_location(entry),
                is_static=node.get("storageClass") == "static",
                is_const=is_const,
                is_volatile=is_volatile,
                is_pure_virtual=bool(node.get("pure")),
                is_deleted=bool(node.get("explicitlyDeleted")),
                is_inline=bool(node.get("inline")),
                access=_access_level(entry.access),
                return_pointer_depth=_pointer_depth(ret_type),
                ref_qualifier=ref_qualifier,
                is_explicit=is_explicit,
                # The `explicit` specifier is conceptually inapplicable
                # outside a constructor/conversion function -- an ordinary
                # method/free function's `is_explicit=None` above is a
                # confirmed non-gap, not missing evidence, so it gets its
                # own explicit Fact rather than falling through the
                # generic bridge into NOT_COLLECTED (Codex review, PR
                # #982).
                is_explicit_fact=(
                    Fact.present(is_explicit)
                    if kind in ("CXXConstructorDecl", "CXXConversionDecl")
                    else Fact.not_applicable()
                ),
                is_hidden_friend=entry.in_friend,
                # ``entry.scope`` is the enclosing-class scope path at the
                # point ``in_friend`` first became True (the FriendDecl's
                # own scope, since FriendDecl never pushes a scope level) —
                # i.e. exactly the befriending class, mirroring castxml's
                # ``befriending`` attribute resolution.
                hidden_friend_owner=(
                    "::".join(entry.scope) if entry.in_friend and entry.scope else None
                ),
                # An owner is conceptually inapplicable for an ordinary
                # (non-friend) function -- not a missing-evidence gap -- so
                # it gets its own explicit Fact rather than falling through
                # the generic bridge into NOT_COLLECTED (Codex review, PR
                # #982, same shape as is_explicit_fact above). A friend
                # whose owner scope couldn't be resolved (in_friend True,
                # scope empty) still falls through to NOT_COLLECTED -- that
                # is a real evidence gap, not an inapplicable field.
                hidden_friend_owner_fact=(
                    Fact.not_applicable()
                    if not entry.in_friend
                    else (Fact.present("::".join(entry.scope)) if entry.scope else None)
                ),
                # clang stamps "variadic": true on FunctionDecl; the
                # qualtype spelling ("void (int, ...)") is the fallback.
                is_variadic=is_variadic,
                contract_attributes=_clang_contract_attributes(
                    node, target_triple=target_triple
                ),
                exception_spec=_clang_exception_spec(quals),
                deprecated=_clang_deprecated_message(node),
                # G31 Phase C backend audit -- see _clang_method_is_override.
                is_override=(
                    _clang_method_is_override(node)
                    if kind in _OVERRIDE_ELIGIBLE_KINDS
                    else None
                ),
                # Same "confirmed non-gap, not missing evidence" shape as
                # is_explicit_fact above: `override` only makes sense on a
                # kind that can actually be virtual.
                is_override_fact=(
                    Fact.present(_clang_method_is_override(node))
                    if kind in _OVERRIDE_ELIGIBLE_KINDS
                    else Fact.not_applicable()
                ),
                is_compiler_generated=False,
                # ADR-063 Phase 2. `mangled_name` is offered here only when
                # `raw_mangled` is genuinely present -- NOT when `mangled`
                # (which may itself be the bare-`name` fallback) is merely
                # non-empty. Passing the fallback through as if it were a
                # real mangling was the exact bug this gate closes (Codex/
                # CodeRabbit review, fresh evidence): two uninstantiated
                # template functions named `f` in different namespaces both
                # have `raw_mangled is None`, so both would otherwise offer
                # the identical bogus "mangled name" `"f"`, which
                # `entity_id_for_function`'s mangled-name branch takes
                # priority over scope for -- collapsing two genuinely
                # distinct functions into one `EntityId`. The C-linkage case
                # is routed through `is_extern_c` instead, same order
                # `finding_identity.resolve_function_identity` applies.
                entity_id=entity_id_for_function(
                    # A hidden friend's OWN scope is the nearest enclosing
                    # namespace, never the befriending class it is lexically
                    # nested inside (Codex review, PR #943) -- see
                    # `strip_record_scopes`'s own docstring for the
                    # confirmed-by-compilation redefinition proof.
                    # `entry.scope_path` stays untouched everywhere else
                    # (display qualified name, `hidden_friend_owner` below),
                    # so this is the one place the distinction matters.
                    (
                        strip_record_scopes(entry.scope_path)
                        if entry.in_friend
                        else entry.scope_path
                    ),
                    leaf_name,
                    mangled_name=(
                        raw_mangled
                        if (raw_mangled is not None and not is_extern_c)
                        else None
                    ),
                    is_extern_c=is_extern_c,
                    param_types=tuple(p.type for p in params),
                    is_const=is_const,
                    is_volatile=is_volatile,
                    ref_qualifier=ref_qualifier,
                    is_variadic=is_variadic,
                    # Distinguishes two uninstantiated function templates
                    # overloaded only by template-parameter KIND (e.g.
                    # `template<class T> void f()` vs. `template<int N> void
                    # f()`) -- both share scope/leaf_name/param_types/
                    # mangled_name=None, so without this the "sig" fallback
                    # tuple would collide them too (Codex review, PR #943).
                    template_param_kinds=entry.template_param_kinds,
                    # Canonicalizes a dependent ORDINARY parameter type
                    # (e.g. `template<class T> void f(T)`) against a pure
                    # template-parameter rename the identical way a
                    # non-type template parameter's own declared type
                    # already is (Codex review, PR #943).
                    type_param_names=entry.template_type_param_names,
                    # A function template's return type can itself depend
                    # on a template parameter (`template<class T> typename
                    # T::x f(T);`) -- two such templates can share every
                    # other "sig" dimension and still be distinct, legal
                    # overloads (Codex review, PR #943). Ignored by
                    # `entity_id_for_function` for a non-template function,
                    # so this is a no-op there.
                    return_type=ret_type,
                ),
            )
        )
    return funcs

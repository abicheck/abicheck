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

"""Type-graph resolution for the castxml backend (ADR-061 D9).

Walks castxml's id-referenced type graph (``PointerType``, ``CvQualifiedType``,
``Typedef``, ``ElaboratedType``, ``ArrayType``, ...) to render a type
spelling, a pointer-nesting depth, an alignment, or a cv/restrict
qualification. Every function takes a
:class:`~.context.CastxmlParserContext` explicitly and reads/writes its
memoization caches directly, rather than being a bound method on the
monolithic parser class — the same "entity/responsibility modules share one
context object" shape D9 describes for parsed-entity modules, applied here
to the type-resolution responsibility every entity kind's parsing depends
on.
"""

from __future__ import annotations

from typing import Any

from ....name_classification import (
    strip_anonymous_type_location as _strip_anonymous_type_location,
)
from .context import CastxmlParserContext


def type_name(ctx: CastxmlParserContext, id_: str, depth: int = 0) -> str:
    # Memoized by id alone (not depth): the same type id is commonly
    # resolved from thousands of call sites on a large ABI surface. A
    # depth-capped ("?") result is never cached, so reaching the same id
    # again within budget still resolves it properly.
    cached = ctx.type_name_cache.get(id_)
    if cached is not None:
        return cached
    result = type_name_uncached(ctx, id_, depth)
    if depth <= 10:
        ctx.type_name_cache[id_] = result
    return result


def type_name_uncached(ctx: CastxmlParserContext, id_: str, depth: int = 0) -> str:
    if depth > 10:
        return "?"
    el = ctx.resolve(id_)
    if el is None:
        return "?"
    tag = el.tag
    if tag == "FundamentalType":
        return el.get("name", "?")
    if tag == "Enumeration":
        # Strip the same marker parse_enums() strips from the declaration.
        return _strip_anonymous_type_location(el.get("name", "?"))
    if tag == "PointerType":
        return type_name(ctx, el.get("type", ""), depth + 1) + "*"
    if tag == "ReferenceType":
        return type_name(ctx, el.get("type", ""), depth + 1) + "&"
    if tag == "RValueReferenceType":
        return type_name(ctx, el.get("type", ""), depth + 1) + "&&"
    if tag == "CvQualifiedType":
        inner_id = el.get("type", "")
        base = type_name(ctx, inner_id, depth + 1)
        # castxml's CvQualifiedType also carries `volatile`; only `const`
        # was read here previously, so a volatile-qualified type's name
        # silently dropped it instead of just missing a dedicated
        # attribute (unlike the genuinely-unmodelable Atomic case below).
        # Order matches the "const volatile" spelling convention already
        # used by the DWARF backend's own qualifier stripping
        # (dwarf_snapshot._strip_type_decorators).
        #
        # Deliberately NOT `restrict` here (Codex review, PR #582):
        # unlike const/volatile — which are real signature-level
        # qualifiers on a pointee position and participate in mangling —
        # `restrict` has zero ABI/mangling effect and is already tracked
        # as its own compatible-classified fact (Param.is_restrict /
        # PARAM_RESTRICT_CHANGED, populated in _parse_function_params
        # via resolve_cv_restrict below). Folding it into the generic
        # type-name spelling would make a restrict-only parameter change
        # look like an ordinary type mismatch and misfire the BREAKING
        # ``FUNC_PARAMS_CHANGED`` generic-type-diff path instead of the
        # dedicated compatible one.
        quals = [
            q
            for q, attr in (("const", "const"), ("volatile", "volatile"))
            if el.get(attr) == "1"
        ]
        if not quals:
            return base
        qual_str = " ".join(quals)
        # A CvQualifiedType directly wrapping a Pointer/Reference type
        # qualifies the pointer/reference VALUE itself (`int *
        # volatile`), not what it points to (`volatile int *`) — two
        # genuinely different declarations that a plain prefix always
        # collapsed to the identical spelling (G28 "known, deferred
        # limitation": confirmed via CodeRabbit review, PR #582). Render
        # the value-qualifier as a suffix instead, matching the "T *
        # const" convention cv_qualifiers_only_differ/
        # canonicalize_type_name already treat as canonical for this
        # case. A pointee-position qualifier (`const int *` —
        # PointerType wrapping CvQualifiedType) is unaffected: this
        # branch never sees it, since it fires from the CvQualifiedType
        # side, not the PointerType side. Deliberately NOT extended
        # through Typedef/ElaboratedType aliasing — see
        # cv_qualifies_pointer_value's docstring (Codex review): the
        # clang backend takes clang's own `qualType` spelling verbatim,
        # which does not relocate a qualifier through an alias either,
        # so doing so here would newly diverge from clang on that case.
        if cv_qualifies_pointer_value(ctx, inner_id):
            return f"{base} {qual_str}"
        return f"{qual_str} {base}"
    if tag == "ElaboratedType":
        # castxml wraps an elaborated-type-specifier (`struct Foo`, `union
        # Foo`, `enum Foo` used directly rather than via a typedef) in an
        # ElaboratedType node with no `name` attribute of its own — resolve
        # through to the real underlying type instead of falling through to
        # the `tag` fallback below (which would literally return
        # "ElaboratedType").
        return type_name(ctx, el.get("type", ""), depth + 1)
    if tag in ("Struct", "Class", "Union"):
        # See strip_anonymous_type_location's docstring.
        return _strip_anonymous_type_location(el.get("name", "?"))
    if tag == "Typedef":
        return el.get("name", "?")
    if tag == "ArrayType":
        max_ = el.get("max", "")
        base = type_name(ctx, el.get("type", ""), depth + 1)
        return f"{base}[{max_}]" if max_ else f"{base}[]"
    if tag == "AtomicType" or (
        tag == "Unimplemented" and el.get("type_class") == "Atomic"
    ):
        # CastXML emits either AtomicType or legacy Unimplemented/Atomic.
        # Preserve a wrapped type when present; retain a bare fallback.
        inner_id = el.get("type", "")
        return (
            f"_Atomic({type_name(ctx, inner_id, depth + 1)})" if inner_id else "_Atomic"
        )
    return el.get("name", tag)


def cv_qualifies_pointer_value(ctx: CastxmlParserContext, type_id: str) -> bool:
    """True if a ``CvQualifiedType`` wrapping *type_id* qualifies a
    pointer/reference VALUE rather than pointee data.

    Deliberately does NOT follow ``Typedef``/``ElaboratedType`` aliasing
    (Codex review): the clang backend's type spelling is clang's own
    ``qualType`` pretty-print, taken verbatim rather than re-derived —
    and clang's printer does not "see through" a typedef to relocate a
    qualifier after an implicit, textually-absent ``*`` either. For
    ``typedef int *IntPtr; volatile IntPtr x;``, clang spells it
    ``"volatile IntPtr"`` (prefix), not ``"IntPtr volatile"``. Following
    the typedef here to detect the aliased pointer and render a suffix
    would make castxml diverge from clang specifically on this case,
    even though both agreed (by prefixing) before this qualifier-suffix
    fix existed. Since the alias name itself carries no visible ``*``/
    ``&`` to move a qualifier around, there is no real prefix-vs-suffix
    ambiguity to resolve for it anyway (unlike a directly-spelled
    pointer) — only a DIRECT wrap is unambiguous and worth fixing.
    """
    el = ctx.resolve(type_id)
    if el is None:
        return False
    return el.tag in ("PointerType", "ReferenceType", "RValueReferenceType")


def type_alignment_bits(
    ctx: CastxmlParserContext, id_: str, depth: int = 0
) -> int | None:
    """Natural (computed) alignment in bits for a type id, if castxml exposes it.

    Unlike a Variable's own explicit ``align`` attribute (see
    ``parse_variables``), this walks through cv-qualifiers, typedefs,
    elaborated types, and arrays to the nearest type node with ``align``.
    CastXML populates it with the compiler's computed alignment, as trusted
    by ``_build_record_type`` for records. ``ArrayType`` carries no ``align``/``size``
    of its own (confirmed empirically: an array's alignment is always its
    element type's) — recursing into its ``type`` is required, not just
    an optimization, or every exported array global would silently fall
    back to the same address-derived false-positive risk this method
    exists to close for scalars. Used as declared-alignment corroboration
    evidence for a plain variable with no explicit override, so
    ``_check_object_alignment_reduced`` isn't left with two ``None``s
    (and therefore no corroboration at all) for the overwhelming majority
    of exported globals that never carry an explicit alignment
    attribute.
    """
    if depth > 10 or not id_:
        return None
    el = ctx.resolve(id_)
    if el is None:
        return None
    align = _optional_int_attr(el, "align")
    if align is not None:
        return align
    if el.tag in ("CvQualifiedType", "Typedef", "ElaboratedType", "ArrayType"):
        return type_alignment_bits(ctx, el.get("type", ""), depth + 1)
    return None


def _optional_int_attr(el: Any, attr: str) -> int | None:
    raw = el.get(attr)
    return int(raw) if raw and raw.isdigit() else None


def resolve_cv_restrict(
    ctx: CastxmlParserContext, id_: str, depth: int = 0
) -> tuple[bool, bool, bool]:
    """Whether *id_*'s own (top-level) qualification is const/volatile/restrict.

    Walks the real XML type chain rather than pattern-matching the
    rendered ``type_name`` spelling: a field or parameter declared
    through a ``Typedef`` whose target is itself cv-qualified (``typedef
    const int T; struct S { T x; };``) renders as the bare alias name
    ("T"), so a regex over the spelling can never see the qualifier
    behind it (Codex review, PR #582). ``ElaboratedType`` is followed for
    the same reason ``type_name`` follows it. A further ``CvQualifiedType``
    or ``Typedef`` reached *through* one already-seen ``CvQualifiedType``
    combines in (rare, but e.g. two typedefs each adding one qualifier);
    any other tag (``PointerType`` chief among them) stops the walk so a
    *pointee*'s qualification is never attributed to the pointer/field
    itself — ``const int *`` is a non-const pointer to const int, not a
    const pointer.
    """
    if depth > 20 or not id_:
        return (False, False, False)
    el = ctx.resolve(id_)
    if el is None:
        return (False, False, False)
    if el.tag == "CvQualifiedType":
        const = el.get("const") == "1"
        volatile = el.get("volatile") == "1"
        restrict = el.get("restrict") == "1"
        inner_const, inner_volatile, inner_restrict = resolve_cv_restrict(
            ctx, el.get("type", ""), depth + 1
        )
        return (
            const or inner_const,
            volatile or inner_volatile,
            restrict or inner_restrict,
        )
    if el.tag in ("Typedef", "ElaboratedType"):
        return resolve_cv_restrict(ctx, el.get("type", ""), depth + 1)
    return (False, False, False)


def is_global_scope(ctx: CastxmlParserContext, el: Any) -> bool:
    """True if *el*'s immediate lexical context is the root ``::``
    namespace — i.e. not nested in any namespace or class.

    Every function-like element carries a ``context`` id; the file-level
    root ``Namespace`` element is the one with no ``context`` of its own
    (``name="::"``). A missing/unresolvable ``context`` is treated as
    global too (conservative default matching this function's callers,
    which only need to positively rule out namespace/class nesting).
    """
    ctx_id = el.get("context", "")
    if not ctx_id:
        return True
    context_el = ctx.resolve(ctx_id)
    if context_el is None:
        return True
    return context_el.tag == "Namespace" and not context_el.get("context")


def qualified_type_name(
    ctx: CastxmlParserContext, el: Any, leaf_name: str | None = None
) -> str | None:
    """Namespace/enclosing-class-qualified name for a Struct/Class/Union
    element, or ``None`` if already at global scope (cycle/depth cap hit).
    Walks castxml's ``context`` chain, prepending each ancestor's name,
    stopping at the root ``Namespace``. Used only where a real namespace
    path is required (internal-leak detection, SYCL-queue param
    matching); ``RecordType.name`` stays bare (see model.py).
    """
    segments: list[str] = []
    seen_ids: set[str] = set()
    cur = el
    for _ in range(16):
        ctx_id = cur.get("context", "")
        if not ctx_id or ctx_id in seen_ids:
            break
        seen_ids.add(ctx_id)
        parent = ctx.resolve(ctx_id)
        if parent is None:
            break
        if parent.tag == "Namespace":
            pname = parent.get("name", "")
            if pname and pname != "::":
                segments.append(pname)
            cur = parent
            continue
        if parent.tag in ("Struct", "Class", "Union"):
            pname = _strip_anonymous_type_location(parent.get("name", ""))
            if pname:
                segments.append(pname)
            cur = parent
            continue
        break
    leaf = _strip_anonymous_type_location(
        leaf_name if leaf_name is not None else el.get("name", "")
    )
    if not segments or not leaf:
        return None
    segments.reverse()
    return "::".join([*segments, leaf])


def pointer_depth(ctx: CastxmlParserContext, id_: str, depth: int = 0) -> int:
    """Count pointer nesting depth: T=0, T*=1, T**=2, etc."""
    # Memoized by id alone, same rationale/safety as type_name above.
    cached = ctx.pointer_depth_cache.get(id_)
    if cached is not None:
        return cached
    result = pointer_depth_uncached(ctx, id_, depth)
    if depth <= 10:
        ctx.pointer_depth_cache[id_] = result
    return result


def pointer_depth_uncached(ctx: CastxmlParserContext, id_: str, depth: int = 0) -> int:
    if depth > 10:
        return 0
    el = ctx.resolve(id_)
    if el is None:
        return 0
    if el.tag == "PointerType":
        return 1 + pointer_depth(ctx, el.get("type", ""), depth + 1)
    if el.tag in ("CvQualifiedType", "Typedef"):
        return pointer_depth(ctx, el.get("type", ""), depth + 1)
    return 0


def underlying_type_name(ctx: CastxmlParserContext, id_: str, depth: int = 0) -> str:
    """Follow typedef chains to the concrete base type name.

    Shared by enum-underlying-type resolution (``enums.parse_enums``) and
    typedef resolution (``dumper_castxml_typedefs.parse_typedefs``) alike —
    not entity-specific, which is why it lives here rather than in
    ``enums.py``.
    """
    if depth > 20:
        return "?"
    el = ctx.resolve(id_)
    if el is None:
        return "?"
    if el.tag == "Typedef":
        return underlying_type_name(ctx, el.get("type", ""), depth + 1)
    return type_name(ctx, id_)

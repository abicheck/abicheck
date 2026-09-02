# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Producer-specific type-spelling ARTIFACT recognition for
``extract.semantic_normalizer`` (ADR-063 Phase 6).

Split out of ``semantic_normalizer.py`` (its only caller) purely to keep
that file under the AI-readiness gate's 800-line production maximum for a
new file -- this module's own contents are otherwise that module's, not a
separate design decision, the identical reason ``model.declarator_
qualifiers.py`` was split out of ``model.signature_normalization.py`` one
round earlier (that module's own docstring). Every function here answers
one question: is this raw type-spelling text a genuine, comparable
canonical spelling, or one of a small, named set of producer-specific
artifacts (an unresolved-type sentinel, a structural fingerprint, an
opaque fallback tag) that must not be published as ``Fact.present(...)``?

Leaf module: imports only stdlib ``re``, per ADR-061 D10's leaf-module
contract for a module split out purely for size, not for a new dependency
direction.
"""

from __future__ import annotations

import re

__all__ = [
    "has_unresolved_component",
    "is_castxml_opaque_function_type",
    "CLANG_EXPR_FINGERPRINT_RE",
]

#: Both header-AST backends use this literal as their type-resolution
#: placeholder when a type couldn't be followed -- a typedef's underlying
#: type, a function's return/parameter type, or a variable's own type
#: (``dumper_castxml.py``/``dumper_castxml_typedefs.py``/``dumper_clang.py``,
#: verified directly) -- never a real, structurally-fixed type spelling. See
#: its use in :func:`has_unresolved_component` below.
_UNRESOLVED_TYPE_SENTINEL = "?"

#: castxml's own literal wrapper prefix for an ``_Atomic`` type
#: (``extract/headers/castxml/type_resolution.py``'s ``AtomicType`` branch)
#: -- see :func:`has_unresolved_component`'s own docstring for why this
#: is treated as transparent rather than a real, depth-increasing paren.
_ATOMIC_WRAPPER_PREFIX = "_Atomic("

#: ``dumper_clang_expr._expr_fingerprint``'s own literal prefix
#: (``"expr:" + sha256(...)[:16]``) -- the value it stamps onto a
#: constant's compound initializer (anything beyond a lone literal) is a
#: build-stable STRUCTURAL fingerprint, not a spelling of the source text,
#: and that module's own docstring is explicit that "cross-backend constant
#: *values* are not expected to match" for exactly this case (castxml's
#: `init` always carries the raw source-text initializer, never a
#: fingerprint). Used in ``normalize_header_ast``'s constants loop
#: (Codex review, sixth round, fresh evidence) to recognize this one
#: producer-specific encoding structurally -- by the value's own shape, not
#: by branching on ``producer == "clang"`` -- and mark it
#: ``Fact.unsupported()`` rather than ``Fact.present(...)``, the same
#: "state genuinely incomparable evidence honestly" discipline ADR-063
#: Phase 0 established: publishing the fingerprint as a confirmed spelling
#: would make `merge_semantic_ir` report a spurious conflict against
#: castxml's real initializer text for every unchanged compound constant.
#:
#: Matches the FULL fingerprint shape -- ``"expr:"`` plus exactly 16
#: lowercase hex digits -- not merely the ``"expr:"`` prefix (Codex review,
#: tenth round, fresh evidence: a plain prefix test also matches castxml's
#: raw, verbatim source-text initializer whenever it happens to spell a
#: qualified name whose next component is literally ``expr``, e.g.
#: ``"expr::NAMESPACE_VALUE"`` for an expression-template library's
#: ``expr::`` namespace -- no fingerprint involved at all, and misreading it
#: as one would silently discard real castxml constant evidence). Mirrors
#: ``diff_default_value_reliability._is_expr_fingerprint``'s identical
#: shape check, duplicated rather than imported since that module is a
#: `compare`-layer detector-reliability leaf, not one `extract/` depends on
#: for anything else -- that module already fixed this identical
#: prefix-vs-full-shape mistake once (PR #720); this is the second,
#: independent site it applies to.
CLANG_EXPR_FINGERPRINT_RE = re.compile(r"^expr:[0-9a-f]{16}$")

#: castxml's own opaque-tag fallback (``extract/headers/castxml/
#: type_resolution.py``'s ``type_name_uncached``, final ``return
#: el.get("name", tag)`` line) for an anonymous ``FunctionType`` node --
#: castxml's resolver has no dedicated branch for one (unlike
#: ``Struct``/``Class``/``Union``/``Typedef``/... above it), so a direct
#: function-pointer parameter/variable/return type resolves to the literal
#: tag string ``"FunctionType"`` wrapped in whatever pointer/reference sigil
#: surrounds it (``"FunctionType*"``), never a real declarator spelling like
#: clang's own ``"void (*)(int)"``. The identical shape is already a named,
#: worked-around castxml limitation elsewhere in this codebase --
#: ``idioms._is_callback_type`` checks for this exact substring for the same
#: reason. Used by :func:`is_castxml_opaque_function_type` (Codex review,
#: ninth round, fresh evidence) to mark such a spelling ``Fact.
#: unsupported()`` rather than ``Fact.present(...)``: castxml did not fail
#: to resolve the type (this is not `_UNRESOLVED_TYPE_SENTINEL`'s "?" at
#: all -- the resolver ran and produced a real, if useless, answer), it
#: structurally cannot render a comparable spelling for it -- publishing the
#: opaque tag as canonical made `merge_semantic_ir` report a spurious
#: conflict against clang's real, useful spelling for an unchanged callback
#: parameter, the same class of "state genuinely incomparable evidence
#: honestly" fix as the clang expr-fingerprint constant case above.
#:
#: **Anchored to the WHOLE (cv/sigil-stripped) string, not a bare substring
#: test (Codex review, eleventh round, fresh evidence).** A naive
#: ``"FunctionType" in raw_type`` also matches a real, legitimately-named
#: type like ``"MyFunctionTypeWrapper*"`` -- castxml's ``Struct``/``Class``
#: branch resolves such a name correctly and verbatim, no opacity involved
#: at all, so substring containment anywhere in the string wrongly rejects
#: a perfectly good spelling. The opaque fallback's own contribution is
#: always exactly the bare tag text with nothing else glued directly onto
#: it -- every character around it comes from an OUTER wrapping node
#: (a pointer/reference sigil, a cv keyword), never concatenated onto the
#: tag itself -- so anchoring requires the entire string to be that exact
#: token, cv-prefixed and/or sigil-suffixed, and nothing else. This does
#: NOT (and structurally cannot, from text alone) distinguish the opaque
#: fallback from a real type that happens to be named EXACTLY
#: ``"FunctionType"`` with no extra characters (``struct FunctionType``)
#: -- the same accepted residual ``idioms._is_callback_type`` already
#: carries for its own, coarser substring check; resolving that fully
#: needs real structural evidence from the parser (which XML tag actually
#: produced the string), not a normalizer-only text fix.
#:
#: **A cv-qualifier can also appear AFTER a pointer/reference sigil, not
#: only before the tag (Codex review, thirteenth round, fresh evidence).**
#: ``type_resolution.py``'s own ``CvQualifiedType`` branch renders a
#: ``CvQualifiedType`` directly wrapping a ``PointerType``/``ReferenceType``
#: (i.e. a cv-qualified POINTER VALUE, not a cv-qualified pointee) as a
#: SUFFIX -- ``f"{base} {qual_str}"`` -- matching this codebase's own
#: "T * const" convention elsewhere (see that function's own docstring), so
#: a const function-pointer resolves to ``"FunctionType* const"``, not
#: ``"const FunctionType*"``. The regex allows a cv-keyword run after
#: EVERY sigil (not just the last), since castxml's recursive wrapping can
#: in principle nest more than one (``"FunctionType** const volatile"``).
_CASTXML_OPAQUE_FUNCTION_TYPE_RE = re.compile(
    r"^(?:(?:const|volatile)\s+)*FunctionType"
    r"(?:\s*(?:[*&]|\[\])(?:\s+(?:const|volatile))*)*$"
)


def is_castxml_opaque_function_type(raw_type: str, producer: str) -> bool:
    """Whether *raw_type* is castxml's own opaque ``FunctionType`` fallback
    spelling -- see :data:`_CASTXML_OPAQUE_FUNCTION_TYPE_RE`'s own comment.

    Gated on *producer* (Codex review, eleventh round, fresh evidence: an
    earlier revision fired for clang too) -- clang never emits this literal
    tag text at all, so a clang-produced type spelling matching this shape
    can only be a real, legitimately-named type, never this artifact.
    """
    return producer == "castxml" and bool(
        _CASTXML_OPAQUE_FUNCTION_TYPE_RE.match(raw_type)
    )


def has_unresolved_component(raw_type: str) -> bool:
    """Whether *raw_type* embeds castxml's unresolved-type sentinel
    anywhere, not only as the WHOLE string (Codex review, second round,
    fresh evidence).

    castxml's own type resolver (``extract/headers/castxml/type_resolution.
    py``'s ``type_name_uncached``) composes an unresolved nested type into
    the ENCLOSING spelling rather than only ever returning the bare
    ``"?"`` itself — a pointer/reference/array wrapping an unresolvable
    pointee renders as ``"?*"``/``"?&"``/``"?[]"``, and a cv-qualified one
    as ``"const ?"`` -- so an exact-equality check (correct for the
    typedef branch's own ``underlying`` value, which is always the
    OUTERMOST ``type_name()`` call's result with nothing further wrapped
    around it) misses every one of these composite shapes for a function/
    parameter/variable type.

    **A plain substring test is NOT safe (Codex review, third round, fresh
    evidence): a real, fully-resolved type spelling CAN legally contain a
    literal ``"?"`` character** -- clang emits one verbatim for a
    dependent, unevaluated ternary expression inside a `decltype(...)` (a
    non-type template argument/parameter's own spelling, e.g.
    ``"S<decltype(flag ? A{} : B{})>"``). Distinguishing the two requires
    exactly the discriminator that makes this safe again: every
    ``"?"`` this resolver's own sentinel ever produces sits at NESTING
    DEPTH ZERO in the string -- the recursive wrapping above only ever
    prepends/appends a bare pointer/reference sigil, array brackets, or a
    cv keyword directly beside it, never inside a `(...)`/`<...>`
    grouping -- while a ternary's ``"?"`` is, by C++ grammar, only ever
    reachable inside an expression context, which for a *type* spelling
    means inside a `decltype(...)`'s parens or a template argument list's
    angle brackets (both already open by the time such a ``"?"`` is
    reached). So this function walks *raw_type* tracking depth over
    ``()``/``[]``/``<>``, and reports unresolved only for a ``"?"`` found
    at depth 0 -- never one already inside a bracketed grouping.

    **One wrapper is a deliberate, named exception (Codex review, fourth
    round, fresh evidence): castxml's own ``_Atomic(...)`` composition.**
    ``type_name_uncached``'s ``AtomicType`` branch renders an unresolved
    wrapped type as the literal ``"_Atomic(?)"`` -- genuine sentinel
    output, using a REAL parenthesis pair as part of the resolver's own
    grammar, not an expression context a real, resolved ``"?"`` could ever
    be found inside instead. Depth-tracking alone would treat that
    ``"("`` exactly like a `decltype(...)`'s, hiding the sentinel at depth
    1 and wrongly reporting the composite as resolved. ``"_Atomic("`` is
    therefore recognized as a transparent token -- skipped without
    incrementing depth -- so a sentinel directly inside it is still caught
    at its effective depth 0, the same treatment a bare `"?"` already gets.
    ``_Atomic(...)`` is also real, valid C11 syntax for an otherwise
    fully-resolved type (``"_Atomic(int)"``), which this special-casing
    does not disturb: only a literal ``"?"`` inside it is ever flagged.

    **Tracks a bracket-kind-aware STACK, not a flat depth counter (Codex
    review, seventh round, fresh evidence): a real right-shift operator
    inside a parenthesized non-type template argument is not two template
    closers.** For a resolved dependent type like ``"S<(N >> 1 ? 1 :
    2)>"``, a flat counter treats each ``>`` in the ``>>`` independently --
    decrementing depth twice for what is actually one shift-operator token
    sitting inside the `(...)` grouping, not two nested `<...>` closes --
    which drops the running depth to zero WHILE STILL INSIDE the
    parenthesized expression and misreads the ternary's own ``"?"`` as the
    sentinel, at real depth > 0. A ``">"`` legitimately closes a template
    level only when the innermost still-open bracket is itself a ``"<"``
    (``vector<vector<int>>``'s own ``>>`` closes two, since each one's
    innermost open bracket at the time it's processed IS a ``"<"``); when
    the innermost open bracket is a ``"("``/``"["`` instead, a ``">"`` is a
    real, resolved operator character belonging to that expression --
    comparison or shift -- and must not be popped as a bracket at all. A
    ``")"``/``"]"`` still pops unconditionally (matching this function's
    existing "never raise, degrade gracefully on malformed/adversarial
    input" discipline for every other close), and every genuinely
    ambiguous ``"<"``/``">"`` pair (a real less-than/greater-than
    comparison, not a template open/close) is an accepted, PRE-EXISTING
    residual this fix does not attempt to solve when it occurs OUTSIDE any
    ``(...)``/``[...]`` grouping -- doing so needs real expression parsing.
    **A ``"<"`` occurring INSIDE an already-open ``(...)``/``[...]`` IS
    handled, symmetrically with the ``">"`` rule above:** it only pushes a
    new bracket level when the innermost still-open entry is NOT itself a
    ``"("``/``"["`` -- i.e. only when genuinely at top level or already
    inside a real ``<...>``. A real comparison ``<`` sitting inside an
    already-open paren/bracket expression (``"S<(N < 0)>"``'s inner ``<``)
    is left untouched the same way its own closing ``>`` would be, so the
    two do not spuriously push a bracket level that a later, real ``)``
    would then incorrectly pop instead of the paren it actually closes.
    """
    stack: list[str] = []
    i = 0
    n = len(raw_type)
    while i < n:
        if raw_type.startswith(_ATOMIC_WRAPPER_PREFIX, i):
            i += len(_ATOMIC_WRAPPER_PREFIX)
            continue
        ch = raw_type[i]
        if ch in "([":
            stack.append(ch)
        elif ch in ")]":
            if stack:
                stack.pop()
        elif ch == "<":
            if not stack or stack[-1] not in "([":
                stack.append(ch)
            # else: a real comparison operator character sitting inside a
            # paren/bracket expression context, not a template opener --
            # leave the stack untouched (symmetric with the ">" rule below).
        elif ch == ">":
            if stack and stack[-1] == "<":
                stack.pop()
            # else: a real comparison/shift-operator character sitting
            # inside a paren/bracket expression context, not a template
            # closer -- leave the stack untouched.
        elif ch == _UNRESOLVED_TYPE_SENTINEL and not stack:
            return True
        i += 1
    return False

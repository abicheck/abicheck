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

"""Template / overload-set pattern detectors.

Generic detectors for failure modes common to template-heavy libraries
(oneDPL, Boost, the C++ standard library implementation surface, …):

* ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` — function-template
  analogue of PR #238's type leak detector. An internal helper template
  signature changed and its instantiations participate in user symbol
  mangling.

* ``CPO_KIND_CHANGED`` — a public name flipped between function and
  function-object (variable of unspecified class type). Call syntax
  preserved; ``decltype`` and trait specializations broken.

* ``OVERLOAD_SET_REROUTED`` — overload set membership shifted enough
  that existing call sites resolve to a different overload.

* ``MANDATORY_TEMPLATE_PARAM_ADDED`` — heuristic for templates whose
  effective parameter count grew (defaulted/deduced → mandatory).

* ``UNSPECIFIED_RETURN_NOW_NAMED`` — a factory function's return-type
  spelling flipped between an unspecified placeholder (``auto``,
  ``__lambda``, ``(unnamed class)``) and a stable named type.

These detectors are deliberately *signature-based* and do not require
running a compiler — they consume the standard ``AbiSnapshot`` produced
by either the header dumper or the binary dumper.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind, ReachabilityState
from .checker_types import Change
from .diff_helpers import make_change

if TYPE_CHECKING:
    from .model import AbiSnapshot, Function

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _strip_template_args(name: str) -> str:
    """Drop everything from the first top-level ``<`` to the matching ``>``."""
    if "<" not in name:
        return name
    depth = 0
    out: list[str] = []
    for ch in name:
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def _is_internal_segment(name: str, internal_segments: tuple[str, ...]) -> bool:
    """Return True if any ``::`` segment of ``name`` (template args
    stripped) matches one of ``internal_segments`` exactly."""
    bare = _strip_template_args(name)
    return any(s in bare.split("::") for s in internal_segments)


_INTERNAL_TEMPLATE_NAMESPACES: tuple[str, ...] = (
    "detail",
    "impl",
    "internal",
    "__detail",
    "_impl",
    "__internal",
)


# A Function whose demangled name contains ``<...>`` is (in Itanium /
# MSVC C++ ABI terms) an instantiated template specialisation. The
# regex requires ``<`` to be followed by a non-bracket character so that
# ``operator<`` and ``operator<<`` don't get false-flagged as
# instantiations (their ``<`` is followed by space, ``=``, or another
# ``<``, none of which match ``[^<>]``).
_TEMPLATE_ARGS_RE = re.compile(r"<[^<>]")

# Matches a genuine `operator` function-name token (`operator+`, `operator
# new`, `operator T`, `::operator()`), not an incidental substring occurrence
# inside an unrelated identifier such as a namespace named `cooperator`. The
# lookaround pair requires a non-identifier character (or start/end of
# string) on both sides, so `operator` must stand as its own token.
_OPERATOR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])operator(?![A-Za-z0-9_])")

# Matches a `decltype` token immediately at the end of a prefix, with no
# separating whitespace before the "(" that follows it -- the one dependent-
# expression shape that reaches `decltype`'s own "(" glued directly to the
# keyword (`decltype(auto)`) rather than with a space (`decltype (expr)`,
# every other shape `_strip_param_signature` handles).
_DECLTYPE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])decltype$")


def _looks_like_template_instantiation(name: str) -> bool:
    """A declared C++ name is a template instantiation iff it contains a
    top-level ``<`` followed by a non-bracket character."""
    return bool(name) and bool(_TEMPLATE_ARGS_RE.search(name))


def _qualified_function_name(name: str, mangled: str) -> str:
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        from .demangle import demangle_batch
        return demangle_batch([mangled]).get(mangled, name)
    return name


def _pointer_declarator_star_index(qualified: str, paren_index: int) -> int:
    """Return the index of the ``*``/``&`` that opens a function-pointer or
    member-function-pointer declarator at the ``(`` found at *paren_index*
    — the real call is *nested inside* this group, immediately after that
    ``*``/``&`` (optionally preceded by a scope like ``Class::`` for a
    member-pointer wrapper) — or -1 if this ``(`` does not open one.

    Distinguishing signal: the group reaches a ``*``/``&`` and *then* a
    further ``(`` before the next ``,``/``)`` at this nesting level. An
    ordinary parameter list can also contain a bare ``*``/``&`` (a
    pointer/reference parameter type, e.g. ``lib::sort(int*, int*)``), but
    never followed by another ``(`` before that parameter ends.

    Returns the *first* such ``*``/``&``'s index rather than merely a bool
    so a caller can later recover the wrapper's own scope prefix without
    re-searching — a plain boolean plus a later whole-prefix ``rindex("*")``
    picks the *last* ``*`` in the prefix instead, which is wrong whenever
    the wrapped call's own name carries a pointer template argument (e.g.
    ``int (*ns::bar<int*>(int*))()`` — the trailing ``int*>`` template
    argument's ``*`` sits after the wrapper's own, and is the rightmost one
    in the eventual prefix) (Codex review).

    Tracks ``<``/``>`` nesting depth so a comma *inside* a template argument
    list is never mistaken for the parameter-list comma that would end the
    scan — a member-pointer wrapper's owning class can itself be a template
    with multiple arguments (``int (ns::C<int, double>::*ns::bar<int>(int))()``),
    and that comma appears before the wrapper's own ``*`` is ever reached
    (Codex review, fresh evidence: the un-tracked version bailed out on it
    and corrupted the eventual leaf).

    Only whitespace, identifier/scope characters (``Ident::Ident::``), and a
    balanced template-argument list may appear *before* the star — anything
    else means this ``(`` was never a scope-qualified pointer/member-pointer
    declarator to begin with, so bail out immediately rather than let some
    later, unrelated ``*``/``&`` be mistaken for the declarator's own. A
    dependent ``decltype`` expression can itself contain a ``*`` that is
    ordinary multiplication, not a declarator (``decltype ({parm#1}*(g()))
    ns::sort<int>(int)`` — real GCC output): the un-gated version accepted
    that arithmetic ``*`` as if it opened a wrapper, matched the following
    ``(`` of the unrelated nested call ``g()`` as the wrapper's real call,
    and returned an empty slice between the adjacent ``*(`` — collapsing the
    whole identity to ``""`` (Codex review, fresh evidence).
    """
    star_index = -1
    depth = 0
    for offset, ch in enumerate(qualified[paren_index + 1:]):
        idx = paren_index + 1 + offset
        if ch == "<":
            depth += 1
            continue
        if ch == ">":
            depth = max(0, depth - 1)
            continue
        if star_index == -1:
            if depth > 0:
                # Inside the owning class's own template-argument list --
                # every character here (including "*"/"&"/",") belongs to
                # those arguments, never to the wrapper's own declarator.
                # A pointer template argument (``ns::C<int*, double>::*...``)
                # would otherwise have its "*" recorded as the declarator
                # star, corrupting the eventual leaf (CodeRabbit review).
                continue
            if ch in "*&":
                # An rvalue-reference declarator is "&&", not a single
                # "&" -- when the second "&" immediately follows, record
                # *its* index instead of the first, so the later slice
                # (which starts right after the recorded index) drops the
                # whole token rather than leaving a stray "&" glued to the
                # front of the recovered name (Codex review, fresh
                # evidence: "int (&&ns::sort<int>(int))()" left "&ns::sort
                # <int>" before this fix).
                if ch == "&" and idx + 1 < len(qualified) and qualified[idx + 1] == "&":
                    star_index = idx + 1
                else:
                    star_index = idx
            elif ch.isalnum() or ch in "_: \t":
                continue
            else:
                return -1
        elif ch == "(":
            return star_index
        elif ch in ",)" and depth == 0:
            return -1
    return -1


def _matching_close_paren(qualified: str, open_index: int) -> int:
    """Return the index of the ``)`` matching the ``(`` at *open_index*
    (depth-balanced across any nested parens), or -1 if unbalanced."""
    depth = 0
    for i in range(open_index, len(qualified)):
        if qualified[i] == "(":
            depth += 1
        elif qualified[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _strip_param_signature(qualified: str) -> str:
    """Strip a function's parameter-list signature (from its own top-level
    ``(``), preserving any ``::`` namespace/class qualification.

    Used to compare a function's demangled name (``lib::sort(int*, int*)``)
    against a variable's demangled name (``lib::sort``) so the two sides of
    a customization point object's function<->variable transition can be
    correlated *by their full qualified name*, not just a bare leaf — two
    unrelated namespaces reusing the same leaf identifier (``ns1::sort`` vs
    ``ns2::sort``) must never collapse together (see :func:`detect_cpo_kind_changed`).

    Two shapes make "the first top-level ``(`` in the string" the wrong
    thing to stop at (both Codex review, all verified with real c++filt
    output shapes — the second subsumes several distinct return-type forms
    found across successive review rounds):

    * A call operator's own name is spelled ``operator()`` — those
      parentheses are the identifier, not an argument list. Naively
      stopping at the first ``(`` corrupted
      ``ns::C::operator()(ns::T const&) const`` down to just
      ``ns::C::operator``. Skipping it needs an actual ``operator`` *token*
      immediately before an *empty* ``()`` pair, not merely a name ending
      in the substring ``"operator"`` — ``ns::cooperator(int)`` is a plain
      function, not a disguised call operator, and stripping its params
      must behave normally.
    * A ``(`` that is any part of the *return type* rather than the real
      parameter list. The general signal: a real call's own ``(`` is
      always glued directly to the identifier/template-close that names
      it, with no separating whitespace in demangled output, while every
      return-type shape found so far has at least one space before its
      first ``(`` — so a whitespace-preceded ``(`` is never the real
      parameter list. What to do next differs by shape, distinguished by
      :func:`_pointer_declarator_star_index`:

      - A function-pointer/member-function-pointer declarator
        (``int (*ns::f<int>(int))()`` / ``int (ns::C::*ns::f<int>(int))()``)
        nests the real call *inside* this group, right after the
        ``*``/``&`` — so only this one ``(`` character is skipped, letting
        the search land on that nested call.
      - Anything else — a dependent ``decltype``/similar expression
        (``decltype ({parm#1}+{parm#1}) ns::sort<int>(int)``, or one whose
        own content has further nested, unrelated parens like a call —
        ``decltype ((g())?{parm#1}:{parm#1}) f<int>(int)``) has no real
        call nested inside it at all; the real call, if any, comes *after*
        this group's own matching close paren. The whole balanced group is
        skipped via :func:`_matching_close_paren` rather than just its
        opening character, so an unrelated nested ``(`` inside the
        expression (``g()`` above) is never mistaken for the real one.

      Neither branch misfires on an ordinary pointer/reference *parameter*
      (``lib::sort(int*, int*)``, whose only ``(`` is glued to ``sort`` —
      never reached by this whitespace-gated logic at all).

    Skipping a pointer-declarator wrapper leaves its own opening ``(`` and
    ``*``/``Class::*`` text sitting in the returned prefix (e.g.
    ``"int (ns::C::*ns::sort"`` for ``int (ns::C::*ns::sort<int>(int))()``)
    — harmless for a caller that only takes the *last* ``::``-segment
    (``_func_index_items``'s leaf), but wrong for a caller using
    the whole result as a qualified-name *identity*
    (:func:`detect_cpo_kind_changed`, which failed to match a
    pointer-to-member-function CPO transition against this corrupted
    prefix — Codex review). The clean name is recovered by taking the text
    after the wrapper's own ``*``/``&`` — but its *position*, found while
    recognizing the declarator via :func:`_pointer_declarator_star_index`,
    must be tracked and reused rather than re-derived from the final prefix
    with ``rindex("*")``: when the wrapped call's own name carries a
    pointer template argument (``int (*ns::bar<int*>(int*))()``), that
    argument's own ``*`` sits later in the prefix than the wrapper's, so a
    whole-prefix ``rindex`` picks the wrong one and corrupts the name down
    to a lone ``">"`` (Codex review, fresh evidence after this function's
    own prior fix for the pointer-wrapper case).

    **Known, deliberately-unfixed limitation**: a return type that is
    itself a template instantiation whose *own* non-type template argument
    contains a comparison operator (``std::enable_if<(sizeof(int)>1),
    T>::type ns::sort<int>(int)`` — real GCC output) still truncates the
    identity at that argument's own ``(``, since the ``>`` in ``>1)`` is
    lexically indistinguishable from a genuine template-close ``>`` (as in
    the overwhelmingly common ``f<int>(args)`` shape) without actually
    parsing the expression grammar — the same "angle bracket problem" real
    C++ parsers resolve with lookahead/backtracking or semantic analysis,
    out of scope for this lexical scanner (Codex review; verified this
    degrades safely — no crash/hang, just a wrong-but-inert leaf, and that
    no simple heuristic here can distinguish the two ``>`` uses without
    also breaking the ordinary template-function-call case).
    """
    i = qualified.find("(")
    saw_pointer_wrapper = False
    wrapper_star_index = -1
    while i != -1:
        prefix = qualified[:i]
        m = _OPERATOR_TOKEN_RE.search(prefix)
        if m is not None and m.end() == len(prefix) and qualified[i:i + 2] == "()":
            # operator()'s own, empty parentheses are part of the
            # identifier — skip exactly that pair and keep searching for
            # the real parameter-list opener.
            i = qualified.find("(", i + 2)
            continue
        if _DECLTYPE_TOKEN_RE.search(prefix):
            # A "decltype(...)" return type with no space before its own
            # "(" (real GCC/Clang output for e.g. "decltype(auto)" or a
            # conversion operator's "decltype(nullptr)" target) bypasses
            # the whitespace-gated branch below entirely and would
            # otherwise be mistaken for the real parameter list, truncating
            # the identity down to just "decltype" (Codex review, fresh
            # evidence — the fixed-"(auto)"-only check missed every other
            # decltype(...) content, e.g. decltype(nullptr)). Skip the
            # whole balanced group, whatever it contains, same as every
            # other decltype shape.
            close = _matching_close_paren(qualified, i)
            if close == -1:
                return qualified
            i = qualified.find("(", close + 1)
            continue
        if i == 0 or qualified[i - 1].isspace():
            star_index = _pointer_declarator_star_index(qualified, i)
            if star_index != -1:
                saw_pointer_wrapper = True
                wrapper_star_index = star_index
                i = qualified.find("(", i + 1)
            else:
                close = _matching_close_paren(qualified, i)
                if close == -1:
                    return qualified
                # A function-pointer/reference *type* used as a return type
                # (e.g. a conversion operator's target,
                # ``operator int (*)(double)() const``) is spelled as two
                # immediately-adjacent parenthesized groups with no
                # separating whitespace: the bare ``(*)``/``(&)`` marker
                # (no name, since it names a type rather than a declarator)
                # glued directly to the target's own parameter list. Both
                # belong to the return type, not the outer function's own
                # parameter list — skip that one glued group too, or the
                # search would land on ``(double)`` and mistake it for the
                # real parameter list, corrupting the identity to
                # ``"...operator int (*)"`` (Codex review, fresh evidence).
                # Deliberately bounded to a *single* extra group: the
                # conversion operator's own trailing ``()`` is glued the
                # exact same way, and must be left for the outer loop's own
                # classification (its lack of a preceding "operator" token
                # immediately before it means it correctly falls through to
                # `return prefix`, keeping the target-type spelling that
                # distinguishes this operator from a same-named one
                # converting to a different type) rather than being
                # swallowed here too.
                if close + 1 < len(qualified) and qualified[close + 1] == "(":
                    inner_close = _matching_close_paren(qualified, close + 1)
                    if inner_close != -1:
                        close = inner_close
                i = qualified.find("(", close + 1)
            continue
        if saw_pointer_wrapper and wrapper_star_index != -1:
            return qualified[wrapper_star_index + 1:i]
        return prefix
    return qualified


def _strip_leading_return_type(sig: str) -> str:
    """Strip a leaked leading return type from a demangled function
    signature, once template args and the param signature are already gone.

    Itanium demangling includes the return type for a function *template*
    instantiation (needed to disambiguate an overload set that differs only
    by return type) but never for a non-template function — so
    ``lib::sort<int>(int*, int*)`` demangles to ``"int lib::sort<int>(int*,
    int*)"``, and after :func:`_strip_template_args` + :func:`_strip_param_signature`
    reduce that to ``"int lib::sort"``, the leaked ``"int "`` prefix must be
    dropped so the function-template side of a CPO transition still matches
    the variable side's plain ``"lib::sort"`` (case88's function-template
    variant; Codex review).

    By this point no ``<...>``/``(...)`` remain, so taking the text after
    the *last* top-level space still isolates the qualified name even for a
    multi-word return type (``"unsigned long lib::sort"``). Left alone for
    an ``operator`` name (itself spelled with a space, e.g. ``"operator
    new"``) — CPOs are never operators, and blindly splitting there would
    corrupt the operator's own name instead of stripping a return type. The
    check for that is a whole-token match, not a substring one: a namespace
    or class merely spelled with ``operator`` as a substring (e.g.
    ``"lib::cooperator::sort"``) is not an operator overload and must still
    get the leaked-return-type strip, or the function-template-to-CPO
    transition living under it goes undetected (Codex review).

    Callers must only invoke this once :func:`_looks_like_template_instantiation`
    has confirmed the *pre-strip* qualified name was actually a template
    instantiation — a non-template demangling can still contain a top-level
    space for an unrelated reason (an ABI thunk marker like ``"non-virtual
    thunk to lib::sort()"``) and must never be routed through here, or the
    thunk collapses to ``"lib::sort"`` and wrongly collides with an
    unrelated same-named CPO variable (Codex review).
    """
    if " " not in sig or _OPERATOR_TOKEN_RE.search(sig):
        return sig
    return sig.rsplit(" ", 1)[-1]


# ---------------------------------------------------------------------------
# INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API
# ---------------------------------------------------------------------------


def _public_functions(snap: AbiSnapshot) -> list[Function]:
    """Return the subset of public functions in *snap*."""
    from .model import Visibility
    return [f for f in snap.functions if f.visibility == Visibility.PUBLIC]


def _normalize_mach_o_mangled(mangled: str) -> str:
    """Strip a Mach-O direct-clang mangled name's extra platform leading
    underscore (``__Z...`` -> ``_Z...``).

    Mirrors ``diff_cxx_rules._itanium_strip_prefix``'s identical
    de-prefixing for the same quirk (confirmed via ``dumper_clang.py``'s
    own ``_visibility()`` docstring: clang's ``mangledName`` is
    ``"__ZN3lib3addEii"`` on macOS, not the plain Itanium
    ``"_ZN3lib3addEii"``) -- a bare ``mangled.startswith("_Z")`` check
    silently skips every symbol on that platform, which would make
    :func:`_canonical_identity_name` fall back to the very declared-name
    text this module exists to stop trusting (Codex review finding).
    """
    if mangled.startswith("__Z"):
        return mangled[1:]
    return mangled


def _batch_demangle_for_identity(funcs: list[Function]) -> dict[str, str]:
    """Demangle every real mangled name in *funcs* in one batch call, for
    :func:`_canonical_identity_name`'s mangled-preferred lookup below.

    Keyed by the *normalized* mangled string (Mach-O's extra leading
    underscore stripped, see :func:`_normalize_mach_o_mangled`) so a
    lookup by the same normalized form always hits regardless of which
    platform's raw mangled spelling produced it.
    """
    from .demangle import demangle_batch
    normalized = [
        n for f in funcs
        if (n := _normalize_mach_o_mangled(f.mangled)).startswith("_Z")
    ]
    return demangle_batch(normalized) if normalized else {}


def _canonical_identity_name(
    name: str, mangled: str, demangled: dict[str, str],
) -> str:
    """Return the most stable available qualified identity for a function,
    for use where "is this the same declaration on both sides" must be
    judged by *linked-symbol* identity rather than the dumper's own
    qualification of ``Function.name``.

    Prefers a successfully demangled ``mangled`` over trusting ``name``'s
    own qualification whenever a real mangled name is available: two
    dumper backends -- or the same backend across different inputs/
    versions -- can populate ``Function.name`` with inconsistent
    qualification for the *identical* linked symbol (a bare leaf on one
    snapshot side, a fully namespace/template-qualified spelling with its
    own formatting quirks on the other). ``mangled`` is the ground truth
    the linker actually resolves against, so demangling it consistently on
    both snapshot sides yields matching identities even when ``name``
    doesn't -- a confirmed real-world false positive: a still-exported
    template method (verified present on both sides via ``nm``) read as
    "removed and re-added" purely because the header dumper changed how
    much of its own name it qualified between two library versions, not
    because the symbol itself changed.

    Deliberately used only for :func:`detect_internal_template_leaks`'s
    stem/instantiation-set grouping, which exists to decide whether a
    specific instantiation is still linkable -- a binary-identity
    question. Every other detector in this module keys off
    :func:`_qualified_function_name`'s declared-name-first precedence
    instead, because they care about the *source-level* spelling
    (``CPO_KIND_CHANGED`` and friends); switching that precedence there
    too would silently defeat detectors that depend on a declared name
    genuinely diverging from its demangled/underlying name (e.g.
    ``diff_namespaces.detect_std_reexport_removed``'s whole premise is
    that split).

    **Residual gap, deliberately not chased here** (Codex review finding):
    when no demangler is available at all (no ``cxxfilt`` package and no
    ``c++filt`` binary on ``PATH`` -- see ``demangle.py``'s own fallback
    chain), ``demangled`` is empty for every symbol and this falls back to
    ``_qualified_function_name``'s declared-name-first behavior, so the
    original false-positive this function exists to fix can still occur
    in that environment. Closing it would need a structural (no-demangler)
    Itanium scope parser for this module's own qname-reconstruction needs,
    the same shape as ``diff_cxx_rules.itanium_scope_components`` -- out of
    scope for this fix, which targets the reachable case (a real demangler
    present, the common case this codebase already assumes throughout
    ``diff_namespaces.py``/``diff_templates.py``'s own batch-demangle
    helpers).

    **MSVC manglings hit the identical fallback, for a different reason**
    (Codex review, fresh evidence): this codebase has no MSVC demangler at
    all (``demangle.py``'s ``demangle_batch`` only ever recognizes the
    Itanium ``_Z`` prefix), so a Windows/``clang-cl`` snapshot's
    ``?``-prefixed manglings never reach ``demangled`` regardless of
    normalization, and this falls back to the declared name exactly as
    above. Unlike the no-demangler-installed case, there IS a structural
    (no-demangler) MSVC parser in this codebase --
    ``diff_cxx_rules.msvc_scope_components``/``msvc_qualified_name`` --
    but it cannot help *this* detector specifically: its own docstring
    documents that it deliberately rejects any template-shaped component
    (the ``?$...`` marker), returning ``None`` rather than mis-parsing an
    MSVC template-argument encoding it doesn't model -- and
    ``detect_internal_template_leaks`` exists to inspect exactly template
    instantiations (gated on ``_looks_like_template_instantiation``), so
    the one MSVC parser available in this codebase is structurally unable
    to answer the one question this function asks for the input class it
    was built for. A real fix needs an MSVC template-argument-aware
    structural parser, which does not exist here and is a much larger,
    independently-scoped undertaking (matching this codebase's existing,
    repeatedly-documented position that no MSVC family-specific parser
    beyond plain scope recovery exists yet).
    """
    normalized = _normalize_mach_o_mangled(mangled)
    if normalized.startswith("_Z"):
        resolved = demangled.get(normalized)
        if resolved:
            return resolved
    return _qualified_function_name(name, mangled)


def _callable_identity_name(
    name: str, mangled: str, demangled: dict[str, str],
) -> str:
    """:func:`_canonical_identity_name`, with any parameter-list signature
    stripped -- the callable-name portion alone.

    A demangled identity carries its full signature (return type included,
    for a template instantiation); if that raw form is fed straight into
    :func:`_looks_like_template_instantiation`/:func:`_strip_template_args`,
    a plain (non-template) internal function taking a *templated parameter
    type* (``lib::detail::foo(std::vector<int>)``) is misclassified as a
    template instantiation itself, because the check only looks for a
    top-level ``<`` anywhere in the string -- including inside a parameter
    type it never should have inspected (Codex review, fresh evidence: this
    module's own ``test_non_template_internal_funcs_ignored`` invariant
    says such a helper is out of scope, and the unstripped form silently
    violated it once identity started preferring the demangled form
    unconditionally). Stripping the signature first restricts template
    detection to the callable's own name, matching what
    :func:`_qualified_function_name`'s declared-name path already gave for
    free (a header-derived declared name never carries a signature to
    begin with).
    """
    return _strip_param_signature(_canonical_identity_name(name, mangled, demangled))


def _internal_template_stems(
    funcs: list[Function],
    internal_namespaces: tuple[str, ...],
    demangled: dict[str, str],
) -> set[str]:
    """Return template stems that live in one of *internal_namespaces*."""
    out: set[str] = set()
    for f in funcs:
        qname = _callable_identity_name(f.name, f.mangled, demangled)
        if not _looks_like_template_instantiation(qname):
            continue
        stem = _strip_template_args(qname)
        if _is_internal_segment(stem, internal_namespaces):
            out.add(stem)
    return out


def _functions_by_stem(
    funcs: list[Function], demangled: dict[str, str],
) -> dict[str, list[Function]]:
    """Group *funcs* by template-args-stripped stem."""
    out: dict[str, list[Function]] = defaultdict(list)
    for f in funcs:
        qname = _callable_identity_name(f.name, f.mangled, demangled)
        out[_strip_template_args(qname)].append(f)
    return out


def _function_signature(f: Function) -> tuple[str, int, str]:
    """Return a comparable signature tuple for *f*."""
    return (
        f.return_type,
        len(f.params),
        "|".join(p.type for p in f.params),
    )


def _instantiation_set(
    funcs: list[Function], demangled: dict[str, str],
) -> set[tuple[str, tuple[str, int, str]]]:
    return {
        (_canonical_identity_name(f.name, f.mangled, demangled), _function_signature(f))
        for f in funcs
    }


def _leak_change(
    stem: str,
    old_sigs: set[tuple[str, tuple[str, int, str]]],
    new_sigs: set[tuple[str, tuple[str, int, str]]],
) -> Change:
    """Build an INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API finding for *stem*."""
    removed_names = sorted({n for n, _ in old_sigs - new_sigs})[:3]
    added_names = sorted({n for n, _ in new_sigs - old_sigs})[:3]
    return make_change(
        ChangeKind.INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API,
        symbol=stem,
        name=stem,
        detail=f"removed={removed_names}, added={added_names}",
        old_value=str(sorted({n for n, _ in old_sigs})[:3]),
        new_value=str(sorted({n for n, _ in new_sigs})[:3]),
        # ADR-044 D1/D2 (Codex review): this finding exists *because* a public
        # signature instantiates an internal-namespace template — mark it
        # reachable so it can't be suppressed by a broad namespace/
        # source_location rule the same way internal_leak._build_leak_change
        # already does for INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API. Runs after
        # ApplySuppression (DetectTemplatePatterns), so MarkReachability never
        # sees this Change to tag it itself.
        public_reachable=True,
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="value_embedding",
    )


def detect_internal_template_leaks(
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: tuple[str, ...] = _INTERNAL_TEMPLATE_NAMESPACES,
) -> list[Change]:
    """Report internal-namespace function templates with a removed instantiation.

    Strategy:
      1. Index OLD functions by ``(stem_without_template_args,
         param_arity_signature)``. Stem includes the internal namespace
         path so "internal" status is preserved.
      2. Match against NEW functions by the same key.
      3. For internal-namespace stems where at least one OLD
         ``(name, signature)`` instantiation pair is absent from NEW
         (removed outright, or replaced by a signature change — the two are
         indistinguishable from a consumer's perspective, since either way
         the old mangled name it linked against is gone), emit a single
         ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` finding per stem. A
         *purely* additive instantiation-set change (every OLD pair still
         present in NEW, only new ones gained) does not fire — an addition
         alone cannot break an already-linked consumer (Codex review).

    The detector is intentionally per-stem rather than per-instantiation
    so a reviewer sees one finding even when 30 instantiations shift.
    """
    old_funcs = _public_functions(old)
    new_funcs = _public_functions(new)
    # One batched demangle call across both sides -- not two -- so a symbol
    # unchanged between old and new resolves to byte-identical canonical
    # text on both sides regardless of demangle_batch's own dict-ordering.
    demangled = _batch_demangle_for_identity(old_funcs + new_funcs)
    internal_stems = (
        _internal_template_stems(old_funcs, internal_namespaces, demangled)
        | _internal_template_stems(new_funcs, internal_namespaces, demangled)
    )
    if not internal_stems:
        return []

    old_by_stem = _functions_by_stem(old_funcs, demangled)
    new_by_stem = _functions_by_stem(new_funcs, demangled)

    changes: list[Change] = []
    for stem in sorted(internal_stems):
        old_sigs = _instantiation_set(old_by_stem.get(stem, []), demangled)
        new_sigs = _instantiation_set(new_by_stem.get(stem, []), demangled)
        if old_sigs == new_sigs:
            continue
        # Direction matters: a (name, signature) pair that existed in OLD but
        # is absent from NEW means a consumer TU that already resolved and
        # linked against that mangled instantiation now has nothing to link
        # against — that's the real "must rebuild every consumer" break this
        # kind exists to report. A pair present only in NEW (a *new*
        # instantiation the library started emitting, with every previously
        # existing one still there unchanged) adds a symbol a consumer could
        # not already be depending on; it cannot break an already-linked
        # consumer, so it is not reported here.
        if not (old_sigs - new_sigs):
            continue
        changes.append(_leak_change(stem, old_sigs, new_sigs))
    return changes


# ---------------------------------------------------------------------------
# CPO_KIND_CHANGED
# ---------------------------------------------------------------------------


def detect_cpo_kind_changed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report public names that flipped between function and function-object.

    A *customization point object* (CPO) — e.g. ``std::ranges::sort`` —
    can be authored either as a free function template or as an
    ``inline constexpr`` variable of an unspecified class type. The
    two forms have the same call syntax but different ``decltype`` and
    therefore different trait specializations.

    Detection compares the set of public functions vs public variables by
    full qualified name (namespace/class path + leaf, no parameter
    signature) — not a bare leaf, so two unrelated namespaces that happen
    to reuse the same leaf identifier (``ns1::sort`` vs ``ns2::sort``)
    never cross-match. A qualified name present only in functions (old) and
    only in variables (new), or vice versa, triggers the finding.
    """
    from .model import Visibility

    def _func_names(snap: AbiSnapshot) -> set[str]:
        out: set[str] = set()
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            if qname:
                stem = _strip_param_signature(_strip_template_args(qname))
                # Only a function *template* instantiation's demangling ever
                # leaks a return type ahead of the qualified name (checked on
                # the pre-strip qname, since the template args themselves are
                # the tell). A non-template name that happens to contain a
                # space for an unrelated reason — e.g. an ABI thunk marker
                # like "non-virtual thunk to lib::sort()" — must not go
                # through the stripper: it would take the text after the
                # last space ("lib::sort") and wrongly collide with an
                # unrelated same-named CPO variable (Codex review).
                if _looks_like_template_instantiation(qname):
                    stem = _strip_leading_return_type(stem)
                out.add(stem)
        return out

    def _var_names(snap: AbiSnapshot) -> set[str]:
        out: set[str] = set()
        for v in snap.variables:
            if v.visibility != Visibility.PUBLIC:
                continue
            # castxml never namespace-qualifies Variable.name itself, but a
            # real external-linkage variable's mangled name demangles to the
            # full qualified path (_qualified_function_name works for a
            # plain variable too — it just returns `name` unchanged when
            # `mangled` isn't a real Itanium mangling to demangle).
            qname = _qualified_function_name(v.name, v.mangled)
            if qname:
                out.add(qname)
        return out

    old_funcs = _func_names(old)
    old_vars = _var_names(old)
    new_funcs = _func_names(new)
    new_vars = _var_names(new)

    changes: list[Change] = []

    # ADR-044 D1 (Codex review): _func_names/_var_names above both filter to
    # Visibility.PUBLIC before contributing a name, so a CPO_KIND_CHANGED
    # finding's mere existence already proves its subject is public — same
    # reliable-signal tagging as detect_experimental_namespace_changes'
    # function path / diff_namespaces._emit_experimental_change. This
    # detector runs via DetectTemplatePatterns, after ApplySuppression, so
    # nothing else would ever tag these.

    # function → variable
    for name in sorted((old_funcs - old_vars) & (new_vars - new_funcs)):
        changes.append(make_change(
            ChangeKind.CPO_KIND_CHANGED,
            symbol=name,
            name=name,
            old="function",
            new="variable (function-object / CPO)",
            new_value="variable",
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_kind="direct_public_symbol",
        ))

    # variable → function
    for name in sorted((old_vars - old_funcs) & (new_funcs - new_vars)):
        changes.append(make_change(
            ChangeKind.CPO_KIND_CHANGED,
            symbol=name,
            name=name,
            old="variable (function-object / CPO)",
            new="function",
            old_value="variable",
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_kind="direct_public_symbol",
        ))

    return changes


# ---------------------------------------------------------------------------
# OVERLOAD_SET_REROUTED
# ---------------------------------------------------------------------------


def detect_overload_set_rerouted(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report overload sets whose membership shifted (additions *and* removals).

    Conservative: only fires when, at the same qualified name,
    at least one overload was removed AND at least one overload was
    added. A pure removal is already caught by ``func_removed``; a pure
    addition by ``func_added``. Simultaneous removal+addition is the
    signal that existing call sites may now resolve to a different
    overload (silent re-routing).
    """
    from .model import Visibility

    def _by_stem(snap: AbiSnapshot) -> dict[str, list[Function]]:
        out: dict[str, list[Function]] = defaultdict(list)
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            stem = _strip_template_args(qname)
            out[stem].append(f)
        return out

    def _overload_key(f: Function) -> tuple[object, ...]:
        # An overload is distinguished by its parameter types *and* its
        # implicit-object cv/ref qualifiers. Two member functions f(int) and
        # f(int) const are separate overloads sharing a parameter-type tuple;
        # keying on params alone would hide the removal/addition of one of them
        # in a mixed change (e.g. {f(int), f(int) const} -> {f(int), f(long)}).
        return (
            tuple(p.type for p in f.params),
            f.is_const,
            f.is_volatile,
            f.ref_qualifier,
        )

    def _fmt_key(key: tuple[object, ...]) -> str:
        params, is_const, is_volatile, ref_qual = key
        sig = "(" + ", ".join(params) + ")"  # type: ignore[arg-type]
        if is_const:
            sig += " const"
        if is_volatile:
            sig += " volatile"
        if ref_qual:
            sig += f" {ref_qual}"
        return sig

    old_by_stem = _by_stem(old)
    new_by_stem = _by_stem(new)

    changes: list[Change] = []
    for stem in sorted(set(old_by_stem) & set(new_by_stem)):
        old_sigs = {_overload_key(f) for f in old_by_stem[stem]}
        new_sigs = {_overload_key(f) for f in new_by_stem[stem]}
        removed = old_sigs - new_sigs
        added = new_sigs - old_sigs
        if not (removed and added):
            continue
        # Re-routing is only possible within a genuine overload set — i.e. at
        # least one side carries two or more overloads under the same name, so a
        # call that bound to a removed overload can silently rebind to a
        # *different* surviving/added one. A name that maps to a single function
        # on both sides (1→1) is just a signature change (already reported as
        # FUNC_PARAMS_CHANGED); C has no overloading at all, so such a name can
        # never re-route. Skip it to avoid a spurious RISK finding that
        # double-counts the same change. Count actual overloads (Function
        # entries) so cv/ref-only overloads (which share a parameter-type tuple)
        # are not under-counted.
        if len(old_by_stem[stem]) < 2 and len(new_by_stem[stem]) < 2:
            continue
        changes.append(make_change(
            ChangeKind.OVERLOAD_SET_REROUTED,
            symbol=stem,
            name=stem,
            detail=f"{len(removed)} overload(s) removed and {len(added)} added in the same revision",
            old_value=str(sorted(_fmt_key(k) for k in old_sigs)),
            new_value=str(sorted(_fmt_key(k) for k in new_sigs)),
            # ADR-044 D1 (Codex review): _by_stem above filters to
            # Visibility.PUBLIC, so this finding's mere existence already
            # proves its subject is public — same reliable-signal tagging as
            # CPO_KIND_CHANGED above.
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_kind="direct_public_symbol",
        ))

    return changes


# ---------------------------------------------------------------------------
# MANDATORY_TEMPLATE_PARAM_ADDED
# ---------------------------------------------------------------------------


def _count_top_level_template_args(name: str) -> int | None:
    """Count the comma-separated top-level template arguments in
    ``name<...>``. Returns ``None`` if there is no top-level ``<``.

    ``Foo<int, std::pair<int, char>>`` → 2.
    """
    if "<" not in name:
        return None
    depth = 0
    args = 0
    saw_any = False
    for ch in name:
        if ch == "<":
            depth += 1
            if depth == 1:
                saw_any = True
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            continue
        if depth == 1 and ch == ",":
            args += 1
    return args + 1 if saw_any else None


def detect_mandatory_template_param_added(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Heuristic: report templates whose minimum effective arg count grew.

    Without true parameter-pack metadata we cannot perfectly distinguish
    "added defaulted param" from "added mandatory param". The heuristic:
    for each stem, take the minimum arity observed across all
    instantiations of that stem in old vs new. If the new minimum is
    strictly greater than the old minimum, emit the finding.

    Conservative — does NOT fire when the minimum stays the same (so an
    added defaulted parameter that keeps at least one ``Foo<X>``
    instantiation visible is invisible to this detector). The opposite
    miss (false positive when the library drops the smallest-arity
    instantiation) is documented as a known limitation; the symbol
    removal is also caught by ``func_removed`` so the user does see a
    finding even when this detector misses.
    """
    from .model import ScopeOrigin, Visibility

    def _arities(snap: AbiSnapshot) -> tuple[dict[str, set[int]], dict[str, bool]]:
        out: dict[str, set[int]] = defaultdict(set)
        # ADR-044 (Codex review): tracks, per stem, whether *any* contributing
        # observation is reliably public — a Visibility.PUBLIC function, or a
        # type explicitly scoped to the public-header set (ADR-024's opt-in
        # ScopeOrigin.PUBLIC_HEADER, from the public-header set).
        # Without that flag every type's origin is ScopeOrigin.UNKNOWN, so
        # this degrades to "public only if a public function contributed"
        # automatically for the common case.
        is_public: dict[str, bool] = defaultdict(bool)
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            if "<" not in qname:
                continue
            stem = _strip_template_args(qname)
            arity = _count_top_level_template_args(qname)
            if arity is not None:
                out[stem].add(arity)
                is_public[stem] = True
        # Types are also indexed.
        for t in snap.types:
            if "<" not in t.name:
                continue
            stem = _strip_template_args(t.name)
            arity = _count_top_level_template_args(t.name)
            if arity is not None:
                out[stem].add(arity)
                if t.origin == ScopeOrigin.PUBLIC_HEADER:
                    is_public[stem] = True
        return out, is_public

    old_ar, old_public = _arities(old)
    new_ar, new_public = _arities(new)

    changes: list[Change] = []
    for stem in sorted(set(old_ar) & set(new_ar)):
        old_min = min(old_ar[stem])
        new_min = min(new_ar[stem])
        if new_min <= old_min:
            continue
        # ADR-044 D1: public_reachable is set only when at least one
        # contributing observation for this stem was reliably public (see
        # _arities above) — a stem whose only evidence is an internal type
        # with no public-header scoping stays untagged, the same
        # no-reliable-signal reasoning as before, now narrowed to exactly
        # the cases that do have one.
        subject_is_public = old_public.get(stem, False) or new_public.get(stem, False)
        changes.append(make_change(
            ChangeKind.MANDATORY_TEMPLATE_PARAM_ADDED,
            symbol=stem,
            name=stem,
            old=str(old_min),
            public_reachable=subject_is_public,
            reachability_state=(
                ReachabilityState.PROVEN_REACHABLE
                if subject_is_public
                else ReachabilityState.UNKNOWN
            ),
            reachability_kind="direct_public_symbol" if subject_is_public else None,
            new=str(new_min),
            old_value=f"min_arity={old_min}",
            new_value=f"min_arity={new_min}",
        ))

    return changes


# ---------------------------------------------------------------------------
# UNSPECIFIED_RETURN_NOW_NAMED
# ---------------------------------------------------------------------------


# Return-type spellings we treat as "unspecified". These all share the
# property that the consumer must use ``auto`` to capture the result.
_UNSPECIFIED_RETURN_MARKERS: tuple[str, ...] = (
    "auto",
    "__lambda",
    "(unnamed",
    "(anonymous",
    "{lambda",
    "<lambda",
    "decltype(",
)


def _return_is_unspecified(rt: str) -> bool:
    if not rt:
        return False
    rt = rt.strip()
    if rt == "auto":
        return True
    return any(m in rt for m in _UNSPECIFIED_RETURN_MARKERS)


def detect_unspecified_return_now_named(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report functions whose return type flipped between unspecified and named.

    Matches public functions across snapshots by ``(qualified_name,
    param-type-tuple)``. If the return type was unspecified in one
    snapshot and named in the other, emit a finding. The two directions
    are reported with different descriptions so reviewers see whether
    they gained or lost a deduced return.
    """
    from .model import Visibility

    def _index(snap: AbiSnapshot) -> dict[tuple[str, tuple[str, ...]], str]:
        out: dict[tuple[str, tuple[str, ...]], str] = {}
        for f in snap.functions:
            if f.visibility != Visibility.PUBLIC:
                continue
            qname = _qualified_function_name(f.name, f.mangled)
            key = (qname, tuple(p.type for p in f.params))
            out[key] = f.return_type
        return out

    old_idx = _index(old)
    new_idx = _index(new)

    changes: list[Change] = []
    for key in sorted(set(old_idx) & set(new_idx)):
        old_rt = old_idx[key]
        new_rt = new_idx[key]
        old_unspec = _return_is_unspecified(old_rt)
        new_unspec = _return_is_unspecified(new_rt)
        if old_unspec == new_unspec:
            continue
        qname, _params = key
        if old_unspec:
            desc = (
                f"Function '{qname}' return type changed from unspecified "
                f"('{old_rt}') to named ('{new_rt}'). Source that captured "
                f"the result with `auto` keeps compiling; source that wrote "
                f"out the deduced type no longer matches."
            )
        else:
            desc = (
                f"Function '{qname}' return type changed from named "
                f"('{old_rt}') to unspecified ('{new_rt}'). Source that "
                f"wrote out the type no longer compiles; only `auto` "
                f"captures it now."
            )
        changes.append(make_change(
            ChangeKind.UNSPECIFIED_RETURN_NOW_NAMED,
            symbol=qname,
            description=desc,
            old_value=old_rt,
            new_value=new_rt,
            # ADR-044 D1 (Codex review): _index() above filters to
            # Visibility.PUBLIC functions only, so this finding's mere
            # existence already proves its subject is public.
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_kind="direct_public_symbol",
        ))

    return changes


# ---------------------------------------------------------------------------
# INSTANTIATION_MISSING_FROM_BINARY (moved from diff_cpp_patterns in PR-D)
# ---------------------------------------------------------------------------


def detect_missing_instantiations(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Emit ``INSTANTIATION_MISSING_FROM_BINARY`` for template-instantiation
    symbols present in *old* that vanished in *new* but whose enclosing
    template still exists.

    Generalised from the library-family detector originally added in
    PR #239; the heuristic (instantiation = function name contains
    ``<`` at top level) is library-agnostic. Re-exported from
    :mod:`abicheck.diff_cpp_patterns` for backwards compatibility.

    Uses :func:`_qualified_function_name` (demangles when ``Function.name``
    itself carries no ``<...>``) rather than the raw ``name`` field: the
    header/castxml backend populates ``.name`` from the bare AST method name
    only (e.g. ``threshold``, never ``descriptor<float>::threshold``), so
    template args are never present there and this check could otherwise
    never match a real instantiation (case79).

    Both the "still present" and "enclosing template survives" checks are
    scoped to Visibility.PUBLIC functions: a header can still *declare* a
    now-absent instantiation (e.g. via a stale ``extern template class``),
    which the merge keeps as a Visibility.HIDDEN entry for cross-reference —
    counting that declaration-only entry as "surviving" would silently mask
    the exact removal this detector exists to catch.
    """
    from .model import Visibility

    old.index()
    new.index()
    new_mangled = {
        f.mangled for f in new.functions if f.visibility == Visibility.PUBLIC
    }
    findings: list[Change] = []
    surviving_stems: set[str] = set()
    for fn in new.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        qname = _qualified_function_name(fn.name, fn.mangled)
        if _looks_like_template_instantiation(qname):
            surviving_stems.add(_strip_template_args(qname))
    for fn in old.functions:
        if fn.visibility != Visibility.PUBLIC:
            continue
        if fn.mangled in new_mangled:
            continue
        qname = _qualified_function_name(fn.name, fn.mangled)
        if not _looks_like_template_instantiation(qname):
            continue
        stem = _strip_template_args(qname)
        if stem not in surviving_stems:
            continue
        findings.append(make_change(
            ChangeKind.INSTANTIATION_MISSING_FROM_BINARY,
            symbol=fn.mangled,
            name=fn.name,
            detail=stem,
            old_value=fn.mangled,
            new_value=None,
            # ADR-044 D1: both loops above filter to Visibility.PUBLIC, so
            # this finding's mere existence already proves its subject is
            # public — same reliable-signal tagging as the detectors in
            # detect_template_patterns. Runs via DetectCppPatterns, also
            # after ApplySuppression.
            public_reachable=True,
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_kind="direct_public_symbol",
        ))
    return findings


# ---------------------------------------------------------------------------
# Lambda-closure function-level findings never exported from either binary
# ---------------------------------------------------------------------------

#: Kinds this demotion may touch. All four are only ever BREAKING/API_BREAK
#: (see ``change_registry.py``): a function-level finding whose subject is a
#: template instantiated over a local lambda closure type, where the
#: *symbol* itself is what changed spelling purely from an unrelated
#: source-line shift (see :func:`demote_lambda_closure_unexported_findings`'s
#: own docstring). ``FUNC_ADDED`` is deliberately excluded: its default
#: verdict is already an addition/compatible kind, so there is nothing to
#: demote.
_LAMBDA_CLOSURE_DEMOTABLE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
    }
)


def _change_mentions_lambda_closure(change: Change) -> bool:
    """Whether *change*'s own recorded text embeds a lambda-closure marker
    (``symbol``, ``old_value``, or ``new_value`` -- every field a producer
    could have put a castxml synthetic ctor/dtor key or parameter/return
    spelling into)."""
    from .name_classification import contains_anonymous_type_marker

    return (
        contains_anonymous_type_marker(change.symbol)
        or contains_anonymous_type_marker(change.old_value)
        or contains_anonymous_type_marker(change.new_value)
    )


def demote_lambda_closure_unexported_findings(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Demote a lambda-closure function-level finding whose subject was
    never actually exported/defined in EITHER binary's real symbol table.

    A template instantiated over a local lambda's closure type has no
    user-nameable identity, so the marker-list fix
    (``name_classification._ANONYMOUS_TYPE_MARKERS``) already stops such a
    *type* from being treated as ABI surface — an unrelated edit that merely
    shifts the lambda's source line must not manufacture a spurious
    ``type_removed``/``type_added`` pair for it. That fix does not reach
    *function*-level findings (a destructor of such an instantiation, or a
    function taking the closure-parameterized type by value): the mangled
    symbol and/or parameter-type spelling embed the closure's source
    coordinates directly (see AGENTS.md's "Lambda-closure churn survives at
    the function level" entry for the full investigation).

    Broadly demoting every such finding was considered and rejected there: a
    symbol an already-compiled consumer linked against by exact spelling can
    genuinely break on renumbering, even though no new consumer could ever
    *write source* against it. So this demotes only when the finding's own
    symbol is confirmed absent from BOTH sides' real ELF exported symbol
    table (:func:`elf_symbol_filter.exported_symbol_names`) — never from
    DWARF/header evidence alone, the different question
    :func:`detect_missing_instantiations` above answers (an instantiation
    present in the old export table and gone from the new one).

    A castxml-synthesized ctor/dtor key
    (``dumper_castxml.is_synthetic_ctor_key``/``is_synthetic_dtor_key``) is
    itself *never* a real exported symbol by construction — castxml
    synthesizes it because it could not produce a real mangled name for this
    overload, typically because one of its (or its enclosing template's)
    arguments is an unnameable local lambda closure type. A bare membership
    check of the key text would therefore always read "confirmed absent"
    vacuously, so instead this asks whether the owning class/class-template
    is exported under *any* instantiation at all, on either side
    (``finding_identity_ctor_dtor.synthetic_ctor_dtor_template_base_name`` +
    ``itanium_source_name_token``, a dependency-free Itanium ``<source-name>``
    substring match against the raw exported symbols). Zero exported members
    under any instantiation means no consumer could have linked against this
    one either; some other instantiation being exported leaves the demotion
    unconfirmed, since it can't rule out that the specific closure-
    parameterized instantiation was the one exported (see AGENTS.md's
    "Lambda-closure churn survives at the function level" entry, item 1).

    Like :func:`abicheck.diff_versioning.demote_internal_version_node_findings`,
    this uses the per-finding ``effective_verdict`` modulation hook (ADR-025)
    rather than removing anything from *changes* (annotate, never remove).
    Conservative: only already-BREAKING/API_BREAK findings are touched
    (never escalates), a prior ``effective_verdict`` is left untouched, and
    missing ELF evidence on either side fails closed. Mutates and returns
    ``changes``.
    """
    from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS, Verdict
    from .diff_symbols import _public_functions
    from .dumper_castxml import is_synthetic_ctor_key, is_synthetic_dtor_key
    from .elf_symbol_filter import FUNCTION_SYMBOL_TYPES, exported_symbol_names
    from .finding_identity_ctor_dtor import (
        itanium_source_name_token,
        itanium_standard_substitution_token,
        synthetic_ctor_dtor_template_base_name,
    )

    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    has_elf_evidence = bool(getattr(old_elf, "symbols", None)) and bool(
        getattr(new_elf, "symbols", None)
    )
    if not has_elf_evidence:
        # Fail closed: without a real dynsym table on BOTH sides, "not
        # found" cannot be told apart from "we never checked" -- see
        # AGENTS.md's "Findings emitted from absent evidence" entry for why
        # treating an evidence gap as a demotion signal is unsafe.
        return changes
    old_exported = exported_symbol_names(old_elf, FUNCTION_SYMBOL_TYPES)
    new_exported = exported_symbol_names(new_elf, FUNCTION_SYMBOL_TYPES)
    # `change.symbol` (the detector's match key) can differ from a function's
    # real accepted export spelling, `Function.name` -- `_public_functions`'s
    # own bare-name export fallback exists because castxml/DWARF can
    # under-mangle a name the real ELF table still carries correctly, so both
    # spellings must be checked before ever claiming absence.
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for change in changes:
        if change.kind not in _LAMBDA_CLOSURE_DEMOTABLE_KINDS:
            continue
        if change.kind not in BREAKING_KINDS and change.kind not in API_BREAK_KINDS:
            continue
        if change.effective_verdict is not None:
            continue
        symbol = change.symbol or ""
        if not symbol:
            continue
        if not _change_mentions_lambda_closure(change):
            continue
        if is_synthetic_ctor_key(symbol) or is_synthetic_dtor_key(symbol):
            base_name = synthetic_ctor_dtor_template_base_name(symbol)
            if base_name is None:
                continue  # scope unrecoverable -- fail closed
            # A std:: owner may mangle via a fixed Itanium substitution
            # instead of its literal source-name -- see
            # itanium_standard_substitution_token's own docstring.
            tokens = {itanium_source_name_token(base_name)}
            std_token = itanium_standard_substitution_token(symbol)
            if std_token is not None:
                tokens.add(std_token)
            if any(
                any(token in exported for token in tokens)
                for exported in (*old_exported, *new_exported)
            ):
                # Exported under SOME instantiation on at least one side --
                # can't rule out it was this one, so stays as severe as made.
                continue
            change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
            change.modulation_reason = (
                "subject is a constructor/destructor of a template "
                "instantiated over a local lambda closure type, and the "
                "owning class/class-template is confirmed absent from both "
                "the old and new binary's exported symbol table under any "
                "instantiation -- no consumer, past or present, could have "
                "linked against it"
            )
            change.modulation_rule = "lambda_closure_never_exported"
            continue
        candidate_spellings = {symbol}
        old_fn = old_map.get(symbol)
        if old_fn is not None:
            candidate_spellings.add(old_fn.name)
        new_fn = new_map.get(symbol)
        if new_fn is not None:
            candidate_spellings.add(new_fn.name)
        if candidate_spellings & (old_exported | new_exported):
            # Genuinely exported under the key or the matched function's own
            # export alias: stays exactly as severe as the detector made it.
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_reason = (
            "subject is a template instantiated over a local lambda closure "
            "type, and the reported symbol is confirmed absent from both "
            "the old and new binary's exported symbol table -- no consumer, "
            "past or present, could have linked against it"
        )
        change.modulation_rule = "lambda_closure_never_exported"
    return changes


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


def detect_template_patterns(
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: tuple[str, ...] = _INTERNAL_TEMPLATE_NAMESPACES,
) -> list[Change]:
    out: list[Change] = []
    out.extend(detect_internal_template_leaks(
        old, new, internal_namespaces=internal_namespaces,
    ))
    out.extend(detect_cpo_kind_changed(old, new))
    out.extend(detect_overload_set_rerouted(old, new))
    out.extend(detect_mandatory_template_param_added(old, new))
    out.extend(detect_unspecified_return_now_named(old, new))
    return out

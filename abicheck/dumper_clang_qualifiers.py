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

"""Type-spelling qualifier helpers for the ``clang -ast-dump=json`` backend.

Split out of ``dumper_clang.py``, which sits at the 2000-line hard cap the
AI-readiness gate enforces. They answer one question between them — *does
this declaration's own (top-level) type carry qualifier X?* — against
clang's **printed** type spelling, which is the only form the JSON AST
offers:

- :func:`_desugared_qualtype` unwraps a typedef, since a qualifier can hide
  behind an alias;
- :func:`_last_top_level_ptr_end` finds the depth-0 pointer boundary, so a
  qualifier belonging to a pointee is not read as the declaration's own;
- :func:`_field_own_cv_source` is the substring those two produce together,
  and is what the const/volatile *field* question uses;
- :func:`_declarator_group` (with :func:`_follows_type_operator_keyword`) finds the
  parenthesized declarator when C's precedence rules put the declared
  entity's own pointer inside parentheses, where depth 0 describes
  something else entirely;
- :func:`_clang_param_is_restrict` is one concrete question asked of them;
  :func:`_clang_param_is_va_list` is another.

Pure and leaf: takes plain AST dicts, imports nothing from ``dumper_clang``,
and is unit-testable with no clang installed.
"""

from __future__ import annotations

import re
from typing import Any


def desugared_qualtype(node: dict[str, Any]) -> str:
    """The fully-desugared type spelling, when clang provides one.

    A field declared through a typedef to a cv-qualified type
    (``typedef const int T; struct S { T x; };``) renders ``qualType`` as
    the bare alias ``"T"`` — the real ``"const int"`` is only visible via
    the separate ``desugaredQualType`` key clang emits precisely when a
    type alias needs unwrapping. A plain (non-aliased) field carries no
    ``desugaredQualType`` key at all (confirmed empirically), so falling
    back to ``qualType`` is exact, not merely a guess, for every other
    case. Used only for the const/volatile regex check below — the
    field's own displayed ``type`` spelling stays the sugared form users
    actually wrote (Codex review, PR #582: mirrors dumper_castxml's
    Typedef-indirection walk for the identical reason — a regex on the
    display spelling alone misses a qualifier hidden behind an alias).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        desugared = type_obj.get("desugaredQualType")
        if isinstance(desugared, str) and desugared:
            return desugared
        return str(type_obj.get("qualType", ""))
    return ""


#: Back-compat alias -- see :func:`desugared_qualtype`'s own docstring.
_desugared_qualtype = desugared_qualtype


def _last_top_level_ptr_end(type_str: str) -> int:
    """Index just past the last depth-0 ``*`` in *type_str*, or -1 if none.

    A ``*`` nested inside a template argument list, function-parameter
    list, or array subscript doesn't count — the value itself isn't a
    pointer at that syntactic position. Depth tracking mirrors
    ``name_classification._has_top_level_ptr_or_ref``.
    """
    depth = 0
    last = -1
    for i, ch in enumerate(type_str):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "*" and depth == 0:
            last = i + 1
    return last


def field_own_cv_source(desugared: str) -> str:
    """Substring of *desugared* that describes the FIELD's OWN const/
    volatile qualifier, as opposed to its pointee's.

    A pointer typedef's desugared spelling puts a POINTEE qualifier before
    the ``*`` (``const int *`` — pointer to const int, the pointer itself
    is NOT const) and the pointer VALUE's own qualifier as a suffix after
    it, with no space (``int *const`` — confirmed against real clang
    output). Scanning the whole string for ``const``/``volatile`` (as an
    earlier version of ``_make_field`` did) misread the pointee's
    qualifier as the field's own, so a field typed through
    ``typedef const int *P;`` was wrongly marked ``is_const=True`` even
    though ``P`` itself is a plain, non-const pointer (Codex review, PR
    #582 — a pointer-typedef sibling of the scalar-typedef case
    ``_desugared_qualtype`` already handles). A non-pointer type has no
    such ambiguity — the whole spelling describes the field itself.
    """
    end = _last_top_level_ptr_end(desugared)
    return desugared[end:] if end >= 0 else desugared


#: Back-compat alias -- see :func:`field_own_cv_source`'s own docstring.
_field_own_cv_source = field_own_cv_source


#: Type operators whose parenthesized operand is an *expression*, not a
#: declarator. Clang prints these inside a type spelling, and their operand
#: routinely begins with ``*`` (a dereference), which is exactly what makes
#: them indistinguishable from a pointer declarator by shape alone.
_TYPE_OPERATOR_KEYWORDS = frozenset(
    {"decltype", "typeof", "__typeof", "__typeof__", "typeof_unqual"}
)


def _follows_type_operator_keyword(type_str: str, paren_index: int) -> bool:
    """Whether the ``(`` at *paren_index* is a type operator's operand.

    Matching the *specific keyword* is load-bearing; two cheaper rules both
    fail, each on a case a previous review round established (Codex review):

    - "directly follows an identifier character" misses ``typeof (*gp)``,
      because clang normalizes the spelling with a space — even when the
      source wrote ``typeof(*gp)``, confirmed against clang 18. It caught
      ``decltype(``, which clang prints unspaced, purely by luck of spacing.
    - "follows an identifier, skipping whitespace" over-corrects and breaks
      the declarator cases: ``int (*restrict)[3]`` and ``void (*)(int
      *restrict)`` also read as ``<identifier><space>(``, so both would be
      dismissed as expression operands and answer False.

    The keyword set is what actually separates them, and it is closed: these
    are the only constructs clang prints in a type spelling whose
    parenthesized operand is an expression.
    """
    k = paren_index - 1
    while k >= 0 and type_str[k].isspace():
        k -= 1
    end = k + 1
    while k >= 0 and (type_str[k].isalnum() or type_str[k] == "_"):
        k -= 1
    return type_str[k + 1 : end] in _TYPE_OPERATOR_KEYWORDS


def _declarator_group(type_str: str) -> str | None:
    """Contents of *type_str*'s parenthesized declarator group, or ``None``.

    C's declarator precedence parenthesizes a pointer whose pointee is an
    array or a function, so clang spells those as ``int (*restrict)[3]`` and
    ``void (*)(int *restrict)`` — in both, the declared entity's own ``*``
    is inside the parentheses and NOTHING at depth 0 describes it. This
    returns that group's contents (``"*restrict"``, ``"*"``) so a caller can
    ask its question there instead.

    A candidate group is a depth-0 parenthesized span whose contents begin
    with ``*``. Two refinements make that identification correct rather than
    merely usually-right, each found by a counterexample rather than by
    reasoning (Codex review):

    - **The INNERMOST such group wins.** Declarators nest, and the declared
      entity's own pointer is the most deeply nested one. Both of these are
      real clang output, and they differ only in which level carries the
      qualifier: ``int (*(*restrict p)[3])[2]`` (``p`` IS restrict) prints as
      ``int (*(*restrict)[3])[2]``, while ``int (*restrict (*p)[3])[2]``
      (``p`` is a plain pointer; the qualifier belongs to the array's element
      type) prints as ``int (*restrict (*)[3])[2]``. Taking the outer group
      answers both the same way and gets the second one wrong.
    - **An expression operand is not a declarator.** ``decltype(*gp + 0)
      *__restrict`` opens a depth-0 ``(`` whose contents begin with ``*`` —
      a dereference, not a pointer declarator — and treating it as the group
      searches ``*gp + 0`` and misses the real qualifier. A declarator
      group's ``(`` is never immediately preceded by an identifier
      character; a call-like operand (``decltype(``, ``typeof(``) always is,
      which separates the two exactly.

    Both refinements leave the simpler cases untouched: the trailing
    parameter list of a function pointer (``(int *restrict)``) still fails
    the leading-``*`` test, and an array extent (``[3]``) is not
    parenthesized at all.

    ``None`` when the spelling has no such group, i.e. an ordinary
    unparenthesized declarator like ``int *restrict``, where depth 0 is
    exactly where the answer lives.
    """
    depth = 0
    i = 0
    while i < len(type_str):
        ch = type_str[i]
        if ch in "<([":
            if (
                ch == "("
                and depth == 0
                and not _follows_type_operator_keyword(type_str, i)
            ):
                j = i + 1
                while j < len(type_str) and type_str[j] == " ":
                    j += 1
                if j < len(type_str) and type_str[j] == "*":
                    inner = 1
                    k = i + 1
                    while k < len(type_str) and inner:
                        if type_str[k] in "<([":
                            inner += 1
                        elif type_str[k] in ">)]":
                            inner -= 1
                        k += 1
                    group = type_str[i + 1 : k - 1]
                    # Descend: the declared entity's own pointer is the
                    # innermost declarator, not this enclosing one.
                    return _declarator_group(group) or group
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        i += 1
    return None


def clang_param_is_restrict(node: dict[str, Any]) -> bool:
    """Whether *node* (a ``ParmVarDecl``) is a ``restrict``-qualified pointer.

    Public (no leading underscore) since ``extract.headers.clang.functions``
    reads it across the module boundary -- ``_clang_param_is_restrict``
    below is kept as a back-compat alias for every existing caller spelling
    the old private name (Codex review, PR #940).

    Matches :meth:`abicheck.dumper_castxml._CastxmlParser._resolve_cv_restrict`'s
    semantics — the parameter's OWN (top-level) qualification, with typedef
    indirection followed — so the two backends produce a directly comparable
    bool rather than a backend-specific encoding:

    - **Typedef indirection** is why this reads
      :func:`_desugared_qualtype` rather than :func:`_qualtype`: a parameter
      declared through ``typedef int *restrict rptr;`` renders ``qualType``
      as the bare alias ``"rptr"``, with the real ``"int *restrict"`` only
      in ``desugaredQualType`` (confirmed against real clang 18 output,
      same mechanism ``_desugared_qualtype``'s own docstring documents for
      const/volatile). castxml's walk follows its ``Typedef`` chain for
      the identical reason.
    - **Top-level only** is why only the span after the last depth-0 ``*``
      is searched: ``int *restrict *`` qualifies the INNER pointer, leaving
      the parameter itself unqualified, while ``int **restrict`` qualifies
      the parameter (both spellings confirmed empirically). Scanning the
      whole spelling would conflate the two.
    - **A parenthesized declarator wins over the top level**, which is the
      one rule that is not obvious from the plain-pointer case. When C's
      declarator precedence forces the parameter's own ``*`` into
      parentheses, everything at depth 0 belongs to something else — the
      pointee (``int *(*restrict)[3]``, whose leading ``*`` is the array
      element's) or the callback's parameter list (``void (*)(int
      *restrict)``, whose ``restrict`` is the CALLBACK ARGUMENT's).
      :func:`_declarator_group` finds the group that actually holds the
      parameter's pointer and the search happens inside it, so both of
      those answer correctly. Two review rounds each found one of them:
      scanning the whole spelling reported a false True for the callback,
      and requiring a depth-0 ``*`` reported a false False for the pointer
      to an array — a real, legal, restrict-qualified object pointer that
      castxml does see. Every spelling in this docstring was confirmed
      against real clang 18 output.

    - **A reference wrapper answers False**, matching castxml rather than the
      qualifier that is still visible in the spelling. `int *__restrict &`
      is a reference to a restrict-qualified pointer; castxml's walk follows
      only ``CvQualifiedType``/``Typedef``/``ElaboratedType`` and stops dead
      at the outer ``ReferenceType``, so it reports False. Reporting True
      here would put the two backends in disagreement on an unchanged header
      — the same cross-backend false positive this extraction exists to
      remove, just arriving from the other side (Codex review).

    A function-pointer parameter answers False, and that costs nothing even
    though it looks like the same shape as the array case: a pointer to a
    *function* may not be ``restrict``-qualified at all (C11 6.7.3p2 —
    ``restrict`` qualifies a pointer to an *object* type), so its declarator
    group can never legally carry the qualifier and no distinction between
    the two declarator kinds is needed here. Verified rather than reasoned
    about: both ``void (*restrict cb)(int)`` and ``int *(*restrict cb)(void)``
    are rejected outright by clang in C and C++ alike ("pointer to function
    type ... may not be 'restrict' qualified").

    Both the C spelling (``restrict``) and the C++ ones (``__restrict`` /
    ``__restrict__``, which clang normalizes to ``__restrict``) are
    recognized; the word-boundary anchors keep a type merely *named*
    ``restrict_like`` from matching.
    """
    desugared = _desugared_qualtype(node)
    scope = _declarator_group(desugared)
    if scope is None:
        scope = desugared
    end = _last_top_level_ptr_end(scope)
    if end < 0:
        return False
    own = scope[end:]
    if "&" in own:
        # A REFERENCE to a restrict-qualified pointer (`int *__restrict &`).
        # castxml's walk follows only CvQualifiedType/Typedef/ElaboratedType
        # and stops at the outer ReferenceType, so it answers False; matching
        # here on the qualifier it can still see would put the two backends in
        # disagreement on an unchanged header -- the false positive this whole
        # extraction exists to remove (Codex review). Everything after the
        # last depth-0 `*` is at depth 0 by construction, so this `&` is the
        # declared entity's own, never a nested one.
        return False
    return bool(re.search(r"\b(?:__)?restrict(?:__)?\b", own))


#: The x86-64 System V (Itanium C++ ABI) spelling of ``__builtin_va_list``
#: once decayed to a function parameter and fully desugared: a pointer to
#: (optionally cv-qualified) ``__va_list_tag``. C keeps the elaborated
#: ``struct`` keyword in the printed spelling; C++ drops it (confirmed
#: against real clang 18 output in both language modes) -- see
#: :func:`_clang_param_is_va_list`.
_VA_LIST_TAG_PTR_RE = re.compile(
    r"^(?:(?:const|volatile)\s+)*(?:struct\s+)?__va_list_tag\s*\*$"
)


#: Back-compat alias -- see :func:`clang_param_is_restrict`'s own docstring.
_clang_param_is_restrict = clang_param_is_restrict


def clang_param_is_va_list(node: dict[str, Any]) -> bool:
    """Whether *node* (a ``ParmVarDecl``) is a ``va_list`` parameter.

    Public (no leading underscore) since ``extract.headers.clang.functions``
    reads it across the module boundary -- ``_clang_param_is_va_list`` below
    is kept as a back-compat alias for every existing caller spelling the
    old private name (Codex review, PR #940).

    ``va_list`` is itself an array-of-one-struct typedef
    (``__builtin_va_list`` on x86-64 System V, the ABI this environment can
    verify against), so a ``va_list`` PARAMETER decays to a pointer the
    moment it crosses a function boundary -- there is no ``VaListType`` node
    or dedicated keyword in the AST to key on, only the decayed pointer's own
    printed spelling. Confirmed against real ``clang -ast-dump=json`` output
    (Clang 18) that this decayed spelling survives BOTH a further user
    typedef (``typedef va_list my_va_list;``) and a top-level ``const``
    (``const va_list ap`` -- the array-level qualifier reappears as a
    pointee-level one on the decayed pointer) with no ``desugaredQualType``
    needed in either case; :func:`_desugared_qualtype` is still used for
    consistency with :func:`_clang_param_is_restrict` and in case a deeper
    indirection someday needs it.

    **Scope, stated rather than silently assumed**: this matches ONLY the
    x86-64 System V spelling (``(const )?(struct )?__va_list_tag *``), the
    one ABI verified here. ``va_list``'s underlying representation is
    genuinely target-defined -- AArch64 AAPCS uses a different multi-field
    struct, and other targets differ again -- and guessing an unverified
    spelling risks a false positive worse than the pre-existing "never
    populated" gap this closes (the same "verify before claiming" discipline
    ``AGENTS.md`` documents throughout G31 Phase C). A parameter on an
    unrecognized target's real ``va_list`` type answers ``False`` here,
    exactly as every backend already did before this function existed --
    a conservative false negative, never a fabricated positive.

    A pointer-TO-``va_list`` (``va_list *``, two stars once decayed) is
    deliberately NOT matched: it names a parameter that forwards a caller's
    already-decayed ``va_list`` by reference (e.g. ``vsnprintf``-style
    wrappers), which is a plain pointer parameter, not a ``va_list``
    parameter itself -- the regex's anchored single trailing ``*`` already
    excludes it.

    **A residual, deliberately-not-closed gap this scope note implies**
    (Codex review): the snapshot-level ``clang_va_list_facts_reliable``
    flag records only whether THIS producer's extraction ran with the fix
    applied -- it carries no per-snapshot record of WHICH target ABI a
    clang parse actually ran against. Two genuinely different-target clang
    snapshots (x86-64 System V vs. AArch64, say) can both be marked
    reliable, and a real cross-architecture comparison (which this tool's
    comparability layer permits in general) would then read the x86-64
    side's real detections against the AArch64 side's uniform ``False`` as
    a spurious ``PARAM_BECAME_VA_LIST``/``PARAM_LOST_VA_LIST``. This is the
    same class of gap as the already-documented "toolchain-identity probe"
    entry in ``AGENTS.md``'s Known gaps (no resolved-target validation
    exists for ANY header-AST fact today, not just this one) rather than a
    new, isolated problem -- closing it here alone, ahead of that general
    mechanism, would be an inconsistent one-off fix for a structural gap.
    """
    desugared = _desugared_qualtype(node)
    return bool(_VA_LIST_TAG_PTR_RE.match(desugared.strip()))


#: Back-compat alias -- see :func:`clang_param_is_va_list`'s own docstring.
_clang_param_is_va_list = clang_param_is_va_list


def record_kind(node: dict[str, Any]) -> str:
    """``"union"``/``"struct"``/``"class"`` from a record's ``tagUsed``."""
    tag = node.get("tagUsed")
    return tag if tag in ("union", "struct") else "class"


#: Back-compat alias -- see :func:`record_kind`'s own docstring.
_record_kind = record_kind


def reduce_opaque_kind_set(kinds: set[str] | None) -> str | None:
    """Reduce all raw kinds observed for one identity's non-def redecls to a
    single ``RecordType.kind`` override (Codex review, PR #719, two follow-up
    rounds).

    Three cases, checked in order:

    1. ``kinds`` is ``None``/empty (the identity has no opaque redecl at all
       -- it only ever appears as a complete definition): no override.
    2. Exactly one DISTINCT raw kind was ever observed: return it UNCHANGED.
       This is the case the second follow-up round fixed -- an identity with
       only `class H;` (no ambiguity at all) must keep reporting `"class"`,
       not a forced canonical spelling, or comparing it against a later
       COMPLETE definition using the same real `"class"` key would produce a
       false ``SOURCE_LEVEL_KIND_CHANGED`` purely from the opaque side being
       relabeled to something the identity was never actually declared with.
    3. Two or more distinct raw kinds were observed (genuine ambiguity, e.g.
       both `struct H;` and `class H;` compatibly forward-declare the same
       identity): fold `"class"`/`"struct"` to one fixed spelling first (they
       are interchangeable class-keys, mirrors
       ``tu_merge._record_kinds_compatible``) so the canonicalized result no
       longer depends on which particular SUBSET of the ambiguous group
       happens to be present in a given snapshot (the first follow-up
       round's fix -- adding/removing a compatible redecl must not flip the
       observed set). If folding collapses to one value, use it. Otherwise
       (a `union` genuinely mixed with `class`/`struct`, an ODR-inconsistent
       header this parser cannot resolve) fall back to a deterministic
       ``min()`` over the raw kinds, the same tie-break used before either
       fix.
    """
    if not kinds:
        return None
    if len(kinds) == 1:
        return next(iter(kinds))
    folded = {"struct" if k == "class" else k for k in kinds}
    return folded.pop() if len(folded) == 1 else min(kinds)


#: Back-compat alias -- see :func:`reduce_opaque_kind_set`'s own docstring.
_reduce_opaque_kind_set = reduce_opaque_kind_set


def clang_method_is_override(node: dict[str, Any]) -> bool:
    """Explicit C++11 ``override`` specifier on *node* (G31 Phase C backend
    audit) — the direct-clang counterpart to ``dumper_castxml.py``'s
    ``is_override`` (a compound-``attributes``-string regex search for the
    ``override`` token), matching its exact semantics: whether the keyword
    was actually written, not whether the method genuinely overrides a base
    virtual (that broader, no-keyword-required signal is
    ``dumper_clang_vtable.py``'s separate reconstruction job).

    Verified against real ``clang -ast-dump=json`` output (Clang 18): unlike
    ``virtual``/``pure``, which are plain boolean keys on the node itself,
    an explicit ``override`` is signaled by a child ``OverrideAttr`` node
    under ``"inner"`` — the same child-node convention
    ``dumper_clang._clang_final_attr``/``_clang_deprecated_message`` already
    read for ``final``/``[[deprecated]]``.

    Public (no leading underscore) since ``extract.headers.clang.functions``
    reads it and ``_OVERRIDE_ELIGIBLE_KINDS`` below across the module
    boundary -- ``_clang_method_is_override`` is kept as a back-compat alias
    for every existing caller spelling the old private name (Codex review,
    PR #940).
    """
    return any(
        isinstance(child, dict) and child.get("kind") == "OverrideAttr"
        for child in node.get("inner", []) or []
    )


#: Back-compat alias -- see :func:`clang_method_is_override`'s own docstring.
_clang_method_is_override = clang_method_is_override


#: clang node kinds castxml's own ``is_override`` restricts to (its
#: ``Method``/``Destructor``/``Converter``/``OperatorMethod`` XML tags) —
#: only a member-function kind that can actually be virtual. Deliberately
#: excludes ``CXXConstructorDecl`` (a constructor can never be virtual) and
#: ``FunctionDecl`` (a free function/operator can't either); clang has no
#: separate "operator method" node kind the way castxml's XML schema does —
#: an overloaded operator is an ordinary ``CXXMethodDecl`` here, already
#: covered. Public (no leading underscore) for the same cross-module reason
#: as :func:`clang_method_is_override` above; ``_OVERRIDE_ELIGIBLE_KINDS`` is
#: kept as a back-compat alias.
OVERRIDE_ELIGIBLE_KINDS = frozenset(
    {"CXXMethodDecl", "CXXDestructorDecl", "CXXConversionDecl"}
)
_OVERRIDE_ELIGIBLE_KINDS = OVERRIDE_ELIGIBLE_KINDS


def clang_record_is_abstract(node: dict[str, Any]) -> bool | None:
    """``RecordType.is_abstract`` from a record's own ``definitionData`` (G31
    Phase C backend audit) — the direct-clang counterpart to
    ``dumper_castxml.py``'s ``el.get("abstract") == "1"`` (castxml's own real
    semantic-analysis attribute).

    Verified against real ``clang -ast-dump=json`` output (Clang 18) before
    wiring this up, following the exact same presence-recovers-``True``/
    absence-recovers-``False`` convention
    ``dumper_clang._clang_record_type_traits`` already established for
    ``isStandardLayout``/``isTriviallyCopyable``: ``definitionData.isAbstract``
    is present only when the class genuinely has an unoverridden pure
    virtual — this is real semantic computation from clang's own class
    analysis, not a shallow "does this class itself declare a pure virtual"
    check: a derived class that inherits a pure virtual from a base WITHOUT
    overriding it still reads ``isAbstract: True`` (confirmed with a real
    three-class hierarchy: an abstract base, a concrete override, and a
    second derived class that leaves the pure virtual unimplemented — the
    third class is genuinely abstract too, and clang reports it as such).

    ``None`` (not ``False``) when ``definitionData`` itself is absent — the
    same two real cases ``dumper_clang._clang_record_type_traits`` documents
    (a plain C ``RecordDecl``, or an incomplete/forward-declared record
    already filtered upstream).
    """
    definition_data = node.get("definitionData")
    if not isinstance(definition_data, dict):
        return None
    return bool(definition_data.get("isAbstract", False))


#: Back-compat alias -- see :func:`clang_record_is_abstract`'s own docstring.
_clang_record_is_abstract = clang_record_is_abstract


def clang_record_type_traits(node: dict[str, Any]) -> tuple[bool | None, bool | None]:
    """``(is_standard_layout, is_trivially_copyable)`` from a record's own
    ``definitionData`` (G31 Phase C schema-completeness audit).

    Verified against real ``clang -ast-dump=json`` output (Clang 18) before
    wiring this up, following G28 Phase 1's discipline: a ``CXXRecordDecl``'s
    ``definitionData`` carries ``isStandardLayout``/``isTriviallyCopyable`` as
    boolean keys, but — confirmed empirically, not assumed from clang's own
    schema docs — clang's ``JSONNodeDumper`` only *emits* a ``definitionData``
    boolean key when the trait is ``true``; a record that does **not** have
    the trait has the key entirely absent rather than present with a literal
    ``false`` (e.g. a class with a private member is not standard-layout, and
    its ``definitionData`` has no ``isStandardLayout`` key at all, confirmed
    by direct comparison against a plain-public-members struct which does).
    So presence recovers ``True``, and absence — while ``definitionData``
    itself is present — recovers ``False``.

    A record with no ``definitionData`` at all yields ``(None, None)`` —
    "not collected", not "false" — matching this module's existing
    ``RecordType.is_standard_layout``/``is_trivially_copyable`` tri-state
    convention (see ``diff_layout.py``'s own True-vs-None handling, which
    only fires ``STANDARD_LAYOUT_LOST``/``TRIVIALLY_COPYABLE_LOST`` on an
    explicit ``True`` on one side, never treating "unknown" as a regression).
    This happens for two real cases, confirmed empirically: a plain C
    ``RecordDecl`` (these are C++-only type-trait concepts, so a C struct's
    node carries no ``definitionData`` key whatsoever — not "trivially true
    by default", genuinely absent), and an incomplete/forward-declared record
    (filtered out upstream by ``dumper_clang_vtable.is_record_definition``
    before this is ever called, but kept conservative here too in case that
    guard's scope ever narrows).
    """
    definition_data = node.get("definitionData")
    if not isinstance(definition_data, dict):
        return None, None
    return (
        bool(definition_data.get("isStandardLayout", False)),
        bool(definition_data.get("isTriviallyCopyable", False)),
    )


#: Back-compat alias -- see :func:`clang_record_type_traits`'s own docstring.
_clang_record_type_traits = clang_record_type_traits

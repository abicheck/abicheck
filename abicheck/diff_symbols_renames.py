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

"""Symbol-rename detection: mangled-name parsing, plausibility gating, and the
ELF fingerprint-based rename detector.

Leaf module (must not import from ``diff_symbols`` to avoid an import cycle).
The symbol-level public surface re-exports these names back from
``diff_symbols`` so ``from abicheck.diff_symbols import ...`` keeps working.
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Mapping
from functools import lru_cache

from .binary_fingerprint import (
    _MIN_SYMBOL_SIZE,
    FunctionFingerprint,
    match_renamed_functions,
)
from .checker_policy import ChangeKind
from .checker_types import Change
from .demangle import demangle, demangle_batch
from .detector_registry import registry
from .diff_cxx_rules import (
    component_embeds_template_args,
    itanium_scope_components_with_template_positions,
    msvc_scope_components,
    qualified_name_scope_components,
    strip_trailing_top_level_parameter_list,
)
from .diff_helpers import make_change
from .dumper_castxml import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, Function, is_cxx_runtime_library
from .model.elf_facts import SymbolType

_log = logging.getLogger(__name__)


def _should_filter_transitive_runtime_symbols(snap: AbiSnapshot) -> bool:
    """Return True when transitive C++ runtime symbols should be filtered.

    Returns False when ``snap.library`` or the ELF SONAME identifies *snap* as
    the C++ runtime itself, where runtime-owned symbols are the inspected ABI.
    """
    elf = getattr(snap, "elf", None)
    return not (
        is_cxx_runtime_library(snap.library)
        or is_cxx_runtime_library(getattr(elf, "soname", ""))
    )


_FUNC_LIKE_TYPES = frozenset({SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE})

# Minimum shared leading/trailing run (in characters) between two unqualified
# leaf names for a *hash-less* (size-only / fuzzy) match to count as a rename.
# When no code hash is available — the only mode the snapshot/elf_only path can
# reach — a "rename" is inferred purely from a coincidental symbol-size
# collision, which on a large library pairs completely unrelated functions that
# merely share a byte size (observed on real libLLVM diffs: e.g. fixupIndexV4 ->
# SmallVectorImpl<...>). A genuine rename or namespace relocation keeps a
# substantial common prefix or suffix token in the *unqualified* leaf name
# (foo_v1->foo_v2, old_only->new_only), whereas distinct leaves — even under a
# shared scope (Class::get vs Class::set, ::begin vs ::end, get<int> vs
# set<int>) — share at most one or two incidental characters. Requiring a
# >=3-char shared affix cleanly separates the two on measured data (genuine
# renames share 4-20, unrelated pairs 0-2).
_RENAME_MIN_SHARED_AFFIX = 3

# The C++ ``operator`` keyword as a whole token: not preceded or followed by an
# identifier character, so substrings like ``cooperator`` or ``operator_v1``
# (ordinary identifiers) and ``myoperator::foo`` (operator inside a qualifier)
# are not mistaken for an operator function name.
_OPERATOR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])operator(?![A-Za-z0-9_])")

# Itanium constructor/destructor variant codes: ``C1``/``C2``/``C3`` (complete /
# base / allocating constructor) and ``D0``/``D1``/``D2`` (deleting / complete /
# base destructor). These variants demangle to the *same* leaf yet are distinct
# exported symbols. A ``<ctor-dtor-name>`` is a real grammar production — it is
# NOT a length-prefixed ``<source-name>`` — so it must be located by parsing the
# nested-name's length-prefixed components, not by substring search (an ordinary
# identifier such as ``fooC1E`` would otherwise match).
_CTOR_DTOR_CODE_RE = re.compile(r"^(C[123]|D[012])E")


def _ctor_dtor_variant(symbol: str) -> str | None:
    """Return the Itanium ctor/dtor variant code (e.g. ``C1``) for a mangled
    name, or None when the symbol is not a constructor/destructor.

    Parses the ``_ZN`` nested-name: skips implicit-object cv/ref qualifiers,
    consumes the ``<len><identifier>`` ``<source-name>`` components (skipping any
    balanced ``I…E`` ``<template-args>`` block that follows a templated class
    name), then checks whether the remainder *begins* with a ``<ctor-dtor-name>``
    code. This distinguishes a real constructor (``_ZN6WidgetC1Ev`` -> ``C1``,
    ``_ZN3FooIiEC1Ev`` = ``Foo<int>::Foo()`` -> ``C1``, ``_ZN3FooI3ErrEC1Ev`` =
    ``Foo<Err>::Foo()`` -> ``C1``) from an ordinary member whose identifier
    merely contains the characters (``_ZN1A6fooC1EEv`` = ``A::fooC1E()`` ->
    None). Encodings this simple parser does not model (exotic template
    arguments) yield None — safe, since the only consequence is not suppressing
    a (rare) templated-ctor variant pair.
    """
    if not symbol.startswith("_ZN"):
        return None
    i = 3
    # Skip implicit-object cv-/ref-qualifiers (K const, V volatile, r restrict,
    # R lvalue-ref, O rvalue-ref).
    while i < len(symbol) and symbol[i] in "KVrRO":
        i += 1
    # Consume <prefix> components: <source-name> (<decimal-length><identifier>),
    # each optionally followed by a <template-args> block ``I…E``. A templated
    # class name (``_ZN3FooIiEC1Ev``) places the args before the ctor/dtor code.
    while i < len(symbol):
        if symbol[i].isdigit():
            i = _skip_source_name(symbol, i)
            if i < 0:
                return None  # malformed length — bail out
        elif symbol[i] == "I":
            i = _skip_template_args(symbol, i)
            if i < 0:
                return None  # unbalanced / unmodeled — bail out
        elif symbol[i] == "S":
            # A standard/standard-library substitution can open the prefix, e.g.
            # ``_ZNSt6vectorIiEC1Ev`` (St = std::) — consume it before the
            # source-name components so the ctor/dtor code is still found.
            i = _skip_substitution(symbol, i)
        elif symbol[i] == "B":
            # ABI-tag component ``B<source-name>`` on the class name, e.g.
            # ``_ZN3FooB1xC1Ev`` (Foo[abi:x]). Consume it so the ctor/dtor code
            # that follows is still reached.
            i += 1
            if i < len(symbol) and symbol[i].isdigit():
                i = _skip_source_name(symbol, i)
                if i < 0:
                    return None  # malformed ABI tag — bail out
            else:
                break  # not a well-formed ABI tag
        else:
            break
    m = _CTOR_DTOR_CODE_RE.match(symbol[i:])
    return m.group(1) if m else None


def _skip_source_name(symbol: str, i: int) -> int:
    """Skip an Itanium ``<source-name>`` (``<decimal-length><identifier>``)
    starting at ``symbol[i]``; return the index past it, or -1 if malformed."""
    j = i
    while j < len(symbol) and symbol[j].isdigit():
        j += 1
    remaining, length = len(symbol) - j, 0
    for c in symbol[i:j]:
        if (length := (length * 10) + (ord(c) - ord("0"))) > remaining:
            return -1
    return j + length


def _skip_substitution(symbol: str, i: int) -> int:
    """Skip an Itanium ``<substitution>`` starting at ``symbol[i]`` (an ``S``);
    return the index past it.

    Handles ``S_``, ``S<seq-id>_`` (seq-id is base-36 ``[0-9A-Z]``), and the
    special two-character abbreviations (``St`` std, ``Ss`` std::string, ``Sa``,
    ``Sb``, ``Si``, ``So``, ``Sd``). Consuming it whole keeps any digits in a
    seq-id from being misread as a ``<source-name>`` length.
    """
    n = len(symbol)
    i += 1  # consume 'S'
    if i < n and (symbol[i].isdigit() or symbol[i].isupper()):
        while i < n and symbol[i] != "_":
            i += 1
        return i + 1  # consume the closing '_'
    return i + 1  # special two-char abbreviation (St, Ss, …) or bare 'S_'


def _skip_template_args(symbol: str, i: int) -> int:
    """Skip a balanced Itanium ``<template-args>`` block (``I…E``) starting at
    ``symbol[i]`` (an ``I``); return the index past the matching ``E``, or -1.

    The block content must be parsed, not merely scanned for ``E``: a
    length-prefixed ``<source-name>`` argument (``Foo<Err>`` = ``...I3ErrE...``)
    contains an ``E`` *inside* its identifier that would otherwise close the
    block early, and an expr-primary literal (``Foo<5>`` = ``...ILi5EE...``)
    carries its own terminating ``E``. So source-names, substitutions, and
    literals are consumed whole; only ``I``/``N``/``F`` openers and their ``E``
    terminators move the nesting depth. Constructs this does not model yield -1.
    """
    n = len(symbol)
    depth = 0
    while i < n:
        c = symbol[i]
        if c.isdigit():
            # <source-name>: consume the identifier whole so its characters
            # (which may include E/I/N/F/L) are not read as structure.
            i = _skip_source_name(symbol, i)
            if i < 0:
                return -1
        elif c == "S":
            # <substitution>: consume whole so its digits are not mistaken for a
            # source-name length.
            i = _skip_substitution(symbol, i)
        elif c == "L":
            # <expr-primary> literal: ``L<type><value>E``. Scan to its own
            # terminating ``E`` literally — its value digits are not lengths.
            i += 1
            while i < n and symbol[i] != "E":
                i += 1
            if i >= n:
                return -1
            i += 1  # consume the literal's 'E'
        elif c in "INF":
            depth += 1
            i += 1
        elif c == "E":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return -1  # unbalanced


def _unqualified_name(symbol: str) -> str:
    """Extract the unqualified (leaf) function name from a symbol, robustly.

    Matching-safe alternative to ``demangle.base_name`` (which is documented
    display-only and mis-parses operators / templates). Demangles when a
    demangler is available, then, using *bracket-depth tracking* so that ``::``,
    ``(`` and spaces inside template arguments are ignored:

    * keeps the whole ``operator...`` token intact;
    * drops the parameter list;
    * drops the namespace/class qualifier (segment after the last top-level
      ``::``);
    * drops a leading return type (global function templates demangle as
      ``ret name<args>(...)``).

    Trailing template arguments are *kept*: a specialization like ``foo<int>``
    is a distinct ABI symbol from ``foo<long>``, so they must not collapse to a
    shared leaf (that would mis-report a specialization swap as a rename).
    """

    return _unqualified_name_of(demangle(symbol) or symbol)


def _unwrap_funcptr_declarator(s: str) -> str:
    """Unwrap a function-pointer/-reference *return* declarator so the real
    function name is visible.

    A C++ function that returns a function pointer demangles to declarator
    syntax — ``RET (*name(args))(fnptr-args)``, e.g. ``int (*foo<int>())()`` —
    where the first top-level ``(`` opens the declarator group, *not* the
    parameter list. Left as-is, leaf extraction would stop at that ``(`` and
    collapse the name to the return type. When ``s`` has this shape (the first
    top-level ``(`` is immediately followed by ``*``/``&``), return the inner
    ``name(args)`` so the normal leaf/parameter logic sees the real name;
    otherwise return ``s`` unchanged. Ordinary parameter lists (whose first char
    is a type or ``)``, never ``*``/``&`` at the very front) are left intact, as
    are functions that merely *take* a function-pointer parameter.
    """
    depth = 0  # <> template depth — ignore '(' inside template arguments
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            j = i + 1
            while j < len(s) and s[j] == " ":
                j += 1
            if j >= len(s) or s[j] not in "*&":
                return s  # ordinary parameter list, not a pointer declarator
            # Find the ')' matching this declarator-group '(' (bracket-aware).
            close = _match_declarator_group(s, i)
            if close is None:
                return s  # unbalanced — leave alone
            return s[i + 1 : close].lstrip("*& ")
    return s


def _match_declarator_group(s: str, open_idx: int) -> int | None:
    """Return the index of the ``)`` matching the ``(`` at *open_idx*, or None.

    Bracket-aware: ``(``/``)`` nested inside template arguments (``<...>``) do
    not affect the paren depth.
    """
    pdepth = 0
    tdepth = 0
    for k in range(open_idx, len(s)):
        c = s[k]
        if c == "<":
            tdepth += 1
        elif c == ">":
            tdepth = max(0, tdepth - 1)
        elif c == "(" and tdepth == 0:
            pdepth += 1
        elif c == ")" and tdepth == 0:
            pdepth -= 1
            if pdepth == 0:
                return k
    return None


def _unqualified_name_of(s: str) -> str:
    """Leaf-name core of ``_unqualified_name`` operating on an already-demangled
    (or raw, when no demangler is available) string. Split out so callers that
    need both the leaf and the parameter signature can demangle once."""
    s = _unwrap_funcptr_declarator(s)
    # An operator name encodes punctuation (``<<``, ``()``, ``[]``) that defeats
    # bracket tracking, so handle it first: keep everything from the ``operator``
    # token to the end. It is stable and symmetric, which is all the matcher
    # needs. Match ``operator`` only as a whole token so ordinary identifiers
    # that merely contain the substring (``cooperator``, ``operator_v1``) are
    # not misclassified.
    op = _OPERATOR_TOKEN_RE.search(s)
    if op is not None:
        return s[op.start() :].strip()
    s = _truncate_at_param_list(s)
    s = _after_last_top_level_scope(s).strip()
    s = _drop_leading_return_type(s)
    return s.strip()


def _truncate_at_param_list(s: str) -> str:
    """Drop everything from the parameter-list ``(`` at template depth 0 on."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            return s[:i]
    return s


def _after_last_top_level_scope(s: str) -> str:
    """Return the segment after the last ``::`` that sits at template depth 0."""
    depth = 0
    last = 0
    i = 0
    while i < len(s) - 1:
        ch = s[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == ":" and s[i + 1] == ":" and depth == 0:
            last = i + 2
            i += 2
            continue
        i += 1
    return s[last:]


def _drop_leading_return_type(s: str) -> str:
    """Drop a leading return type by taking the part after the last top-level
    space (e.g. ``void get<int>`` -> ``get<int>``)."""
    depth = 0
    sp = -1
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == " " and depth == 0:
            sp = i
    if sp != -1:
        return s[sp + 1 :]
    return s


def _strip_template_args(leaf: str) -> str:
    """Drop trailing template arguments from a leaf (``get<int>`` -> ``get``)."""
    if leaf.endswith(">"):
        depth = 0
        for i in range(len(leaf) - 1, -1, -1):
            if leaf[i] == ">":
                depth += 1
            elif leaf[i] == "<":
                depth -= 1
                if depth == 0:
                    return leaf[:i]
    return leaf


def _shared_affix_len(a: str, b: str) -> int:
    """Length of the longer of the common leading / common trailing run."""

    def common_prefix(x: str, y: str) -> int:
        n = 0
        for cx, cy in zip(x, y):
            if cx != cy:
                break
            n += 1
        return n

    return max(common_prefix(a, b), common_prefix(a[::-1], b[::-1]))


def _param_signature(symbol: str) -> str:
    """The parameter-list portion of a symbol (``foo(int)`` -> ``(int)``).

    Empty when there is no parameter list — a plain C symbol, a variable, or a
    mangled C++ symbol with no demangler available. A genuine rename or
    namespace relocation keeps the parameters; a parameter change is a distinct
    ABI symbol, so comparing this lets the gate reject ``foo(int)`` -> ``foo(long)``.
    """

    return _param_signature_of(demangle(symbol) or symbol)


def _param_signature_of(s: str) -> str:
    """Parameter-signature core of ``_param_signature`` operating on an
    already-demangled (or raw) string."""
    s = _unwrap_funcptr_declarator(s)
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            return s[i:]
    return ""


def _return_type_of(s: str) -> str:
    """The leading return type of a demangled name, or "" when there is none.

    A return type appears in demangled output only when it is part of the
    mangled ABI symbol — chiefly C++ function-template instantiations
    (``int foo<int>()``) — so for ordinary functions this is empty and the
    comparison in ``_plausible_rename`` is a no-op. It is the run before the
    last top-level space that precedes the (qualified) function name, with
    template ``<…>`` and ``::`` kept intact (``unsigned int foo<int>()`` ->
    ``unsigned int``; ``std::vector<int> bar()`` -> ``std::vector<int>``).
    """
    s = _unwrap_funcptr_declarator(s)
    if _OPERATOR_TOKEN_RE.search(s):
        return ""  # operator spellings carry no separable leading return type
    # Truncate at the parameter-list '(' at template depth 0.
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            s = s[:i]
            break
    # The return type, if any, is everything before the last top-level space.
    depth = 0
    sp = -1
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == " " and depth == 0:
            sp = i
    return s[:sp].strip() if sp != -1 else ""


@lru_cache(maxsize=65536)
def _rename_name_parse(name: str) -> tuple[str | None, str, str, str]:
    """Per-name pieces used by :func:`_plausible_rename`, demangled once.

    Returns ``(ctor_dtor_variant, leaf, param_signature, return_type)``. The
    name-similarity gate compares every removed symbol against every size-
    eligible added one, so the same name is parsed many times; caching the
    per-name derivation keeps that gate from re-demangling and re-parsing the
    same symbol on each pair (the dominant cost of rename detection on large
    ELF-only libraries). Bounded so it cannot grow without limit.
    """

    d = demangle(name) or name
    return (
        _ctor_dtor_variant(name),
        _unqualified_name_of(d),
        _param_signature_of(d),
        _return_type_of(d),
    )


def _plausible_rename(old_name: str, new_name: str) -> bool:
    """Whether two symbol names are similar enough to credibly be a rename.

    Compares the *unqualified* leaf names (see ``_unqualified_name``). A rename
    or namespace relocation keeps the leaf name (identical leaf, template
    arguments included) or a substantial common prefix/suffix token **and** the
    same parameter list; unrelated functions that merely share a byte size are
    rejected. Rejected cases include different methods under a common scope
    (``Class::get`` vs ``Class::set``), different template specializations of
    one name (``foo<int>`` vs ``foo<long>``), and same-name parameter changes
    (``foo(int)`` vs ``foo(long)``) — all of which are distinct ABI symbols.
    Used only to gate hash-less matches, where size alone is not evidence of
    identity.
    """
    if old_name == new_name:
        return True
    # Itanium ctor/dtor variants (C1/C2/C3, D0/D1/D2) demangle to the same leaf
    # but are distinct exported symbols. A pair is a plausible ctor/dtor rename
    # only when BOTH sides are the *same* variant (a genuine relocation keeps
    # it). Any mismatch is rejected: differing variants (complete-object C1 vs
    # base-object C2), and — crucially — a one-sided match where only one side
    # is a ctor/dtor (e.g. removed ctor ``A::A()`` vs added ordinary method
    # ``B::A()`` both reduce to leaf ``A()``), since a constructor ABI symbol
    # cannot be satisfied by an ordinary member. (Checked on the raw mangled
    # name, so it catches the case the demangler collapses to an identical leaf.)
    ov, a, pa, ra = _rename_name_parse(old_name)
    nv, b, pb, rb = _rename_name_parse(new_name)
    if (ov is not None or nv is not None) and ov != nv:
        return False
    # Undemangleable mangled names: when no demangler is available the leaf is
    # the raw Itanium spelling, whose shared boilerplate (``_ZN``, type codes,
    # …) would inflate the affix score and pair unrelated symbols. Demangling is
    # optional for this package, so treat such names conservatively — accept
    # only an exact match (rejected here, since removed/added names differ).
    if a.startswith("_Z") or b.startswith("_Z"):
        return a == b
    # Operator leaves include their parameters and share the literal
    # ``operator`` token; a destructor leaf (``~Widget``) shares the class name
    # with that class's constructor leaf (``Widget``). For both, an affix match
    # would pair genuinely different ABI functions (operator+ vs operator-, ctor
    # vs dtor), so accept only an exact leaf match.
    for leaf in (a, b):
        if _OPERATOR_TOKEN_RE.match(leaf) is not None or leaf.startswith("~"):
            return a == b
    # A rename/relocation preserves the full signature: parameters AND — for
    # the function templates whose mangling encodes it — the return type. A
    # change to either is a distinct ABI symbol (foo(int) -> foo(long), or
    # int foo<int>() -> long foo<int>()), not a rename. Ordinary (non-template)
    # functions demangle without a return type, so that check is a no-op there.
    sig_match = pa == pb and ra == rb
    if a == b:
        # Same unqualified name + template args: a rename only if the signature
        # also matches (else it is a signature change).
        return sig_match
    base_a = _strip_template_args(a)
    base_b = _strip_template_args(b)
    # Same base name but different leaves means the template arguments differ:
    # distinct specializations are distinct ABI symbols, not a rename — a
    # consumer of foo<int> still fails to link against foo<long>.
    if base_a == base_b:
        return False
    return sig_match and _shared_affix_len(base_a, base_b) >= _RENAME_MIN_SHARED_AFFIX


def _fingerprints_from_elf(snap: AbiSnapshot) -> dict[str, FunctionFingerprint]:
    """Build FunctionFingerprint dict from ELF metadata (size-only, no code hash).

    Uses ElfSymbol.size from .dynsym to create fingerprints for rename matching.
    Includes FUNC, IFUNC, and NOTYPE symbols — matching dumper.py's
    ``exported_dynamic_funcs`` categorization for elf_only_mode snapshots.
    Code hashing requires the binary file and is handled by
    ``binary_fingerprint.compute_function_fingerprints()`` when a path is available.
    """
    if snap.elf is None:
        return {}
    filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(snap)
    result: dict[str, FunctionFingerprint] = {}
    for sym in snap.elf.symbols:
        if sym.sym_type not in _FUNC_LIKE_TYPES:
            continue
        if not is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
        ):
            continue
        if sym.size < _MIN_SYMBOL_SIZE:
            continue
        result[sym.name] = FunctionFingerprint(
            name=sym.name,
            size=sym.size,
            code_hash="",  # no code hash from metadata alone
        )
    return result


@registry.detector(
    "fingerprint_renames",
    requires_support=lambda o, n: (
        o.elf is not None
        and n.elf is not None
        and (o.elf_only_mode or n.elf_only_mode),
        "requires ELF metadata in elf_only_mode",
    ),
)
def _diff_fingerprint_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect likely function renames using binary fingerprint matching.

    Only runs in elf_only_mode (stripped binaries without debug info or headers),
    where rename churn is most problematic.  Uses function code size from
    ELF .dynsym to find removed+added pairs that likely represent the same
    function under a different name.

    Fires when *either* snapshot is elf_only — the rename churn problem exists
    even if only one side is stripped.
    """
    changes: list[Change] = []

    old_fps = _fingerprints_from_elf(old)
    new_fps = _fingerprints_from_elf(new)

    if not old_fps or not new_fps:
        return changes

    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    old_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(
        old
    )
    new_filter_transitive_runtime_symbols = _should_filter_transitive_runtime_symbols(
        new
    )
    old_exported_funcs = {
        sym.name
        for sym in (old_elf.symbols if old_elf is not None else [])
        if sym.sym_type in _FUNC_LIKE_TYPES
        and is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=old_filter_transitive_runtime_symbols,
        )
    }
    new_exported_funcs = {
        sym.name
        for sym in (new_elf.symbols if new_elf is not None else [])
        if sym.sym_type in _FUNC_LIKE_TYPES
        and is_abi_relevant_elf_symbol(
            sym.name,
            filter_transitive_runtime_symbols=new_filter_transitive_runtime_symbols,
        )
    }
    retained_exported_funcs = old_exported_funcs & new_exported_funcs
    old_fps = {
        name: fp for name, fp in old_fps.items() if name not in retained_exported_funcs
    }
    new_fps = {
        name: fp for name, fp in new_fps.items() if name not in retained_exported_funcs
    }
    if not old_fps or not new_fps:
        return changes

    # Matches in this path are hash-less (size-only), inferred from symbol size
    # alone since _fingerprints_from_elf has no code bytes. Pass the name-
    # similarity predicate into the matcher so it participates in candidate
    # *selection*: a coincidental same-size symbol can neither be reported as a
    # rename nor greedily consume a partner that a plausible rename should claim.
    # P11: one batched c++filt warm so the rename gate's demangle() hits cache, not per-symbol forks.
    demangle_batch([n for n in (*old_fps, *new_fps) if n.startswith("_Z")])
    candidates = match_renamed_functions(
        old_fps, new_fps, name_filter=_plausible_rename
    )
    for c in candidates:
        conf_pct = int(c.confidence * 100)
        changes.append(
            make_change(
                ChangeKind.FUNC_LIKELY_RENAMED,
                symbol=c.old_name,
                name=str(conf_pct),
                detail=str(c.old_fingerprint.size),
                old=c.old_name,
                new=c.new_name,
            )
        )

    if candidates:
        _log.info(
            "Fingerprint rename detection: %d candidate(s) found",
            len(candidates),
        )

    return changes


# ── Batch rename / namespace-move roll-up (SYMBOL_RENAMED_BATCH) ──────────
#
# Moved here from ``diff_symbols`` (which sits at the 2000-line hard cap) so
# both batch shapes live next to the rest of the rename machinery.


def _is_destructor_leaf(name: str) -> bool:
    """True when *name*'s own leaf component names a destructor.

    Splitting on the *last* ``"::"`` is enough for this predicate: a
    destructor's ``~`` is always the first character of the leaf component,
    and any ``"::"`` inside a template argument only ever appears *before*
    the leaf's ``~`` would, never between it and the end.
    """
    return name.rsplit("::", 1)[-1].startswith("~")


def _prefix_ends_at_a_name_boundary(prefix: str) -> bool:
    """True when *prefix* is a plausible *prepended* naming prefix.

    A batch rename prepends a namespace or library prefix to an existing
    leaf name, so the added text ends where a name legitimately starts: at a
    scope separator (``ns::foo``) or an underscore (``mylib_foo``). Anything
    else means the "prefix" cuts into the middle of an identifier or is a
    declarator sigil rather than a name — the ``~`` of a destructor being the
    case this rule exists for (``Wrapper`` -> ``~Wrapper`` is not a rename of
    ``Wrapper``, it is a different declaration that happens to end with the
    same spelling).
    """
    return prefix.endswith(("::", "_"))


def find_prefix_rename_pairs(
    removed: set[str],
    added: set[str],
    old_map: Mapping[str, Function],
    new_map: Mapping[str, Function],
) -> list[tuple[str, str]]:
    """Return (old_name, new_name) pairs where new_name has a common prefix added to old_name.

    The match condition is ``a_name.endswith(r_name)`` with ``a_name`` strictly
    longer (a prefix was prepended). The old ``endswith("_" + r_name)`` branch
    was redundant — any name ending with ``"_" + r_name`` already ends with
    ``r_name``. To avoid the O(removed × added) cross-product, index the added
    names *reversed* so the suffix test becomes a prefix lookup: a binary search
    locates the contiguous block of reversed added names that start with the
    reversed removed name. Both ``removed`` and the reversed index are iterated
    in sorted order, so the result is deterministic.

    Two gates keep the raw suffix test from manufacturing pairs out of
    unrelated declarations that merely share a trailing spelling:

    * the two names must agree on being a destructor
      (:func:`_is_destructor_leaf`), and
    * the prepended text must end at a name boundary
      (:func:`_prefix_ends_at_a_name_boundary`).

    Either one alone rejects the reported ``Wrapper`` -> ``~Wrapper`` /
    ``graph`` -> ``~graph`` noise; both are kept because they state
    independent facts. The destructor rule is about *what the two
    declarations are* and holds no matter how the prefix is spelled (it also
    rejects ``~Foo`` -> ``ns::Foo``, where the prefix is perfectly
    well-formed); the boundary rule is about *where the added text stops*
    and rejects mid-identifier cuts that have nothing to do with
    destructors.
    """
    rev_index = sorted(
        (new_map[a_sym].name[::-1], new_map[a_sym].name) for a_sym in added
    )
    rev_keys = [k for k, _ in rev_index]
    pairs: list[tuple[str, str]] = []
    for r_sym in sorted(removed):
        r_name = old_map[r_sym].name
        rk = r_name[::-1]
        i = bisect.bisect_left(rev_keys, rk)
        while i < len(rev_keys) and rev_keys[i].startswith(rk):
            a_name = rev_index[i][1]
            if len(a_name) > len(r_name):
                prefix = a_name[: len(a_name) - len(r_name)]
                if _is_destructor_leaf(a_name) == _is_destructor_leaf(
                    r_name
                ) and _prefix_ends_at_a_name_boundary(prefix):
                    pairs.append((r_name, a_name))
                break
            i += 1
    return pairs


def emit_prefix_batch_rename(rename_pairs: list[tuple[str, str]]) -> list[Change]:
    """Emit a SYMBOL_RENAMED_BATCH change if all pairs share a single common prefix."""
    if len(rename_pairs) < 2:
        return []
    prefixes = {
        new_name[: new_name.rfind(old_name)] for old_name, new_name in rename_pairs
    }
    if len(prefixes) != 1:
        return []
    prefix = prefixes.pop()
    pair_desc = ", ".join(f"{o} → {n}" for o, n in rename_pairs[:5])
    if len(rename_pairs) > 5:
        pair_desc += f", ... ({len(rename_pairs)} total)"
    return [
        make_change(
            ChangeKind.SYMBOL_RENAMED_BATCH,
            symbol=f"batch_rename:{prefix}*",
            name=prefix,
            detail=f"{len(rename_pairs)} symbols ({pair_desc})",
            old_value=", ".join(o for o, _ in rename_pairs),
            new_value=", ".join(n for _, n in rename_pairs),
        )
    ]


#: Sentinel standing in for the one scope component a candidate namespace
#: substitution replaces. A real Itanium component can never contain a NUL,
#: so it cannot collide with one.
_MASKED = "\x00"


def _qualified_key_scope_components(key: str) -> list[str] | None:
    """Scope chain for a header-tier snapshot key that was never mangled.

    Many real findings this detector needs to see are keyed by something
    other than a mangled symbol — a header-only (L2) backend can leave
    ``Function.mangled`` as a *qualified display name* instead, and
    :func:`itanium_scope_components`/:func:`msvc_scope_components` both
    correctly return ``None`` for those (they parse mangling grammars, not
    qualified text). Two shapes are recognized here, both produced by
    ``dumper_castxml`` when castxml omits a ctor/dtor's real mangled name
    (see ``SYNTHETIC_CTOR_KEY_PREFIX``/``is_synthetic_dtor_key``):

    * ``__abicheck_ctor__<scope>(<params>)`` — a synthesized constructor
      identity. The ``<scope>`` is a real, qualified class path
      (``"tbb::detail::d1::graph"``); the parameter signature is stripped
      before splitting.
    * ``~<scope>`` — a synthesized destructor identity, the same qualified
      class path with a leading ``~``.

    For both, a synthetic trailing leaf (``"{ctor}"``/``"{dtor}"``) is
    appended after splitting the scope on ``"::"`` — mirroring the shape
    :func:`itanium_scope_components` already produces for a *real* mangled
    ctor/dtor (``_ZN1CC1Ev -> ["C", "{ctor}"]``, never the class name
    itself as the leaf). This keeps the "which index is the leaf, and
    therefore excluded from namespace substitution" position semantics
    identical regardless of whether the chain came from a real mangling or
    from this fallback — without it, a synthesized ctor/dtor key would let
    :func:`find_namespace_move_groups` treat the class's own name as a
    substitutable "namespace segment", which a real mangled ctor/dtor never
    permits.

    Any other key containing ``"::"`` (a header-tier function with no
    mangled name at all, but a real ``"ns::Class::member"`` display name)
    is split as-is via :func:`qualified_name_scope_components` — its own
    last component already is the leaf, same as a plain mangled free
    function's single component is.

    A key with neither a recognized synthetic prefix nor any ``"::"`` at
    all (a bare, unqualified name — the plain-C-linkage fallback) carries
    no scope to substitute, so it returns ``None`` the same as an
    unmodelled mangled form would.
    """
    if is_synthetic_ctor_key(key):
        scope = strip_trailing_top_level_parameter_list(
            key[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
        )
        comps = qualified_name_scope_components(scope)
        return [*comps, "{ctor}"] if comps else None
    if is_synthetic_dtor_key(key):
        comps = qualified_name_scope_components(key[1:])
        return [*comps, "{dtor}"] if comps else None
    if "::" not in key:
        return None
    return qualified_name_scope_components(key)


def _scope_components(mangled: str) -> tuple[list[str], frozenset[int]] | None:
    """Return (*mangled*'s scope chain, template-bearing component indices), or None.

    Itanium first, MSVC second — the two prefixes (``_Z``/``__Z`` vs. ``?``)
    are mutually exclusive, so trying both in order is unambiguous, and it is
    the same order :func:`diff_cxx_rules.owner_class_of` already uses. When
    neither recognizes *mangled* as a mangling at all — a header-tier key
    that was never mangled in the first place, see
    :func:`_qualified_key_scope_components` — fall back to parsing it as
    already-qualified text. A component list shorter than two carries no
    namespace to substitute, so it is reported as "no usable chain" rather
    than as a one-element chain, regardless of which of the three parsers
    produced it.

    The second element identifies which components embed a template-
    argument list, so a caller can exclude them from ever being treated as
    a bare namespace/class segment (see the two masking loops in
    :func:`find_namespace_move_groups`). For an Itanium mangling this is
    the *exact* structural answer from
    :func:`diff_cxx_rules.itanium_scope_components_with_template_positions`
    — not a guess back out of the assembled text, which is unsound (Codex
    review, fresh evidence: a text-based heuristic misreads an ordinary
    identifier like ``"ICE"`` as a template block by coincidental
    spelling). For MSVC this is always empty:
    :func:`diff_cxx_rules.msvc_scope_components` already rejects the whole
    symbol outright when any component starts with the template marker
    ``?$``, so a template-bearing MSVC component never reaches here. Only
    the qualified-name/header-tier-fallback shape has no parser to ask, so
    it falls back to :func:`diff_cxx_rules.component_embeds_template_args`'s
    text-based ``<...>`` check — exact for that shape, since a pretty-
    printed spelling never coincidentally contains a raw Itanium encoding.
    """
    itanium = itanium_scope_components_with_template_positions(mangled)
    if itanium is not None:
        comps, template_positions = itanium
        return (comps, template_positions) if len(comps) >= 2 else None
    fallback = msvc_scope_components(mangled) or _qualified_key_scope_components(
        mangled
    )
    if fallback is None or len(fallback) < 2:
        return None
    fallback_template_positions = frozenset(
        i for i, c in enumerate(fallback) if component_embeds_template_args(c)
    )
    return fallback, fallback_template_positions


def find_namespace_move_groups(
    removed: set[str],
    added: set[str],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Group removed/added symbols by a *shared namespace-segment substitution*.

    A namespace move (oneTBB 2022's flow graph: every ``tbb::detail::d1::X``
    became ``tbb::detail::d2::X``) is neither a prefix nor a suffix of the old
    name, so :func:`find_prefix_rename_pairs` structurally cannot see it —
    every moved symbol was reported as an unpaired ``func_removed`` next to an
    unpaired ``func_added`` with nothing recording that the two halves are the
    same declaration under a new scope.

    Matching is on the *mangled* names' parsed scope chains
    (:func:`_scope_components`), not demangled text: the chain is exactly
    the namespace/class components plus the leaf, ctor/dtor markers already
    normalized, so "differs in exactly one component" is about scoping, not
    string spelling. A pair is recorded when the two chains have the same
    length, differ at exactly one position, and that position is **not**
    the leaf (a differing leaf is a renamed *declaration* -- the prefix
    shape above's job). Pairs are grouped by the ``(old_segment,
    new_segment)`` substitution they support, so unrelated coincidental
    one-component differences never accumulate into one group; the caller
    requires 2+ supporting pairs before reporting anything. Deliberately
    *not* keyed on position too: the same namespace rename can legitimately
    show up at different depths, and requiring an equal index would split
    one real move into several under-supported groups.

    Returns ``{(old_segment, new_segment): [(old_qualified, new_qualified)]}``
    with deterministic ordering (both sides iterated sorted).

    Known, accepted limitation (Codex review, fresh evidence): matching is
    on the *scope chain only* -- :func:`_scope_components` deliberately
    discards a function's own parameter-type signature, so two overloads of
    the same declaration share an identical chain. The many-to-one
    rejection above therefore can't distinguish a genuine collision (two
    unrelated old namespaces both proposing themselves as the source of one
    target) from a legitimate consolidation (two old namespaces
    contributing *different overloads* of the same name to one new
    namespace) -- both look identical once parameter types are stripped, so
    the rejection fires on the overload case too, even though the mangled
    symbols themselves carry the disambiguating suffix. Not a new gap: the
    primitive was already signature-blind before this check existed; the
    check just makes an already-ambiguous shape REJECT (individual
    ``func_removed``/``func_added`` still reported) rather than arbitrarily
    ACCEPT a pairing that might be wrong -- the same false-negative-over-
    false-positive default this module's other guards use. A correct fix
    needs real parameter-signature matching threaded through the whole
    primitive (``_scope_components``, ``added_index``, candidate
    resolution, the key itself) -- a genuine redesign, not a scoped patch.
    """
    added_index: dict[tuple[str, ...], list[tuple[str, list[str]]]] = {}
    for a_sym in sorted(added):
        resolved = _scope_components(a_sym)
        if resolved is None:
            continue
        comps, template_positions = resolved
        for i in range(len(comps) - 1):
            # A component that itself carries a template-argument list is not
            # a bare namespace/class segment -- it is an instantiation whose
            # *spelling* can differ between old and new purely because one of
            # its template arguments names a declaration that moved (e.g.
            # ``concurrent_priority_queue<tbb::detail::d1::graph_task *, ...>``
            # vs. ``...d2::graph_task...``, where the enclosing scope of
            # ``concurrent_priority_queue`` itself never changed). Treating
            # such a component as "the segment that changed" fabricates a
            # spurious, redundant substitution group keyed on the whole
            # instantiation text instead of on the real namespace segment --
            # which the un-instantiated symbol that actually names the moved
            # type already supplies evidence for. See
            # ``_scope_components``'s own docstring: for a real Itanium
            # mangling *i* is checked against the EXACT structural answer
            # (``template_positions``), never guessed back out of the
            # assembled text -- a text-based guess is unsound (Codex review,
            # fresh evidence: ordinary identifiers like ``"ICE"`` coincide
            # with a balanced raw template-args spelling), which is exactly
            # why this primitive reasons about scope chains via a per-
            # position flag rather than re-deriving it here.
            if i in template_positions:
                continue
            masked = tuple(comps[:i]) + (_MASKED,) + tuple(comps[i + 1 :])
            added_index.setdefault(masked, []).append((a_sym, comps))

    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}

    # Phase 1: for every (removed symbol, masking position) pair, resolve at
    # most one unambiguous-on-the-target-side candidate (the existing
    # one-to-many rejection below), and record which OLD segment value it
    # came from under that masked context. This also builds
    # `masked_to_old_segments`, the reciprocal (many-to-one) signal Phase 2
    # needs: a masked context claimed by more than one distinct old segment
    # value means several different removed namespaces are each proposing
    # themselves as the source of the SAME added symbol -- e.g. removed
    # old1::{f,g}/old2::{f,g} vs. added only new::{f,g}: `old1::f` and
    # `old2::f` masked at their differing position both resolve to the
    # single candidate `new::f` (no one-to-many ambiguity on the target
    # side at all), yet there is no evidence which of old1/old2 actually
    # moved -- the other was simply deleted. Without this check both
    # `old1 -> new` and `old2 -> new` would independently clear the 2+-pair
    # threshold and emit two contradictory SYMBOL_RENAMED_BATCH findings
    # (Codex review, fresh evidence).
    entries: list[tuple[tuple[str, ...], list[str], int, list[str]]] = []
    masked_to_old_segments: dict[tuple[str, ...], set[str]] = {}
    # `added_id_to_removed_symbols`/`removed_id_to_added_symbols`: the two
    # cross-position collision signals Phase 2 below needs, built from
    # EVERY raw candidacy below -- including one a masking position's own
    # LOCAL one-to-many check (a few lines down) is about to discard
    # entirely from `entries` (Codex review, fresh evidence: an earlier
    # revision built these two dicts only from `entries`, i.e. only from
    # candidacies that already survived the local filter -- removed
    # `p1::old::{f,g}` and `new::p2::{f,g}`, added `new::old::{f,g}` and
    # `x::old::{f,g}`: `p1::old::f` masked at position 0 matches BOTH
    # `new::old::f` and `x::old::f`, so the local one-to-many check
    # discards that entry before it ever reaches `entries` -- but
    # discarding it as unusable EVIDENCE FOR A SPECIFIC PAIRING does not
    # mean `new::old::f` stops being a real, live alternative explanation
    # for `p1::old::f`. `new::p2::f` (masking position 1) then matched
    # `new::old::f` uniquely and, with `p1::old::f`'s own claim invisible
    # to the tracking built only from `entries`, appeared uncontested --
    # wrongly emitting a `p2 -> old` batch even though `p1::old::f` is
    # just as plausibly `new::old::f`'s real source). Built here, in the
    # SAME loop that computes `candidates`, before the local filter runs,
    # so every raw candidacy -- ambiguous-at-its-own-position or not --
    # counts as evidence contesting/claiming its target, matching this
    # function's own stated false-negative-over-false-positive default.
    added_id_to_removed_symbols: dict[str, set[str]] = {}
    removed_id_to_added_symbols: dict[str, set[str]] = {}
    # Every raw (old_segment, new_segment) key a removed symbol could propose
    # at ANY masking position, even one `distinct_targets` below locally
    # rejects (so it never gets an `entries` row) -- the global tie-break
    # further down still needs to see it, or a key only reachable through a
    # locally-rejected position looks uncontested when another symbol's raw
    # candidacy at that key would reveal a genuine tie (Codex review).
    raw_symbol_keys: dict[str, set[tuple[str, str]]] = {}
    # A repeated bare segment can make two DIFFERENT added declarations
    # collapse to the identical key for the SAME removed symbol (Codex
    # review): removed "old::old::f" against added "new::old::f" (position
    # 0) AND "old::new::f" (position 1) both key as ("old", "new") -- a
    # shared key must not be treated as agreement. Keyed by (symbol_id, key), not merged into `raw_symbol_keys`, so a single-target key is unaffected.
    raw_symbol_key_targets: dict[tuple[str, tuple[str, str]], set[str]] = {}
    # Added-side mirrors of `raw_symbol_keys`/`raw_symbol_key_targets`
    # (item 6 follow-up): the corroboration logic Phase 2 already applies
    # when ONE removed symbol has multiple candidate added targets
    # (`removed_id_to_added_symbols`) was never extended to the symmetric
    # case -- ONE added symbol claimed by multiple distinct removed
    # identities (`added_id_to_removed_symbols`) was rejected outright,
    # unconditionally, with no attempt to see whether one of the competing
    # claims is corroborated elsewhere in the same comparison while the
    # other is an isolated coincidence. Real-world effect: a genuine
    # namespace-move batch that is overwhelmingly well-supported can still
    # lose a handful of its own members purely because an unrelated,
    # uncorroborated removed identity happens to coincidentally collide
    # with one of the batch's added targets -- those members' underlying
    # evidence is silently dropped from the group's supporting-pairs list
    # even though the roll-up they belong to is real (`emit_namespace_move_
    # batches`'s "additive" per-symbol removals/additions still cover the
    # symbol elsewhere, but the batch's own supporting-pairs count and
    # description under-represent it). `raw_added_keys`/
    # `raw_added_key_targets` are keyed by ADDED identity exactly the way
    # `raw_symbol_keys`/`raw_symbol_key_targets` are keyed by removed
    # identity, so an analogous corroboration test can run in this
    # direction too -- see Phase 2 below for why it cannot be the exact
    # same test, just mirrored.
    raw_added_keys: dict[str, set[tuple[str, str]]] = {}
    raw_added_key_targets: dict[tuple[str, tuple[str, str]], set[str]] = {}
    for r_sym in sorted(removed):
        r_resolved = _scope_components(r_sym)
        if r_resolved is None:
            continue
        r_comps, r_template_positions = r_resolved
        symbol_id = "::".join(r_comps)
        for i in range(len(r_comps) - 1):
            # Mirrors the identical skip in the `added_index` build above: a
            # templated component (pretty-printed, or the exact structural
            # answer for a raw-Itanium-encoded one) can never be treated as
            # *the* differing namespace/class segment.
            if i in r_template_positions:
                continue
            masked = tuple(r_comps[:i]) + (_MASKED,) + tuple(r_comps[i + 1 :])
            candidates = added_index.get(masked, [])
            for _cand_sym, cand_comps in candidates:
                if r_comps[i] == cand_comps[i]:
                    continue
                cand_id = "::".join(cand_comps)
                added_id_to_removed_symbols.setdefault(cand_id, set()).add(symbol_id)
                removed_id_to_added_symbols.setdefault(symbol_id, set()).add(cand_id)
                rkey = (r_comps[i], cand_comps[i])
                raw_symbol_keys.setdefault(symbol_id, set()).add(rkey)
                raw_symbol_key_targets.setdefault((symbol_id, rkey), set()).add(cand_id)
                raw_added_keys.setdefault(cand_id, set()).add(rkey)
                raw_added_key_targets.setdefault((cand_id, rkey), set()).add(symbol_id)
            # Reject an AMBIGUOUS substitution at the source (Codex review,
            # fresh evidence): when the SAME masked context (this removed
            # symbol's scope chain with position `i` blanked out) matches
            # MORE THAN ONE added symbol -- e.g. removed old1::{f,g}/
            # old2::{f,g} vs. added new1::{f,g}/new2::{f,g}, where
            # `old1::f` masked at its differing position matches BOTH
            # `new1::f` and `new2::f` -- there is no way to tell which
            # candidate is the real rename target for this symbol, so
            # neither is recorded. Deliberately LOCAL (per masked context),
            # not a global "does this bare segment string ever appear with
            # two different targets anywhere" check: two genuinely
            # independent, unambiguous moves that happen to reuse the same
            # bare segment NAME in different scopes (`p1::old::{f,g} ->
            # p1::new1::{f,g}` alongside the unrelated `p2::old::{h,i} ->
            # p2::new2::{h,i}`) must still both be reported -- each
            # individual masked lookup there has exactly one candidate, so
            # neither is ambiguous by this test, even though the bare
            # segment "old" ends up mapped to two different bare targets
            # across the two unrelated groups. The same
            # false-negative-over-false-positive default this codebase's
            # other ambiguity guards use (see e.g. type_reachability.py's
            # collision handling) -- applied at the granularity the
            # ambiguity actually exists at.
            #
            # Distinct by TARGET SEGMENT VALUE, not by candidate count: the
            # same class can legitimately appear twice in `added` under two
            # different string identities that parse to the identical
            # scope-component list -- a real mangled ctor symbol and a
            # header-tier synthetic ctor key for the SAME move both
            # normalize to e.g. ["tbb","detail","d2","graph","{ctor}"].
            # That is not ambiguity (both candidates agree on the target),
            # only genuinely differing target segments are.
            distinct_targets = {a_comps[i] for _a_sym, a_comps in candidates}
            if len(distinct_targets) != 1:
                continue
            _a_sym, a_comps = candidates[0]
            if r_comps[i] == a_comps[i]:
                continue
            masked_to_old_segments.setdefault(masked, set()).add(r_comps[i])
            entries.append((masked, r_comps, i, a_comps))

    # `added_id_to_removed_symbols`/`removed_id_to_added_symbols` were
    # already fully built above, from every raw candidacy at every masking
    # position, independent of whether that position's own local
    # one-to-many check passed (see their declaration above for the full
    # history of why building them only from `entries`, or filtering by
    # `masked_to_old_segments`, is unsound).
    #
    # `added_id_to_removed_symbols` answers: is this added declaration
    # claimed by more than one distinct removed identity, whether they
    # collide at the SAME masking position (`masked_to_old_segments` above)
    # or at DIFFERENT ones. Tracked by distinct CLAIMING REMOVED-SYMBOL
    # IDENTITY, not by substitution key text, since two different removed
    # originals can spell the identical key.
    #
    # `removed_id_to_added_symbols` answers the symmetric question: does
    # this removed symbol resolve to more than one distinct added
    # declaration across its masking positions -- two mutually exclusive
    # substitutions backed by the identical removed symbol.

    # Phase 2: record only the entries whose masked context was claimed by
    # exactly one distinct old segment value (the position-scoped
    # many-to-one rejection), whose added declaration was claimed by
    # exactly one distinct removed-symbol identity (the cross-position
    # many-to-one rejection), AND whose removed symbol was itself resolved
    # to exactly one distinct added declaration across all its masking
    # positions (the symmetric cross-position one-to-many rejection) --
    # then apply the pre-existing per-symbol/per-key/per-pair dedup exactly
    # as before.
    seen_here: dict[str, set[tuple[str, str]]] = {}
    # Tracks which (old_qualified, new_qualified) pairs have already been
    # recorded per substitution key, so the SAME declaration reported under
    # two different `removed` string identities (a real mangled symbol and
    # a header-tier synthetic key that normalize to the identical
    # scope-component list -- see the co-matching comment above) is only
    # ever counted once toward the 2+-pairs support threshold
    # (Codex review, fresh evidence: without this, one moved declaration
    # reported both ways produced two identical list entries, passing
    # emit_namespace_move_batches' threshold and reporting a false
    # BREAKING batch for what was really a single symbol).
    recorded_pairs: dict[tuple[str, str], set[tuple[str, str]]] = {}
    # Cross-position ambiguity (removed_id_to_added_symbols[symbol_id] > 1)
    # need not be a dead end: one candidate key independently reused by a
    # DIFFERENT removed symbol is real corroborating evidence the other
    # candidate lacks (code-review item 6, "rank by global support"). Support
    # is scored only from `entries` (locally-confirmed); the competing keys
    # come from `raw_symbol_keys` instead, so a key raised at a
    # locally-ambiguous position still counts as a competitor even without
    # its own entry. A genuine tie (both/neither corroborated) still rejects.
    #
    # The SAME idea -- don't let an unresolved-but-uncorroborated coincidence
    # veto a well-supported pairing -- also applies on the added side (item 6
    # follow-up): `added_id_to_removed_symbols[added_id] > 1` -- this added
    # declaration is claimed by more than one distinct removed identity --
    # used to be rejected outright, unconditionally, unlike its removed-side
    # mirror just above. That asymmetry silently dropped a well-supported
    # namespace-move batch member whenever an unrelated, uncorroborated
    # removed identity happened to coincidentally collide with one of the
    # batch's own added targets: the real member's evidence never reached
    # `groups[key]` even though the substitution it supports is otherwise
    # heavily corroborated elsewhere in the same comparison.
    #
    # This CANNOT reuse the removed-side test's exact shape, though: there,
    # every competing key belongs to the SAME symbol (its own alternate
    # masking positions), so subtracting that one `symbol_id` from a key's
    # supporter set is enough to ask "does anyone ELSE back this option".
    # Here, each competing key belongs to a DIFFERENT removed identity, and
    # that competitor may itself never have resolved to any single key at
    # all -- exactly the round-4 scenario `TestFindNamespaceMoveGroupsRetains
    # LocallyAmbiguousCandidatesGlobally` pins (`p1::old::f` is itself stuck
    # between two candidates at its only masking position and so never gets
    # an `entries` row for either): such a competitor is a live, irreducible
    # threat, not a dismissible coincidence, and must still veto -- there is
    # no key of its own whose support could be checked. Only a competitor
    # that DID resolve to its own key(s) (via `entries`) can be assessed and
    # potentially dismissed, and only when NONE of ITS resolved keys carry
    # support from anyone other than that competitor itself.
    key_support: dict[tuple[str, str], set[str]] = {}
    symbol_entries_keys: dict[str, set[tuple[str, str]]] = {}
    for masked, r_comps, i, a_comps in entries:
        sid = "::".join(r_comps)
        k = (r_comps[i], a_comps[i])
        key_support.setdefault(k, set()).add(sid)
        symbol_entries_keys.setdefault(sid, set()).add(k)

    def _added_side_competitor_is_dismissible(competitor_id: str) -> bool:
        resolved_keys = symbol_entries_keys.get(competitor_id)
        if not resolved_keys:
            # Never resolved to any key of its own -- an irreducible,
            # unresolved rival that cannot be ruled out. Always vetoes.
            return False
        # Resolved, but only dismissible if NONE of its own keys are
        # independently backed by anyone besides itself.
        return not any(
            key_support.get(ok, set()) - {competitor_id} for ok in resolved_keys
        )

    for masked, r_comps, i, a_comps in entries:
        if len(masked_to_old_segments[masked]) != 1:
            continue
        added_id = "::".join(a_comps)
        symbol_id = "::".join(r_comps)
        key = (r_comps[i], a_comps[i])
        if len(added_id_to_removed_symbols[added_id]) != 1:
            # This key itself may be reachable from >1 distinct removed
            # symbol for this SAME added identity (the added-side mirror of
            # raw_symbol_key_targets's repeated-segment collision, just
            # below) -- reject before considering corroboration.
            if len(raw_added_key_targets[(added_id, key)]) != 1:
                continue
            if not key_support[key] - {symbol_id}:
                continue
            competitors = added_id_to_removed_symbols[added_id] - {symbol_id}
            if not all(_added_side_competitor_is_dismissible(c) for c in competitors):
                continue
        if len(removed_id_to_added_symbols[symbol_id]) != 1:
            # This key itself may resolve to >1 distinct target for this
            # symbol (see raw_symbol_key_targets's docstring) -- reject
            # before considering corroboration.
            if len(raw_symbol_key_targets[(symbol_id, key)]) != 1:
                continue
            if not key_support[key] - {symbol_id}:
                continue
            other_keys = raw_symbol_keys[symbol_id] - {key}
            if any(key_support.get(ok, set()) - {symbol_id} for ok in other_keys):
                continue
        symbol_seen = seen_here.setdefault(symbol_id, set())
        if key in symbol_seen:
            continue
        symbol_seen.add(key)
        pair = (symbol_id, "::".join(a_comps))
        already_recorded = recorded_pairs.setdefault(key, set())
        if pair in already_recorded:
            continue
        already_recorded.add(pair)
        groups.setdefault(key, []).append(pair)
    return groups


def _declaring_entity(qualified: str) -> str:
    """Collapse a synthesized ``{ctor}``/``{dtor}`` leaf marker so both
    facets of one class -- its constructor and its destructor -- count as
    the SAME declaring entity for support-counting purposes.

    Every class, real or coincidentally paired, contributes exactly a
    ``{ctor}`` pair and a ``{dtor}`` pair once *any* one-component
    substitution happens to line its removed and added mangled names up
    (see :func:`emit_namespace_move_batches`) -- so two such pairs are not
    two independent pieces of evidence, they are one class counted twice.
    An ordinary (non-ctor/dtor) leaf is returned unchanged: two distinct
    member functions of the same class are still two distinct declarations.
    """
    for suffix in ("::{ctor}", "::{dtor}"):
        if qualified.endswith(suffix):
            return qualified[: -len(suffix)]
    return qualified


def emit_namespace_move_batches(
    groups: dict[tuple[str, str], list[tuple[str, str]]],
) -> list[Change]:
    """Emit one SYMBOL_RENAMED_BATCH per namespace substitution supported by
    2+ pairs from 2+ *distinct declaring entities*.

    ``len(pairs) >= 2`` alone gives zero protection at class granularity: an
    unrelated deleted class and an unrelated added class that happen to
    share an enclosing scope always contribute exactly two pairs -- the
    class's compiler-generated constructor and destructor -- to whatever
    substitution key their names happen to mask into, regardless of
    whether the class actually moved namespaces or was simply deleted while
    an unrelated, differently-named class was simultaneously added in the
    same scope. Neither :func:`find_namespace_move_groups`'s per-position
    ambiguity guards catches this: there is exactly one candidate per
    masked context (so ``distinct_targets`` never fires), and the ctor/dtor
    pairs are recorded under different leaves (so the header-tier
    double-counting guard's ``recorded_pairs`` dedup never collapses them
    either) -- the two pairs are both genuine, unambiguous matches, they
    are just not *independent* evidence of a scope move (oneCCL report,
    fresh evidence: ``broadcastExt_attr`` deleted and an unrelated
    ``window`` added in the same enclosing scope reported a fabricated
    ``broadcastExt_attr`` -> ``window`` "namespace segment" rename).
    Requiring support from 2+ distinct declaring entities (via
    :func:`_declaring_entity`, which folds a class's own ctor/dtor pair
    down to one entity) closes this at the class-count level without
    touching :func:`find_namespace_move_groups`'s ambiguity logic, which
    is unaffected by this exact shape.

    Ordered by support (most-supported substitution first, then by the
    substitution itself) so the report is stable across runs and the dominant
    move leads.
    """
    changes: list[Change] = []
    for (old_seg, new_seg), pairs in sorted(
        groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
    ):
        if len(pairs) < 2:
            continue
        if len({_declaring_entity(old) for old, _new in pairs}) < 2:
            continue
        pair_desc = ", ".join(f"{o} → {n}" for o, n in pairs[:5])
        if len(pairs) > 5:
            pair_desc += f", ... ({len(pairs)} total)"
        changes.append(
            make_change(
                ChangeKind.SYMBOL_RENAMED_BATCH,
                symbol=f"batch_rename:{old_seg}→{new_seg}",
                description=(
                    "Batch symbol rename detected (namespace refactoring): "
                    f"namespace segment '{old_seg}' → '{new_seg}' on "
                    f"{len(pairs)} symbols ({pair_desc})"
                ),
                old_value=", ".join(o for o, _ in pairs),
                new_value=", ".join(n for _, n in pairs),
            )
        )
    return changes

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

import logging
import re
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
from .diff_helpers import make_change
from .elf_metadata import SymbolType
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, is_cxx_runtime_library

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

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

Namespace-move batch-rename detection (``find_namespace_move_groups``/
``emit_namespace_move_batches``) lives in ``compare/namespace_move.py``
instead, per ADR-061 -- this module owns prefix-rename detection and the ELF
fingerprint-based rename detector only, and re-exports the two namespace-move
entry points below purely so ``diff_symbols.py``'s own existing re-export
block (and any external caller of ``abicheck.diff_symbols_renames``) keeps
working unchanged.
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Mapping

from .binary_fingerprint import (
    _MIN_SYMBOL_SIZE,
    FunctionFingerprint,
    match_renamed_functions,
)
from .checker_types import Change
from .compare.namespace_move import (  # noqa: F401  (public-surface re-exports)
    emit_namespace_move_batches as emit_namespace_move_batches,
    find_namespace_move_groups as find_namespace_move_groups,
)
from .compare.rename_evidence import all_constituents_elf_bound
from .demangle import demangle, demangle_batch
from .detector_registry import registry
from .diff_helpers import make_change
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, Function, is_cxx_runtime_library
from .model.change_catalog.kinds import ChangeKind
from .model.elf_facts import SymbolType
from .model.execution_cache import memoized
from .model.name_decoration import itanium_structors

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
# leaf names for a match to count as a rename. A code hash (schema v56+) can
# confirm a size match but never replaces this gate; without one, a "rename"
# is inferred purely from a coincidental symbol-size
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


@memoized(maxsize=65536)
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
    structor = itanium_structors.decode(name)
    return (
        structor.code if structor is not None else None,
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
    Gates every match: size alone is not evidence of identity, and identical
    code bytes are not either (two unrelated stubs compile alike).
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


def _plausible_rename_keys(name: str) -> tuple[tuple[object, ...], ...]:
    """Blocking keys for :func:`_plausible_rename`: it accepts a pair only
    when their key sets intersect (``match_renamed_functions``' contract).

    Every accepting path of ``_plausible_rename`` either has equal names, or
    passes the ctor/dtor gate -- which means both names have the *same*
    structor variant (``None`` included) -- and then needs either an equal
    leaf (the ``_Z``/operator/destructor exact-leaf paths and the same-leaf
    path) or an equal parameter signature *and* return type (the affix
    path's ``sig_match``). So: the name itself, ``(variant, leaf)`` and
    ``(variant, params, return)``.
    """
    variant, leaf, params, ret = _rename_name_parse(name)
    return (("name", name), ("leaf", variant, leaf), ("sig", variant, params, ret))


def _fingerprints_from_elf(snap: AbiSnapshot) -> dict[str, FunctionFingerprint]:
    """Build FunctionFingerprint dict from ELF metadata.

    Uses ElfSymbol.size from .dynsym, and ElfSymbol.code_hash where the dump
    recorded one (schema v56+; "" otherwise, which the matcher reads as "no
    hash"). Includes FUNC, IFUNC, and NOTYPE symbols — matching dumper.py's
    ``exported_dynamic_funcs`` categorization for elf_only_mode snapshots.
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
            code_hash=sym.code_hash,
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

    # A size match is not identity, and a code hash only confirms one. Pass
    # the name-similarity predicate into the matcher so it participates in candidate
    # *selection*: a coincidental same-size symbol can neither be reported as a
    # rename nor greedily consume a partner that a plausible rename should claim.
    # P11: one batched c++filt warm so the rename gate's demangle() hits cache, not per-symbol forks.
    demangle_batch([n for n in (*old_fps, *new_fps) if n.startswith("_Z")])
    candidates = match_renamed_functions(
        old_fps,
        new_fps,
        name_filter=_plausible_rename,
        name_keys=_plausible_rename_keys,
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


def emit_prefix_batch_rename(
    rename_pairs: list[tuple[str, str]],
    old_map: Mapping[str, Function] | None = None,
) -> list[Change]:
    """Emit a SYMBOL_RENAMED_BATCH change if all pairs share a single common prefix.

    *old_map*, when given, is consulted so the emitted change's
    ``symbol_binding`` reflects whether *every* constituent pair is actually
    ELF-backed (:func:`compare.rename_evidence.all_constituents_elf_bound`) --
    ``SYMBOL_RENAMED_BATCH`` is a member of ``_ELF_BINDING_STAMPED_KINDS``
    (``checker_policy.py``), so an unset ``symbol_binding`` on an
    ``"elf"``-tiered run downgrades the whole batch to
    ``EvidenceStatus.UNATTRIBUTED`` -- the correct outcome when even one
    constituent pair was never actually matched against a real symbol-table
    entry, not just when none were (Codex review, Finding C(i)).
    """
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
    # Only truthiness of `symbol_binding` is ever consulted
    # (`checker_policy.evidence_status_for_result`) -- the specific binding
    # kind (global/weak/...) has no meaning at batch granularity, since
    # constituents can legitimately differ.
    binding = (
        "global"
        if all_constituents_elf_bound(
            (o for o, _ in rename_pairs), old_map, lambda f: f.name
        )
        else None
    )
    return [
        make_change(
            ChangeKind.SYMBOL_RENAMED_BATCH,
            symbol=f"batch_rename:{prefix}*",
            name=prefix,
            detail=f"{len(rename_pairs)} symbols ({pair_desc})",
            old_value=", ".join(o for o, _ in rename_pairs),
            new_value=", ".join(n for _, n in rename_pairs),
            symbol_binding=binding,
        )
    ]

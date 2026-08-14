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

"""Ambiguity-safe reconciliation for castxml's own synthetic ctor/dtor key
*format drift* across abicheck versions -- not a general namespace-alias
matcher.

**The bug this closes.** ``dumper_castxml.py``'s ``SYNTHETIC_CTOR_KEY_PREFIX``/
``is_synthetic_ctor_key``/``_SYNTHETIC_DTOR_KEY_PREFIX``/``is_synthetic_dtor_key``
document a real, intentional key-format change (PR #582): a constructor's
synthesized snapshot key changed from a bare-class-name scope
(``__abicheck_ctor__Calculator()``) to a namespace-qualified one
(``__abicheck_ctor__abicheck_lab::Calculator()``), to stop two same-named
classes in different namespaces from colliding on one key. That fix is
correct going forward, but it means an OLD baseline snapshot persisted
before PR #582 and a FRESH snapshot taken with current abicheck disagree on
the key for the exact same, completely unchanged constructor -- purely
because abicheck's own key format evolved between the two snapshots. Before
this module, that key mismatch fell straight through to
``diff_symbols._match_old_function``'s final ``_check_removed_function``
fallback (and the equivalent ``FUNC_ADDED`` on the new side), producing a
spurious removed+added pair -- a false BREAKING finding on a class that
never changed.

**Why this is NOT another attempt at the reverted namespace-alias-merging
heuristics.** ``AGENTS.md``'s "Known gaps" section documents, at length,
three reverted attempts to merge two *user source-level* declarations
(a using-declaration re-exporting a namespace-scope constant) by guessing
from name shape alone -- and why that is fundamentally unsound: a
using-declaration can legally introduce a name in either direction relative
to a namespace segment that merely *looks* version-tagged, so no
name-shape rule can tell which of two spellings is the real declaration and
which is the alias, for arbitrary user code. This module solves a
categorically different problem: both sides here are not two source-level
declarations that alias each other -- they are two different SERIALIZATIONS,
by two different versions of abicheck's own code, of the exact same single
declaration, using a key format abicheck itself invented and fully
controls. There is no user-authored ambiguity to resolve, no "which
spelling is real" question -- only "undo abicheck's own key-format
evolution deterministically", by re-deriving the canonical (fully
namespace-stripped) form both formats can produce from a value abicheck
itself always fully qualifies (the class's own scope, never a source-level
alias target). The two problems must be kept conceptually distinct, which
is why this stays its own narrowly-scoped module rather than folding into
``diff_helpers``' or ``diff_namespaces``' general merge machinery.

**Scope, deliberately narrow:**

- Applies *only* to :class:`~abicheck.model.Function` entries whose key
  satisfies :func:`~abicheck.dumper_castxml.is_synthetic_ctor_key` or
  :func:`~abicheck.dumper_castxml.is_synthetic_dtor_key` -- these are
  abicheck's own invented per-overload identity strings, never a real
  Itanium/MSVC mangled symbol observed on the binary. A real mangled name
  is never routed through this module.
- Runs strictly as a *fallback*, over the sets of old/new ctor-or-dtor
  synthetic keys that already failed BOTH the exact-key join and the
  real-mangled-name join (``diff_symbols._diff_functions`` builds those two
  sets from functions with no counterpart in the opposite side's map --
  a synthetic key is never a real mangling, so it never participates in
  the extern "C" alias fallback either, and ordering with that fallback is
  therefore moot).
- Matches only when exactly one unmatched old candidate and exactly one
  unmatched new candidate share an identical canonical form -- the same
  "zero or several is not a match" discipline
  :meth:`~abicheck.finding_identity.SymbolIdentityIndex.unique_alias_match`
  already established for the extern "C" fallback. Two distinct classes
  that happen to share a bare (namespace-stripped) name, each
  independently gaining/losing an unrelated constructor, therefore never
  cross-merge: both sides would carry 2+ candidates for that bare name and
  the match is refused.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .dumper_castxml import (
    _SYNTHETIC_DTOR_KEY_PREFIX,
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .name_classification import canonicalize_type_name
from .type_reachability import _bare_type_name

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import Function

_logger = logging.getLogger(__name__)

#: Which synthetic key shape a :class:`CtorDtorCanonicalKey` was derived
#: from -- ``"ctor"`` keys carry a canonicalized parameter-type tuple to
#: distinguish overloads (default/copy/move/converting), ``"dtor"`` keys
#: never carry parameters (a class has at most one destructor).
CtorDtorKind = Literal["ctor", "dtor"]


@dataclass(frozen=True)
class CtorDtorCanonicalKey:
    """The abicheck-key-format-independent identity of one constructor or
    destructor: the bare (namespace-stripped) owner name, whether it's a
    ctor or dtor, and -- for a ctor -- its canonicalized parameter-type
    tuple.

    Two synthetic keys canonicalize to an equal :class:`CtorDtorCanonicalKey`
    exactly when they name the same overload of the same class, regardless
    of which abicheck version's namespace-qualification convention
    produced the key's ``scope`` portion -- see this module's docstring for
    why unqualified-vs-qualified scope is the ONLY axis this collapses.
    """

    owner: str
    kind: CtorDtorKind
    params: tuple[str, ...] = ()


def _split_synthetic_ctor_key_body(body: str) -> tuple[str, str] | None:
    """Split a synthetic ctor key's body (everything after
    :data:`~abicheck.dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX`) into
    ``(scope, param_sig)``.

    The key format is ``f"{scope}({param_sig})"`` (see
    ``dumper_castxml._CastxmlParser._function_mangled_name``), where
    *scope* is a (possibly namespace-qualified, possibly template-
    instantiated) class name and *param_sig* is a comma-joined parameter
    type list that can itself legally contain commas, parens, and angle
    brackets (template arguments, function-pointer parameter lists). A
    naive ``body.find("(")`` would misfire if *scope* itself were ever a
    template instantiation containing a literal ``(`` -- not currently
    possible for a class name, but this scans depth-aware over ``<``/``>``
    the same way ``type_reachability_spelling._namespace_suffix_spellings``
    does, so the split stays correct even if that ever changed, and finds
    the first ``(`` at template-bracket depth zero as the scope/params
    boundary.
    """
    depth = 0
    for i, ch in enumerate(body):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            if not body.endswith(")"):
                return None
            return body[:i], body[i + 1 : -1]
    return None


def synthetic_ctor_scope(mangled: str) -> str | None:
    """Qualified scope in a castxml synthetic-ctor key
    (``SYNTHETIC_CTOR_KEY_PREFIX + "scope(params)"``), or ``None`` (Codex
    review, PR #608 follow-up; moved here from ``diff_symbols.py`` to sit
    alongside this module's other ctor/dtor synthetic-key helpers --
    ``diff_symbols._converting_ctors_by_class`` still imports it under its
    original name for its owner-class fallback). Reuses
    :func:`_split_synthetic_ctor_key_body`'s depth-aware split rather than
    the naive ``body.find("(")`` this originally did, so a scope containing
    a template argument is no longer at risk of the same corruption
    :func:`canonicalize_synthetic_ctor_dtor_key` guards against.
    """
    if not is_synthetic_ctor_key(mangled):
        return None
    body = mangled[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
    split = _split_synthetic_ctor_key_body(body)
    return split[0] if split is not None else None


def _canonicalize_ctor_param_sig(param_sig: str) -> tuple[str, ...]:
    """Canonicalized parameter-type tuple from a synthetic ctor key's
    comma-joined ``param_sig`` portion.

    Splits on top-level (template/paren/bracket-depth-zero) commas only --
    a parameter type can itself contain a comma inside a template argument
    (``std::pair<int, int>``) or a function-pointer parameter list -- then
    canonicalizes each part through the same
    :func:`~abicheck.name_classification.canonicalize_type_name` every
    other identity/param comparison in this codebase already goes through,
    so a castxml/clang spelling discrepancy on an otherwise-identical type
    doesn't fragment two overloads of the same constructor into different
    canonical forms. An empty (whitespace-only) ``param_sig`` -- the
    default constructor -- canonicalizes to the empty tuple, not a
    single empty-string element.
    """
    if not param_sig.strip():
        return ()
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(param_sig):
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(param_sig[start:i])
            start = i + 1
    parts.append(param_sig[start:])
    return tuple(canonicalize_type_name(part.strip()) for part in parts)


def canonicalize_synthetic_ctor_dtor_key(key: str) -> CtorDtorCanonicalKey | None:
    """Canonical, key-format-independent identity for a castxml synthetic
    ctor/dtor key, or ``None`` if *key* is not one (including any real
    mangled symbol -- this function must never be applied to one, per this
    module's own scope note).

    The owner name is reduced to its fully bare (namespace-stripped) form
    via :func:`~abicheck.type_reachability._bare_type_name` -- the same
    depth-aware (template-argument-nesting-safe) helper
    ``type_reachability.py`` already uses for the adjacent, but distinct,
    problem of matching a partially-qualified signature spelling back to a
    fully-qualified record identity. Reusing it here (rather than a naive
    ``rsplit("::", 1)``) is what keeps a class whose own name legitimately
    embeds ``::`` in a template argument (``Wrapper<ns::Tag>``) from being
    corrupted the same way that helper's own docstring documents.
    """
    if is_synthetic_ctor_key(key):
        body = key[len(SYNTHETIC_CTOR_KEY_PREFIX) :]
        split = _split_synthetic_ctor_key_body(body)
        if split is None:
            return None
        scope, param_sig = split
        if not scope:
            return None
        return CtorDtorCanonicalKey(
            owner=_bare_type_name(scope),
            kind="ctor",
            params=_canonicalize_ctor_param_sig(param_sig),
        )
    if is_synthetic_dtor_key(key):
        scope = key[len(_SYNTHETIC_DTOR_KEY_PREFIX) :]
        if not scope:
            return None
        return CtorDtorCanonicalKey(owner=_bare_type_name(scope), kind="dtor")
    return None


@dataclass(frozen=True)
class CtorDtorKeyDriftMatch:
    """One resolved old/new pairing produced by
    :func:`find_ctor_dtor_key_drift_matches` -- an internal match-reasoning
    trail, not a user-facing finding. A merged pair contributes exactly the
    ``Change``s a same-key match would (i.e. none, for a genuinely
    unchanged declaration): unlike a *demoted* finding elsewhere in this
    codebase, there is no real ABI difference being suppressed here, only
    two spellings of one unchanged declaration being recognized as one.
    """

    old_key: str
    new_key: str
    canonical: CtorDtorCanonicalKey


def find_ctor_dtor_key_drift_matches(
    old_unmatched: Mapping[str, Function],
    new_unmatched: Mapping[str, Function],
) -> list[CtorDtorKeyDriftMatch]:
    """Ambiguity-safe old/new pairing among already-unmatched ctor/dtor
    synthetic-key functions.

    *old_unmatched*/*new_unmatched* must already be narrowed by the caller
    to functions with no exact-key (and, for a real mangled name, no
    real-mangled-name) counterpart on the opposite side -- this function
    performs no such filtering itself, only the canonical-form grouping and
    the one-to-one ambiguity check. A key that does not canonicalize (not a
    synthetic ctor/dtor key at all) is silently ignored, so passing a wider
    map than strictly necessary is harmless, just wasted work.

    A canonical form present exactly once on the old side and exactly once
    on the new side merges; any other count (zero, or two-or-more, on
    either side) is left unmatched, so a genuine ambiguity -- e.g. two
    distinct classes sharing a bare name in different namespaces, each
    independently gaining/losing an unrelated constructor -- never
    cross-merges. This mirrors
    :meth:`~abicheck.finding_identity.SymbolIdentityIndex.unique_alias_match`'s
    same "zero or several answers None" rule, generalized to require
    uniqueness on BOTH sides at once (not just the side being looked up
    into), since here either side could independently be ambiguous.
    """
    old_groups: dict[CtorDtorCanonicalKey, list[str]] = {}
    for key in old_unmatched:
        canonical = canonicalize_synthetic_ctor_dtor_key(key)
        if canonical is not None:
            old_groups.setdefault(canonical, []).append(key)

    new_groups: dict[CtorDtorCanonicalKey, list[str]] = {}
    for key in new_unmatched:
        canonical = canonicalize_synthetic_ctor_dtor_key(key)
        if canonical is not None:
            new_groups.setdefault(canonical, []).append(key)

    matches: list[CtorDtorKeyDriftMatch] = []
    for canonical, old_keys in old_groups.items():
        if len(old_keys) != 1:
            continue
        new_keys = new_groups.get(canonical)
        if new_keys is None or len(new_keys) != 1:
            continue
        match = CtorDtorKeyDriftMatch(
            old_key=old_keys[0], new_key=new_keys[0], canonical=canonical
        )
        _logger.debug(
            "resolved ctor/dtor synthetic-key format drift: old=%r new=%r "
            "(canonical owner=%r kind=%r params=%r)",
            match.old_key,
            match.new_key,
            canonical.owner,
            canonical.kind,
            canonical.params,
        )
        matches.append(match)
    return matches


def reconcile_ctor_dtor_key_drift(
    old_map: MutableMapping[str, Function],
    new_map: Mapping[str, Function],
    check_signature: Callable[..., list[Change]],
    params_unconfirmed: bool,
    is_llp64: bool,
) -> tuple[set[str], list[Change]]:
    """End-to-end entry point for ``diff_symbols._diff_functions``.

    Narrows *old_map*/*new_map* to their ctor/dtor synthetic-key entries
    with no exact-key counterpart on the opposite side (a synthetic key is
    never a real mangled name, so it never wins the real-mangled-name join
    either, and it is never extern "C", so it never competes with that
    fallback -- this tier is a pure, order-independent addition), resolves
    :func:`find_ctor_dtor_key_drift_matches` over that narrowed pair, and
    for each resolved match: pops the matched entry out of *old_map* (so
    the caller's own old/new matching loop, run afterward, never re-visits
    it as a plain removal) and calls *check_signature* -- the caller's own
    ``diff_symbols._check_function_signature``, passed by reference (its
    ``mangled, f_old, f_new, *, params_unconfirmed, is_llp64`` signature
    already matches what this function calls it with) -- the same way an
    exact-key match would, so a genuine non-key-format difference is still
    reported and a truly unchanged pair contributes zero findings.

    Returns ``(consumed new_map keys, resolved Changes)``. The caller
    should ``extend`` its own ``changes`` list with the second element, and
    skip the first element's keys when it separately walks *new_map* for
    additions (that walk cannot itself consult *old_map* for this, since a
    matched new key was never a member of *old_map* to begin with -- it
    only failed to independently surface as an addition because this
    function already accounted for it here).
    """
    old_unmatched = {
        k: f
        for k, f in old_map.items()
        if k not in new_map and (is_synthetic_ctor_key(k) or is_synthetic_dtor_key(k))
    }
    new_unmatched = {
        k: f
        for k, f in new_map.items()
        if k not in old_map and (is_synthetic_ctor_key(k) or is_synthetic_dtor_key(k))
    }
    matches = find_ctor_dtor_key_drift_matches(old_unmatched, new_unmatched)
    consumed_new: set[str] = set()
    resolved_changes: list[Change] = []
    for m in matches:
        resolved_changes.extend(
            check_signature(
                m.new_key,
                old_map.pop(m.old_key),
                new_map[m.new_key],
                params_unconfirmed=params_unconfirmed,
                is_llp64=is_llp64,
            )
        )
        consumed_new.add(m.new_key)
    return consumed_new, resolved_changes

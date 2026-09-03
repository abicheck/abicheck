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

**Current status: the bare-vs-qualified reconciliation this module was
built to perform is disabled.** Investigation (PR #761 finding 1, detailed
in the "Scope, deliberately narrow" section below) found the heuristic
that decided "this is drift, not a real move" structurally unsound and
found no independent evidence to replace it with, so
:func:`find_ctor_dtor_key_drift_matches` currently never produces a match
and the false-positive this module describes is, once again, reported
rather than silently reconciled -- deliberately, since the alternative
(occasionally hiding a real breaking namespace move) is worse. The
canonicalization/uniqueness machinery and the ``iter_matched_function_pairs``
wiring described below remain fully in place and exercised by tests; only
the one predicate that used to gate a match on key shape is disabled.

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
- **The bare-vs-qualified fallback is permanently disabled -- investigated
  and found structurally unsound, not merely narrowed (Codex review, PR
  #761 finding 1).** An earlier revision of this module matched a
  canonical-form pair only when exactly one of the two raw (pre-bare-strip)
  owner scopes contained ``"::"`` and the other did not, reasoning that a
  bare owner scope is the positive signal that side predates PR #582. That
  reasoning
  does not hold: a *current*-format snapshot's non-namespaced (global)
  class also always gets a bare key (``dumper_castxml.py``'s own comment:
  "a non-namespaced class's qualified name is just its bare name, so this
  is a no-op for the common case"). So a real, breaking move of a class
  from the global namespace into a named one on two CURRENT-schema
  snapshots produces the *identical* bare-old/qualified-new split as
  genuine key-format drift, by construction -- there is no observation on
  the pair of keys alone that tells the two apart. This is the same
  category of unsound name-shape inference ``AGENTS.md``'s "Known gaps"
  section already documents being tried and reverted three times for the
  using-declaration/namespace-alias case, for the identical underlying
  reason: no name-shape rule can tell which of two spellings is real and
  which is drift, because both explanations produce the same string.

  **Investigated whether any genuinely independent, per-snapshot evidence
  exists to gate on instead -- none does.** ``AbiSnapshot`` (``model.py``)
  carries no ``schema_version`` field at all: ``serialization.py``'s
  ``schema_version`` is a value read from the JSON payload purely to
  derive several ``*_facts_reliable`` booleans at load time (e.g.
  ``clang_restrict_facts_reliable``), and every load re-stamps a
  reserialized snapshot's ``schema_version`` to the current
  ``SCHEMA_VERSION`` -- it is not a queryable fact on the in-memory object
  a detector could branch on. Nor would a schema bump help here even if
  one existed: cross-checking ``serialization.py``'s full v1-v25 history
  comment against ``dumper_castxml.py``'s own "PR #582" commentary around
  ``_function_mangled_name``'s ``qualified_scope`` parameter confirms the
  ctor/dtor key-format change shipped WITHOUT any accompanying schema
  bump -- so even a hypothetical "reject anything below schema version N"
  rule could not distinguish a bare-format snapshot from a qualified-format
  one, since both key formats can occur at the SAME schema version. The
  only other candidate fields are ``git_commit``/``git_tag``/``created_at``
  (schema v4 provenance) -- these record the *scanned library's* own build
  provenance at dump time, not which version of abicheck's own
  ``dumper_castxml.py`` produced the snapshot, so they carry no signal
  about the key-format epoch either. There is, in short, no field on
  ``AbiSnapshot`` today -- and none that could be retroactively added now,
  since the ambiguity is already baked into already-serialized key
  *strings* with no accompanying metadata -- that reliably answers "did
  this snapshot's ctor/dtor keys use the old or new format".

  **Decision: disable the fallback rather than ship a heuristic already
  proven unsound by counterexample.** Per this file's own "known gaps
  over risky reactive patches" convention and the "attempted twice,
  reverted twice" discipline documented in ``AGENTS.md`` for the
  structurally identical using-declaration case: prefer under-merging (the
  pre-``ed6b1898`` behavior -- a spurious ``FUNC_REMOVED``/``FUNC_ADDED``
  pair on an unchanged ctor/dtor moved across the PR #582 key-format
  boundary, a visible, previously-accepted false positive) over
  over-merging (silently collapsing a REAL, breaking global-to-namespace
  move into ``NO_CHANGE``, which is strictly worse -- a hidden break is
  worse than a noisy non-break). :func:`_is_legacy_qualification_drift_pair`
  therefore now unconditionally returns ``False`` --
  :func:`find_ctor_dtor_key_drift_matches` never produces a bare/qualified
  match, regardless of uniqueness. The canonicalization and
  one-to-one-uniqueness machinery is kept in place (not deleted) precisely
  so a FUTURE fix has a real foundation to build on if abicheck ever grows
  a genuine per-snapshot producer-version marker -- re-enabling the
  fallback would then mean replacing only this one predicate's body, not
  rebuilding the module.

**Which detectors see a resolved match (Codex review, PR #761 finding 2).**
A resolved ctor/dtor pair's old and new keys differ from each other by
construction, so any OTHER ``diff_symbols.py``-family detector that joins
old/new functions by exact key would never independently see the pair as
matched -- it reads as a plain removal on one side and a plain addition on
the other, both silently absorbed by ``reconcile_ctor_dtor_key_drift``
before that detector ever runs, so a genuine non-key-format property
change on the SAME pair went unreported entirely. :func:`iter_matched_function_pairs`
closes this by exposing the resolved pairs as an additional explicit
source every affected per-pair detector's own join consults, not by
reinventing per-detector logic. Wired in
(``diff_symbols.py``: ``_check_inline_transitions``,
``_check_method_access_changes``, ``_diff_param_defaults``,
``_diff_param_renames``, ``_diff_pointer_levels``, ``_diff_func_deprecated``;
``diff_param_qualifiers.py``: ``param_restrict_changes``,
``param_va_list_changes``) -- every per-pair ``Function`` detector in
both modules that does its own map join, except two deliberately left
out:

- ``_diff_func_override_specifier`` -- not applicable. ``override`` can
  only appear on a virtual member function, and a constructor/destructor
  is never virtual, so a ctor/dtor pair can never carry this property to
  begin with; wiring it in would add dead code, not coverage.
- ``_diff_symbol_renames`` -- a batch-rename HEURISTIC (2+ independently
  removed/added symbols whose names share a naming pattern), not a
  per-pair join over a resolved match; it re-derives its own
  removed/added sets from ``_public_functions`` directly rather than
  consulting any resolved pairing. Threading ctor/dtor reconciliation
  through a heuristic built for an unrelated problem (namespace-prefix
  refactors across MANY symbols) risks changing its unrelated behavior
  more than it closes a real gap here, so this is left as a documented
  gap rather than a same-PR extension. In practice this detector requires
  2+ removed symbols with no already-reconciled match consuming them
  first, so a single reconciled ctor/dtor pair does not feed it spurious
  input either way -- only an entirely separate, coincidental batch-rename
  pattern elsewhere in the same comparison could interact with it, which
  this module's reconciliation does not create.

Variable-only detectors (``_diff_var_deprecated``, ``_diff_var_access``,
``var_access_changes``) are out of scope by construction -- this module
never reconciles a ``Variable``, only a ``Function``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
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


def _synthetic_ctor_dtor_scope(symbol: str) -> str | None:
    """The raw (possibly namespace/template-qualified) scope embedded in a
    castxml synthetic ctor/dtor key, or ``None`` if *symbol* is not such a
    key or the scope cannot be recovered. Shared by
    :func:`synthetic_ctor_dtor_template_base_name` and
    :func:`itanium_standard_substitution_token`.
    """
    if is_synthetic_ctor_key(symbol):
        return synthetic_ctor_scope(symbol)
    if is_synthetic_dtor_key(symbol):
        scope = symbol[len(_SYNTHETIC_DTOR_KEY_PREFIX) :]
        return scope or None
    return None


def synthetic_ctor_dtor_template_base_name(symbol: str) -> str | None:
    """The bare (namespace- and template-argument-stripped) name of the
    class/class-template a castxml synthetic ctor/dtor key names as its
    owner, or ``None`` if *symbol* is not such a key or its scope cannot be
    recovered. Sits alongside this module's other synthetic-key helpers
    (used by ``diff_templates.demote_lambda_closure_unexported_findings``
    to ask "was this class template ever exported under ANY
    instantiation" via a binary-export substring check, rather than "was
    THIS EXACT instantiation exported" — the real, per-instantiation
    Itanium mangling of a closure-type template argument uses the
    compiler's own unnamed-type encoding (``Ul<parameter-types>E_``),
    never castxml's ``(lambda:file:line:col)`` spelling, so the exact
    mangled substring for one specific instantiation can't be reconstructed
    from the snapshot text alone).
    """
    scope = _synthetic_ctor_dtor_scope(symbol)
    if not scope:
        return None
    bare = _bare_type_name(scope)
    # Only the OUTERMOST template-argument list's opening bracket matters --
    # once found, everything from it onward (nested brackets included) is
    # dropped in one slice, so no depth tracking is needed here (unlike
    # `_bare_type_name`'s own namespace-suffix splitting, which must find
    # every "::" at depth zero, not just the first "<").
    idx = bare.find("<")
    base = bare[:idx] if idx != -1 else bare
    return base or None


#: The Itanium C++ ABI (section 5.1.2) reserves a fixed, mandatory
#: 2-character substitution for these six ``std::`` component names --
#: the mangler ALWAYS emits the abbreviation instead of spelling out the
#: source-name, even on the very first occurrence in a symbol (unlike an
#: ordinary repeated-component substitution, which is optional and only
#: ever applies to a name already spelled out earlier in the SAME symbol).
#: `"basic_string"` deliberately maps to the *name* substitution `Sb`, not
#: the additional, narrower full-type substitution `Ss` (`std::string`
#: with its exact default template arguments) -- `Sb` is what a
#: differently-parameterized `std::basic_string<...>` instantiation uses.
_ITANIUM_STD_NAME_SUBSTITUTIONS: dict[str, str] = {
    "allocator": "Sa",
    "basic_string": "Sb",
    "basic_istream": "Si",
    "basic_ostream": "So",
    "basic_iostream": "Sd",
}


def itanium_standard_substitution_token(symbol: str) -> str | None:
    """The fixed Itanium ABI substitution abbreviation for *symbol*'s owning
    class, if that class's qualified name is exactly ``std::<name>`` for one
    of the six names :data:`_ITANIUM_STD_NAME_SUBSTITUTIONS` covers -- or
    ``None`` otherwise (including when *symbol* is not a synthetic ctor/dtor
    key, or its scope can't be recovered).

    A caller asking "does any real exported symbol reference this class"
    via :func:`itanium_source_name_token` alone is unsafe for these six
    names specifically: a real ``std::allocator<T>::allocator()`` mangles
    to ``_ZNSaIiEC1Ev`` (using the substitution `Sa`), never to anything
    containing the literal source-name substring ``"9allocator"`` -- so a
    literal-token-only search would always read "never exported" for such a
    class regardless of the truth, silently letting a genuine removal
    demote to ``COMPATIBLE_WITH_RISK``. Callers should check both this AND
    :func:`itanium_source_name_token` of
    :func:`synthetic_ctor_dtor_template_base_name`'s result.
    """
    scope = _synthetic_ctor_dtor_scope(symbol)
    if not scope:
        return None
    idx = scope.find("<")
    qualified = scope[:idx] if idx != -1 else scope
    if not qualified.startswith("std::"):
        return None
    return _ITANIUM_STD_NAME_SUBSTITUTIONS.get(qualified[len("std::") :])


def itanium_source_name_token(name: str) -> str:
    """The Itanium ``<source-name>`` encoding of a plain identifier: its
    encoded byte length immediately followed by the identifier text, e.g.
    ``"raii_guard"`` → ``"10raii_guard"``. Every real Itanium-mangled symbol
    naming *name* as a namespace/class/template component embeds this exact
    substring (Itanium C++ ABI §5.1.1), so a substring search for it is a
    dependency-free way to ask "does any real exported symbol reference
    this identifier" without invoking an external demangler.

    The length is the identifier's encoded **byte** count, not its Python
    ``len()`` (character count): a non-ASCII identifier (GCC/Clang mangle a
    UTF-8 source encoding) has a longer byte length than its character
    count, e.g. ``"Café"`` mangles with length prefix ``5`` (4 characters,
    one of them 2 bytes), not ``4``.
    """
    return f"{len(name.encode('utf-8'))}{name}"


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


def _is_legacy_qualification_drift_pair(old_key: str, new_key: str) -> bool:
    """Always ``False`` (Codex review, PR #761 finding 1).

    This predicate used to compare *old_key*/*new_key*'s raw (pre-bare-strip)
    owner scopes and answer ``True`` when exactly one was namespace-qualified
    (contained ``"::"``) and the other was bare, on the theory that a bare
    owner scope is the positive signature of a pre-PR-#582 snapshot. That
    theory is false: a CURRENT-format snapshot's non-namespaced (global)
    class also always produces a bare owner scope (``dumper_castxml.py``'s
    own comment: "a non-namespaced class's qualified name is just its bare
    name, so this is a no-op for the common case"), so a real, breaking move
    of a class from the global namespace into a named one between two
    current-schema snapshots produces the *identical* bare-old/qualified-new
    split as genuine key-format drift -- the two are indistinguishable from
    the keys alone. Investigated whether any other per-snapshot evidence
    (``AbiSnapshot.schema_version``, a producer/tool-version marker, ...)
    could gate this instead -- none exists (see this module's docstring for
    the full investigation) -- so this predicate is permanently disabled
    rather than replaced with another name-shape heuristic. Kept as a named
    function (returning a hardcoded ``False``), rather than deleted, so
    :func:`find_ctor_dtor_key_drift_matches` reads as "this additional gate
    never currently passes" and a future fix with a real evidence source has
    exactly one function to replace.
    """
    del old_key, new_key  # Unused -- see docstring: this gate is disabled.
    return False


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

    A candidate pair unique on both sides is additionally required to pass
    :func:`_is_legacy_qualification_drift_pair`, which -- since PR #761
    finding 1 -- always returns ``False``: the bare-vs-qualified owner-scope
    heuristic that function used to implement was found structurally unsound
    (a real global-to-namespace move produces the identical bare/qualified
    split as genuine key-format drift, so the two cannot be told apart from
    the keys alone) and no independent per-snapshot evidence exists to gate
    on instead. This function therefore currently never produces a match;
    see this module's docstring for the full investigation and the
    "prefer under-merging" reasoning behind disabling rather than narrowing
    the heuristic further.
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
        if not _is_legacy_qualification_drift_pair(old_keys[0], new_keys[0]):
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


def _resolve_ctor_dtor_matches(
    old_map: Mapping[str, Function], new_map: Mapping[str, Function]
) -> list[CtorDtorKeyDriftMatch]:
    """Narrow *old_map*/*new_map* to their ctor/dtor synthetic-key entries
    with no exact-key counterpart on the opposite side, and resolve
    :func:`find_ctor_dtor_key_drift_matches` over that narrowed pair.

    Shared by :func:`reconcile_ctor_dtor_key_drift` (which additionally
    mutates *old_map* and runs the caller's signature check) and
    :func:`iter_matched_function_pairs` (which only needs the resolved
    ``(old_key, new_key)`` pairs, for a caller that does not itself go
    through ``_diff_functions``'s own exact-key loop -- see that function's
    docstring, ADR/Codex review PR #761 finding 2).
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
    return find_ctor_dtor_key_drift_matches(old_unmatched, new_unmatched)


def reconcile_ctor_dtor_key_drift(
    old_map: Mapping[str, Function],
    new_map: Mapping[str, Function],
    check_signature: Callable[..., list[Change]],
    params_unconfirmed: bool,
    is_llp64: bool,
) -> tuple[set[str], set[str], list[Change]]:
    """End-to-end entry point for ``diff_symbols._diff_functions``.

    Narrows *old_map*/*new_map* to their ctor/dtor synthetic-key entries
    with no exact-key counterpart on the opposite side (a synthetic key is
    never a real mangled name, so it never wins the real-mangled-name join
    either, and it is never extern "C", so it never competes with that
    fallback -- this tier is a pure, order-independent addition), resolves
    :func:`find_ctor_dtor_key_drift_matches` over that narrowed pair (via
    :func:`_resolve_ctor_dtor_matches`), and for each resolved match calls
    *check_signature* -- the caller's own
    ``diff_symbols._check_function_signature``, passed by reference (its
    ``mangled, f_old, f_new, *, params_unconfirmed, is_llp64`` signature
    already matches what this function calls it with) -- the same way an
    exact-key match would, so a genuine non-key-format difference is still
    reported and a truly unchanged pair contributes zero findings.

    Returns ``(consumed old_map keys, consumed new_map keys, resolved
    Changes)``. **Deliberately read-only -- *old_map* is never mutated**
    (Codex review, PR #761 finding 2 follow-up: an earlier revision popped
    the matched entry out of *old_map* in place, which left the caller's
    OWN subsequent, differently-shaped calls -- e.g. re-passing the same
    mutated *old_map* into ``_check_inline_transitions`` -- unable to
    rediscover the identical match a second time, since the popped key was
    simply gone). The caller should ``extend`` its own ``changes`` list
    with the third element, skip the first element's keys when it walks
    *old_map* for removals, and skip the second element's keys when it
    walks *new_map* for additions (neither walk can otherwise recognize
    these keys as matched, since a matched old/new key pair was never a
    member of the *opposite* side's map to begin with).

    **This only covers** ``_check_function_signature``'s own checks
    (return type, params, ref-qualifier, linkage, noexcept, virtual,
    hidden-friend, explicit, variadic, contract attributes, exception spec,
    vtable index). A property compared by a DIFFERENT, independently
    map-joining detector in ``diff_symbols.py``/``diff_param_qualifiers.py``
    (inline transitions, method access narrowing, parameter
    defaults/renames, pointer levels, deprecated, restrict/va_list) is
    invisible to THIS reconciliation on its own --
    :func:`iter_matched_function_pairs` is what independently exposes the
    resolved pairs to those detectors too, see its docstring.
    """
    matches = _resolve_ctor_dtor_matches(old_map, new_map)
    consumed_old: set[str] = set()
    consumed_new: set[str] = set()
    resolved_changes: list[Change] = []
    for m in matches:
        resolved_changes.extend(
            check_signature(
                m.new_key,
                old_map[m.old_key],
                new_map[m.new_key],
                params_unconfirmed=params_unconfirmed,
                is_llp64=is_llp64,
            )
        )
        consumed_old.add(m.old_key)
        consumed_new.add(m.new_key)
    return consumed_old, consumed_new, resolved_changes


def iter_matched_function_pairs(
    old_map: Mapping[str, Function], new_map: Mapping[str, Function]
) -> Iterator[tuple[str, Function, Function]]:
    """Yield ``(key, f_old, f_new)`` for every old/new function pair that is
    either an exact-key match OR a resolved ctor/dtor synthetic-key
    format-drift match (:func:`find_ctor_dtor_key_drift_matches`).

    This is the single join every per-pair function detector in
    ``diff_symbols.py``/``diff_param_qualifiers.py`` that does its own
    ``old_map``/``new_map`` join -- rather than going through
    ``_diff_functions``'s own exact-key loop -- should use, so a ctor/dtor
    pair reconciled by :func:`reconcile_ctor_dtor_key_drift` for
    ``_check_function_signature`` is also visible to every OTHER per-pair
    detector: inline transitions, method-access narrowing, parameter
    default/rename/pointer-level changes, ``[[deprecated]]`` transitions,
    ``restrict``/``va_list`` qualifier changes (Codex review,
    ``diff_symbols.py:945``, PR #761 finding 2). Without this, a reconciled
    pair's new key is popped out of the caller's own exact-key
    intersection (its old key no longer exists in *old_map* by the time
    these detectors run, and its keys differ from each other by
    construction), so a genuine OTHER property change on the same
    reconciled pair -- e.g. its constructor going public-to-private --
    silently returned no finding at all before this existed.

    For the ``key`` yielded on a reconciled pair, the NEW side's key is
    used (matching what ``reconcile_ctor_dtor_key_drift`` already reports
    findings under for the signature-check tier, so a symbol referenced in
    one of these OTHER detectors' findings stays consistent with that).

    The reconciliation performed here is read-only and independent per
    call -- unlike ``reconcile_ctor_dtor_key_drift``, this never mutates
    *old_map*/*new_map*, so it is safe for every detector to call
    independently against its own freshly-built maps.
    """
    for key, f_old in old_map.items():
        f_new = new_map.get(key)
        if f_new is not None:
            yield key, f_old, f_new
    for m in _resolve_ctor_dtor_matches(old_map, new_map):
        yield m.new_key, old_map[m.old_key], new_map[m.new_key]


def ctor_dtor_drift_old_by_new_key(
    old_map: Mapping[str, Function], new_map: Mapping[str, Function]
) -> dict[str, Function]:
    """Map each NEW-side key reachable only via ctor/dtor synthetic-key
    format-drift reconciliation (i.e. absent from *old_map* under its own
    key) to the OLD-side :class:`~abicheck.model.Function` it resolves to.

    For a caller that walks the *new* side keyed by mangled/synthetic name
    and looks up its old peer with ``old_map.get(that_same_key)`` -- correct
    for every ordinary function, but blind to a reconciled ctor/dtor pair,
    whose old and new keys differ from each other by construction (Codex
    review, ``diff_symbols.py:948``, PR #761 finding 2). Such a caller
    should fall back to this mapping when the direct lookup misses, the
    same way :func:`iter_matched_function_pairs` is the fallback for a
    caller that walks BOTH sides together as pairs rather than looking one
    side up from the other.

    ``diff_symbols._detect_newly_deleted_functions`` is the motivating
    caller: it walks ``new_all`` for a newly ``is_deleted`` function and
    looks up ``old_all.get(mangled)`` using the NEW side's key to find the
    (necessarily not-yet-deleted) old peer -- for a reconciled pair, that
    lookup must use this mapping instead, or a legacy-key constructor that
    gains ``= delete`` while the new snapshot already uses the qualified
    key is read as having no old peer at all, and the deletion goes
    unreported.

    Read-only and independent per call, like :func:`iter_matched_function_pairs`
    -- never mutates *old_map*/*new_map*, so a caller may build this once
    per detector invocation against its own freshly-built maps.
    """
    return {
        m.new_key: old_map[m.old_key]
        for m in _resolve_ctor_dtor_matches(old_map, new_map)
    }

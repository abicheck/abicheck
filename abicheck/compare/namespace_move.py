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

"""Namespace-move batch-rename detection: groups removed/added symbols by a
shared namespace-segment substitution and emits one ``SYMBOL_RENAMED_BATCH``
change per well-supported group (ADR-061: new rename-matching logic routes
to ``compare/``, not the debt-baselined flat ``diff_symbols_renames.py``,
matching the split already done for ``compare/rename_ambiguity.py``).

Leaf module within ``compare``: does not import ``diff_symbols_renames`` or
``diff_symbols`` to avoid an import cycle, mirroring
``compare/rename_ambiguity.py``'s own leaf-module constraint. Prefix-rename
detection (``find_prefix_rename_pairs``/``emit_prefix_batch_rename``) and the
ELF fingerprint-based rename detector stay in ``diff_symbols_renames.py`` --
this module owns only the namespace-move-substitution half of that file's
former scope. ``diff_symbols_renames.py`` re-exports these two entry points
from here so both ``diff_symbols.py``'s existing re-export block and any
external caller of ``from abicheck.diff_symbols import ...`` keep working.
"""

from __future__ import annotations

from ..checker_policy import ChangeKind
from ..checker_types import Change
from ..diff_cxx_rules import (
    component_embeds_template_args,
    itanium_scope_components_with_template_positions,
    msvc_scope_components,
    qualified_name_scope_components,
    strip_trailing_top_level_parameter_list,
)
from ..diff_helpers import make_change
from ..model.synthetic_key import (
    SYNTHETIC_CTOR_KEY_PREFIX,
    is_synthetic_ctor_key,
    is_synthetic_dtor_key,
)
from .rename_ambiguity import added_side_ambiguity_resolver

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
    key_support, is_added_side_acceptable = added_side_ambiguity_resolver(
        entries, added_id_to_removed_symbols, raw_symbol_key_targets
    )
    for masked, r_comps, i, a_comps in entries:
        if len(masked_to_old_segments[masked]) != 1:
            continue
        added_id = "::".join(a_comps)
        symbol_id = "::".join(r_comps)
        key = (r_comps[i], a_comps[i])
        if not is_added_side_acceptable(symbol_id, added_id, key):
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

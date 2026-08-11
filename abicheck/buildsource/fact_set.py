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

"""Canonical fact-set identity, coverage rollup, and comparison rules (ADR-038 C.8).

Every producer of :class:`~abicheck.buildsource.source_abi.SourceAbiTu` records
(the reference ``clang.py`` extractor, the Clang facts plugin, a future
GCC/MSVC producer) collects the same complete canonical fact family for its
declared ``fact_set`` version — never a user-selectable subset (see
``source_abi.py``'s ``FACT_FAMILIES``/``coverage_state_for_family``). This
module is pure: it rolls per-TU ``fact_set``/``coverage`` up to a per-surface
summary and implements the comparison-compatibility rules a diff/policy layer
consults before trusting an old/new pair's L4 evidence:

1. Old and new authoritative baselines should use the same fact-set version.
2. Old and new baselines should normally use the same producer family
   (``compiler_family``).
3. Opaque body/template hashes are only comparable when producer recipes
   match — approximated here by requiring the same ``producer`` id, the same
   ``producer_version``, *and* the same ``compiler_version``, since a
   producer release can change its canonicalization/hashing recipe without
   bumping the mandatory-family ``fact_set.version``, and the clang-family
   producers' hash recipe ports the *compiler's own* JSON AST dump (ADR-038
   C.2/C.7) — not guaranteed byte-stable across compiler versions even for
   the same abicheck producer release.
4. A missing mandatory fact family (``partial``/``failed`` coverage) must not
   be read as "unchanged" — surfaced via :func:`incomplete_families`.

Nothing here parses binaries or runs external tools; wiring these checks into
a verdict lives in ``source_diff.py`` / ``source_link.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_abi import (
    COVERAGE_STATES,
    FACT_FAMILIES,
    INCOMPLETE_COVERAGE_STATES,
    SourceAbiTu,
)

#: Worst-first precedence for rolling up one family's coverage across TUs: the
#: least-trustworthy state observed anywhere wins, so one bad TU cannot be
#: averaged away by many clean ones (recommendation P0 #6: "a partial or
#: failed collection must be visible").
_STATE_RANK = {
    "failed": 0,
    "partial": 1,
    "unsupported": 2,
    "complete": 3,
    "empty-confirmed": 4,
}


@dataclass(frozen=True)
class FactSetIssue:
    """One comparison-compatibility finding (ADR-038 C.8 rule 1/2/3/4)."""

    severity: str  # "error" | "warning"
    rule: str
    message: str


@dataclass(frozen=True)
class FactCompatibility:
    """Structured comparability verdict for one old/new ``fact_set`` pair.

    ``check_fact_set_compatibility`` reports *what* differs as prose-bearing
    :class:`FactSetIssue` rows; nothing previously packaged that into a
    boolean a diff pass could act on, so ``source_diff.diff_source_abi`` used
    to emit a "these hashes may be unreliable" finding and then unconditionally
    diff the (possibly incomparable) opaque hashes anyway (P1 gating gap,
    latest-main Clang plugin review). This type is that missing verdict:

    - ``structured_facts_comparable``: compiler-neutral facts (signatures,
      declarations, type shapes) — false only on a ``fact_set`` name/version
      mismatch, since those change what the mandatory-family contract even
      promises. This specifically gates *existence/removal* claims (an
      identity present old, absent new): under a contract mismatch, absence
      may only mean the old contract never mandated collecting that family,
      not that the entity disappeared. `source_diff.py`'s removal-detection
      loops in `_diff_generated`/`_diff_typedefs`/`_diff_macros` are what this
      gates.
    - ``structured_content_comparable``: whether a structured fact's hash
      (``type_hash``/``value``) means the same thing for a *content*
      comparison on an identity present on **both** sides. An earlier design
      here argued this never needs producer-recipe gating — "a signature/type
      hash means the same thing regardless of which mandatory-family contract
      collected it" — on the premise that a structured fact's extraction
      recipe cannot change independently of ``fact_set.version`` the way an
      opaque hash's canonicalization recipe can. G31 Phase C fact-completeness
      broke that premise for real: `EnumType.underlying_type` went from an
      always-``"int"`` placeholder to a genuinely extracted value on a
      castxml-producer version bump alone, with no `fact_set.version` change
      — so a persisted old-producer baseline's enum `type_hash` and a
      fresh new-producer extraction's `type_hash` can now differ for an
      UNCHANGED header, purely from the extractor upgrade (Codex review, PR
      #719). Gated on the same producer/producer_version/compiler_version
      identity rules as ``opaque_hashes_comparable`` — but, unlike that
      flag, NEVER overridden by a matching :func:`hash_recipe_id`: that id
      is a declared statement about the OPAQUE-hash canonicalization recipe
      specifically, and proves nothing about whether structured fact
      extraction also stayed identical (a second Codex review round on the
      same PR — a producer could keep one stable opaque-hash recipe id
      release over release while still changing what a structured fact
      extracts, exactly the `EnumType.underlying_type` shape above).
      Trusted unconditionally would mean accepting that a genuine content
      change spanning a real producer/compiler upgrade may go unreported
      until both sides are re-collected with the same producer — the same
      trade-off ``opaque_hashes_comparable`` already accepts for body/
      template hashes, just without the recipe-id escape hatch.
    - ``opaque_hashes_comparable``: producer-specific body/template hashes
      (``inline_body_changed``, ``template_body_changed``) — false on a
      producer/producer_version/compiler_version mismatch too, since the
      canonicalization recipe can change independently of ``fact_set.version``
      (rule 3 in the module docstring), *unless* both sides declare the same
      :func:`hash_recipe_id`, which overrides a producer/version mismatch that
      a differential conformance run (ADR-038 C.6) has proven irrelevant.
    - ``source_edges_comparable``: plugin/replay-produced graph edge endpoint
      identities share the same producer-dependent-recipe risk as opaque
      hashes (endpoint identity is derived from the same AST-dump recipe), so
      it is gated identically.
    """

    structured_facts_comparable: bool
    structured_content_comparable: bool
    opaque_hashes_comparable: bool
    source_edges_comparable: bool
    issues: tuple[FactSetIssue, ...]


def rollup_coverage(tus: list[SourceAbiTu]) -> dict[str, str]:
    """Worst-of-across-TUs coverage state per fact family.

    Families absent from every TU's ``coverage`` (an older/hand-edited
    producer) are omitted rather than guessed at. But a TU that *does*
    declare the canonical ``fact_set`` (so it participates in the C.8
    protocol) and still has no entry for a mandatory family is not "silent"
    the way a pre-C.8 producer's TU is — that missing entry is treated as
    the worst state so it cannot be averaged away by other TUs in the same
    pack that did report the family (Codex review, P2).
    """
    out: dict[str, str] = {}
    for family in FACT_FAMILIES:
        seen: list[str] = []
        for tu in tus:
            cov = tu.coverage if isinstance(tu.coverage, dict) else {}
            if family in cov:
                # A hand-written/third-party producer's unrecognized state
                # string (typo, or a value from a newer/incompatible
                # coverage vocabulary) must not slip through as-is: it isn't
                # in COVERAGE_STATES, so incomplete_families() (which only
                # matches known states) would never flag it, silently
                # reading malformed coverage as clean. Coerce it to the
                # worst known state instead (Codex review, P2).
                state = cov[family]
                seen.append(state if state in COVERAGE_STATES else "failed")
            elif tu.fact_set:
                seen.append("failed")
        if not seen:
            continue
        out[family] = min(seen, key=lambda s: _STATE_RANK[s])
    return out


def rollup_fact_set(tus: list[SourceAbiTu]) -> dict[str, Any]:
    """The common ``fact_set`` across *tus*, or ``{}`` if none/inconsistent.

    A mixed-producer pack (rare — normally one build uses one producer) has no
    single fact_set to report; callers should treat that itself as a
    comparison-compatibility concern rather than silently picking one. A pack
    that mixes TUs *with* a fact_set and TUs *without* one (e.g. a stale
    pre-C.8 record alongside current ones) is exactly that concern too: some
    TUs' mandatory-family coverage is simply unknown, so this must not report
    the non-empty subset's fact_set as if it described the whole pack (Codex
    review). Only "every TU agrees" or "every TU is silent" roll up to
    something other than ``{}``.
    """
    if not tus:
        return {}
    non_empty = [dict(tu.fact_set) for tu in tus if tu.fact_set]
    if not non_empty:
        return {}
    if len(non_empty) != len(tus):
        return {}
    first = non_empty[0]
    if all(s == first for s in non_empty[1:]):
        return first
    return {}


def fact_set_rollup_is_inconsistent(tus: list[SourceAbiTu]) -> bool:
    """Whether *tus* disagreed on ``fact_set`` -- as opposed to
    :func:`rollup_fact_set` returning ``{}`` because no TU ever populated one
    at all. Both collapse to the identical ``{}``, but they are not the same
    claim: "TUs disagree" must not receive the same forgiveness
    :func:`check_fact_compatibility` grants a genuinely symmetric pair of
    pre-C.8 (never-populated) fact sets (Codex review, PR #719).
    """
    non_empty = [dict(tu.fact_set) for tu in tus if tu.fact_set]
    if not non_empty or len(non_empty) != len(tus):
        return bool(non_empty)  # some reported, some silent -- a real mismatch
    first = non_empty[0]
    return not all(s == first for s in non_empty[1:])


def incomplete_families(coverage: dict[str, str]) -> list[str]:
    """Mandatory families whose rolled-up coverage means "do not trust absence"."""
    return sorted(
        f for f, state in coverage.items() if state in INCOMPLETE_COVERAGE_STATES
    )


def check_fact_set_compatibility(
    old_fact_set: dict[str, Any], new_fact_set: dict[str, Any]
) -> list[FactSetIssue]:
    """Compare two rolled-up ``fact_set`` blocks (ADR-038 C.8 rules 1-3).

    Either side may be ``{}`` (a pre-C.8 producer, or a mixed-producer pack);
    that is reported as a warning, not an error — an unknown fact-set never
    forces the comparison to abort (ADR-028's "never abort a hand-edited
    pack" convention), it is only reported so a strict CI gate can act on it.
    """
    issues: list[FactSetIssue] = []
    if not old_fact_set or not new_fact_set:
        issues.append(
            FactSetIssue(
                "warning",
                "fact_set_unknown",
                "one or both sides carry no fact_set identity (pre-C.8 producer, "
                "or a mixed-producer pack); fact-set compatibility could not be "
                "verified for this comparison.",
            )
        )
        return issues

    old_name = old_fact_set.get("name")
    new_name = new_fact_set.get("name")
    if old_name != new_name:
        issues.append(
            FactSetIssue(
                "error",
                "fact_set_name_mismatch",
                f"old baseline used fact_set name {old_name!r}, new baseline "
                f"used {new_name!r} — these are different canonical fact-set "
                "contracts even if their version numbers happen to match, so "
                "family coverage and opaque hashes are not comparable.",
            )
        )

    old_version = old_fact_set.get("version")
    new_version = new_fact_set.get("version")
    if old_version != new_version:
        issues.append(
            FactSetIssue(
                "error",
                "fact_set_version_mismatch",
                f"old baseline used fact_set version {old_version!r}, new baseline "
                f"used {new_version!r} — the declared mandatory-family contract "
                "differs, so a missing family on one side may only mean it was "
                "not yet defined, not that it disappeared.",
            )
        )

    old_family = old_fact_set.get("compiler_family")
    new_family = new_fact_set.get("compiler_family")
    if old_family != new_family:
        issues.append(
            FactSetIssue(
                "warning",
                "compiler_family_mismatch",
                f"old baseline compiler_family={old_family!r}, new "
                f"baseline compiler_family={new_family!r} — structured facts may "
                "still be comparable, but treat this pair's evidence cautiously.",
            )
        )

    old_producer = old_fact_set.get("producer")
    new_producer = new_fact_set.get("producer")
    if old_producer != new_producer:
        issues.append(
            FactSetIssue(
                "warning",
                "producer_mismatch",
                f"old baseline producer={old_producer!r}, new baseline "
                f"producer={new_producer!r} — opaque body/template hashes "
                "(inline_body_changed, template_body_changed) are producer-specific "
                "and are not guaranteed comparable across different producers; "
                "compiler-neutral structured facts remain comparable.",
            )
        )
    else:
        # Same producer, but a different producer_version: the recipe can
        # still have changed (a canonicalization/hashing fix, say) without the
        # mandatory-family fact_set.version moving, so opaque hashes are not
        # guaranteed comparable even here (Codex review).
        old_producer_version = old_fact_set.get("producer_version")
        new_producer_version = new_fact_set.get("producer_version")
        if old_producer_version != new_producer_version:
            issues.append(
                FactSetIssue(
                    "warning",
                    "producer_version_mismatch",
                    f"old baseline producer_version={old_producer_version!r}, new "
                    f"baseline producer_version={new_producer_version!r} — the "
                    f"same producer ({old_producer!r}) may have changed its "
                    "canonicalization/hashing recipe between versions; opaque "
                    "body/template hashes (inline_body_changed, "
                    "template_body_changed) are not guaranteed comparable across "
                    "producer versions.",
                )
            )
        else:
            # Same producer AND producer_version, but a different
            # compiler_version: the plugin/clang-extractor hash recipe ports
            # clang's own JSON AST dump (ADR-038 C.2/C.7), so opaque body/
            # template hashes are not guaranteed byte-stable across the
            # *compiler's* version even when the abicheck producer release
            # didn't change (Codex review).
            old_compiler_version = old_fact_set.get("compiler_version")
            new_compiler_version = new_fact_set.get("compiler_version")
            if old_compiler_version != new_compiler_version:
                issues.append(
                    FactSetIssue(
                        "warning",
                        "compiler_version_mismatch",
                        f"old baseline compiler_version={old_compiler_version!r}, "
                        f"new baseline compiler_version={new_compiler_version!r} "
                        f"— the same producer ({old_producer!r} "
                        f"{old_producer_version!r}) was loaded by a different "
                        "compiler version; opaque body/template hashes "
                        "(inline_body_changed, template_body_changed) are not "
                        "guaranteed comparable across compiler versions.",
                    )
                )

    return issues


#: A mismatch in the mandatory-family *contract itself* (rules 1/2) always
#: invalidates everything -- it means the two sides don't even agree on what
#: the fact set promises to collect, which no declared hash_recipe_id can
#: paper over. Never overridable by a matching hash_recipe_id.
_HARD_BLOCKING_RULES = frozenset(
    {"fact_set_name_mismatch", "fact_set_version_mismatch"}
)

#: FactSetIssue rules whose presence means opaque body/template hashes (and,
#: since G31 Phase C's enum-underlying-type finding, a structured fact's own
#: content hash too) may not be byte-/meaning-comparable across the old/new
#: pair (rule 3: producer/producer_version/compiler_version/compiler_family
#: identify the extraction/canonicalization recipe -- a different compiler
#: family mangles/canonicalizes differently even when the abicheck producer
#: identity matches, same reasoning as a compiler_version drift). These *are*
#: overridable by a matching hash_recipe_id -- a differential conformance run
#: can prove two different producer/compiler identities emit
#: byte-/meaning-comparable hashes.
_RECIPE_OVERRIDABLE_RULES = frozenset(
    {
        "producer_mismatch",
        "producer_version_mismatch",
        "compiler_family_mismatch",
        "compiler_version_mismatch",
    }
)

#: source_edges endpoint identities are derived from the same producer/
#: compiler-dependent AST-dump recipe as opaque hashes (mangled-name
#: resolution, canonicalization), so they are gated by the same rule set.
_SOURCE_EDGE_OVERRIDABLE_RULES = _RECIPE_OVERRIDABLE_RULES

#: Only a mismatch in the mandatory-family *contract itself* invalidates
#: compiler-neutral structured facts (signatures, declarations, type shapes);
#: a different producer/compiler can still extract the same structured facts
#: correctly even if its opaque-hash recipe differs.
_STRUCTURED_FACT_INVALIDATING_RULES = _HARD_BLOCKING_RULES


def hash_recipe_id(fact_set: dict[str, Any]) -> str:
    """Stable id for the opaque-hash canonicalization recipe a ``fact_set`` used.

    Two sides reporting the *same* recipe id have declared — typically because
    a differential conformance run (ADR-038 C.6) proved it — that their
    opaque body/template hashes are byte-comparable even when their producer
    name/version differs formally, which is more precise than treating any
    producer-name difference as inherently incompatible. Absent an explicit
    ``"hash_recipe_id"`` field, falls back to the ``producer``/
    ``producer_version``/``compiler_family``/``compiler_version`` quadruple
    :func:`check_fact_set_compatibility` already keys its rule-3 checks on, so
    fact-sets recorded before this field existed still compare consistently
    (two such fact-sets only share a fallback recipe id when they'd already
    pass rule 3 with no issues). ``compiler_family`` is included so a gcc vs.
    clang pair can never fall back to the same recipe id even when producer/
    producer_version/compiler_version otherwise match.
    """
    recipe = fact_set.get("hash_recipe_id")
    if isinstance(recipe, str) and recipe:
        return recipe
    return "|".join(
        str(fact_set.get(key, ""))
        for key in (
            "producer",
            "producer_version",
            "compiler_family",
            "compiler_version",
        )
    )


def check_fact_compatibility(
    old_fact_set: dict[str, Any],
    new_fact_set: dict[str, Any],
    *,
    old_inconsistent: bool = False,
    new_inconsistent: bool = False,
) -> FactCompatibility:
    """Structured, actionable comparability verdict (ADR-038 C.8 / PR2 gating).

    Wraps :func:`check_fact_set_compatibility` so a diff pass can gate
    specific evidence categories instead of only reporting prose that nothing
    downstream reads. A matching :func:`hash_recipe_id` on both sides
    overrides an otherwise-invalidating producer/producer_version/
    compiler_version mismatch for ``opaque_hashes_comparable`` and
    ``source_edges_comparable`` specifically — those sides have declared
    they use the same canonicalization recipe despite differing producer
    identity strings. Deliberately NOT ``structured_content_comparable``
    (Codex review, PR #719): ``hash_recipe_id`` is a declared statement
    about the OPAQUE body/template hash canonicalization recipe
    specifically — a differential conformance run (ADR-038 C.6) proves two
    producers emit byte-comparable opaque hashes, it says nothing about
    whether their STRUCTURED fact extraction (what a `type_hash` is built
    from) stayed identical. A producer could legitimately keep the same
    opaque-hash recipe id release over release while still changing what a
    structured fact like `EnumType.underlying_type` extracts, so honoring
    the override here would silently reopen the exact false-negative this
    flag exists to prevent for a producer pair that happens to declare
    conformant opaque hashes.

    When **both** sides carry no ``fact_set`` at all (a genuinely pre-C.8
    producer, or two hand-edited packs), :func:`check_fact_set_compatibility`
    reports only the informational ``fact_set_unknown`` warning — not one of
    the invalidating rules — so every category stays comparable here too,
    preserving the existing forward-compat behavior of never gating a
    symmetric pre-C.8 baseline pair's findings.

    An **asymmetric** absence (exactly one side stamped a ``fact_set``, the
    other didn't) is a different, riskier case and is NOT given that same
    forgiveness for the recipe-overridable categories
    (``structured_content_comparable``/``opaque_hashes_comparable``/
    ``source_edges_comparable``): the unstamped side may be a baseline
    persisted by literally the OLD producer version this PR's own gating was
    built to guard against (Codex review, PR #719 — the castxml extractor
    only started stamping ``fact_set`` in this same change, so every
    already-persisted castxml L4 baseline predating it hits exactly this
    one-sided shape when compared against a freshly re-collected new side).
    Content comparability there defaults to "unknown, don't trust it" rather
    than the symmetric case's "nothing to check, so nothing is gated".
    Existence/removal detection (``structured_facts_comparable``) is
    untouched by this distinction — that pre-C.8 forward-compat contract
    predates this PR and is not the gap being closed here.

    ``old_inconsistent``/``new_inconsistent`` (from
    :func:`fact_set_rollup_is_inconsistent`) name a THIRD shape that also
    collapses to an empty ``{}`` fact_set but must not receive the
    symmetric-absence forgiveness either: a mixed-producer pack whose own
    TUs disagreed on `fact_set`. That side's ``{}`` means "TUs disagree",
    not "no evidence either way", so it is exactly as untrustworthy as the
    asymmetric case above (Codex review, PR #719) -- and unlike that
    asymmetric case, it also gates ``structured_facts_comparable``
    (existence/removal detection): a genuinely inconsistent side cannot
    establish absence either (a family missing because a mixed-in producer
    variant never collects it looks identical to a real removal), whereas
    the pre-C.8 asymmetric-absence exemption above predates this PR and is
    intentionally left as-is.
    """
    issues = check_fact_set_compatibility(old_fact_set, new_fact_set)
    rules = {issue.rule for issue in issues}
    both_present = bool(old_fact_set) and bool(new_fact_set)
    one_sided = bool(old_fact_set) != bool(new_fact_set)
    inconsistent = old_inconsistent or new_inconsistent
    if inconsistent:
        # check_fact_set_compatibility() above only ever compares the two
        # ROLLED-UP fact_set dicts it was given -- it has no visibility into
        # old_inconsistent/new_inconsistent at all, so it reports nothing
        # when those dicts happen to be identical (Codex review, PR #719,
        # follow-up): a serialized/forward-produced surface can carry
        # fact_set_inconsistent=True while still stamping matching,
        # non-empty representative fact_set content on both sides (the
        # inconsistency is a fact about the surface's own constituent TUs,
        # not about the rolled-up dict this comparison sees). Without an
        # explicit issue here, that shape left `issues` empty even though
        # every category below is gated off by `inconsistent` -- a caller
        # like `_diff_fact_coverage()` that only reports when `issues` is
        # non-empty would then silently suppress findings with no
        # SOURCE_FACT_COVERAGE_INCOMPLETE to explain why.
        sides = [
            label
            for label, flag in (("old", old_inconsistent), ("new", new_inconsistent))
            if flag
        ]
        issues.append(
            FactSetIssue(
                "warning",
                "fact_set_inconsistent",
                f"{' and '.join(sides)} side's own TUs disagreed on fact_set "
                "(a mixed-producer pack); structured content, opaque "
                "body/template hashes, source_edges, and removal detection "
                "are all treated as not comparable for this comparison.",
            )
        )
    # Gated on `not inconsistent` (Codex review, PR #719): `rollup_fact_set()`
    # itself always collapses an inconsistent side to `{}`, so this couldn't
    # fire for that caller -- but `old_fact_set`/`new_fact_set` and
    # `old_inconsistent`/`new_inconsistent` are independent parameters, and a
    # hand-authored or forward-produced `source_abi.json` can legally set
    # `fact_set_inconsistent: true` in `coverage` while its `coverage.fact_set`
    # block still carries non-empty, matching representative content on both
    # sides. Without this guard a matching `hash_recipe_id` there would
    # override the inconsistency the same way it overrides an ordinary
    # producer/version mismatch, silently re-enabling opaque-hash and
    # source-edge comparisons for a pack whose own TUs disagreed on fact_set
    # -- exactly the untrustworthy shape `old_inconsistent`/`new_inconsistent`
    # exist to flag, not something a declared recipe id can vouch for.
    same_recipe = (
        both_present
        and not inconsistent
        and hash_recipe_id(old_fact_set) == hash_recipe_id(new_fact_set)
    )
    hard_blocked = bool(rules & _HARD_BLOCKING_RULES)
    # No hash_recipe_id override here -- see this function's own docstring
    # for why a declared opaque-hash recipe match must not also vouch for
    # structured-fact extraction staying identical.
    agrees_base = not one_sided and not inconsistent
    structured_content_agrees = agrees_base and not (rules & _RECIPE_OVERRIDABLE_RULES)
    recipe_agrees = same_recipe or (
        agrees_base and not (rules & _RECIPE_OVERRIDABLE_RULES)
    )
    source_edge_recipe_agrees = same_recipe or (
        agrees_base and not (rules & _SOURCE_EDGE_OVERRIDABLE_RULES)
    )
    return FactCompatibility(
        structured_facts_comparable=not hard_blocked and not inconsistent,
        structured_content_comparable=not hard_blocked and structured_content_agrees,
        opaque_hashes_comparable=not hard_blocked and recipe_agrees,
        source_edges_comparable=not hard_blocked and source_edge_recipe_agrees,
        issues=tuple(issues),
    )

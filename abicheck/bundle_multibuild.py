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

"""Multibuild variant pairing for the bundle layer (G38 Phase 3, amendment to
ADR-023 — see ``docs/contribute/plans/g38-bundle-facts-model-and-multibuild-
comparability.md``).

A release can ship more than one *build variant* of the same source tree from
one shared header surface — a CPU-only build and an ``ONEDAL_DATA_PARALLEL``
(SYCL/DPC) build of oneDAL are the motivating example. Comparing "the old
release" against "the new release" as if each were a single bundle is wrong
in a specific, silent way: if a declaration is present in the DPC variant and
missing from the CPU variant only, *unioning* the two variants' facts before
diffing makes the union report "present" on both old and new sides, hiding a
real CPU-only-build regression entirely.

This module never unions. It answers exactly two questions:

1. :func:`variant_fingerprint` — is one build the "same logical variant" as
   another? Fingerprints on stable, logical-identity build-axis coordinates
   only (which distinct configuration this is), never on build *state* that
   legitimately drifts release to release (macro defines, ``-std=``, which
   libraries the build happened to produce this time) — see that function's
   own docstring for the full list of deliberate exclusions and why each one
   is excluded.
2. :func:`pair_variants` — given two variant-labelled sets of
   :class:`~abicheck.bundle_facts.BundleFacts`, pair up same-fingerprint
   variants for an ordinary diff, and report every variant that exists on
   only one side as its own, explicit outcome — never dropped, never merged
   with the nearest available build.

This is a leaf module with respect to :mod:`abicheck.bundle`/
:mod:`abicheck.bundle_facts`: it only needs the ``BundleFacts`` type (a plain
dataclass, no live-binary or filesystem dependency) and doesn't call into
either module's own comparison machinery — pairing decides *which* facts get
compared, not *how*.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .bundle_facts import DEFAULT_VARIANT_FINGERPRINT, BundleFacts
from .bundle_models import BundleFinding
from .checker_policy import ChangeKind


def variant_fingerprint(
    *,
    target_triple: str = "",
    compiler_family: str = "",
    feature_toggles: Mapping[str, str] | None = None,
) -> str:
    """Stable fingerprint over LOGICAL VARIANT IDENTITY only.

    Deliberately takes explicit, named coordinates rather than parsing a raw
    :class:`~abicheck.buildsource.build_evidence.BuildEvidence`/
    :class:`~abicheck.environment_matrix.EnvironmentMatrix` itself: telling a
    genuine logical-identity feature toggle (``ONEDAL_DATA_PARALLEL`` — "this
    is the DPC build, not the CPU build") apart from build STATE that
    legitimately drifts release to release (an ABI-relevant ``-D`` define, a
    raised ``-std=``) cannot be done reliably from the raw evidence alone —
    both can appear as an indistinguishable ``BuildOption``/``CompileUnit``
    entry. Pushing that judgement call to the caller (the future multibuild
    CLI/config surface, not yet wired — see this module's own file-level
    docstring) keeps this function's own contract exact and testable, rather
    than embedding a heuristic that could silently misclassify either
    direction: mistaking a toggle for state would let two genuinely distinct
    variants collide into the same fingerprint (a straight return to the
    "union" failure mode this module exists to prevent — an unmatched
    coordinate on one side reads as absent, and two present-on-only-one-side
    coordinates each looking like "no evidence for the other side" is exactly
    what pairing wrongly matches as compatible); mistaking build state for a
    toggle would fingerprint every ordinary drifted-build-state comparison as
    two different, unmatched variants, replacing a real, classified finding
    (e.g. ``cxx_standard_floor_raised``) with a generic coverage regression.

    Deliberately EXCLUDES, by construction (there is no parameter for any of
    these):

    1. Artifact membership (which libraries actually shipped). Folding this
       in would make an ordinary library addition/removal look like "this is
       a different variant" instead of what it is — a real, versioned change
       *inside* the matched variant (``bundle_library_added``/
       ``bundle_library_removed`` already handle exactly this).
    2. Deployment/comparison-policy inputs (``EnvironmentMatrix.
       runtime_floors``, ``SyclConstraints.min_pi_version``,
       ``CudaConstraints.driver_range``) — these say how a finding should be
       *classified*, not which build produced the facts.
    3. C/C++ standard and ABI-affecting flags/defines — real build facts, but
       legitimately-drifting build STATE, not variant identity.
       ``comparability.py``'s own machinery already exists to let a
       *corroborated* language-standard or macro-defines change between old
       and new be compared and classified rather than refused; fingerprinting
       these fields for *pairing* would instead make same-variant,
       drifted-build-state comparisons read as two unmatched variants.
    4. **Compiler *version*** — a FOURTH instance of the same "drifting
       build state, not variant identity" class as (3), caught in review
       (Codex) against an earlier revision of this function that fingerprinted
       it: a routine toolchain upgrade between releases (GCC 13 -> 14
       building the identical CPU-only variant, say) would otherwise make
       :func:`pair_variants` read the old and new release as two *different*,
       unmatched variants — ``OLD_ONLY`` + ``NEW_ONLY`` — silently skipping
       every real per-library comparison for that variant and replacing it
       with a spurious ``bundle_variant_coverage_regressed`` finding, exactly
       the failure mode exclusion (3) already exists to prevent for
       ``-std=``/defines. Unlike ``target_triple``/``compiler_family``
       (below), a compiler version bump has no dedicated classified finding
       to defer to today, but that is an argument for adding one at the
       comparability layer if this ever needs to be surfaced, not for
       reintroducing it into variant *identity*.

    ``target_triple``/``compiler_family`` ARE part of the fingerprint (the
    declared, logical-identity toolchain facts a real cross-target/cross-
    toolchain-family multibuild release ships — a target or compiler-family
    switch is a real, deliberate distribution-channel decision, not routine
    build drift the way a version bump is) — this is deliberately narrower
    than a genuine toolchain-identity *probe* validating a resolved compiler
    binding's real family/target against these declared coordinates, which is
    out of scope here (see the plan doc's own "Out of scope" section; already
    tracked as its own gap in the root ``AGENTS.md``'s "Toolchain-profile
    compiler-family rendering" entry and G34 Phase A).

    All coordinates degrade to the empty string / empty mapping when unknown
    — a caller with no real per-variant identity at all (every caller today)
    gets exactly :data:`~abicheck.bundle_facts.DEFAULT_VARIANT_FINGERPRINT`
    back (``"default"``), the same literal value an existing/deserialized
    ``BundleFacts`` with no multibuild distinction already carries — so
    pairing a legacy unqualified baseline against an equivalently unqualified
    side computed through this function still pairs, rather than reading as
    an old-only-plus-new-only false coverage regression (Codex review, fresh
    evidence).

    The non-default encoding is JSON, not a hand-joined delimited string:
    a naive ``"|".join(...)``/``","``-joined encoding lets a delimiter
    character embedded in a caller-supplied value (a toggle key/value
    containing ``,``/``=``, or a coordinate string containing ``|``) collide
    with the encoding's own separators, so two genuinely different inputs
    could fingerprint identically -- silently reintroducing the very
    "distinct variants read as the same identity" failure this module exists
    to prevent (Codex review, fresh evidence: ``{"A": "1,B=2"}`` and
    ``{"A": "1", "B": "2"}`` collided under the original join-based scheme).
    JSON's own string escaping and structural (list-of-strings) encoding
    make that collision impossible regardless of what characters a caller's
    values contain.
    """
    toggles = feature_toggles or {}
    if not target_triple and not compiler_family and not toggles:
        return DEFAULT_VARIANT_FINGERPRINT
    # Sorted so fingerprint order never depends on the caller's mapping
    # iteration order (dict insertion order is not a fact about the build).
    return json.dumps(
        [
            target_triple,
            compiler_family,
            sorted(toggles.items()),
        ]
    )


class VariantOutcome(str, Enum):
    """How one fingerprint-identified variant compared across old/new."""

    #: Present on both sides -- an ordinary per-variant comparison applies.
    PAIRED = "paired"
    #: Present only in OLD -- a real build-coverage regression. The release
    #: stopped building a variant a consumer may still depend on.
    OLD_ONLY = "old_only"
    #: Present only in NEW -- coverage EXPANSION, not regression. A newly
    #: added variant is good news, not a build that "went missing"; this
    #: outcome deliberately never produces a ChangeKind finding (see
    #: :func:`coverage_regression_findings`).
    NEW_ONLY = "new_only"


@dataclass(frozen=True)
class VariantComparison:
    """One fingerprint-identified variant's pairing outcome.

    ``old_label``/``new_label`` are the caller-supplied dict keys from
    :func:`pair_variants`' own ``old``/``new`` mappings (e.g. ``"cpu"``,
    ``"dpc"``) -- kept purely for display/provenance. Pairing itself is by
    ``fingerprint`` equality, never by label: two variants with the same
    label on both sides but different fingerprints are NOT paired (the build
    actually changed identity), and two variants with different labels but
    the same fingerprint ARE paired (a caller renamed a variant between
    releases without changing what it is).
    """

    fingerprint: str
    outcome: VariantOutcome
    old_label: str | None = None
    new_label: str | None = None
    old_facts: BundleFacts | None = None
    new_facts: BundleFacts | None = None


def _index_by_fingerprint(
    variants: Mapping[str, BundleFacts], *, side: str
) -> dict[str, tuple[str, BundleFacts]]:
    """Group *variants* by their own ``BundleFacts.variant_fingerprint``.

    Raises ``ValueError`` on a same-side fingerprint collision rather than
    silently keeping one and dropping the other -- a silent drop is exactly
    the "quietly lose coverage of a variant" failure mode this module exists
    to prevent, just reached from the input side instead of the pairing
    logic. A caller that genuinely wants two labels to be the same variant
    should give them the same label, not rely on fingerprint collision.

    Also raises on an empty-string ``variant_fingerprint`` (Codex review):
    :func:`variant_fingerprint` itself never produces one -- the
    no-coordinates case returns the explicit ``DEFAULT_VARIANT_FINGERPRINT``
    sentinel, never ``""`` -- so an empty fingerprint here means the
    ``BundleFacts`` came from somewhere that skipped that function (a
    malformed or hand-edited serialized pack; ``bundle_facts_from_dict()``
    only substitutes the default sentinel when the key is entirely
    *missing*, not when it is present but empty). Pairing two such entries
    on the shared empty string would treat them as the same variant despite
    carrying no real identity evidence at all -- exactly the union-style
    failure this whole module exists to prevent, just reached via malformed
    input instead of a caller's own fingerprint choice.
    """
    by_fp: dict[str, tuple[str, BundleFacts]] = {}
    for label, facts in variants.items():
        fp = facts.variant_fingerprint
        if not fp:
            raise ValueError(
                f"pair_variants: {side} variant {label!r} has an empty "
                f"variant_fingerprint -- variant_fingerprint() never "
                f"produces one (the no-coordinates case is the "
                f"DEFAULT_VARIANT_FINGERPRINT sentinel, not ''), so this "
                f"BundleFacts did not come from it; fix the input rather "
                f"than pairing it on an empty, non-identifying key"
            )
        if fp in by_fp:
            other_label = by_fp[fp][0]
            raise ValueError(
                f"pair_variants: {side} variants {other_label!r} and {label!r} "
                f"share the same variant_fingerprint {fp!r} -- each entry in "
                f"the mapping must represent one distinct build variant"
            )
        by_fp[fp] = (label, facts)
    return by_fp


def pair_variants(
    old: Mapping[str, BundleFacts], new: Mapping[str, BundleFacts]
) -> list[VariantComparison]:
    """Pair *old* and *new* build variants by fingerprint equality.

    Pairs by ``BundleFacts.variant_fingerprint`` equality (not nearest-match,
    not by the caller's own dict key). A variant present on both sides is
    diffed normally by the caller (``VariantOutcome.PAIRED``). A variant
    present only in ``old`` is a real coverage regression
    (``VariantOutcome.OLD_ONLY`` -- the caller renders this as
    ``ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED`` via
    :func:`coverage_regression_findings`). A variant present only in ``new``
    is coverage EXPANSION, not regression (``VariantOutcome.NEW_ONLY``) --
    recorded so a caller can still report "N variants compared, 1 newly
    added", but never emitted as a finding. Neither shape is ever dropped or
    paired with a mismatched variant: this function has no code path that
    merges two different fingerprints' facts, and it always returns exactly
    one :class:`VariantComparison` per distinct fingerprint on either side.

    Result order is by fingerprint string, for a deterministic, input-order-
    independent result -- callers that care about original insertion order
    should sort ``old_label``/``new_label`` themselves.
    """
    old_by_fp = _index_by_fingerprint(old, side="old")
    new_by_fp = _index_by_fingerprint(new, side="new")

    results: list[VariantComparison] = []
    for fp in sorted(set(old_by_fp) | set(new_by_fp)):
        old_entry = old_by_fp.get(fp)
        new_entry = new_by_fp.get(fp)
        if old_entry is not None and new_entry is not None:
            outcome = VariantOutcome.PAIRED
        elif old_entry is not None:
            outcome = VariantOutcome.OLD_ONLY
        else:
            outcome = VariantOutcome.NEW_ONLY
        results.append(
            VariantComparison(
                fingerprint=fp,
                outcome=outcome,
                old_label=old_entry[0] if old_entry else None,
                new_label=new_entry[0] if new_entry else None,
                old_facts=old_entry[1] if old_entry else None,
                new_facts=new_entry[1] if new_entry else None,
            )
        )
    return results


def coverage_regression_findings(
    comparisons: Iterable[VariantComparison],
) -> list[BundleFinding]:
    """Render every ``OLD_ONLY`` comparison as a
    ``BUNDLE_VARIANT_COVERAGE_REGRESSED`` finding.

    A ``NEW_ONLY`` comparison (coverage expansion) and a ``PAIRED`` one
    (ordinary per-variant diff, handled by the caller's own comparison, not
    by this module) never produce a finding here -- this function is
    intentionally the only place ``ChangeKind.BUNDLE_VARIANT_COVERAGE_
    REGRESSED`` is constructed, so "which outcomes become findings" has one
    answer.
    """
    findings: list[BundleFinding] = []
    for comparison in comparisons:
        if comparison.outcome is not VariantOutcome.OLD_ONLY:
            continue
        old_facts = comparison.old_facts
        assert old_facts is not None  # OLD_ONLY always carries old_facts
        libraries: Sequence[str] = sorted(old_facts.per_library_snapshots)
        label = comparison.old_label or comparison.fingerprint
        detail = (
            f"{len(libraries)} librar{'y' if len(libraries) == 1 else 'ies'} "
            f"in this variant: {', '.join(libraries)}"
            if libraries
            else "no libraries recorded for this variant"
        )
        findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED,
                symbol=label,
                description=(
                    f"Build variant '{label}' present in the old release has "
                    f"no matching variant in the new release ({detail})."
                ),
                affected_libraries=list(libraries),
                old_value=comparison.fingerprint,
                new_value=None,
            )
        )
    return findings

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

"""One bundle-analysis orchestrator (G38 stabilization Phase 12).

Before this module existed, "bundle analysis" meant two independently-called
functions that a caller had to remember to invoke together:
:func:`abicheck.bundle.compare_bundle` (the core graph-native/diff-derived
detector suite — library add/remove, intra-dep signature drift, intra-type
drift, provider migration, version drift, SONAME skew, manifest
enforcement) and :func:`abicheck.bundle_signature_evidence.
find_unverified_signature_findings` (G38 Phase 4's C-boundary
signature-evidence gate — a sibling's undefined import resolving by name to
a new export whose type evidence can't confirm or deny the signature
actually agrees).

The live ``compare --release`` CLI path (``cli_compare_release_helpers.
_run_bundle_analysis``) called both, in order, folding the second into the
first's own ``bundle_findings`` list. The stored-baseline path
(:func:`abicheck.bundle_facts.compare_bundle_from_facts`) called only the
first — a stored side had no full ``AbiSnapshot`` map (a
:class:`~abicheck.bundle_facts.BundleFacts` document doesn't have to carry
one for the *new* side at all, only its own captured old side) and no
second call site ever threaded one through. So a stored-baseline comparison
never ran the Phase 4 gate, even when both sides' signature evidence was
genuinely available — "live vs. live" and "stored old vs. live new" bundle
analysis could disagree on findings for identical underlying evidence, which
is exactly the parity Phase 2's own design section promised and Phase 4's
wiring quietly broke.

:func:`analyze_bundle` is the fix: **the one bundle-analysis entry point**
both the live release path and the stored-facts path call. It takes
optional per-library signature-evidence maps (old/new), each accepting
either a real :class:`~abicheck.model.AbiSnapshot` or the compact
:class:`~abicheck.bundle_models.BundleSignatureEvidence` projection (G38
Phase 9) — duck-type compatible, so a stored old side with no retained full
snapshot (only ``BundleFacts.per_library_snapshots``, which *is* a real
``AbiSnapshot`` map — see that field's own docstring) and a live new side
with only the compact projection (Phase 9's memory-regression fix) can both
participate in the same call. :func:`abicheck.bundle.compare_bundle` stays
the core graph-native/diff-derived detector implementation — this module
does not reimplement or duplicate any of its logic, it only sequences it
alongside the signature-evidence gate and merges the two results — so it is
no longer presented as the *complete* bundle-analysis surface on its own;
:func:`analyze_bundle` is.

Both stages degrade additively on failure, mirroring the existing
Phase 11 contract (``BundleDiffResult.analysis_errors``): a bug in either
stage must not blank out whatever the other stage already found, and every
degradation is recorded structurally (not only echoed to a caller's own
logging), so a JSON/Markdown report consumer can distinguish "bundle
analysis ran clean" from "ran, but degraded" without grepping logs.

Leaf module with respect to both halves it orchestrates:
:mod:`abicheck.bundle` and :mod:`abicheck.bundle_signature_evidence` are
each importable at module scope here with no cycle (neither imports this
module, or anything that transitively would), so unlike
:mod:`abicheck.bundle_facts` (which imports :mod:`abicheck.bundle` only
lazily to avoid a real cycle through :mod:`abicheck.bundle_models`), this
module needs no lazy-import dance of its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bundle import BundleDiffResult
    from .bundle_manifest import InstantiationManifest
    from .bundle_models import BundleSignatureEvidence, BundleSnapshot
    from .checker_types import DiffResult
    from .model import AbiSnapshot
    from .policy_file import PolicyFile


def analyze_bundle(
    old: BundleSnapshot,
    new: BundleSnapshot,
    per_library_results: list[DiffResult],
    *,
    manifest: InstantiationManifest | None = None,
    system_providers: Iterable[str] | None = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    old_signature_evidence: Mapping[str, AbiSnapshot | BundleSignatureEvidence]
    | None = None,
    new_signature_evidence: Mapping[str, AbiSnapshot | BundleSignatureEvidence]
    | None = None,
) -> BundleDiffResult:
    """Run the complete bundle-analysis pipeline: the core graph-native/
    diff-derived detector suite (:func:`abicheck.bundle.compare_bundle`)
    plus, when evidence for both sides is given, the Phase 4 C-boundary
    signature-evidence gate
    (:func:`abicheck.bundle_signature_evidence.find_unverified_signature_
    findings`) -- one orchestrator, so a live comparison and a stored-facts
    comparison over the same underlying evidence can no longer diverge on
    which detectors actually ran.

    Args:
        old: Bundle snapshot of the old release (mirrors ``compare_bundle``).
        new: Bundle snapshot of the new release (mirrors ``compare_bundle``).
        per_library_results: Per-library ``checker.compare()`` output
            (mirrors ``compare_bundle``). Not modified.
        manifest / system_providers / cohorts / policy / policy_file:
            Forwarded verbatim to ``compare_bundle`` -- see that function's
            own docstring.
        old_signature_evidence: Bundle-canonical-key -> ``AbiSnapshot`` (or
            the compact ``BundleSignatureEvidence`` projection) for the OLD
            side. When this and *new_signature_evidence* are both given and
            non-empty, ``find_unverified_signature_findings`` runs and its
            output is folded into the returned result's ``bundle_findings``.
            Omitted (the default) or empty on either side: the Phase 4 gate
            simply does not run, matching every pre-Phase-12 caller's
            behavior exactly (a stored-facts comparison with no
            *new_signature_evidence* given, or a live comparison that never
            collected per-library evidence at all).
        new_signature_evidence: The NEW side's counterpart to
            *old_signature_evidence*.

    Returns:
        One :class:`~abicheck.bundle_models.BundleDiffResult` carrying both
        stages' findings merged into ``bundle_findings``, and either stage's
        own failure recorded additively in ``analysis_errors`` (G38
        stabilization Phase 11's structured-degradation contract) -- a
        failure in one stage never discards what the other stage already
        found.
    """
    # Lazy, per-call imports -- not for cycle-avoidance (this module is a
    # genuine leaf, see the module docstring), but so a test that
    # monkeypatches `abicheck.bundle.compare_bundle`/`abicheck.
    # bundle_signature_evidence.find_unverified_signature_findings` on the
    # module object (the established pattern this codebase's own bundle-
    # analysis tests already use) is honored here exactly the way it was
    # honored by the pre-Phase-12 call sites, which did the identical lazy
    # import inside their own function bodies for the same reason.
    from .bundle import BundleDiffResult, compare_bundle
    from .bundle_signature_evidence import find_unverified_signature_findings

    try:
        result = compare_bundle(
            old,
            new,
            per_library_results,
            manifest=manifest,
            system_providers=system_providers,
            cohorts=cohorts,
            policy=policy,
            policy_file=policy_file,
        )
    except Exception as exc:
        # Mirrors the pre-Phase-12 `_run_bundle_analysis` contract exactly:
        # a `compare_bundle()` failure still yields a usable (if empty)
        # `BundleDiffResult`, carrying the resolved `policy` so a caller's
        # later severity/exit-code fold (Phase 10) doesn't silently score
        # this stub under an implicit `policy=None`.
        result = BundleDiffResult(
            old_root=old.root,
            new_root=new.root,
            # Preserve every already-computed per-library verdict even
            # though the bundle-level detector itself failed -- otherwise
            # `.verdict`/`.per_library_verdict` silently read NO_CHANGE for
            # a library that was, e.g., BREAKING, which is a false-green
            # aggregate a caller (including `compare_bundle_from_facts`,
            # which used to propagate this exception rather than swallow
            # it) could act on directly (Codex review).
            per_library=list(per_library_results),
            policy=policy,
            policy_file=policy_file,
            analysis_errors=[f"bundle analysis raised: {exc}"],
        )

    if old_signature_evidence and new_signature_evidence:
        # G38 Phase 4, now shared by both callers instead of being a second,
        # independently-wired call site: same additive-degradation contract
        # as the compare_bundle() stage above -- a bug in this detector must
        # not blank out bundle findings compare_bundle() already computed.
        try:
            result.bundle_findings.extend(
                find_unverified_signature_findings(
                    old,
                    new,
                    per_library_results,
                    old_signature_evidence,
                    new_signature_evidence,
                )
            )
        except Exception as exc:
            result.analysis_errors.append(
                f"bundle signature-evidence check raised: {exc}"
            )

    return result

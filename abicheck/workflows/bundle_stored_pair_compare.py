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

"""Stored/stored ``BundleFacts`` pair comparison (CLI cleanup phase two,
PR I).

:func:`compare_stored_bundle_facts_pair` is the driver behind ``compare``'s
stored/stored operand shape: both OLD_INPUT and NEW_INPUT are already
persisted :class:`~abicheck.bundle_facts.BundleFacts` documents (each from a
prior ``compare --bundle-facts-out``), so this is a pure in-memory diff --
no binaries read, no header AST parsed on either side.

Lives under ``abicheck/workflows/`` (ADR-061 -- "Coordinate dump, compare,
scan, release, aggregate, project, or dependency behavior" is exactly what
this function does) rather than alongside its stored/live sibling in
``bundle_side_input.py`` -- that module is itself a grandfathered flat-root
legacy file (see its own module docstring), and this is genuinely new
compare-workflow coordination, not a resolution primitive that module's
existing ``LiveBundleInput``/``StoredBundleFactsInput``/``resolve_bundle_
side`` shapes already cover (Codex review, PR #1060: "put the stored-pair
workflow in workflows" -- the same reasoning that already moved
``known_libraries_for_new_side`` out to
``workflows/bundle_facts_library_overrides.py`` rather than growing
``bundle_side_input.py`` for it).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult
    from ..checker_types import DiffResult
    from ..policy_file import PolicyFile
    from .suppression import SuppressionList
from .release_scope import (
    build_stored_baseline_scope_record,
    out_of_scope_provider_names,
    restrict_bundle_facts,
    scope_manifest_to_members,
)


def compare_stored_bundle_facts_pair(
    old_facts_path: Path,
    new_facts_path: Path,
    *,
    manifest_path: Path | None = None,
    system_providers: list[str] | None = None,
    cohorts: list[str] | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppress: SuppressionList | None = None,
    old_max_json_object_nodes: int | None = None,
    new_max_json_object_nodes: int | None = None,
    depth: str | None = None,
) -> BundleDiffResult:
    """End-to-end driver: two stored ``BundleFacts`` documents compared
    against each other -- the stored/stored operand shape (CLI cleanup
    phase two, PR I).

    No binaries are read and no header AST is parsed on either side --
    both sides are already resolved, per-library ``AbiSnapshot``s, so this
    driver is a pure in-memory diff: matched-key intersection, then one
    ``workflows.compare_policy.compare_snapshots()`` call per matched library, exactly the
    same Tier-2 chokepoint every other comparison entry point in this
    codebase uses (never ``checker.compare`` directly). Mirrors
    :func:`~abicheck.bundle_side_input.compare_release_against_bundle_facts`'s
    own final step (a direct call into
    :func:`~abicheck.bundle_facts.compare_bundle_from_facts`, never
    :func:`~abicheck.bundle_side_input.compare_bundle_sides`/
    :func:`~abicheck.bundle_side_input.resolve_bundle_side`) for the
    identical reason documented there: each stored document is already
    loaded in memory for the per-library matching loop below, and routing
    through ``StoredBundleFactsInput``/``resolve_bundle_side`` instead would
    reload and re-parse both documents from disk a second time for no
    benefit.

    Refuses to diff two documents captured from different logical build
    variants (Codex review, PR #1060): ``BundleFacts.variant_fingerprint``
    (``bundle_multibuild.variant_fingerprint`` -- stable build-axis identity
    only, e.g. a CPU-only build vs. a SYCL/DPC build of the same source
    tree) must match on both sides, or this raises ``ValueError`` rather
    than silently intersecting their library names and diffing artifacts
    that were never the same build to begin with -- the same "never union,
    never silently pair a mismatch" discipline
    ``bundle_multibuild.pair_variants`` already establishes for the
    *multi*-variant case. This function deliberately doesn't route through
    ``pair_variants`` itself: that machinery pairs whole *mappings* of
    labelled variants (the not-yet-wired multibuild CLI/config surface --
    see that module's own docstring), which is the wrong shape for two
    single, already-identified documents -- a direct fingerprint-equality
    check is the proportionate check for exactly two inputs. An ordinary
    document that never set a variant (``BundleFacts.variant_fingerprint``
    defaults to ``DEFAULT_VARIANT_FINGERPRINT``) is unaffected: both sides
    share that same default and compare equal.

    A library present in only one of the two documents' own
    ``per_library_snapshots`` is simply not diffed, the same "not itself a
    release fan-out's ``--fail-on-removed-library``/added-library
    accounting" narrowing ``compare_release_against_bundle_facts``'s own
    docstring already states for its NEW-live side -- neither stored side
    can name a *set* of on-disk libraries to fail on the absence of, both
    are already a fixed, resolved snapshot.

    *old_max_json_object_nodes*/*new_max_json_object_nodes*, each when
    given, override ``bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES`` for that
    side's own load -- forwarded to ``serialization.load_bundle_facts``,
    mirroring ``StoredBundleFactsInput``'s own field of the same purpose.
    ``None`` (the default) uses the library default for that side.

    *manifest_path*/*system_providers*/*cohorts*/*policy*/*policy_file*/
    *suppress* all mean exactly what they mean on
    ``compare_release_against_bundle_facts`` -- see that function's own
    docstring for the full account of each. *policy*/*policy_file* are
    forwarded to every per-library ``workflows.compare_policy.compare_snapshots()`` call
    *and* to the final ``compare_bundle_from_facts()`` call, so
    ``BundleDiffResult.bundle_verdict`` is scored under the same policy as
    every per-library diff, not just the live-NEW-side driver's own case.

    *depth*, when given, is enforced as a floor (``workflows.artifact.
    execute.enforce_requested_depth`` -- raises ``ValueError`` when a
    matched pair's resolved evidence falls short of what was requested)
    and then applied as a ceiling to every stored snapshot on both sides
    (``policy.depth_projection.project_snapshot_to_depth``) before diffing
    -- the same floor-then-ceiling pairing ``classify_compare_pair``
    (``service_compare_pipeline.py``) applies for every other
    resolved-snapshot comparison path (``cli_compare_helpers.py``,
    ``cli_scan_baseline.py``). Only ``binary``/``headers`` are reachable
    here at all, since ``--depth build``/``source`` are rejected
    unconditionally for a stored OLD_INPUT elsewhere: this driver has no
    channel to *collect* L3-L5 evidence, only to enforce and project
    already-resolved evidence. Unlike a live NEW-side dump (which
    ``service.resolve_input`` can be asked to skip header parsing for),
    both sides here are *already* fully-resolved ``AbiSnapshot``s with
    whatever evidence they were captured with baked in -- rejecting the
    flag outright, which an earlier version of this function did on the
    mistaken premise that no such projection primitive existed, was wrong
    (Codex review, PR #1060, fresh evidence: it does, and every other
    resolved-snapshot comparison path in this codebase already calls it).
    Both projected maps -- not the raw, unprojected
    ``old_facts``/``new_facts.per_library_snapshots`` -- are also what
    reaches ``compare_bundle_from_facts()``'s own signature-evidence gate
    below (Codex review, PR #1060, round 6): otherwise a header-complete
    stored snapshot could still satisfy ``find_unverified_signature_
    findings``'s type-evidence check under an explicit depth ceiling the
    per-library diff itself was capped to. ``None`` (the default) is a
    no-op for both the floor check and the projection, matching
    ``enforce_requested_depth``'s/``project_snapshot_to_depth``'s own
    documented contracts.
    """
    from ..analysis_assurance import compute_analysis_assurance
    from ..bundle_facts import bundle_snapshot_from_facts, compare_bundle_from_facts
    from ..bundle_manifest import load_manifest
    from ..policy.depth_projection import project_snapshot_to_depth
    from ..serialization import load_bundle_facts
    from .artifact.execute import enforce_requested_depth
    from .compare_policy import compare_snapshots

    old_facts = load_bundle_facts(
        old_facts_path, max_json_object_nodes=old_max_json_object_nodes
    )
    new_facts = load_bundle_facts(
        new_facts_path, max_json_object_nodes=new_max_json_object_nodes
    )

    # Codex review, PR #1060, fresh evidence after the mismatch check just
    # below landed: an empty variant_fingerprint carries no real identity
    # evidence at all (bundle_multibuild.variant_fingerprint() itself never
    # produces one -- the no-coordinates case is the DEFAULT_VARIANT_
    # FINGERPRINT sentinel, never "" -- but the plain BundleFacts loader
    # preserves an empty string verbatim if the document was hand-authored
    # or otherwise malformed), so two such documents comparing "" == ""
    # would pass the equality check below despite neither side actually
    # attesting to being the same build. Reject rather than let an
    # identity-free coincidence stand in for a real match, mirroring
    # bundle_multibuild._index_by_fingerprint's own identical rejection for
    # the multi-variant case.
    for _facts, _path in ((old_facts, old_facts_path), (new_facts, new_facts_path)):
        if not _facts.variant_fingerprint:
            raise ValueError(
                f"{_path} has an empty variant_fingerprint -- "
                "variant_fingerprint() never produces one (the no-coordinates "
                "case is the DEFAULT_VARIANT_FINGERPRINT sentinel, not ''), "
                "so this document did not come from it; fix the input rather "
                "than comparing it on an empty, non-identifying key"
            )
    if old_facts.variant_fingerprint != new_facts.variant_fingerprint:
        raise ValueError(
            f"{old_facts_path} and {new_facts_path} were captured from "
            f"different build variants (variant_fingerprint "
            f"{old_facts.variant_fingerprint!r} vs "
            f"{new_facts.variant_fingerprint!r}) -- refusing to diff them "
            "as if they were the same build. Compare two documents captured "
            "from the same logical variant (or use "
            "bundle_multibuild.pair_variants() for a genuine multi-variant "
            "comparison)."
        )

    matched_keys = sorted(
        set(old_facts.per_library_snapshots) & set(new_facts.per_library_snapshots)
    )

    # Codex review, PR #1060, round 6: project *every* library's stored
    # snapshot (not just the matched pairs fed to compare_snapshots below)
    # down to the requested depth once, up front, and reuse the same
    # projected maps both for the per-library diff and for the bundle-level
    # signature-evidence gate a few lines down -- previously that gate read
    # old_facts.per_library_snapshots/new_facts.per_library_snapshots
    # (raw, unprojected) directly, so a header-complete stored snapshot
    # could still satisfy find_unverified_signature_findings' type-evidence
    # check under an explicit --depth binary/headers ceiling even though the
    # equivalent live binary-depth view would mark those exports ELF_ONLY
    # and flag BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED. project_snapshot_to_
    # depth is a pure per-snapshot projection (no cross-side interaction),
    # so projecting the whole map here is equivalent to -- and replaces --
    # the previous per-matched-pair project_pair_to_depth() call.
    projected_old_snapshots = {
        key: project_snapshot_to_depth(snap, depth)
        for key, snap in old_facts.per_library_snapshots.items()
    }
    projected_new_snapshots = {
        key: project_snapshot_to_depth(snap, depth)
        for key, snap in new_facts.per_library_snapshots.items()
    }

    per_library_results: list[DiffResult] = []
    # ADR-065 D8: a member whose capture degraded to an ELF-only snapshot
    # is *failed* on that side -- its stored snapshot is not evidence of
    # what the library declared, so diffing it would read every real
    # declaration as an addition/removal. Skipped here and named in
    # `analysis_errors` below; the graph-level bundle analysis still sees
    # the member's ELF facts, which the degraded capture does carry.
    degraded_notes = [
        f"{key}: {side} side was captured degraded ({reason}); per-library "
        "comparison skipped (ADR-065 D8)"
        for key in matched_keys
        for side, facts in (("OLD", old_facts), ("NEW", new_facts))
        for reason in (facts.degraded_members.get(key),)
        if reason is not None
    ]
    degraded_keys = {
        key
        for key in matched_keys
        if key in old_facts.degraded_members or key in new_facts.degraded_members
    }
    for key in matched_keys:
        if key in degraded_keys:
            continue
        raw_old = old_facts.per_library_snapshots[key]
        raw_new = new_facts.per_library_snapshots[key]
        # Codex review, PR #1060, round 6: the floor half of the same
        # binary/headers/build/source contract project_snapshot_to_depth()
        # (the ceiling half, applied above) already enforces below --
        # classify_compare_pipeline pairs the two unconditionally
        # (enforce_requested_depth() confirms the resolved evidence
        # *reaches* depth, then the projection caps it back down), and this
        # driver had only ever picked up the second half. Without this,
        # --depth headers over two binary-only stored documents silently
        # reported NO_CHANGE instead of failing loudly: the projection is a
        # no-op ceiling on evidence that was already at or below the
        # requested rung, so the comparison would proceed on binary-only
        # facts as if headers-level evidence had genuinely backed it.
        enforce_requested_depth(
            depth, ((f"old:{key}", raw_old), (f"new:{key}", raw_new))
        )
        diff = compare_snapshots(
            projected_old_snapshots[key],
            projected_new_snapshots[key],
            suppress,
            policy=policy,
            policy_file=policy_file,
        )
        if depth is not None:
            # Codex review, PR #1060, round 10: service_compare_pipeline.
            # resolve_compare_request() stamps DiffResult.requested_depth
            # and recomputes analysis_assurance (depth_satisfied included)
            # after its own floor/ceiling pair -- this driver enforced and
            # projected depth (immediately above) but never did either, so
            # every stored/stored --depth run persisted analysis_assurance.
            # requested_depth/depth_satisfied as null despite the evidence
            # contract this driver actually enforced. compute_analysis_
            # assurance is pure/cheap (reads fields already on diff/the
            # projected snapshots, no re-extraction) -- safe to call once
            # more per matched library here, the same "safe to call
            # unconditionally, again later once enriched" contract that
            # function's own docstring states.
            diff.requested_depth = depth
            diff.analysis_assurance = compute_analysis_assurance(
                diff, projected_old_snapshots[key], projected_new_snapshots[key]
            )
        per_library_results.append(diff)

    # Codex review, PR #1060: compare_bundle_from_facts()'s own precedence
    # (an explicit manifest, else old_facts.manifest) is the wrong contract
    # here -- it was written for the stored/live driver, whose NEW side is
    # never itself a BundleFacts document with its own captured manifest.
    # Both sides genuinely can carry one here, so this mirrors
    # compare_bundle_sides()'s own three-tier precedence instead: an
    # explicit --manifest always wins, then OLD's own captured manifest,
    # then NEW's -- rather than silently discarding a real NEW-only
    # manifest (which would drop its BUNDLE_MANIFEST_INSTANTIATION_REMOVED
    # coverage entirely).
    manifest = (
        load_manifest(manifest_path)
        if manifest_path is not None
        else (old_facts.manifest or new_facts.manifest)
    )
    # ADR-065 S2: the record first, so the bundle graph below sees matched
    # members and proven removals/additions only (D2; Codex review) and
    # the skipped member is `failed` on the record for the dispatcher's
    # completeness axis (a nonempty `per_library` alone read a partially
    # unchecked bundle as complete).
    scope_record = build_stored_baseline_scope_record(
        old_facts.per_library_snapshots,
        new_facts.per_library_snapshots,
        compared=[key for key in matched_keys if key not in degraded_keys],
        degraded={
            key: (
                old_facts.degraded_members[key]
                if key in old_facts.degraded_members
                else new_facts.degraded_members[key]
            )
            for key in sorted(degraded_keys)
        },
        old_provenance="stored bundle-facts capture that made no complete-inventory assertion",
        new_provenance="stored bundle-facts capture that made no complete-inventory assertion",
        old_complete=old_facts.inventory_complete,
        new_complete=new_facts.inventory_complete,
        # An unmatched degraded member is `failed` on its own side (Codex
        # review, twenty-ninth round): never a proven removal/addition, and
        # dropped with the member before the bundle graph is rebuilt.
        old_failed={
            k: f"OLD side was captured degraded ({v}); comparison skipped (ADR-065 D8)"
            for k, v in old_facts.degraded_members.items()
            if k not in new_facts.per_library_snapshots
        },
        new_failed={
            k: f"NEW side was captured degraded ({v}); comparison skipped (ADR-065 D8)"
            for k, v in new_facts.degraded_members.items()
            if k not in old_facts.per_library_snapshots
        },
    )
    # ADR-065 D2 (Codex review): a promise only an excluded member could
    # answer is withheld, not reported as manifest drift.
    manifest, manifest_note = scope_manifest_to_members(manifest, scope_record)
    new_bundle_snapshot = bundle_snapshot_from_facts(
        restrict_bundle_facts(new_facts, scope_record)
    )
    result = compare_bundle_from_facts(
        # The scoped effective manifest even when None (fully withheld), so
        # the fallback to `old_facts.manifest` cannot re-enforce a stored
        # manifest an explicit one replaced (CodeRabbit review).
        replace(restrict_bundle_facts(old_facts, scope_record), manifest=manifest),
        new_bundle_snapshot,
        per_library_results,
        manifest=manifest,
        system_providers=[
            *(system_providers or ()),
            *out_of_scope_provider_names(scope_record),
        ],
        cohorts=cohorts,
        policy=policy,
        policy_file=policy_file,
        # Codex review, PR #1060, round 6: both maps must be the same
        # depth-projected snapshots the per-library diff above just used --
        # passing the raw, unprojected old_facts.per_library_snapshots (the
        # old_signature_evidence default) or new_facts.per_library_snapshots
        # here would let find_unverified_signature_findings judge type-
        # evidence completeness against evidence richer than what --depth
        # actually allowed the comparison to see.
        old_signature_evidence=dict(projected_old_snapshots),
        new_signature_evidence=dict(projected_new_snapshots),
    )
    result.analysis_errors.extend(degraded_notes)
    if manifest_note is not None:
        result.analysis_errors.append(manifest_note)
    result.scope_record = scope_record
    return result

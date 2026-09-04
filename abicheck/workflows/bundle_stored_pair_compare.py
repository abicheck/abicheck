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

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bundle_models import BundleDiffResult
    from ..checker_types import DiffResult
    from ..policy_file import PolicyFile
    from .suppression import SuppressionList


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

    *depth*, when given, caps both sides' evidence to what ``compare``'s
    ``--depth`` requested before diffing (``binary``/``headers`` -- the
    only two values reachable here at all, since ``--depth build``/
    ``source`` are rejected unconditionally for a stored OLD_INPUT
    elsewhere: this driver has no channel to *collect* L3-L5 evidence, only
    to project already-resolved evidence down). Unlike a live NEW-side dump
    (which ``service.resolve_input`` can be asked to skip header parsing
    for), both sides here are *already* fully-resolved ``AbiSnapshot``s
    with whatever evidence they were captured with baked in -- so this
    calls the same ``policy.depth_projection.project_pair_to_depth()``
    ordinary live comparisons use (``service_compare_pipeline.py``,
    ``cli_compare_helpers.py``, ``cli_scan_baseline.py``) rather than
    rejecting the flag outright, which an earlier version of this function
    did on the mistaken premise that no such projection primitive existed
    (Codex review, PR #1060, fresh evidence: it does, and every other
    resolved-snapshot comparison path in this codebase already calls it).
    ``None`` (the default) is a no-op, matching
    ``project_snapshot_to_depth``'s own documented contract.
    """
    from ..bundle_facts import bundle_snapshot_from_facts, compare_bundle_from_facts
    from ..bundle_manifest import load_manifest
    from ..policy.depth_projection import project_pair_to_depth
    from ..serialization import load_bundle_facts
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
    per_library_results: list[DiffResult] = []
    for key in matched_keys:
        old_snapshot, new_snapshot = project_pair_to_depth(
            old_facts.per_library_snapshots[key], new_facts.per_library_snapshots[key], depth
        )
        diff = compare_snapshots(
            old_snapshot,
            new_snapshot,
            suppress,
            policy=policy,
            policy_file=policy_file,
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
    new_bundle_snapshot = bundle_snapshot_from_facts(new_facts)
    return compare_bundle_from_facts(
        old_facts,
        new_bundle_snapshot,
        per_library_results,
        manifest=manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
        policy_file=policy_file,
        new_signature_evidence=dict(new_facts.per_library_snapshots),
    )

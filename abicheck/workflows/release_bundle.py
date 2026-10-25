# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The release's cross-library (bundle) stage: manifest resolution, bundle
analysis over the members' already-computed results, and the
``--bundle-facts-out`` baseline capture.

Moved from the retired ``cli_compare_release_helpers.py`` into the release compare owner
(:mod:`abicheck.workflows.release_compare`). Failures that used to be raised
as ``click`` exceptions from inside this work are now the typed
:class:`~abicheck.errors.ReleaseOperandError` family, which the CLI boundary
translates into the same exit codes and messages: a bad ``--manifest`` file is
a :class:`~abicheck.errors.ReleaseOperandContentError` (exit 1), an
undecodable embedded manifest or an unwritable ``--bundle-facts-out`` a
:class:`~abicheck.errors.ReleaseOperandUsageError`. Bundle *analysis* stays
additive: its failures degrade to warnings through *notice*.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..bundle import BundleDiffResult
from ..bundle_models import BundleSignatureEvidence
from ..checker_types import DiffResult
from ..errors import ReleaseOperandContentError, ReleaseOperandUsageError, SnapshotError
from ..model import AbiSnapshot
from .gate import worse_release_verdict
from .release_scope import StrandedLibraryResolution, scope_manifest_to_members

if TYPE_CHECKING:
    from ..bundle_manifest import InstantiationManifest
    from ..model.release_surface import ReleasePublicSurface
    from ..model.scope_acquisition import ScopeAcquisitionRecord
    from .policy_file import PolicyFile

__all__ = [
    "collect_bundle_result",
    "resolve_bundle_manifest",
    "run_bundle_analysis",
    "write_bundle_facts_out",
]


def _no_notice(_text: str) -> None:
    pass


def resolve_bundle_manifest(
    manifest_path: Path | None,
    old_root: Path | None,
    new_root: Path | None,
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    *,
    old_variant: str | None = None,
    new_variant: str | None = None,
    old_side_only: bool = False,
) -> InstantiationManifest | None:
    """The one place ``--manifest``/embedded-manifest resolution happens for
    a release comparison -- shared by bundle analysis and the
    ``--bundle-facts-out`` capture, so a captured baseline keeps the
    manifest-drift contract the live comparison enforced.

    An explicit *manifest_path* always wins and fails loudly on a bad file.
    Otherwise falls back to a stored side's own embedded
    ``InstantiationManifest``: *old_root*/*new_root* (the release operands)
    are checked first, so a package whose selected variant carries zero
    artifacts still has its own manifest consulted; *old_map*/*new_map*'s
    member sub-packages are the fallback. OLD before NEW.
    *old_variant*/*new_variant* select the matching root's own variant.
    *old_side_only* (the baseline capture) restricts the fallback to OLD, so
    NEW's manifest is never attributed to an OLD baseline lacking its own.
    """
    from ..bundle import load_manifest

    if manifest_path is not None:
        try:
            return load_manifest(manifest_path)
        except Exception as exc:
            raise ReleaseOperandContentError(
                f"Failed to load manifest {manifest_path}: {exc}"
            ) from exc

    all_roots = ((old_root, old_variant), (new_root, new_variant))
    roots = all_roots[:1] if old_side_only else all_roots
    candidates: list[tuple[Path, str | None]] = [
        (r, v) for r, v in roots if r is not None
    ]
    if not candidates:
        maps = (old_map,) if old_side_only else (old_map, new_map)
        candidates = [(p, None) for m in maps for p in m.values()]
    from .release_package import read_embedded_manifest

    for candidate_root, candidate_variant in candidates:
        if not candidate_root.is_dir():
            continue
        try:
            manifest = read_embedded_manifest(candidate_root, candidate_variant)
        except Exception as exc:
            raise ReleaseOperandUsageError(
                f"{candidate_root}: embedded instantiation manifest is "
                f"declared but could not be decoded: {exc}"
            ) from exc
        if manifest is not None:
            return manifest
    return None


def run_bundle_analysis(
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    per_lib_results: list[DiffResult],
    *,
    manifest_path: Path | None,
    bundle_system_providers: tuple[str, ...],
    bundle_cohorts: tuple[str, ...] = (),
    policy: str = "strict_abi",
    old_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] | None = None,
    new_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] | None = None,
    old_root: Path | None = None,
    new_root: Path | None = None,
    old_variant: str | None = None,
    new_variant: str | None = None,
    scope_record: ScopeAcquisitionRecord | None = None,
    notice: Callable[[str], None] = _no_notice,
) -> BundleDiffResult | None:
    """Run bundle-level (ADR-023) analysis over a release's member results.

    Reuses the per-library :class:`DiffResult`\\ s the member fan-out already
    computed -- no second per-pair compare. Returns ``None`` when there is
    nothing to analyze or the bundle snapshot cannot be built (reported via
    *notice* as a warning: bundle analysis is additive). A failure inside the
    analysis itself is recorded in the result's ``analysis_errors`` (G38
    Phase 11) and re-surfaced through *notice*.

    *old_snapshots*/*new_snapshots* (G38 Phase 4) are keyed by each member's
    bundle-canonical key (``_bundle_key`` on its entry) and feed the
    unverified-signature check. Both stages run through
    :func:`abicheck.bundle_analysis.analyze_bundle`, shared with the stored
    facts path. *old_root*/*new_root*/*old_variant*/*new_variant* (ADR-062
    A1.7) feed the embedded-manifest fallback; *scope_record* (ADR-065 D2)
    scopes that manifest to the retained members.
    """
    from ..bundle import build_bundle_snapshot_mixed
    from ..bundle_analysis import analyze_bundle

    # Resolved before the empty-maps check: an empty BundleFacts package can
    # still declare a required-symbol manifest.
    manifest = resolve_bundle_manifest(
        manifest_path,
        old_root,
        new_root,
        old_map,
        new_map,
        old_variant=old_variant,
        new_variant=new_variant,
    )
    manifest, manifest_note = scope_manifest_to_members(manifest, scope_record)
    if not old_map and not new_map and manifest is None:
        return None
    try:
        # ADR-062 A1.7: a map may hold a stored ProjectSnapshot sub-package
        # directory as well as a live binary; build_bundle_snapshot_mixed
        # resolves either kind, so a stored side is never silently dropped.
        old_snap = build_bundle_snapshot_mixed(
            dict(old_map), resolved_evidence=old_snapshots
        )
        new_snap = build_bundle_snapshot_mixed(
            dict(new_map), resolved_evidence=new_snapshots
        )
    except Exception as exc:
        # Additive degradation: the per-library report is still useful.
        notice(f"Warning: bundle analysis skipped: {exc}")
        return None

    result = analyze_bundle(
        old_snap,
        new_snap,
        per_lib_results,
        manifest=manifest,
        system_providers=list(bundle_system_providers) or None,
        cohorts=list(bundle_cohorts) or None,
        policy=policy,
        old_signature_evidence=old_snapshots or None,
        new_signature_evidence=new_snapshots or None,
    )
    if manifest_note is not None:
        result.analysis_errors.append(manifest_note)
    for err in result.analysis_errors:
        notice(f"Warning: {err}")
    return result


def collect_bundle_result(
    library_results: list[dict[str, object]],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    worst_verdict: str,
    manifest_path: Path | None,
    bundle_system_providers: tuple[str, ...],
    bundle_cohorts: tuple[str, ...] = (),
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    old_root: Path | None = None,
    new_root: Path | None = None,
    old_variant: str | None = None,
    new_variant: str | None = None,
    scope_record: ScopeAcquisitionRecord | None = None,
    notice: Callable[[str], None] = _no_notice,
) -> tuple[BundleDiffResult | None, str]:
    """Read the members' stashed results, run bundle analysis, fold its
    verdict into *worst_verdict*.

    Each entry carries *either* the full ``_old_snapshot``/``_new_snapshot``
    or the compact ``_old_bundle_evidence``/``_new_bundle_evidence``
    (:mod:`abicheck.workflows.release_snapshot_retention`); either serves the
    signature-evidence check. *policy_file* (G38 Phase 16) is set on the
    result before ``bundle_verdict`` is read.
    """
    stashed_diffs: list[DiffResult] = []
    old_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] = {}
    new_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] = {}
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        diff = entry.get("_diff_result")
        if isinstance(diff, DiffResult):
            stashed_diffs.append(diff)
        bundle_key = entry.get("_bundle_key")
        old_snap = entry.get("_old_snapshot") or entry.get("_old_bundle_evidence")
        new_snap = entry.get("_new_snapshot") or entry.get("_new_bundle_evidence")
        if (
            isinstance(bundle_key, str)
            and isinstance(old_snap, (AbiSnapshot, BundleSignatureEvidence))
            and isinstance(new_snap, (AbiSnapshot, BundleSignatureEvidence))
        ):
            old_snapshots[bundle_key] = old_snap
            new_snapshots[bundle_key] = new_snap
    bundle_result = run_bundle_analysis(
        old_map,
        new_map,
        stashed_diffs,
        manifest_path=manifest_path,
        bundle_system_providers=bundle_system_providers,
        bundle_cohorts=bundle_cohorts,
        policy=policy,
        old_snapshots=old_snapshots,
        new_snapshots=new_snapshots,
        old_root=old_root,
        new_root=new_root,
        old_variant=old_variant,
        new_variant=new_variant,
        scope_record=scope_record,
        notice=notice,
    )
    if bundle_result is not None:
        bundle_result.policy_file = policy_file  # G38 Phase 16
        worst_verdict = worse_release_verdict(
            worst_verdict, bundle_result.bundle_verdict.value
        )
    return bundle_result, worst_verdict


def write_bundle_facts_out(
    bundle_facts_out: Path,
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]],
    manifest_path: Path | None,
    old_map: dict[str, Path],
    *,
    resolve_stranded_library: Callable[[Path], AbiSnapshot | StrandedLibraryResolution],
    inherited_degraded: Mapping[str, str] | None = None,
    resolved_manifest: InstantiationManifest | None = None,
    inventory_complete: bool = False,
    public_surface: ReleasePublicSurface | None = None,
) -> None:
    """Persist the OLD side's per-library snapshots (plus manifest) to
    *bundle_facts_out* as a :class:`~abicheck.model.bundle_facts.BundleFacts`
    file (G38 Phase 2's ``--bundle-facts-out`` producer).

    *diff_pairs* is ``release_old_snapshot_pairs(library_results)`` -- the
    whole OLD snapshot, never JUnit's compact inventory. Each persisted entry
    is keyed by *old_map*'s **canonical** release-matching key (e.g.
    ``libfoo.so`` for ``libfoo.so.1.2``), the same identity a live
    ``build_bundle_snapshot(dict(old_map))`` uses -- keying by basename would
    read as a false ``bundle_library_removed``/``_added`` pair later.

    Every *old_map* key without a successful pair -- an old-only library, or
    a matched one whose compare failed -- is resolved by
    *resolve_stranded_library* with the same extraction context the release
    used (a real snapshot, or an ELF-only stand-in carrying its failure), so
    the baseline never silently omits a real member. *old_map* is also
    handed to the capture as ``library_paths`` so filesystem aliases are
    recorded while the files exist.

    *resolved_manifest*, when given, is used as-is (it covers the embedded
    fallback ``--manifest`` alone does not). *inherited_degraded* (ADR-065
    D8) is a stored OLD package's persisted marker; *inventory_complete*
    (ADR-065 D2) asserts the capture covers every member OLD enumerated.

    A bad *manifest_path* or an unwritable *bundle_facts_out* is a
    :class:`~abicheck.errors.ReleaseOperandUsageError`, unlike bundle
    analysis, which degrades to a warning.
    """
    from ..bundle_manifest import load_manifest
    from ..serialization import save_bundle_facts
    from .bundle_facts_capture import capture_bundle_facts
    from .extraction import _canonical_library_key

    try:
        manifest: InstantiationManifest | None
        if resolved_manifest is not None:
            manifest = resolved_manifest
        else:
            manifest = (
                load_manifest(manifest_path) if manifest_path is not None else None
            )

        # Canonicalize DiffResult.library the way old_map's keys were derived;
        # a stored operand's value is a materialized sub-package dirname a
        # basename match would miss (capturing that pair twice).
        per_library_snapshots: dict[str, AbiSnapshot] = {}
        for diff, old_snapshot in diff_pairs:
            key = _canonical_library_key(Path(diff.library))
            if key not in old_map:
                key = Path(diff.library).name
            per_library_snapshots[key] = old_snapshot
        # ADR-065 D8: a failed stranded dump is persisted *with* its failure.
        degraded_members: dict[str, str] = {
            k: v for k, v in (inherited_degraded or {}).items() if k in old_map
        }
        for key, old_path in old_map.items():
            if key in per_library_snapshots:
                continue
            resolved = resolve_stranded_library(old_path)
            if isinstance(resolved, StrandedLibraryResolution):
                per_library_snapshots[key] = resolved.snapshot
                if resolved.failure is not None:
                    degraded_members.setdefault(key, resolved.failure)
            else:
                per_library_snapshots[key] = resolved
        facts = capture_bundle_facts(
            per_library_snapshots,
            manifest=manifest,
            library_paths=dict(old_map),
            degraded_members=degraded_members,
            inventory_complete=inventory_complete,
            # The OLD side's own acquired public contract (schema_version 4).
            public_surface=public_surface,
        )
        save_bundle_facts(facts, bundle_facts_out)
    except (OSError, ValueError, SnapshotError) as exc:
        raise ReleaseOperandUsageError(
            f"--bundle-facts-out {bundle_facts_out}: {exc}"
        ) from exc

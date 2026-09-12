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

"""Pure helpers for the ``compare-release`` command.

Leaf module: it must not import from :mod:`abicheck.cli` or
:mod:`abicheck.cli_compare_release`. The render/format helpers for the
release summary (JSON / Markdown / JUnit) live here, split out of
:mod:`abicheck.cli_compare_release` to keep that module under the
AI-readiness file-size limit. They are re-exported from
``cli_compare_release`` to preserve the public import surface.

``GateOptions``/``resolve_release_gate_options``/``apply_release_gate_pack``/
``_resolve_release_severity_config`` (ADR-064's release-fan-out gate
resolution) live in :mod:`abicheck.policy.release_gate_options` instead --
this package's own home for deciding gate/severity effect
(``abicheck/policy/AGENTS.md``), and also outside the file-size no-growth
budget this module is at. This (a ``frontends``-classified module) reaches
them through :mod:`abicheck.workflows.gate`'s facade rather than importing
``policy`` directly (``frontends -> policy`` is forbidden), and re-exports
them here so every pre-existing import of them from this module keeps
working.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .bundle_models import BundleSignatureEvidence
from .checker import DiffResult
from .errors import SnapshotError
from .frontends.cli.options.params import DEFAULT_POLICY_PROFILE
from .frontends.cli.release_exit import _exit_compare_release as _exit_compare_release
from .model import AbiSnapshot
from .report.comparison_scope import ComparisonScopeTerms, comparison_scope_terms
from .report.release_assurance import ReleaseAssuranceTerms, release_assurance_terms
from .report.render_release_markdown import (  # re-exported, moved (ADR-065 S2)
    _release_md_bundle_findings as _release_md_bundle_findings,
    _release_md_changed_libraries as _release_md_changed_libraries,
    _release_md_coverage_warnings as _release_md_coverage_warnings,
    _release_md_evidence_contract as _release_md_evidence_contract,
    _release_md_libraries_table as _release_md_libraries_table,
    _release_md_matrix_findings as _release_md_matrix_findings,
)
from .reporter_markdown import (
    release_bundle_findings_for_view,
    release_matrix_changes_for_view,
)
from .schemas import RELEASE_SCHEMA_VERSION
from .workflows.contract_conflicts import (
    # E-S3 case 3: does a package's declared contract match its own
    # contained binary. Needs `elf_metadata` (`extract`, forbidden directly
    # from `frontends`), so the real logic lives in `workflows`.
    debian_symbols_release_conflict_lines as debian_symbols_release_conflict_lines,  # re-exported, E-S3
)
from .workflows.gate import (
    GateOptions as GateOptions,  # re-exported, ADR-064
    _resolve_release_severity_config as _resolve_release_severity_config,  # re-exported, ADR-064
    apply_release_gate_pack as apply_release_gate_pack,  # re-exported, ADR-064
    resolve_release_assurance_decision,
    resolve_release_exit_decision_for_report,
    resolve_release_gate_options as resolve_release_gate_options,  # re-exported, ADR-064
    resolve_scope_decision,
)
from .workflows.release_scope import (
    StrandedLibraryResolution,
    scope_manifest_to_members,
)

if TYPE_CHECKING:
    from .bundle_manifest import InstantiationManifest
    from .model.scope_acquisition import ScopeAcquisitionRecord
    from .pack_application import PackApplication
    from .workflows.extraction import PackageExtractor
    from .workflows.gate import SeverityConfig
    from .workflows.policy_file import PolicyFile


_RELEASE_VERDICT_ORDER: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
    "ERROR": 5,
    # ADR-050 D2 — ranked above even ERROR: a not_comparable library means
    # the comparison couldn't establish what changed at all, so it dominates
    # the release-level "worst verdict wins" rollup over every other outcome
    # in the same release, including a genuine crash.
    "not_comparable": 6,
}


def _release_global_verdict(bundle_result: BundleDiffResult | None, matrix_result: DiffResult | None) -> str:
    """Release-global (bundle/probe-matrix) verdict alone -- unlike
    ``worst_verdict``'s own fold of it, never masked by an unrelated
    library's ``ERROR``/``not_comparable`` (Codex review, fresh evidence)."""
    worst = "NO_CHANGE"
    for v in (bundle_result.bundle_verdict.value if bundle_result else None, matrix_result.verdict.value if matrix_result else None):
        if v is not None and _RELEASE_VERDICT_ORDER.get(v, 0) > _RELEASE_VERDICT_ORDER.get(worst, 0):
            worst = v
    return worst


#: The two release-level sentinels that are not real `Verdict` values and
#: must never mask a *different*, already-completed compatibility result
#: on `RunOutcome.compatibility`'s own independent axis (Codex review, fresh
#: evidence): `worst_verdict`'s own `_RELEASE_VERDICT_ORDER` rollup ranks
#: both above every real verdict by design (an operational failure/refusal
#: dominates the release's own reported "verdict"), which is exactly the
#: right behavior for the *reported* release verdict but the wrong one for
#: `run_outcome.compatibility`, a genuinely separate axis.
_RELEASE_OPERATIONAL_SENTINELS = frozenset(
    {"ERROR", "not_comparable", "unsupported", "failed"}
)


def _release_completed_compatibility_verdict(
    library_results: list[dict[str, object]],
    release_global_verdict: str,
    *,
    release_global_ran: bool,
) -> str | None:
    """The worst real `Verdict` among *library_results* + *release_global_
    verdict*, with the two operational sentinels excluded -- for
    ``run_outcome.compatibility``, never for the release's own reported
    ``verdict`` (``worst_verdict`` stays exactly what it always was).

    One `BREAKING` library plus a second, unrelated library's `ERROR` still
    surfaces `compatibility: "BREAKING"` here, even though `worst_verdict`
    itself (correctly) reports `"ERROR"` -- the real compatibility result
    is not lost just because a different library's operational failure
    dominates the release-level rollup.

    Returns ``None`` -- never the floor ``"NO_CHANGE"`` -- when no real
    compatibility result was actually observed at all (every library
    result is one of the two operational sentinels, and no bundle/probe-
    matrix comparison ran either): `run_outcome.compatibility` must stay
    unknown, not falsely claim a clean completed comparison (Codex review,
    fresh evidence). *release_global_ran* -- whether a bundle or matrix
    comparison actually ran -- must be passed explicitly rather than
    inferred from *release_global_verdict* alone: `_release_global_
    verdict`'s own floor default is `"NO_CHANGE"`, indistinguishable from a
    real completed no-change bundle/matrix result by string value alone.
    """
    worst: str | None = None
    for entry in library_results:
        v = str(entry.get("verdict", "NO_CHANGE"))
        if v in _RELEASE_OPERATIONAL_SENTINELS:
            continue
        if worst is None or _RELEASE_VERDICT_ORDER.get(
            v, 0
        ) > _RELEASE_VERDICT_ORDER.get(worst, 0):
            worst = v
    if (
        release_global_ran
        and release_global_verdict not in _RELEASE_OPERATIONAL_SENTINELS
        and (
            worst is None
            or _RELEASE_VERDICT_ORDER.get(release_global_verdict, 0)
            > _RELEASE_VERDICT_ORDER.get(worst, 0)
        )
    ):
        worst = release_global_verdict
    return worst








def _resolve_bundle_manifest(
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
    """The one place `--manifest`/embedded-manifest resolution happens for
    a release comparison -- shared by `_run_bundle_analysis` and
    `cli_compare_release.py`'s own `--bundle-facts-out` call site (Codex
    review, fresh evidence: the embedded-manifest resolution this function
    performs was previously computed only for the local bundle-analysis
    result and never reached `write_bundle_facts_out`, so a captured
    baseline silently lost the manifest-drift contract even when the
    release comparison itself enforced it).

    An explicit *manifest_path* always wins and fails loudly on a bad file
    (a user error, not an environmental quirk). Otherwise falls back to a
    stored side's own embedded `InstantiationManifest`
    (`materialize_release_variant_artifacts` already preserves the selected
    variant's own composition section on disk):
    *old_root*/*new_root* (the release operands themselves) are checked
    directly first, so a package whose selected variant carries zero
    artifacts still has its own manifest consulted; *old_map*/*new_map*'s
    member sub-packages are the fallback for a caller that predates the
    root parameters. `old_root`/`old_map` before `new_root`/`new_map` (the
    side a manifest more naturally describes as a baseline).
    *old_variant*/*new_variant* select the matching root's own variant.
    *old_side_only*, when true (`write_bundle_facts_out`'s own baseline
    capture, Codex review), restricts the fallback to OLD alone -- the
    shared search could otherwise attribute NEW's manifest to an OLD baseline lacking its own; live-comparison enforcement keeps both.
    """
    from .bundle import load_manifest

    if manifest_path is not None:
        try:
            return load_manifest(manifest_path)
        except Exception as exc:
            raise click.ClickException(
                f"Failed to load manifest {manifest_path}: {exc}",
            ) from exc

    all_roots = ((old_root, old_variant), (new_root, new_variant))
    roots = all_roots[:1] if old_side_only else all_roots
    candidates: list[tuple[Path, str | None]] = [(r, v) for r, v in roots if r is not None]
    if not candidates:
        maps = (old_map,) if old_side_only else (old_map, new_map)
        candidates = [(p, None) for m in maps for p in m.values()]
    from .workflows.release_package import read_embedded_manifest

    for candidate_root, candidate_variant in candidates:
        if not candidate_root.is_dir():
            continue
        try:
            manifest = read_embedded_manifest(candidate_root, candidate_variant)
        except Exception as exc:
            raise click.UsageError(
                f"{candidate_root}: embedded instantiation manifest is "
                f"declared but could not be decoded: {exc}"
            ) from exc
        if manifest is not None:
            return manifest
    return None


def _run_bundle_analysis(
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
) -> BundleDiffResult | None:
    """Run bundle-level (ADR-023) analysis on a compare-release run.

    Reuses the per-library :class:`DiffResult`s already computed by
    :func:`_compare_release_libraries` — no second per-pair compare pass.

    Returns None when there is nothing to analyze (e.g. all libraries
    failed to dump, or the bundle snapshot itself could not be built --
    the two cases a caller has no meaningful ``BundleDiffResult`` to
    inspect regardless). Errors during analysis are caught and reported
    as a warning rather than aborting; bundle analysis is additive. A
    failure in ``compare_bundle()`` itself or in the Phase 4
    signature-evidence check is additionally recorded structurally, in
    the returned result's own ``analysis_errors`` (G38 stabilization
    Phase 11 / P0-D), so a JSON/Markdown report consumer can tell
    "bundle analysis ran clean" apart from "ran, but degraded" without
    grepping stderr.

    *old_snapshots*/*new_snapshots* (G38 Phase 4), when both given and
    non-empty, additionally run
    :func:`~abicheck.bundle_signature_evidence.find_unverified_signature_findings`
    and fold its output into the returned ``bundle_findings`` list, the
    same additive-degradation-on-error philosophy as the rest of this
    function. Keyed by each library's bundle-canonical key (``_bundle_key``
    on the stashed release entry, the same key ``old_map``/``new_map`` use
    -- *not* the library's file basename), matching what
    ``BundleSnapshot.resolution`` itself keys providers/consumers by.

    G38 stabilization Phase 12: both stages run through the single
    :func:`abicheck.bundle_analysis.analyze_bundle` orchestrator (shared
    with :func:`abicheck.bundle_facts.compare_bundle_from_facts`); this
    function only builds the two live ``BundleSnapshot``\\ s, resolves the
    manifest, and re-surfaces ``analysis_errors`` as stderr warnings.

    *old_root*/*new_root* (ADR-062 A1.7) are the two release operands
    themselves, used only for the embedded-``InstantiationManifest``
    fallback -- a package whose selected variant carries zero artifacts has
    no *old_map*/*new_map* entry to search (Codex review). *old_variant*/
    *new_variant* select which variant's manifest that fallback reads.
    *scope_record* (ADR-065 D2) scopes that manifest to the retained members.
    """
    from .bundle import build_bundle_snapshot_mixed
    from .bundle_analysis import analyze_bundle

    # Resolved before the empty-maps check: an empty BundleFacts package can still declare a required-symbol manifest (Codex review).
    manifest = _resolve_bundle_manifest(
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
        # ADR-062 A1.7: old_map/new_map may hold a stored ProjectSnapshot
        # sub-package directory for some (or all) libraries, not only live
        # binary paths -- build_bundle_snapshot_mixed resolves either kind,
        # rather than build_bundle_snapshot's live-only ELF parse, which
        # would otherwise silently drop every stored-side library from
        # bundle-level analysis (Codex review, security finding); an empty map still builds a valid, empty BundleSnapshot.
        old_snap = build_bundle_snapshot_mixed(dict(old_map))
        new_snap = build_bundle_snapshot_mixed(dict(new_map))
    except Exception as exc:
        # Treat snapshot-build failures as additive degradation: the
        # per-library compare-release report is still useful, and the
        # user has an obvious escape hatch (--no-bundle-analysis) if they
        # want to silence this. A surprise CLI exit here would block CI
        # pipelines that previously didn't see bundle analysis at all.
        click.echo(f"Warning: bundle analysis skipped: {exc}", err=True)
        return None

    # Consumed directly as a sequence -- no comma-join/split round trip
    # (Codex review: that would corrupt a provider entry containing a comma;
    # entries are already stripped/filtered once, at BuildConfig.from_dict()).
    system_extra: list[str] = list(bundle_system_providers)
    result = analyze_bundle(
        old_snap,
        new_snap,
        per_lib_results,
        manifest=manifest,
        system_providers=system_extra or None,
        cohorts=list(bundle_cohorts) or None,
        policy=policy,
        old_signature_evidence=old_snapshots or None,
        new_signature_evidence=new_snapshots or None,
    )
    # Re-surface analyze_bundle()'s structured `analysis_errors` as the
    # same stderr warnings this function has always emitted -- the
    # orchestrator itself is a pure/leaf function with no CLI-echoing
    # concerns of its own (it's shared with the stored-facts path, which
    # has no `click` context to echo into).
    if manifest_note is not None:
        result.analysis_errors.append(manifest_note)
    for err in result.analysis_errors:
        click.echo(f"Warning: {err}", err=True)

    return result


#: Four pure helpers that raise nothing moved to ``workflows.
#: release_inputs`` alongside the rest of release input resolution (ADR-061
#: gap D) and are re-exported here under their original private names --
#: there is no Click translation to do for a function that cannot fail.
from .workflows import release_inputs as _release_inputs  # noqa: E402

# Plain assignments, not `from ... import x as _x`: under mypy's
# `no_implicit_reexport` a renaming import is not an explicit re-export, and
# `cli_compare_release.py` imports these names from here.
_debian_symbols_warning = _release_inputs.debian_symbols_warning
_discover_include_roots = _release_inputs.discover_include_roots
_match_release_keys = _release_inputs.match_release_keys
_resolve_release_headers = _release_inputs.resolve_release_headers


def _extract_if_package(
    input_path: Path,
    debug_pkg: Path | None,
    devel_pkg: Path | None,
    make_temp_dir: Callable[[str], Path],
    is_package: Callable[[Path], bool],
    detect_extractor: Callable[[Path], PackageExtractor | None],
) -> tuple[Path, Path | None, Path | None, Path | None, bool]:
    """The Click-translating wrapper over :func:`abicheck.workflows.
    release_inputs.extract_if_package` -- see that module's docstring for
    why the extraction itself is engine-side now. Same messages, same exit
    code."""
    from .errors import ReleaseOperandContentError
    from .workflows.release_inputs import extract_if_package

    try:
        return extract_if_package(
            input_path, debug_pkg, devel_pkg, make_temp_dir, is_package, detect_extractor
        )
    except ReleaseOperandContentError as exc:
        raise click.ClickException(str(exc)) from exc






def reject_bundle_facts_out_collision(
    bundle_facts_out: Path | None,
    output: Path | None,
    *secondary_outputs: Path | None,
) -> None:
    """Reject ``--bundle-facts-out`` naming the same file as ``--output``/
    ``-o`` (G38 Phase 2).

    A command-specific extra check, deliberately not
    ``reject_incoherent_secondary_output()``'s job (see that leaf module's
    own docstring) -- without it, ``--bundle-facts-out result.json --output
    result.json`` silently overwrites the requested baseline with the
    report while still reporting success (Codex review).

    *secondary_outputs* is variadic because ``-o`` is repeatable: every
    requested artifact's PATH is checked, not just the first. Checking one
    let ``-o json=a.json -o markdown=b.md --bundle-facts-out
    b.md`` through.
    """
    if bundle_facts_out is None:
        return
    for label, other in (
        ("--output/-o", output),
        *(("--write", out) for out in secondary_outputs),
    ):
        if other is not None and bundle_facts_out.resolve() == other.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from {label}: writing "
                "both to the same file would silently overwrite the "
                "requested bundle-facts baseline with the report."
            )


def reject_bundle_facts_out_dir_collision(
    bundle_facts_out: Path | None,
    output_dir: Path | None,
    old_map: dict[str, Path],
) -> None:
    """Reject ``--bundle-facts-out`` naming a path a per-component export will
    also write (G38 Phase 2, Codex review, fresh evidence).

    ``reject_bundle_facts_out_collision()`` above only knows about
    the export set -- it can't see a per-component export's own
    ``summary.json`` or per-library ``<stem>.json`` files, since those
    paths depend on *output_dir* and (for the per-library case) the
    resolved OLD-side library map, neither known at that earlier
    validation point. Called once ``old_map`` is resolved, before
    ``output_dir`` is created or anything is written into it.
    """
    if bundle_facts_out is None or output_dir is None:
        return
    resolved = bundle_facts_out.resolve()
    summary_path = output_dir / "summary.json"
    if resolved == summary_path.resolve():
        raise click.UsageError(
            "--bundle-facts-out's PATH must differ from the per-component "
            "export's own summary.json: writing both to the same file would silently "
            "overwrite the requested bundle-facts baseline with the "
            "per-library summary report."
        )
    for name, old_path in old_map.items():
        lib_path = output_dir / f"{old_path.stem}.json"
        if resolved == lib_path.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from the per-component "
                f"export's own report for {name!r} ({lib_path}): writing "
                "both to the same file would silently overwrite whichever "
                "was written second."
            )


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
) -> None:
    """Persist the OLD side's per-library snapshots (plus manifest, if any)
    to *bundle_facts_out* as a :class:`~abicheck.bundle_facts.BundleFacts`
    file (G38 Phase 2's ``--bundle-facts-out`` producer).

    *diff_pairs* is ``_compare_release_libraries``'s own
    ``(DiffResult, old_snapshot)`` collection -- the caller must have
    passed ``collect_diff_results=True`` for it to be populated.
    *old_map* is ``_match_release_keys``'s own map: every key is the
    **canonical** release-matching key
    (``_canonical_library_key()`` -- e.g. ``libfoo.so`` for a discovered
    ``libfoo.so.1.2``), the identical keys a live ``build_bundle_snapshot
    (dict(old_map))`` call uses for its ``BundleSnapshot.libraries``. Each
    persisted entry is keyed by that same canonical key, not by
    ``Path(diff.library).name`` (the real, possibly-versioned basename
    ``DiffResult.library`` carries) -- keying by the basename instead would
    make a reconstructed old bundle disagree with a live new bundle on a
    versioned library's very identity, reading as a false
    ``bundle_library_removed``/``_added`` pair for a library that did not
    change at all (Codex review, fresh evidence: caught after the P1 fix
    below already existed, which itself still keyed by the wrong basename).

    *old_map* also covers what ``diff_pairs`` alone cannot -- and not only
    for an old-only library removed in the new release: ``diff_pairs``
    only ever holds an entry for a library whose per-library compare
    actually *succeeded*, so a *matched* library whose compare returned
    ``ERROR``/``not_comparable`` has no entry there either, even though a
    live bundle analysis includes it (straight from ``old_map``) same as
    any other member (Codex review, fresh evidence: caught after an
    earlier revision of this fix only back-filled ``removed_keys``,
    missing this second, matched-but-failed case entirely). Both cases are
    the identical gap -- a real old-release library silently absent from
    the persisted baseline, so a later ``compare_bundle_from_facts()``
    call could never emit ``bundle_library_removed``/dependency-removal/
    version-resolution findings a live comparison of the same old release
    would -- so both are closed the same way: *every* ``old_map`` key not
    already covered by a successful ``diff_pairs`` entry is resolved via
    *resolve_stranded_library*, a caller-supplied callable that produces a
    **real** ``AbiSnapshot`` (or degrades to a bare-``ElfMetadata`` stand-in
    on failure) with the exact same extraction context every other library
    in this release was dumped with (Codex review, fresh evidence: an
    earlier revision of this fix only captured bare ``ElfMetadata``, which
    is sufficient for bundle-level graph resolution but is missing the
    functions/types/headers a stored-baseline consumer's own documented
    ``old_facts.per_library_snapshots[name]`` → ``compare_snapshots()``
    workflow needs -- an ELF-only snapshot compared against a real future
    dump would read every declaration as a compatible addition instead of
    the real diff, hiding a genuine breaking change).

    The resolution itself is deliberately injected rather than performed in
    this module: this is a **leaf module** (see its own docstring -- it
    must not import ``cli``/``cli_compare_release``), and a real resolve
    needs ``abicheck.cli_resolve._resolve_input``/``abicheck.service
    .resolve_input`` (per ADR-037 D1/D10.1's Tier-1/Tier-2 CLI-contract
    boundary), both of which already sit inside the large CLI-registration
    import cycle (``scripts/check_ai_readiness.py``'s
    ``IMPORT_CYCLE_ALLOWLIST``) -- importing either one *from this module*
    would pull this otherwise-leaf module into that cycle for the first
    time, which the ``import-cycle-growth`` gate correctly rejects as
    *new* SCC membership rather than "reuse of an already-member module"
    (Codex review, fresh evidence: an earlier revision of this fix called
    ``cli_resolve._resolve_input`` directly from here and passed
    ``check_ai_readiness.py``'s ``cli-contract`` check, but failed
    ``import-cycle-growth`` in CI for exactly this reason).
    ``cli_compare_release.py`` -- the sole caller, already a member of that
    cycle -- builds the callable and owns the actual resolve.

    *old_map* itself (already canonical-key-keyed) is handed to
    :func:`~abicheck.bundle_facts.capture_bundle_facts` as
    ``library_paths``, so real filesystem aliases (symlink targets,
    hard-linked siblings) are captured while the files still exist on
    disk -- see that function's own docstring.

    *resolved_manifest*, when given, is used as-is instead of loading
    *manifest_path* here -- the caller's own already-resolved manifest
    (`_resolve_bundle_manifest`, which also covers the embedded-manifest
    fallback `--manifest` alone does not), so a stored side's manifest-
    drift contract is captured into this baseline too, not just enforced
    against the live comparison this call runs alongside (Codex review,
    fresh evidence: this parameter's absence meant a captured
    ``--bundle-facts-out`` baseline silently dropped that contract).
    *manifest_path* is still respected when *resolved_manifest* is
    `None`. *inherited_degraded* (ADR-065 D8) is a stored OLD package's own
    persisted marker, keyed like *old_map*: it stays marked in this recapture
    even though its ELF-only stand-in reloads fine (Codex review).
    *inventory_complete* (ADR-065 D2): the caller asserts this capture covers
    every member OLD enumerated (``BundleFacts.inventory_complete``).

    Failure here (a bad *manifest_path*, an unwritable *bundle_facts_out*)
    is a usage error, unlike bundle *analysis* (which degrades to a warning).
    """
    from .bundle_manifest import load_manifest
    from .serialization import save_bundle_facts
    from .workflows.bundle_facts_capture import capture_bundle_facts
    from .workflows.extraction import _canonical_library_key

    try:
        manifest: InstantiationManifest | None
        if resolved_manifest is not None:
            manifest = resolved_manifest
        else:
            manifest = load_manifest(manifest_path) if manifest_path is not None else None

        # Canonicalize DiffResult.library the way old_map's keys were derived,
        # not by basename against old_map's *values* -- a stored operand's value
        # is a materialized sub-package dirname a basename match misses, so the
        # pair read as "stranded" too and was captured twice (Codex review).
        per_library_snapshots: dict[str, AbiSnapshot] = {}
        for diff, old_snapshot in diff_pairs:
            key = _canonical_library_key(Path(diff.library))
            if key not in old_map:
                key = Path(diff.library).name
            per_library_snapshots[key] = old_snapshot
        # ADR-065 D8: a failed stranded dump is persisted *with* its failure
        # (`BundleFacts.degraded_members`); a bare AbiSnapshot means resolved.
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
        )
        save_bundle_facts(facts, bundle_facts_out)
    except (OSError, ValueError, SnapshotError) as exc:
        # SnapshotError too -- a declared-but-malformed filesystem_aliases array (Codex review).
        raise click.UsageError(f"--bundle-facts-out {bundle_facts_out}: {exc}") from exc


def _collect_bundle_result(
    library_results: list[dict[str, object]],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    worst_verdict: str,
    manifest_path: Path | None,
    bundle_system_providers: tuple[str, ...],
    bundle_cohorts: tuple[str, ...] = (), policy: str = "strict_abi", policy_file: PolicyFile | None = None,
    old_root: Path | None = None,
    new_root: Path | None = None,
    old_variant: str | None = None,
    new_variant: str | None = None,
    scope_record: ScopeAcquisitionRecord | None = None,
) -> tuple[BundleDiffResult | None, str]:
    """Extract stashed DiffResults, run bundle analysis, update worst verdict.

    Each entry carries *either* the full ``_old_snapshot``/``_new_snapshot``
    (JUnit/``--bundle-facts-out`` in effect) *or* the much smaller
    ``_old_bundle_evidence``/``_new_bundle_evidence`` (G38 stabilization
    Phase 9's memory fix — see :class:`~abicheck.bundle_models.
    BundleSignatureEvidence`) — never both, per
    ``cli_compare_release._compare_one_library``'s own stash logic. Either
    is duck-type compatible with what
    :func:`~abicheck.bundle_signature_evidence.find_unverified_signature_
    findings` reads, so both are folded into the same ``old_snapshots``/
    ``new_snapshots`` mapping this function has always built. *policy_file* (G38 Phase 16) is set on the result before ``bundle_verdict`` is read.
    *old_root*/*new_root*/*old_variant*/*new_variant* (ADR-062 A1.7) forward unchanged to :func:`_run_bundle_analysis`'s own fallback.
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
    bundle_result = _run_bundle_analysis(
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
    )
    if bundle_result is not None:
        bundle_result.policy_file = policy_file  # G38 Phase 16
        bv = bundle_result.bundle_verdict.value
        if _RELEASE_VERDICT_ORDER.get(bv, 0) > _RELEASE_VERDICT_ORDER.get(worst_verdict, 0):
            worst_verdict = bv
    return bundle_result, worst_verdict


def _cleanup_temp_dirs(temp_dir_paths: list[str]) -> None:
    """Remove temporary directories created during package extraction.

    Always cleans up now -- ``--keep-extracted`` is gone (Phase 7d,
    ADR-068 D5), no replacement."""
    import shutil as _shutil

    for td_path in temp_dir_paths:
        _shutil.rmtree(td_path, ignore_errors=True)


def _compute_release_severity_exit_code(
    library_results: list[dict[str, object]],
    gate: GateOptions,
) -> int | None:
    """Compute the severity-aware exit code aggregated across all libraries.

    Returns ``None`` when no severity setting was in effect (callers
    keep the legacy verdict-based exit) -- i.e. when ``gate.severity is
    None``. Otherwise returns the worst :func:`compute_exit_code` over the
    per-library changes. Each library is
    classified with *its own* ``DiffResult._effective_kind_sets()`` (kind-level
    ``--policy-file`` overrides) *and* its own ``policy``/``policy_file`` (the
    per-finding frozen-namespace floor — Codex review on #549: without
    ``policy_file`` here, a policy override that downgrades a kind could still
    silently exit 0 for a finding tagged ``frozen_namespace_violation``, even
    though that same finding's annotation, via ``collect_annotations``, does
    honour the floor and emits ``::error``) so per-library overrides are
    honored in the exit code exactly as they are in the report.

    This only covers per-library findings and must run before ``_diff_result``
    entries are stripped; release-global bundle/matrix findings are folded in
    separately via :func:`_fold_release_global_severity`.
    """
    if gate.severity is None:
        return None

    from .workflows.gate import compute_exit_code

    worst = 0
    for entry in library_results:
        diff = entry.get("_diff_result") if isinstance(entry, dict) else None
        if isinstance(diff, DiffResult):
            code = compute_exit_code(
                diff.changes,
                gate.severity,
                policy=diff.policy,
                kind_sets=diff._effective_kind_sets(),
                policy_file=diff.policy_file,
            )
            worst = max(worst, code)
    return worst


def _fold_release_global_severity(
    base_code: int,
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    gate: GateOptions,
) -> int:
    """Fold release-global (bundle + matrix) findings into the severity exit.

    The per-library aggregation in :func:`_compute_release_severity_exit_code`
    cannot see bundle-level findings or build-config matrix findings, which are
    computed later and update ``worst_verdict``. Without this, a release whose
    per-library diffs are clean but whose bundle/matrix analysis flags an
    error-level break would exit 0 under, e.g., the default preset. Returns the
    worst of *base_code* and the bundle/matrix severity codes. A no-op
    (returns *base_code* unchanged) when ``gate.severity is None``.
    """
    config = gate.severity
    if config is None:
        return base_code

    from .workflows.gate import compute_exit_code

    worst = base_code
    if bundle_result is not None and bundle_result.bundle_findings:
        # Bundle findings carry canonical (partitioned) ChangeKinds.
        # G38 stabilization Phase 10 (Codex review, fresh evidence): this
        # omitted `policy=` entirely, unlike the matrix_result branch right
        # below it -- so a policy that reclassifies a bundle kind (e.g.
        # `plugin_abi` demoting `calling_convention_changed`, which
        # `BundleDiffResult.bundle_verdict` already honors via its own
        # `.policy` field) never reached the severity-aware exit code,
        # letting the displayed verdict and the process exit disagree.
        # G38 Phase 16 (Codex review): `policy_file` had the identical gap.
        bundle_changes = [f.to_change() for f in bundle_result.bundle_findings]
        worst = max(
            worst,
            compute_exit_code(bundle_changes, config, policy=bundle_result.policy, policy_file=bundle_result.policy_file),
        )
    if matrix_result is not None and matrix_result.changes:
        worst = max(
            worst,
            compute_exit_code(
                matrix_result.changes,
                config,
                policy=matrix_result.policy,
                kind_sets=matrix_result._effective_kind_sets(),
                policy_file=matrix_result.policy_file,
            ),
        )
    return worst


def _release_findings_for_render(
    library_results: list[dict[str, object]], show_only: str | None
) -> list[dict[str, object]]:
    """Project *library_results* into the shape one *primary*-format render
    should see, without mutating the shared list every render call reads.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): ``_strip_diff_results_and_adjust_verdict``
    stashes two views per library alongside the always-full ``findings``/
    ``findings_truncated`` pair -- a private ``findings_view``/
    ``findings_view_truncated`` pair, present only when a ``--view show=``
    filter was active for this run. This function is the one place that
    view is ever read: when *show_only* is given (the *primary*
    ``--format``'s own selection), it swaps ``findings``/
    ``findings_truncated`` for the filtered view; either way it strips the
    private ``findings_view``/``findings_view_truncated`` keys so they never
    leak into a rendered report. A secondary ``-o`` report calls this
    with ``show_only=None`` (its own contract: always full/unfiltered,
    mirroring single-pair ``compare``'s own ``-o`` behaviour), which
    still routes through here so the private keys are stripped from *that*
    render too.

    The identical swap applies to the analogous ``impact_table``/
    ``impact_table_view`` pair (present only under ``--view impact``,
    Codex review, PR #1154 follow-up: "Reject unsupported impact views
    instead of silently dropping them") -- same private-key contract, same
    full-vs-filtered rule.

    Returns a new list of shallow-copied dicts; *library_results* itself
    (and the dicts inside it) is never mutated, so the same shared list can
    feed a filtered primary render and a full secondary render in either
    order.
    """
    result: list[dict[str, object]] = []
    for entry in library_results:
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        projected = dict(entry)
        has_view = "findings_view" in projected
        view = projected.pop("findings_view", None)
        view_truncated = projected.pop("findings_view_truncated", False)
        # CodeRabbit review: the per-kind truncation ledger is exactly as
        # full-vs-filtered as `findings`/`findings_truncated` themselves --
        # swapping the findings list but leaving the *unfiltered* ledger in
        # place let a rendered, filtered `findings` list carry a
        # `findings_truncated_kinds` breakdown describing what the *full*
        # diff cut, not what this filtered view cut. Popped unconditionally
        # either way, matching `findings_view`/`findings_view_truncated`'s
        # own "private, never reaches a render" contract.
        view_truncated_kinds = projected.pop("findings_view_truncated_kinds", None)
        if show_only is not None and has_view:
            if view:
                projected["findings"] = view
            else:
                projected.pop("findings", None)
            if view_truncated:
                projected["findings_truncated"] = True
                if view_truncated_kinds:
                    projected["findings_truncated_kinds"] = view_truncated_kinds
                else:
                    projected.pop("findings_truncated_kinds", None)
            else:
                projected.pop("findings_truncated", None)
                projected.pop("findings_truncated_kinds", None)
        has_impact_view = "impact_table_view" in projected
        impact_view = projected.pop("impact_table_view", None)
        if show_only is not None and has_impact_view:
            if impact_view:
                projected["impact_table"] = impact_view
            else:
                projected.pop("impact_table", None)
        # `findings_total_count`/`findings_total_count_view` (Codex review,
        # fresh evidence: "Count uncapped findings in release filter
        # totals") are private accounting keys `_strip_diff_results_and_
        # adjust_verdict` stashes for `_format_release_json`'s own
        # `filtered_summary`-equivalent totals to read directly off the
        # *raw*, unfiltered `library_results` -- never meant to reach a
        # rendered per-library entry, the same "private, popped here"
        # contract `findings_view`/`impact_table_view` above already
        # establish.
        projected.pop("findings_total_count", None)
        projected.pop("findings_total_count_view", None)
        result.append(projected)
    return result


def _format_release_summary(
    fmt: str,
    worst_verdict: str,
    old_dir: Path,
    new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    warning_msgs: list[str],
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] | None = None,
    bundle_result: BundleDiffResult | None = None, matrix_result: DiffResult | None = None,
    severity_config: SeverityConfig | None = None, severity_exit_code: int | None = None,
    contract_coverage_exit_contribution: int = 0, contract_coverage_failure_count: int = 0,
    fail_on_removed: bool = False,
    policy: str = DEFAULT_POLICY_PROFILE, policy_file_path: Path | None = None,
    suppress: Path | None = None, pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    assurance_terms: ReleaseAssuranceTerms | None = None,
    demangle: bool = False,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
    require_complete_analysis: bool = False,
) -> str:
    """Format the release comparison summary as JSON, markdown, or JUnit XML.

    *env_matrix_source_sha256* (Codex review, P2 follow-up) is the release-
    wide deployment-floor digest computed once, at release scope, by the
    caller directly from the resolved ``EnvironmentMatrix`` -- forwarded to
    the JSON branch's envelope field and effective-config-fields block only
    (markdown/JUnit render no such field today). ``None`` when this
    release's candidate declared no ``deployment:`` contract at all.
    *scope_terms* (ADR-065 S2): the one resolved scope every format reads.

    *demangle* (Codex review, PR #1154 follow-up: `compare --view demangle`/
    `--no-demangle` on a directory/package input) only affects the markdown
    render below, applied as the identical post-hoc whole-text
    ``demangle.demangle_text`` pass a single-pair `compare`'s own markdown
    render uses (`service_render.render_output`'s markdown branch) --
    json/junit are machine formats whose consumers match on the raw mangled
    symbol, so *demangle* is a no-op for either, matching that same
    single-pair behaviour (``service_render.render_output``'s own
    docstring: "machine formats ... always keep raw mangled symbols").

    *show_only* (Codex review, PR #1154 second follow-up: "Apply release
    show filters inside each renderer") is forwarded to whichever format
    branch below is selected -- each one applies it to its *own* rendered
    view (per-library findings, and the release-global bundle/matrix
    findings) rather than to a shared upstream projection, so a caller that
    reaches this function once for the primary ``--format`` and once more
    for a secondary ``-o`` (passing ``show_only=None`` for the latter,
    per that render's own "always full" contract) gets two independently
    correct renders from the same already-computed ``library_results``/
    ``bundle_result``/``matrix_result``.
    """
    if fmt == "oneline":
        # The "just tell me" flow at release cardinality -- a count fold over
        # the already-stripped per-library summaries, through the same
        # `format_stat_line` a single-pair `compare` renders, so the two
        # cannot drift. See `report/release_oneline.py` for why this is the
        # one of `compare`'s four remaining formats that generalizes without
        # a per-member `DiffResult`.
        from .report.release_oneline import (
            format_release_oneline,
            release_global_counts,
        )

        return format_release_oneline(
            worst_verdict,
            library_results,
            severity_exit_code=severity_exit_code,
            env_matrix_source_sha256=env_matrix_source_sha256,
            # Bundle/probe-matrix findings belong to no library, so they are
            # absent from `library_results` even though `worst_verdict` folds
            # them -- see `release_global_counts`.
            release_global=release_global_counts(bundle_result, matrix_result),
        )
    if fmt == "junit":
        return _format_release_junit(
            diff_pairs, matrix_result, library_results, severity_config=severity_config,
            scope_terms=scope_terms, show_only=show_only,
            env_matrix_source_sha256=env_matrix_source_sha256,
        )
    if fmt == "json":
        return _format_release_json(
            worst_verdict, old_dir, new_dir, library_results, removed_keys, added_keys,
            old_map, new_map, warning_msgs, bundle_result, matrix_result,
            severity_config=severity_config,
            severity_exit_code=severity_exit_code,
            contract_coverage_exit_contribution=contract_coverage_exit_contribution,
            contract_coverage_failure_count=contract_coverage_failure_count,
            fail_on_removed=fail_on_removed,
            policy=policy, policy_file_path=policy_file_path,
            suppress=suppress, pack_application=pack_application,
            scope_public_headers=scope_public_headers,
            scope_terms=scope_terms,
            assurance_terms=assurance_terms,
            show_only=show_only,
            env_matrix_source_sha256=env_matrix_source_sha256,
            require_complete_analysis=require_complete_analysis,
        )
    md = _format_release_markdown(
        worst_verdict, old_dir, new_dir, library_results, removed_keys, added_keys,
        old_map, new_map, bundle_result, matrix_result,
        scope_section=scope_terms.section if scope_terms is not None else None,
        severity_config=severity_config,
        show_only=show_only,
        env_matrix_source_sha256=env_matrix_source_sha256,
    )
    if demangle:
        from .demangle import demangle_text

        md = demangle_text(md)
    return md


def _format_release_junit(
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] | None,
    matrix_result: DiffResult | None,
    library_results: list[dict[str, object]],
    *,
    severity_config: SeverityConfig | None = None,
    scope_terms: ComparisonScopeTerms | None = None,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
) -> str:
    """Render the release summary as a JUnit XML report.

    *scope_terms* (ADR-065 S2): ``unsupported`` errors only when the
    completeness decision blocks; see ``report.junit_scope`` otherwise.

    *severity_config*, when given, is forwarded to
    :func:`to_junit_xml_multi` (Codex review on #549) so a finding a severity
    config promotes to ``error`` fails its JUnit testcase the same way it
    contributes to the release's severity-aware exit code — otherwise a CI
    dashboard reading this JUnit file could show zero failures for a release
    that just exited non-zero on that exact finding.

    A ``"not_comparable"`` library (ADR-050 D2 — its own dedicated verdict
    string, not folded into ``"ERROR"``, see ``_RELEASE_VERDICT_ORDER``) gets
    the same treatment as a genuine ``"ERROR"``: without this, it would
    contribute zero testsuites here, so a CI dashboard reading only this
    JUnit file would show no failures for a release that just exited 16 on
    exactly this library. ``entry["reason"]`` (not the ``"error"`` key
    ``_build_error_testsuite`` defaults to) carries the message for this
    verdict.

    *show_only* (Codex review, PR #1154 second follow-up) is forwarded to
    :func:`to_junit_xml_multi`, which already knows how to filter each
    ``<testsuite>`` by it (the identical single-pair ``to_junit_xml``
    machinery) -- this was previously the one primary format that silently
    ignored the release's ``--view show=`` selection entirely, since
    *pairs* here carries the full, un-filtered per-library ``DiffResult``s
    (and, folded in below, the release-global matrix result) rather than a
    pre-filtered projection. Forwarding it here is what makes JUnit agree
    with JSON/Markdown for the same ``--view show=`` selection, matrix
    findings included (the synthetic testsuite appended below is filtered
    the same way a real library's is, since it rides through the identical
    ``(DiffResult, old_snapshot)`` shape).

    *show_impact* (``--view impact``, Codex review, PR #1154 third
    follow-up: "Fresh evidence after the prior per-library impact-view
    fix ... this JUnit branch renders from ``diff_pairs`` and forwards
    only ``show_only`` ... silently omits the requested impact view")
    is deliberately **not** a parameter here at all, matching single-pair
    ``compare``'s own already-shipped behaviour: ``service_render.
    render_output``'s ``fmt == "junit"`` branch never receives or forwards
    ``show_impact`` either, so ``compare -o junit=... --view impact`` on
    a single old/new pair already renders ordinary JUnit XML with no
    impact representation, silently, today -- this is not a release-only
    gap this function introduced, it is the release engine agreeing with
    the single-pair contract it exists to mirror (ADR-037 D1/D7). An
    impact table is inherently prose (a ranked list of affected symbols
    with explanations, see ``report/build.py``'s ``impact_table``) with no
    natural ``<testsuite>``/``<testcase>`` projection the way a filtered
    finding set has, and JUnit's actual consumers (CI dashboards matching
    on pass/fail per testcase) have no use for it -- the same reasoning
    that makes *demangle* a no-op for junit/json above. Silently doing
    nothing here (rather than rejecting ``--view impact`` outright for a
    release JUnit render) preserves that parity: rejecting only the
    release path would make it *more* restrictive than the single-pair
    command for the identical flag combination, which is the opposite of
    what every other ``--view``-forwarding fix in this file does.

    *env_matrix_source_sha256* (Codex review, P2 follow-up, round-8): the
    same release-wide deployment-floor digest the JSON envelope carries
    (``_format_release_json``'s own parameter of the same name), forwarded
    to :func:`abicheck.junit_report.to_junit_xml_multi`, which renders it as
    a release-level ``<testsuite name="abicheck.deployment">`` property --
    *not* left to whichever per-library ``DiffResult`` happens to carry its
    own ``env_matrix_source_sha256`` field, since a release with zero
    matched/completed pairs would then lose the digest entirely (no
    ``<testsuite>`` exists to carry it), even though this same invocation's
    JSON output records it correctly.
    """
    from .junit_report import to_junit_xml_multi

    pairs: list[tuple[DiffResult, AbiSnapshot | None]] = list(diff_pairs or [])
    # Release-global matrix findings ride in as their own synthetic
    # testsuite so CI dashboards reading the JUnit report see the failure.
    if matrix_result is not None:
        pairs.append((matrix_result, None))
    # An `unsupported`/`failed` member is the scope suite's to render
    # (`append_scope_suite`: skipped under `warn`, an error under `block`)
    # -- listing it here too emitted the same failure twice (CodeRabbit).
    error_libs = [
        {**entry, "error": entry.get("reason", entry["verdict"])}
        if entry.get("verdict") == "not_comparable"
        else entry
        for entry in library_results
        if entry.get("verdict") in ("ERROR", "not_comparable")
    ]
    # A member short of the pinned `--depth` rung is an error suite too: the
    # XML is rendered before the exit is taken, so without this a release
    # exiting 7 produced `failures="0" errors="0"` (Codex review).
    from .frontends.cli.release_evidence_contract import (
        evidence_contract_error_entries,
    )

    _already = {entry.get("library") for entry in error_libs}
    error_libs += [
        entry
        for entry in evidence_contract_error_entries(library_results)
        if entry["library"] not in _already
    ]
    return to_junit_xml_multi(
        pairs,
        show_only=show_only,
        severity_config=severity_config,
        error_libraries=error_libs if error_libs else None,
        comparison_scope=scope_terms.section if scope_terms is not None else None,
        env_matrix_source_sha256=env_matrix_source_sha256,
    )


def _list_len(value: object) -> int:
    """``len(value)`` when *value* is a list, else 0 -- a small type-safe
    helper for reading the length of an ``object``-typed dict value (e.g.
    ``dict[str, object]["some_key"]``) without a bare ``len(object)`` mypy
    error."""
    return len(value) if isinstance(value, list) else 0


def _release_filtered_summary_counts(
    library_results: list[dict[str, object]],
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    *,
    displayed_bundle_count: int,
    displayed_matrix_count: int,
) -> dict[str, int]:
    """Aggregate ``{"displayed", "total"}`` finding counts across every
    release-level projection a ``--view show=...`` selection was applied
    to, for both ``_format_release_json`` and ``_format_release_markdown``
    to record under their own ``release_filtered_summary`` field (Codex
    review, fresh evidence, both formats: "Record the active filter in
    release JSON" / "Disclose active filters in release Markdown") --
    deliberately a separately-named, separately-shaped structure from
    scalar ``compare`` JSON's own ``filtered_summary`` (Codex review, fresh
    evidence, second round: "Preserve the scalar filtered_summary schema"),
    since scalar's shape (``breaking``/``source_breaks``/``risk_changes``/
    ``total_changes``, one ``DiffResult``'s own severity buckets) has no
    release-level equivalent to compute from an aggregate across many
    libraries plus the bundle/matrix sections -- reusing that name for an
    incompatible shape would silently break a consumer reading
    ``filtered_summary.breaking`` off a release document the same way it
    does off a scalar one.

    ``total``/``displayed`` are summed from each library's own real
    (uncapped) finding count, plus the bundle/matrix findings when either
    ran. Reading ``findings_total_count``/``findings_total_count_view``
    (stashed per-library by ``_strip_diff_results_and_adjust_verdict``,
    before its own ``_MAX_RELEASE_FINDINGS_PER_LIBRARY`` display cap is
    applied to ``entry["findings"]``) rather than ``len(lib["findings"])``
    is deliberate too (Codex review, fresh evidence, third round: "Count
    uncapped findings in release filter totals") -- summing the
    already-capped display list under-reports a library with more than the
    per-library findings cap (e.g. 25 real findings reads as 10), the exact
    "trace of what a filter hid" this field exists to preserve.

    *displayed_bundle_count*/*displayed_matrix_count* are passed in rather
    than recomputed here, since each caller already has its own
    already-filtered bundle/matrix projection in a different shape (JSON's
    own list-of-dicts vs. Markdown's list of live objects) -- counting is
    the only thing both need from it.
    """

    def _as_count(value: object) -> int:
        return value if isinstance(value, int) else 0

    total_findings = sum(
        _as_count(lib.get("findings_total_count"))
        for lib in library_results
        if isinstance(lib, dict)
    )
    displayed_findings = sum(
        _as_count(lib.get("findings_total_count_view"))
        for lib in library_results
        if isinstance(lib, dict)
    )
    if bundle_result is not None:
        total_findings += len(bundle_result.bundle_findings)
        displayed_findings += displayed_bundle_count
    if matrix_result is not None:
        total_findings += len(matrix_result.changes)
        displayed_findings += displayed_matrix_count
    return {"displayed": displayed_findings, "total": total_findings}


def _format_release_json(
    worst_verdict: str,
    old_dir: Path,
    new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    warning_msgs: list[str],
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    severity_config: SeverityConfig | None = None,
    severity_exit_code: int | None = None,
    contract_coverage_exit_contribution: int = 0, contract_coverage_failure_count: int = 0,
    fail_on_removed: bool = False,
    policy: str = DEFAULT_POLICY_PROFILE, policy_file_path: Path | None = None,
    suppress: Path | None = None, pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
    scope_terms: ComparisonScopeTerms | None = None,
    assurance_terms: ReleaseAssuranceTerms | None = None,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
    require_complete_analysis: bool = False,
) -> str:
    """Render the release summary as a JSON document (``release_schema_
    version``: :data:`~abicheck.schemas.RELEASE_SCHEMA_VERSION`).
    ``unmatched_old``/``unmatched_new`` name the members with no counterpart;
    *removed_keys*/*added_keys* are the **proven** sets ``exit`` reads.

    *show_only* (Codex review, PR #1154 second follow-up) is applied here,
    at render time, to three independent things -- never by mutating
    *library_results* itself, so this function's own caller can also use it
    unfiltered (a secondary ``-o`` passes ``show_only=None``): the
    embedded per-library ``"findings"`` (via
    :func:`_release_findings_for_render`), and the release-global
    ``bundle_findings``/``matrix_findings`` lists below (filtered directly,
    since those carry no pre-computed view of their own).

    When *show_only* is active, the document also records the selector
    itself (``show_only_filter``, the identical field name/shape scalar
    ``compare`` JSON already uses) and a release-wide
    ``release_filtered_summary`` (``displayed``/``total`` finding counts,
    aggregated across every library plus the bundle/matrix sections above,
    using each library's real uncapped count rather than its display-capped
    ``findings`` list) -- Codex review, fresh evidence, three rounds: (1)
    without either field, a filtered-to-empty ``findings`` list next to
    ``verdict: BREAKING`` was indistinguishable from missing/truncated
    detail; (2) the counts live under a *new* name (``release_filtered_
    summary``, not ``filtered_summary``) since the release-level aggregate
    has no per-severity-bucket breakdown, and reusing the scalar field's
    name for an incompatible shape would silently break a consumer reading
    ``filtered_summary.breaking``; (3) the counts must come from each
    library's real, uncapped total (``findings_total_count``/
    ``findings_total_count_view``, stashed by ``_strip_diff_results_and_
    adjust_verdict`` before its own per-library display cap applies), not
    ``len(lib["findings"])``, which under-reports past that cap.
    """
    display_library_results = _release_findings_for_render(library_results, show_only)
    changed_libraries = [
        str(lib["library"])
        for lib in library_results
        if str(lib.get("verdict")) != "NO_CHANGE"
        and str(lib.get("verdict")) not in _RELEASE_OPERATIONAL_SENTINELS
    ]
    from .report.not_comparable import run_outcome_dict_for_release
    from .workflows.release_scope import release_global_ran, unmatched_names
    terms = scope_terms if scope_terms is not None else comparison_scope_terms(resolve_scope_decision(None, None))
    # ADR-071, already decided by the caller; a direct unit-test/legacy call
    # falls back to an empty, setting-off decision (contributes `0`, emits no
    # section) -- the same default `scope_terms` uses just above.
    a_terms = assurance_terms if assurance_terms is not None else release_assurance_terms(
        resolve_release_assurance_decision((), require_complete=False))
    release_global_verdict = _release_global_verdict(bundle_result, matrix_result)
    # The release's own assurance gate (`assurance.require_complete`),
    # threaded into *every* decision a reader sees, not just the process
    # exit: the persisted `exit` block, `run_outcome` and
    # `effective_config_fields["gate.require_complete_analysis"]` are
    # what a machine consumer accepts or rejects a run on, and a run that
    # exits 1 while its own report says 0/clean/false is exactly the
    # disagreement `_exit_compare_release` exists to make impossible
    # (Codex review, PR #1238, P1).
    exit_dict = resolve_release_exit_decision_for_report(
        worst_verdict, fail_on_removed, removed_keys, severity_exit_code,
        contract_coverage_exit_contribution, library_results, release_global_verdict,
        incomplete_scope_contribution=terms.decision.incomplete_scope_exit_contribution,
        no_comparison_completed_contribution=terms.decision.no_comparison_completed_exit_contribution,
        require_complete_analysis=require_complete_analysis,
    ).to_dict()
    record = terms.record
    summary: dict[str, object] = {
        # No version field existed here before (Codex review); see
        # RELEASE_SCHEMA_VERSION's own docstring.
        "release_schema_version": RELEASE_SCHEMA_VERSION,
        "verdict": worst_verdict,
        "old_dir": str(old_dir),
        "new_dir": str(new_dir),
        "libraries": display_library_results,
        "changed_libraries": changed_libraries,
        # ADR-065 S4: read off the acquisition record, never a set
        # difference. A driver with no record has nothing that could
        # establish what is unmatched, so it reports nothing here.
        "unmatched_old": unmatched_names(record, side="old") if record else [],
        "unmatched_new": unmatched_names(record, side="new") if record else [],
        "warnings": warning_msgs,
        "exit": exit_dict,
        "run_outcome": run_outcome_dict_for_release(
            _release_completed_compatibility_verdict(
                library_results,
                release_global_verdict,
                release_global_ran=release_global_ran(bundle_result, matrix_result, record),
            ),
            exit_dict,
            scope=terms.completeness,
        ),
    }
    if terms.section is not None:
        summary["comparison_scope"] = terms.section
    # Severity config block (present only when a severity setting was in effect), mirroring
    # compare mode so downstream consumers (e.g. the PR-comment renderer) can see
    # which categories are gated to error and bucket findings accordingly.
    if severity_config is not None:
        # Escalate to 4 (the abi_breaking ceiling) when the removed-required-
        # library axis is what's driving run_outcome.gate above, mirroring
        # buildsource/check_report.py's _escalate_removed_library_severity
        # exactly (Codex review, fresh evidence): without this, a severity-
        # scheme release whose ordinary findings contribute 0 emits
        # severity.exit_code: 0 alongside run_outcome.gate: abi_breaking --
        # the exact disagreement GateInfo.from_report_data's own
        # contradiction check (this same PR) fails closed on, turning a
        # legitimate --fail-on-removed-library escalation into an
        # unavailable target for aggregate rather than preserving it.
        removed_lib_contribution = exit_dict.get("removed_required_library_contribution")
        escalated_exit_code = (
            max(severity_exit_code or 0, 4)
            if isinstance(removed_lib_contribution, int) and removed_lib_contribution != 0
            else severity_exit_code
        )
        summary["severity"] = {
            "config": {
                "abi_breaking": severity_config.abi_breaking.value,
                "potential_breaking": severity_config.potential_breaking.value,
                "quality_issues": severity_config.quality_issues.value,
                "addition": severity_config.addition.value,
            },
            "exit_code": escalated_exit_code,
        }
    # ADR-071 D6's orthogonal analysis-assurance axis, max()-aggregated
    # across members. Same "present only when active" convention as the
    # severity/coverage blocks around it, so a release report produced
    # without the setting is byte-identical (D4). Key name matches
    # single-pair `compare` JSON's; the shape differs (it is the *fold*),
    # which is why it carries its own `schema_version`.
    if a_terms.section is not None:
        summary["analysis_assurance"] = a_terms.section
        # The canonical top-level key (report schema 2.40) -- sibling of
        # `contract_coverage_exit_contribution` just below, and what
        # `aggregate.gate._analysis_assurance_exit` and the Action's deferred
        # gate read. With the floor only inside `exit`/`analysis_assurance`
        # both read `0` for a run that really exited `1`, so aggregating a
        # release report dropped the gate (Codex security review, P1).
        summary["analysis_assurance_exit_contribution"] = (
            a_terms.decision.exit_contribution
        )
    # ADR-049 Phase 7's orthogonal contract-coverage axis (CLI-audit P1,
    # release/package parity), max()-aggregated across every library. Only
    # present when at least one library entry carries the per-library key --
    # i.e. --contract was active -- mirroring the severity block's
    # own "present only when active" convention, and matching single-pair
    # `compare` JSON's `contract_coverage_exit_contribution` field name so a
    # consumer reads the same key regardless of which command produced it.
    if any("contract_coverage_exit_contribution" in lib for lib in library_results):
        summary["contract_coverage_exit_contribution"] = (
            contract_coverage_exit_contribution
        )
        # Independent of the exit-code fold above: `contract.unresolved:
        # warn` deliberately zeroes the contribution while the failures
        # themselves stay real (Codex review, CLI-audit P2 follow-up) --
        # without this, a warn-accepted release-level coverage gap would be
        # indistinguishable from a genuinely clean run anywhere this JSON is
        # read from. Same "present only when active" gate as the field above.
        summary["contract_coverage_failure_count"] = contract_coverage_failure_count
    # Codex review, P2 follow-up: the declared-deployment-floor contract's
    # content digest, promoted once to the release envelope. Previously
    # inferred by reading it off the first per-library entry that carried
    # the key (`env_matrix` is threaded identically to every library's
    # comparison in one release fan-out, `cli_compare_release_pairwise.py`'s
    # own `env_matrix` parameter, so every entry that has it carries the
    # identical digest) -- but a release with no matched pairs, or every
    # pair failing before producing a `DiffResult`, then had *no* entry to
    # read it off of at all, making a genuinely-configured `deployment:`
    # contract indistinguishable from none. Now passed in directly by the
    # caller, computed once at release scope from the resolved
    # `EnvironmentMatrix` before any per-library compare even runs
    # (`checker.env_matrix_content_digest`), so this envelope field (and
    # `effective_config_fields["policy.env_matrix"]` below) is correct
    # regardless of how many library comparisons actually completed.
    # Omitted, not `null`, when this release's candidate declared no
    # `deployment:` contract at all -- same additive convention as every
    # other "present only when active" key in this function.
    if env_matrix_source_sha256 is not None:
        summary["env_matrix_source_sha256"] = env_matrix_source_sha256
    # Release-level public-surface scoping rollup (ADR-024, issue #235).
    # Present only when --scope-public-headers was active (per-library
    # entries then carry a "scope_resolved" key).
    scoped_libs = [lib for lib in library_results if "scope_resolved" in lib]
    if scoped_libs:
        summary["scope"] = _release_json_scope(scoped_libs)
    if bundle_result is not None:
        summary["bundle_verdict"] = bundle_result.bundle_verdict.value
        summary["bundle_findings"] = [
            {
                "kind": f.kind.value,
                "symbol": f.symbol,
                "consumer_library": f.consumer_library,
                "provider_library": f.provider_library,
                "description": f.description,
                "old_value": f.old_value,
                "new_value": f.new_value,
                "affected_libraries": list(f.affected_libraries),
            }
            for f in release_bundle_findings_for_view(bundle_result, show_only)
        ]
        # G38 P0-D: surface a bundle-analysis-step failure structurally
        # instead of only as a stderr `click.echo`, so a JSON-consuming
        # caller (CI gate, PR-comment renderer) can tell "bundle analysis
        # ran clean" apart from "bundle analysis partially failed, treat
        # bundle_verdict/bundle_findings as a possibly-incomplete view" --
        # present only when non-empty, matching this file's established
        # "present only when active" convention for the other optional
        # summary keys above.
        if bundle_result.analysis_errors:
            summary["bundle_analysis_errors"] = list(bundle_result.analysis_errors)
    if matrix_result is not None:
        # Release-global build-configuration findings (G2: probe matrix).
        # `.changes` is post-suppression, so suppressed findings are
        # excluded here just as they are from the verdict. Codex review, PR
        # #1154 second follow-up: this list is also filtered by
        # `--view show=` (via `release_matrix_changes_for_view`), same as
        # every per-library findings list above -- `matrix_verdict` itself
        # stays the real, unfiltered verdict, matching how a per-library
        # entry's own `verdict` is never display-filtered either.
        summary["matrix_verdict"] = matrix_result.verdict.value
        summary["matrix_findings"] = [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "old_value": c.old_value,
                "new_value": c.new_value,
            }
            for c in release_matrix_changes_for_view(matrix_result, show_only)
        ]
    if show_only:
        # Codex review, fresh evidence ("Record the active filter in
        # release JSON"): the release document substituted the filtered
        # library/bundle/matrix projections above with no trace of *why* --
        # a filtered-to-empty `findings` list next to `verdict: BREAKING`
        # was indistinguishable from missing/truncated detail, unlike
        # scalar `compare` JSON, which has always carried
        # `show_only_filter`/`filtered_summary` for exactly this reason
        # (`reporter._add_show_only_filter`). See
        # :func:`_release_filtered_summary_counts` for why the counts live
        # under a separately-named `release_filtered_summary` field rather
        # than a same-shaped `filtered_summary`.
        summary["show_only_filter"] = show_only
        summary["release_filtered_summary"] = _release_filtered_summary_counts(
            library_results,
            bundle_result,
            matrix_result,
            displayed_bundle_count=_list_len(summary.get("bundle_findings")),
            displayed_matrix_count=_list_len(summary.get("matrix_findings")),
        )
    # CLI cleanup phase two, PR B (Codex review, PR #803): the release-level
    # *summary* JSON is a separate computation from the optional per-library
    # `to_json` sidecar files, which reach `add_contract_context` on their
    # own -- so the release-fan-out parity this digest exists to provide
    # needs its own, explicit stamp here too. `_release_summary_effective_
    # config_block` (`cli_compare_receipt.py` -- this module is at its
    # `no_growth` line-count cap, P1/CLI-audit) is the one shared helper
    # both this function and `_write_release_summary_file`
    # (`cli_compare_release_matrix.py`) call, so the two summary documents
    # can never independently drift.
    from .cli_compare_receipt import (
        _release_summary_effective_config_block,
        release_disposition_audit_block,
    )

    digest, fields = _release_summary_effective_config_block(
        severity_config, policy=policy, policy_file_path=policy_file_path,
        suppress=suppress, pack_application=pack_application,
        scope_public_headers=scope_public_headers, on_incomplete_scope=terms.policy,
        fail_on_removed_library=fail_on_removed,
        env_matrix_source_sha256=env_matrix_source_sha256,
        # ADR-071: the receipt must name the gate that produced this report.
        require_complete_analysis=require_complete_analysis,
    )
    summary["effective_config_digest"] = digest
    summary["effective_config_fields"] = fields
    # ADR-067 C-S2: unconditional, like scalar `compare`'s own block -- D3's
    # rule is that the raw-versus-effective counts are never dropped, only
    # ever collapsed in detail.
    summary["disposition_audit"] = release_disposition_audit_block(
        library_results, matrix_result, severity_config, bundle_result
    )
    return json.dumps(summary, indent=2)


def _release_json_scope(scoped_libs: list[dict[str, object]]) -> dict[str, object]:
    """Build the release-level public-surface scoping rollup for JSON output."""

    def _as_int(v: object) -> int:
        return v if isinstance(v, int) else 0

    return {
        "public_headers_applied": True,
        "manual_review_required": any(
            not bool(lib.get("scope_resolved", True)) for lib in scoped_libs
        ),
        "public_additions": sum(
            _as_int(lib.get("compatible_additions", 0)) for lib in scoped_libs
        ),
        "filtered_internal_changes": sum(
            _as_int(lib.get("filtered_internal_count", 0)) for lib in scoped_libs
        ),
    }


def _format_release_markdown(
    worst_verdict: str,
    old_dir: Path,
    new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    bundle_result: BundleDiffResult | None,
    matrix_result: DiffResult | None,
    scope_section: Mapping[str, object] | None = None,
    severity_config: SeverityConfig | None = None,
    show_only: str | None = None,
    env_matrix_source_sha256: str | None = None,
) -> str:
    """Render the release summary as a Markdown document.

    *scope_section* (ADR-065 S2) is the JSON ``comparison_scope`` mapping;
    rendered by ``report.comparison_scope.render_comparison_scope_markdown``,
    and when it says no comparison completed the verdict row says so too,
    rather than showing the compared-members floor ``NO_CHANGE`` as the
    whole scope's answer. *severity_config* (ADR-067 C-S2) feeds the release-
    global probe-matrix comparison's own audit contribution to the folded
    ``disposition_audit`` section, the same gate every per-library block was
    already computed under.

    *show_only* (Codex review, PR #1154 second follow-up) is applied here,
    at this call site, the same three ways :func:`_format_release_json`
    applies it: the per-library findings section (via
    :func:`_release_findings_for_render`), and the release-global bundle/
    matrix findings sections (via
    :func:`abicheck.reporter_markdown.release_bundle_findings_for_view`/
    :func:`abicheck.reporter_markdown.release_matrix_changes_for_view`).
    Each filtered projection (``display_bundle_findings``/
    ``display_matrix_changes``) is computed once, here, and handed to
    ``render_release_markdown``'s two Markdown functions as an already-
    filtered, immutable value (Codex review, fresh evidence, PR #1154
    follow-up: "Move release filtering out of the Markdown renderer") --
    `report/AGENTS.md`'s renderer contract forbids the ``report``-classified
    renderer itself from calling either filter function. *library_results*
    itself is never mutated, so this function's caller can also render an
    unfiltered (``show_only=None``) secondary ``-o`` from the same data.

    When *show_only* is active, the document also gets a ``> Filtered by:
    ...`` note (Codex review, fresh evidence: "Disclose active filters in
    release Markdown") -- the identical rendering and aggregate counts
    JSON's ``release_filtered_summary`` field carries, via the shared
    :func:`_release_filtered_summary_counts`, mirroring scalar ``compare``
    Markdown's own long-standing ``> Filtered by: ...`` note for the same
    reason: without it, a filtered-to-empty findings section next to
    ``verdict: BREAKING`` was indistinguishable from missing detail.

    *env_matrix_source_sha256* (Codex review, P2 follow-up, round-8): the
    same release-wide deployment-floor digest ``_format_release_json``'s
    envelope field and ``_format_release_junit``'s release-level testsuite
    property carry, rendered here as a bullet line -- mirroring scalar
    ``compare`` Markdown's own ``render_markdown_document`` "Deployment
    floor digest" bullet for the identical field. Omitted entirely (never
    a placeholder line) when ``None``, i.e. this release's candidate
    declared no ``deployment:`` contract at all.
    """
    from .cli_compare_receipt import (
        _release_md_library_findings,
        release_disposition_audit_block,
    )
    from .report.comparison_scope import render_comparison_scope_markdown
    from .report.disposition_audit import (
        DispositionAudit,
        render_disposition_audit_section,
    )

    display_library_results = _release_findings_for_render(library_results, show_only)
    display_bundle_findings = (
        release_bundle_findings_for_view(bundle_result, show_only)
        if bundle_result is not None
        else []
    )
    # Codex review, fresh evidence (PR #1154 follow-up: "Move release
    # filtering out of the Markdown renderer"): precomputed here, at this
    # workflow-boundary call site, rather than inside `render_release_
    # markdown._release_md_matrix_findings` itself -- `report/AGENTS.md`'s
    # renderer contract forbids a renderer from filtering findings; it may
    # only consume an already-computed, immutable projection. Mirrors
    # `display_bundle_findings` immediately above, which already followed
    # this rule.
    display_matrix_changes = (
        release_matrix_changes_for_view(matrix_result, show_only)
        if matrix_result is not None
        else []
    )

    _VERDICT_EMOJI = {
        "NO_CHANGE": "✅",
        "COMPATIBLE": "✅",
        "COMPATIBLE_WITH_RISK": "⚠️",
        "API_BREAK": "⚠️",
        "BREAKING": "❌",
        "ERROR": "💥",
        "not_comparable": "❓",
        "unsupported": "🚫",
        "failed": "💥",
    }
    verdict_cell = f"{_VERDICT_EMOJI.get(worst_verdict, '?')} `{worst_verdict}`"
    if scope_section is not None and scope_section.get("no_comparison_completed"):
        verdict_cell = "🛑 no comparison completed"
    elif scope_section is not None and scope_section.get("completeness") == "incomplete":
        verdict_cell += " (compared members only — scope incompletely checked)"
    lines: list[str] = [
        "# ABI Release Comparison",
        "",
        "| | |",
        "|---|---|",
        f"| **Old** | `{old_dir}` |",
        f"| **New** | `{new_dir}` |",
        f"| **Verdict** | {verdict_cell} |",
    ]
    bundle_count = len(display_bundle_findings)
    if bundle_result is not None:
        bundle_em = _VERDICT_EMOJI.get(bundle_result.bundle_verdict.value, "?")
        lines.append(
            f"| **Bundle** | {bundle_em} `{bundle_result.bundle_verdict.value}` "
            f"({bundle_count} cross-library finding{'s' if bundle_count != 1 else ''}) |",
        )
    if env_matrix_source_sha256 is not None:
        lines.append("")
        lines.append(f"- Deployment floor digest: `{env_matrix_source_sha256}`")
    if show_only:
        # Codex review, fresh evidence ("Disclose active filters in release
        # Markdown"): the release Markdown substituted the filtered per-
        # library/bundle/matrix projections above with no trace of *why* --
        # mirrors scalar `compare` Markdown's own `> Filtered by: ...` note
        # (`report/render_markdown_document.py`), reusing the identical
        # `render_show_only_cli_hint` rendering and the same aggregate
        # counts JSON's `release_filtered_summary` field now carries (see
        # `_release_filtered_summary_counts`'s own docstring).
        from .reporter_markdown import render_show_only_cli_hint

        counts = _release_filtered_summary_counts(
            library_results,
            bundle_result,
            matrix_result,
            displayed_bundle_count=len(display_bundle_findings),
            displayed_matrix_count=len(display_matrix_changes),
        )
        cli_hint = render_show_only_cli_hint(show_only)
        lines.append("")
        lines.append(
            f"> Filtered by: `{cli_hint}` "
            f"({counts['displayed']} of {counts['total']} findings shown)"
        )
        lines.append("")
    if scope_section is not None:
        lines += render_comparison_scope_markdown(scope_section)
    lines += _release_md_libraries_table(display_library_results, _VERDICT_EMOJI)
    lines += _release_md_coverage_warnings(library_results)
    lines += _release_md_evidence_contract(library_results)
    lines += _release_md_changed_libraries(removed_keys, added_keys, old_map, new_map)
    # The human summary stays bounded even though the shared projection is
    # complete -- the cap is a presentation choice, applied here, and since
    # plan slice 7m it is *automatic*: `--max-findings-per-library` and its
    # environment variable are retired, so there is one cap and no resolver
    # for the Markdown render to disagree with anyone about (the constant
    # lives in the leaf, so reading it here is not an import cycle).
    from .report.release_display_limits import MAX_RELEASE_FINDINGS_PER_LIBRARY

    lines += _release_md_library_findings(
        display_library_results,
        display_cap=MAX_RELEASE_FINDINGS_PER_LIBRARY,
    )
    lines += _release_md_bundle_findings(bundle_result, display_bundle_findings)
    lines += _release_md_matrix_findings(matrix_result, display_matrix_changes)
    lines += render_disposition_audit_section(
        DispositionAudit.from_dict(
            release_disposition_audit_block(
                library_results, matrix_result, severity_config, bundle_result
            )
        )
    )
    return "\n".join(lines)

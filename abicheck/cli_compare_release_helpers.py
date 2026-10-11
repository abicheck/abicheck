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
:mod:`abicheck.cli_compare_release`. The release report itself is no longer
formatted here: :mod:`abicheck.report.release_document` builds the one
release report and :mod:`abicheck.report.render_release_report` renders
every format from it, reached through
:func:`abicheck.frontends.cli.release_report.render_release_summary`.

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

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .bundle_models import BundleSignatureEvidence
from .checker import DiffResult
from .errors import SnapshotError
from .frontends.cli.release_exit import _exit_compare_release as _exit_compare_release
from .model import AbiSnapshot
from .model.release_surface import ReleasePublicSurface
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
    resolve_release_gate_options as resolve_release_gate_options,  # re-exported, ADR-064
    worse_release_verdict,
)
from .workflows.release_scope import (
    StrandedLibraryResolution,
    scope_manifest_to_members,
)

if TYPE_CHECKING:
    from .bundle_manifest import InstantiationManifest
    from .model.scope_acquisition import ScopeAcquisitionRecord
    from .workflows.extraction import PackageExtractor
    from .workflows.policy_file import PolicyFile


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
    candidates: list[tuple[Path, str | None]] = [
        (r, v) for r, v in roots if r is not None
    ]
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
    with :func:`abicheck.model.bundle_facts.compare_bundle_from_facts`); this
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
        old_snap = build_bundle_snapshot_mixed(
            dict(old_map), resolved_evidence=old_snapshots
        )
        new_snap = build_bundle_snapshot_mixed(
            dict(new_map), resolved_evidence=new_snapshots
        )
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
            input_path,
            debug_pkg,
            devel_pkg,
            make_temp_dir,
            is_package,
            detect_extractor,
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
    public_surface: ReleasePublicSurface | None = None,
) -> None:
    """Persist the OLD side's per-library snapshots (plus manifest, if any)
    to *bundle_facts_out* as a :class:`~abicheck.model.bundle_facts.BundleFacts`
    file (G38 Phase 2's ``--bundle-facts-out`` producer).

    *diff_pairs* is ``release_old_snapshot_pairs(library_results)``, never
    the ``diff_pairs`` the same fan-out hands JUnit -- that one carries the
    compact ``SymbolInventory``, which cannot reconstruct a baseline.
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
    :func:`~abicheck.model.bundle_facts.capture_bundle_facts` as
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
            manifest = (
                load_manifest(manifest_path) if manifest_path is not None else None
            )

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
            # The OLD side's own acquired public contract, stored once for
            # the whole document (schema_version 4); `None` keeps the
            # document at its previous version.
            public_surface=public_surface,
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
    bundle_cohorts: tuple[str, ...] = (),
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
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
        worst_verdict = worse_release_verdict(
            worst_verdict, bundle_result.bundle_verdict.value
        )
    return bundle_result, worst_verdict


def _cleanup_temp_dirs(temp_dir_paths: list[str]) -> None:
    """Remove temporary directories created during package extraction.

    Always cleans up now -- ``--keep-extracted`` is gone (Phase 7d,
    ADR-068 D5), no replacement."""
    import shutil as _shutil

    for td_path in temp_dir_paths:
        _shutil.rmtree(td_path, ignore_errors=True)

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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click

from .bundle import BundleDiffResult, render_bundle_findings_markdown
from .bundle_models import BundleSignatureEvidence
from .checker import DiffResult
from .frontends.cli.options.params import DEFAULT_POLICY_PROFILE
from .model import AbiSnapshot
from .workflows.gate import (
    GateOptions as GateOptions,  # re-exported, ADR-064
    _resolve_release_severity_config as _resolve_release_severity_config,  # re-exported, ADR-064
    apply_release_gate_pack as apply_release_gate_pack,  # re-exported, ADR-064
    resolve_release_exit_decision_for_report,
    resolve_release_gate_options as resolve_release_gate_options,  # re-exported, ADR-064
)

if TYPE_CHECKING:
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
_RELEASE_OPERATIONAL_SENTINELS = frozenset({"ERROR", "not_comparable"})


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


def _resolve_release_headers(
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_header_dir: Path | None,
    new_header_dir: Path | None,
) -> tuple[list[Path], list[Path]]:
    """Resolve per-side headers for compare-release."""
    old_h: list[Path] = list(old_headers_only) if old_headers_only else list(headers)
    new_h: list[Path] = list(new_headers_only) if new_headers_only else list(headers)
    if old_header_dir and not old_headers_only:
        old_h = [old_header_dir]
    if new_header_dir and not new_headers_only:
        new_h = [new_header_dir]
    return old_h, new_h


def _discover_include_roots(header_dir: Path | None) -> list[Path]:
    """Return common include roots from an extracted devel/header package."""
    if header_dir is None:
        return []
    candidates = [
        header_dir,
        header_dir / "usr" / "include",
        header_dir / "usr" / "local" / "include",
    ]
    usr_include = header_dir / "usr" / "include"
    if usr_include.is_dir():
        candidates.extend(p for p in usr_include.iterdir() if p.is_dir())
    seen: set[Path] = set()
    roots: list[Path] = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def _match_release_keys(
    old_dir: Path,
    new_dir: Path,
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_files: list[Path],
    new_files: list[Path],
    is_package: Callable[[Path], bool],
) -> tuple[list[str], list[str], list[str], dict[str, Path], dict[str, Path]]:
    """Match library keys between old and new, handling direct file pairs."""
    direct_file_pair = (
        old_dir.is_file()
        and new_dir.is_file()
        and not is_package(old_dir)
        and not is_package(new_dir)
    )
    if direct_file_pair:
        matched_keys = ["__direct_pair__"]
        old_map = {"__direct_pair__": old_files[0]}
        new_map = {"__direct_pair__": new_files[0]}
        return matched_keys, [], [], old_map, new_map

    matched_keys = sorted(set(old_map) & set(new_map))
    removed_keys = sorted(set(old_map) - set(new_map))
    added_keys = sorted(set(new_map) - set(old_map))
    return matched_keys, removed_keys, added_keys, old_map, new_map


def _collect_release_warnings(
    warning_msgs: list[str],
    matched_keys: list[str],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> None:
    """Collect warning messages for unmatched libraries."""
    for k in removed_keys:
        warning_msgs.append(f"Warning: library removed: {old_map[k].name}")
    for k in added_keys:
        warning_msgs.append(f"Info: library added: {new_map[k].name}")
    if not matched_keys:
        warning_msgs.append(
            "Warning: no matching library pairs found between OLD and NEW inputs."
        )


def _run_bundle_analysis(
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    per_lib_results: list[DiffResult],
    *,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...] = (),
    policy: str = "strict_abi",
    old_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] | None = None,
    new_snapshots: dict[str, AbiSnapshot | BundleSignatureEvidence] | None = None,
    old_root: Path | None = None,
    new_root: Path | None = None,
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

    G38 stabilization Phase 12: both stages (the core
    ``compare_bundle()`` suite and the Phase 4 signature-evidence gate)
    now run through the single :func:`abicheck.bundle_analysis.
    analyze_bundle` orchestrator -- the same one
    :func:`abicheck.bundle_facts.compare_bundle_from_facts` calls for a
    stored-baseline comparison -- rather than being sequenced by hand here.
    This function's own job narrows to what only the live release path
    needs: building the two live ``BundleSnapshot``\\ s, loading an
    explicit ``--manifest``, and re-surfacing ``analyze_bundle``'s
    structured ``analysis_errors`` as the same ``click.echo(...,
    err=True)`` warnings this function has always emitted.

    *old_root*/*new_root* (ADR-062 A1.7) are the two release operands
    themselves (a stored ``ProjectSnapshot`` package directory or a live
    directory), used only for the embedded-``InstantiationManifest``
    fallback below -- a package whose selected variant carries zero
    artifacts has no entry in *old_map*/*new_map* at all to search for one
    (Codex review, fresh evidence: a valid empty ``BundleFacts`` package
    can still carry a manifest, and the required-symbol check was silently
    skipped for it).
    """
    from .bundle import build_bundle_snapshot_mixed, load_manifest
    from .bundle_analysis import analyze_bundle

    if not old_map and not new_map:
        return None
    try:
        # ADR-062 A1.7: old_map/new_map may hold a stored ProjectSnapshot
        # sub-package directory for some (or all) libraries, not only live
        # binary paths -- build_bundle_snapshot_mixed resolves either kind,
        # rather than build_bundle_snapshot's live-only ELF parse, which
        # would otherwise silently drop every stored-side library from
        # bundle-level analysis (Codex review, security finding).
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

    manifest = None
    if manifest_path is not None:
        try:
            manifest = load_manifest(manifest_path)
        except Exception as exc:
            # Manifest is an *explicit* user input. A malformed --manifest
            # is a user error, not an environmental quirk; fail loudly so
            # the contract violation isn't hidden behind a stderr warning.
            raise click.ClickException(
                f"Failed to load manifest {manifest_path}: {exc}",
            ) from exc
    else:
        # ADR-062 A1.7: fall back to a stored side's own embedded
        # InstantiationManifest (`materialize_release_variant_artifacts`
        # already preserves `write_bundle_facts_package`'s project_sections
        # section on disk) rather than silently skipping the manifest-drift
        # contract entirely for a stored comparison with no explicit
        # --manifest (Codex review, fresh evidence). old_map is checked
        # first (the side a manifest more naturally describes as a
        # baseline); new_map only if old carries none. Best-effort: a
        # package written by `storage.import_bundle_facts` stores its own
        # captured manifest in a different shape this reader does not
        # translate (see `read_embedded_instantiation_manifest`'s own
        # docstring) and simply yields None, same as no manifest at all.
        from .bundle_facts_store import read_embedded_instantiation_manifest

        roots: list[Path] = [r for r in (old_root, new_root) if r is not None]
        if not roots:
            # No root threaded through (a caller that predates this
            # parameter) -- fall back to searching stored member
            # sub-packages, same as before. Never reaches a zero-artifact
            # variant's own manifest (see this function's own docstring).
            roots = [p for m in (old_map, new_map) for p in m.values()]
        for candidate_root in roots:
            if not candidate_root.is_dir():
                continue
            try:
                manifest = read_embedded_instantiation_manifest(candidate_root)
            except Exception as exc:
                # A declared-but-corrupted manifest section (see that
                # function's own docstring) is a genuine usage error, not
                # a best-effort miss -- silently degrading here would let a
                # required-symbol check disappear along with the evidence
                # that named it (CodeRabbit review, security finding).
                raise click.UsageError(
                    f"{candidate_root}: embedded instantiation manifest is "
                    f"declared but could not be decoded: {exc}"
                ) from exc
            if manifest is not None:
                break

    system_extra: list[str] = [
        s.strip() for s in bundle_system_providers.split(",") if s.strip()
    ]
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
    for err in result.analysis_errors:
        click.echo(f"Warning: {err}", err=True)

    return result


def _extract_if_package(
    input_path: Path,
    debug_pkg: Path | None,
    devel_pkg: Path | None,
    make_temp_dir: Callable[[str], Path],
    is_package: Callable[[Path], bool],
    detect_extractor: Callable[[Path], PackageExtractor | None],
) -> tuple[Path, Path | None, Path | None, Path | None]:
    """Extract package to tempdir if needed, return
    (lib_dir, debug_dir, header_dir, symbols_file).

    When *input_path* is a plain directory (not a package archive), it is used
    as-is for lib_dir.  Side packages (*debug_pkg*, *devel_pkg*) are still
    extracted in that case so that standalone debug/devel packages paired with
    an already-extracted directory are not silently ignored.

    *symbols_file* (CLI-audit P2) is the Debian dpkg-gensymbols(1) contract
    file from *input_path*'s own control.tar.* when it is a .deb -- ``None``
    for every other package format and for a .deb that ships none. Only the
    primary package is consulted, not *debug_pkg*/*devel_pkg*: a `-dbg`/`-dev`
    companion package does not carry the library's own symbols contract.
    """
    # Default: treat input_path as an already-extracted library directory.
    lib_dir: Path = input_path
    debug_dir: Path | None = None
    header_dir: Path | None = None
    symbols_file: Path | None = None

    if is_package(input_path):
        extractor = detect_extractor(input_path)
        if extractor is None:
            raise click.ClickException(f"Unrecognized package format: {input_path}")
        target = make_temp_dir("abicheck_pkg_")
        result = extractor.extract(input_path, target)
        lib_dir = result.lib_dir
        debug_dir = result.debug_dir
        header_dir = result.header_dir
        symbols_file = result.symbols_file

    if debug_pkg is not None:
        dbg_ext = detect_extractor(debug_pkg)
        if dbg_ext is None:
            raise click.ClickException(
                f"Unrecognized debug package format: {debug_pkg}"
            )
        dbg_target = make_temp_dir("abicheck_dbg_")
        dbg_result = dbg_ext.extract(debug_pkg, dbg_target)
        debug_dir = dbg_result.debug_dir or dbg_result.lib_dir

    if devel_pkg is not None:
        dev_ext = detect_extractor(devel_pkg)
        if dev_ext is None:
            raise click.ClickException(
                f"Unrecognized devel package format: {devel_pkg}"
            )
        dev_target = make_temp_dir("abicheck_dev_")
        dev_result = dev_ext.extract(devel_pkg, dev_target)
        header_dir = dev_result.header_dir or dev_result.lib_dir

    return lib_dir, debug_dir, header_dir, symbols_file


def _debian_symbols_warning(
    old_symbols_file: Path | None,
    new_symbols_file: Path | None,
) -> str | None:
    """Compare two .deb packages' dpkg-gensymbols(1) contracts, if both sides
    have one (CLI-audit P2: "Debian .symbols not integrated" -- extraction
    alone does not make the contract participate in a package compare).

    Returns a formatted diff report to fold into the release warnings list
    when the contracts disagree, else ``None`` -- purely additive/informational
    (never gates the compare's verdict/exit code): the binary ABI diff
    already gates BREAKING/API_BREAK; a symbols-contract mismatch by itself
    only means the *packaging* metadata (minimum versions, listed symbols)
    has drifted from what the binary actually exports, which is useful
    context but not on its own proof of an ABI break (ADR-028 D3's "evidence
    may add context, never silently delete/invent a break" principle,
    applied here to a cross-source packaging check the same way
    crosscheck.py's D4 checks apply it to build/source evidence).
    """
    if old_symbols_file is None or new_symbols_file is None:
        return None
    from .debian_symbols import (
        diff_symbols_files,
        format_diff_report,
        load_symbols_file,
    )

    try:
        old_symbols = load_symbols_file(old_symbols_file)
        new_symbols = load_symbols_file(new_symbols_file)
    except (OSError, ValueError) as exc:
        return f"Debian symbols file could not be parsed: {exc}"
    diff = diff_symbols_files(old_symbols, new_symbols)
    if not (diff.removed or diff.added or diff.version_changed):
        return None
    return "Debian symbols contract changed:\n" + format_diff_report(diff)


def reject_bundle_facts_out_collision(
    bundle_facts_out: Path | None,
    output: Path | None,
    secondary_output: Path | None,
) -> None:
    """Reject ``--bundle-facts-out`` naming the same file as ``--output``/
    ``--write`` (G38 Phase 2).

    A command-specific extra check, deliberately not
    ``reject_incoherent_secondary_output()``'s job (see that leaf module's
    own docstring) -- without it, ``--bundle-facts-out result.json --output
    result.json`` silently overwrites the requested baseline with the
    report while still reporting success (Codex review).
    """
    if bundle_facts_out is None:
        return
    for label, other in (("--output/-o", output), ("--write", secondary_output)):
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
    """Reject ``--bundle-facts-out`` naming a path ``--output-dir`` will
    also write (G38 Phase 2, Codex review, fresh evidence).

    ``reject_bundle_facts_out_collision()`` above only knows about
    ``--output``/``--write`` -- it can't see ``--output-dir``'s own
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
            "--bundle-facts-out's PATH must differ from --output-dir's own "
            "summary.json: writing both to the same file would silently "
            "overwrite the requested bundle-facts baseline with the "
            "per-library summary report."
        )
    for name, old_path in old_map.items():
        lib_path = output_dir / f"{old_path.stem}.json"
        if resolved == lib_path.resolve():
            raise click.UsageError(
                f"--bundle-facts-out's PATH must differ from --output-dir's "
                f"own per-library report for {name!r} ({lib_path}): writing "
                "both to the same file would silently overwrite whichever "
                "was written second."
            )


def write_bundle_facts_out(
    bundle_facts_out: Path,
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]],
    manifest_path: Path | None,
    old_map: dict[str, Path],
    *,
    resolve_stranded_library: Callable[[Path], AbiSnapshot],
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

    Failure here (a bad *manifest_path*, an unwritable *bundle_facts_out*)
    is a genuine usage error -- unlike bundle *analysis* itself, which
    degrades to a warning on failure (see ``_run_bundle_analysis``'s own
    docstring), writing an explicitly-requested output file that silently
    fails would leave a user believing a baseline was captured when it
    was not.
    """
    from .bundle_facts import capture_bundle_facts
    from .bundle_manifest import load_manifest
    from .serialization import save_bundle_facts

    try:
        manifest = load_manifest(manifest_path) if manifest_path is not None else None
        # Basename -> canonical key, so a matched pair's DiffResult.library
        # (the real, possibly-versioned filename) maps back onto the same
        # key old_map/build_bundle_snapshot() would use for it. Falls back
        # to the basename itself only if truly unmatched (shouldn't happen
        # for a genuine diff_pairs entry, but degrades safely either way).
        basename_to_key = {path.name: key for key, path in old_map.items()}
        per_library_snapshots: dict[str, AbiSnapshot] = {}
        for diff, old_snapshot in diff_pairs:
            basename = Path(diff.library).name
            per_library_snapshots[basename_to_key.get(basename, basename)] = (
                old_snapshot
            )
        for key, old_path in old_map.items():
            if key in per_library_snapshots:
                continue
            per_library_snapshots[key] = resolve_stranded_library(old_path)
        facts = capture_bundle_facts(
            per_library_snapshots, manifest=manifest, library_paths=dict(old_map)
        )
        save_bundle_facts(facts, bundle_facts_out)
    except (OSError, ValueError) as exc:
        raise click.UsageError(f"--bundle-facts-out {bundle_facts_out}: {exc}") from exc


def _collect_bundle_result(
    library_results: list[dict[str, object]],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    worst_verdict: str,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...] = (), policy: str = "strict_abi", policy_file: PolicyFile | None = None,
    old_root: Path | None = None,
    new_root: Path | None = None,
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
    *old_root*/*new_root* (ADR-062 A1.7) are forwarded unchanged to
    :func:`_run_bundle_analysis`'s own embedded-manifest fallback.
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
    )
    if bundle_result is not None:
        bundle_result.policy_file = policy_file  # G38 Phase 16
        bv = bundle_result.bundle_verdict.value
        if _RELEASE_VERDICT_ORDER.get(bv, 0) > _RELEASE_VERDICT_ORDER.get(worst_verdict, 0):
            worst_verdict = bv
    return bundle_result, worst_verdict


def _cleanup_temp_dirs(temp_dir_paths: list[str], keep_extracted: bool) -> None:
    """Remove or report temporary directories created during package extraction."""
    import shutil as _shutil

    if not keep_extracted:
        for td_path in temp_dir_paths:
            _shutil.rmtree(td_path, ignore_errors=True)
    elif temp_dir_paths:
        kept_paths = ", ".join(temp_dir_paths)
        click.echo(f"Extracted files kept in: {kept_paths}", err=True)


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


def _exit_compare_release(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
    severity_exit_code: int | None = None,
    *,
    contract_coverage_exit_contribution: int = 0,
) -> None:
    """Exit compare-release with ABI-compatible status code mapping.

    When *severity_exit_code* is not None, the severity-aware scheme is in
    effect: that code replaces the verdict-based 2/4 mapping, except that
    (a) a removed library still exits 8 in preference to the severity code, and
    (b) an operational ERROR verdict (a library failed to dump/extract/compare)
    still floors the exit at 4 — such failures produce no ``DiffResult.changes``
    so the severity aggregation cannot see them, and must never be downgraded.
    When None, the legacy verdict-based mapping is unchanged.

    ``worst_verdict == "not_comparable"`` (ADR-050 D2) is checked first, in
    both schemes, ahead of even ``--fail-on-removed-library``'s exit 8: a
    not_comparable result means the comparison couldn't establish what
    changed at all, so an apparent "library removed" reading from an
    incomparable pair is an unproven inference, not a real removal finding
    entitled to its own exit code. Exits 16 — identical to native
    ``compare``'s own not_comparable code, since it fires before severity
    classification or the removed-library check ever run.

    *contract_coverage_exit_contribution* is ADR-049 Phase 7's orthogonal
    axis (release/package parity, CLI-audit P1), already aggregated with
    max() across every library by the caller. Folded in with max() at every
    exit point below (mirroring ``contract_coverage_exit.fold_coverage_exit``
    for a single-pair ``compare``) except ``not_comparable``, which fires
    before any library was even scored: it can raise a clean 0 to 1, never
    lower a real 2/4/8, and is `0` (a no-op fold) for every run that never
    passed ``--contract``.
    """
    if worst_verdict == "not_comparable":
        sys.exit(16)
    if severity_exit_code is not None:
        # Severity-aware scheme: removed-library 8 takes precedence over the
        # severity code, otherwise emit the aggregated severity exit code.
        if fail_on_removed and removed_keys:
            sys.exit(8)
        code = severity_exit_code
        if worst_verdict == "ERROR":
            code = max(code, 4)
        code = max(code, contract_coverage_exit_contribution)
        if code != 0:
            sys.exit(code)
        return
    # ERROR is a compare-release-specific operational-failure sentinel (not a
    # Verdict); it floors at 4. Otherwise the verdict→code mapping is the shared
    # canonical one, so compare and compare-release never disagree (C7).
    if worst_verdict == "ERROR":
        sys.exit(max(4, contract_coverage_exit_contribution))
    from .checker_policy import Verdict
    from .workflows.gate import legacy_exit_code

    code = (
        legacy_exit_code(Verdict[worst_verdict])
        if worst_verdict in Verdict.__members__
        else 0
    )
    if code != 0:
        # A real verdict-based break always wins outright; folding coverage
        # in here is a no-op in practice (its own floor is 0/1, never above
        # a real 2/4) but keeps the "never lowers a real code" invariant
        # explicit rather than implicit in max()'s commutativity.
        sys.exit(max(code, contract_coverage_exit_contribution))
    if fail_on_removed and removed_keys:
        # A removed library stays its own, separately-aggregated signal
        # (AGENTS.md: "не смешивая его с entity contract relevance") --
        # it is checked ahead of the coverage-only fallback below, mirroring
        # the severity-scheme branch above.
        sys.exit(8)
    if contract_coverage_exit_contribution != 0:
        sys.exit(contract_coverage_exit_contribution)


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
) -> str:
    """Format the release comparison summary as JSON, markdown, or JUnit XML."""
    if fmt == "junit":
        return _format_release_junit(
            diff_pairs, matrix_result, library_results, severity_config=severity_config,
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
        )
    return _format_release_markdown(
        worst_verdict,
        old_dir,
        new_dir,
        library_results,
        removed_keys,
        added_keys,
        old_map,
        new_map,
        bundle_result,
        matrix_result,
    )


def _format_release_junit(
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] | None,
    matrix_result: DiffResult | None,
    library_results: list[dict[str, object]],
    *,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Render the release summary as a JUnit XML report.

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
    """
    from .junit_report import to_junit_xml_multi

    pairs: list[tuple[DiffResult, AbiSnapshot | None]] = list(diff_pairs or [])
    # Release-global matrix findings ride in as their own synthetic
    # testsuite so CI dashboards reading the JUnit report see the failure.
    if matrix_result is not None:
        pairs.append((matrix_result, None))
    error_libs = [
        {**entry, "error": entry.get("reason", "not comparable")}
        if entry.get("verdict") == "not_comparable"
        else entry
        for entry in library_results
        if entry.get("verdict") in ("ERROR", "not_comparable")
    ]
    return to_junit_xml_multi(
        pairs,
        severity_config=severity_config,
        error_libraries=error_libs if error_libs else None,
    )


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
) -> str:
    """Render the release summary as a JSON document."""
    changed_libraries = [
        str(lib["library"])
        for lib in library_results
        if str(lib.get("verdict")) not in ("NO_CHANGE", "ERROR")
    ]
    from .report.not_comparable import run_outcome_dict_for_release
    release_global_verdict = _release_global_verdict(bundle_result, matrix_result)
    exit_dict = resolve_release_exit_decision_for_report(worst_verdict, fail_on_removed, removed_keys, severity_exit_code, contract_coverage_exit_contribution, library_results, release_global_verdict).to_dict()
    summary: dict[str, object] = {
        "verdict": worst_verdict,
        "old_dir": str(old_dir),
        "new_dir": str(new_dir),
        "libraries": library_results,
        "changed_libraries": changed_libraries,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
        "warnings": warning_msgs,
        "exit": exit_dict,
        "run_outcome": run_outcome_dict_for_release(
            _release_completed_compatibility_verdict(
                library_results,
                release_global_verdict,
                release_global_ran=(
                    bundle_result is not None or matrix_result is not None
                ),
            ),
            exit_dict,
        ),
    }
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
            for f in bundle_result.bundle_findings
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
        # excluded here just as they are from the verdict.
        summary["matrix_verdict"] = matrix_result.verdict.value
        summary["matrix_findings"] = [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "old_value": c.old_value,
                "new_value": c.new_value,
            }
            for c in matrix_result.changes
        ]
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
    from .cli_compare_receipt import _release_summary_effective_config_block

    digest, fields = _release_summary_effective_config_block(
        severity_config, policy=policy, policy_file_path=policy_file_path,
        suppress=suppress, pack_application=pack_application,
        scope_public_headers=scope_public_headers,
    )
    summary["effective_config_digest"] = digest
    summary["effective_config_fields"] = fields
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
) -> str:
    """Render the release summary as a Markdown document."""
    from .cli_compare_receipt import _release_md_library_findings

    _VERDICT_EMOJI = {
        "NO_CHANGE": "✅",
        "COMPATIBLE": "✅",
        "COMPATIBLE_WITH_RISK": "⚠️",
        "API_BREAK": "⚠️",
        "BREAKING": "❌",
        "ERROR": "💥",
        "not_comparable": "❓",
    }
    lines: list[str] = [
        "# ABI Release Comparison",
        "",
        "| | |",
        "|---|---|",
        f"| **Old** | `{old_dir}` |",
        f"| **New** | `{new_dir}` |",
        f"| **Verdict** | {_VERDICT_EMOJI.get(worst_verdict, '?')} `{worst_verdict}` |",
    ]
    bundle_count = len(bundle_result.bundle_findings) if bundle_result else 0
    if bundle_result is not None:
        bundle_em = _VERDICT_EMOJI.get(bundle_result.bundle_verdict.value, "?")
        lines.append(
            f"| **Bundle** | {bundle_em} `{bundle_result.bundle_verdict.value}` "
            f"({bundle_count} cross-library finding{'s' if bundle_count != 1 else ''}) |",
        )
    lines += _release_md_libraries_table(library_results, _VERDICT_EMOJI)
    lines += _release_md_coverage_warnings(library_results)
    lines += _release_md_changed_libraries(removed_keys, added_keys, old_map, new_map)
    lines += _release_md_library_findings(library_results)
    lines += _release_md_bundle_findings(bundle_result)
    lines += _release_md_matrix_findings(matrix_result)
    return "\n".join(lines)


def _release_md_libraries_table(
    library_results: list[dict[str, object]],
    emoji: dict[str, str],
) -> list[str]:
    """Markdown per-library results table."""
    lines = [
        "",
        "## Libraries",
        "",
        "| Library | Verdict | Breaking | Source | Risk | Additions |",
        "|---|---|---|---|---|---|",
    ]
    for lib in library_results:
        em = emoji.get(str(lib["verdict"]), "?")
        lines.append(
            f"| `{lib['library']}` | {em} `{lib['verdict']}` "
            f"| {lib.get('breaking', '—')} | {lib.get('source_breaks', '—')} "
            f"| {lib.get('risk_changes', '—')} | {lib.get('compatible_additions', '—')} |"
        )
    return lines


def _release_md_coverage_warnings(library_results: list[dict[str, object]]) -> list[str]:
    """Per-library `coverage_warnings` (e.g. same-binary) -- absent when none carry any (Codex review: the release table alone omits this signal)."""
    entries = [f"- `{lib['library']}`: {w}" for lib in library_results for w in cast(list[str], lib.get("coverage_warnings") or [])]
    return ["", "## ⚠️ Coverage Warnings", "", *entries] if entries else []


def _release_md_changed_libraries(
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> list[str]:  # Markdown sections listing removed/added libraries.
    lines: list[str] = []
    if removed_keys:
        lines += ["", "## ⚠️ Removed Libraries", ""]
        lines += [f"- `{old_map[k].name}`" for k in removed_keys]
    if added_keys:
        lines += ["", "## ℹ️ Added Libraries", ""]
        lines += [f"- `{new_map[k].name}`" for k in added_keys]
    return lines


def _release_md_bundle_findings(bundle_result: BundleDiffResult | None) -> list[str]:
    """Markdown section for cross-library (bundle) findings. G38 P0-D: a partial ``analysis_errors`` warning is rendered even when ``bundle_findings`` is empty -- an empty finding list after a raised exception means "nothing was checked", not "nothing was found", and a reader must not conflate the two."""
    lines: list[str] = []
    if bundle_result is not None and bundle_result.analysis_errors:
        lines += ["", "## ⚠️ Bundle Analysis Warnings", ""]
        lines += [f"- {msg}" for msg in bundle_result.analysis_errors]
    if bundle_result is None or not bundle_result.bundle_findings:
        return lines
    lines += [
        "",
        "## 🔗 Bundle (Cross-Library) Findings",
        "",
        *render_bundle_findings_markdown(bundle_result.bundle_findings),
    ]
    return lines


def _release_md_matrix_findings(matrix_result: DiffResult | None) -> list[str]:
    """Markdown section for build-configuration (matrix) findings."""
    if matrix_result is None or not matrix_result.changes:
        return []
    lines = ["", "## 🛠️ Build-Configuration (Matrix) Findings", ""]
    for c in matrix_result.changes:
        lines.append(
            f"- **{c.kind.value}**" + (f" — `{c.symbol}`" if c.symbol else ""),
        )
        lines.append(f"  - {c.description}")
    return lines

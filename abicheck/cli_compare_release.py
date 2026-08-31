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

"""CLI — compare-release command and its helpers.

Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom
of :mod:`abicheck.cli` so the ``@main.command("compare-release")``
decorator runs.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .api_types import CompareResult
from .bundle import BundleDiffResult
from .bundle_models import BundleSignatureEvidence
from .checker import Change, DiffResult
from .cli import (
    _build_match_map,
    _collect_release_inputs,
    _normalize_binary_input,
    _safe_write_output,
    _setup_verbosity,
    _write_or_echo,
)
from .cli_compare_receipt import record_release_resolved_config
from .cli_compare_release_helpers import (  # noqa: F401
    _RELEASE_VERDICT_ORDER,
    _cleanup_temp_dirs,
    _collect_bundle_result,
    _collect_release_warnings,
    _compute_release_severity_exit_code,
    _debian_symbols_warning,
    _discover_include_roots,
    _exit_compare_release,
    _extract_if_package,
    _fold_release_global_severity,
    _format_release_json,
    _format_release_junit,
    _format_release_markdown,
    _format_release_summary,
    _match_release_keys,
    _release_json_scope,
    _release_md_bundle_findings,
    _release_md_changed_libraries,
    _release_md_libraries_table,
    _release_md_matrix_findings,
    _resolve_release_headers,
    _resolve_release_severity_config,
    _run_bundle_analysis,
    apply_release_gate_pack,
    reject_bundle_facts_out_collision,
    reject_bundle_facts_out_dir_collision,
    write_bundle_facts_out,
)
from .cli_options import (
    include_dependencies_option,
    lang_option,
    output_options,
    policy_options,
    release_input_options,
    scope_options,
    severity_options,
    verbose_option,
)
from .cli_params import _load_suppression_and_policy
from .errors import ProfileMismatchError, ScopeMismatchError
from .frontends.cli.options import (
    reject_incoherent_secondary_output,
    secondary_output_options,
)
from .model import AbiSnapshot
from .pack_application import resolve_bundle_policy_file
from .reporter import to_json

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig

# ---------------------------------------------------------------------------
# release-comparison engine helpers
# ---------------------------------------------------------------------------


def _run_compare_pair(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path],
    new_headers: list[Path],
    old_includes: list[Path],
    new_includes: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
    scope_to_public_surface: bool = True,
    pattern_verdicts: bool = False,
    include_dependencies: bool = True,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    pack_application: PackApplication | None = None,
    compile_context: CompileContext | None = None,
) -> CompareResult:
    """Run compare for one old/new pair and return result + resolved snapshots.

    Routes through the single Tier-2 chokepoint (:func:`service.run_compare`,
    ADR-037 D1) rather than calling ``checker.compare`` directly — this is what
    keeps ``compare-release`` and ``compare`` on one classification path so a
    library gets the same verdict from either command (no ``scope_public``
    default drift). ``include_dependencies`` (default ``True``) is the same
    reasoning applied to dependency-scope: without threading it through here
    too, a directory/package `compare` would silently stay unfiltered
    regardless of `--include-system-declarations`, drifting from a single-pair
    `compare` of the identical library (Codex review). ``contract_evaluation``/
    ``contract_mode`` (CLI-audit P1, release/package contract parity) are the
    same pass-through: ``service.run_compare`` already runs ADR-049's whole
    contract-relevance pipeline internally when asked, so threading these two
    flags here is what makes a library compared through the release fan-out
    get the identical contract decision it would from comparing it alone.

    *pack_application* (CLI cleanup phase two, "PR B" slice 1) is this run's
    already-resolved ``--pack`` contribution (``resolve_release_pack_
    application``, resolved once for the whole release, not per library) --
    forwarded to ``service.run_compare`` as ``pack_policy_overrides``/
    ``pack_internal_namespaces``, which ``service_compare_pipeline.
    classify_compare_pair`` folds into *this pair's own* freshly-loaded
    ``PolicyFile`` the same way a single-pair ``compare`` folds its packs
    into its one ambient policy file.

    *compile_context* is the release's already-resolved, both-sides L2
    header-AST compile context (``--ast-frontend``/``--compiler``/
    ``--compiler-prefix``/``--compiler-option``/``--sysroot``/``--nostdinc``/
    ``--frontend-context``, resolved once for the whole release by
    ``cli_compare_helpers.run_compare`` the same way a single-pair
    ``compare`` resolves it -- see ``cli_options.resolve_compile_context``).
    Forwarded to ``service.run_compare`` unchanged so each library's header
    dump parses with the same cross-toolchain/frontend context a single-pair
    ``compare`` of that library would use, closing the gap this function's
    own historical docstring used to flag ("the per-library fan-out does not
    thread the L2 compile context" -- see AGENTS.md's whole-product-bundle
    known-gap entry). ``None`` (the default) is a true no-op, matching every
    pre-existing caller.
    """
    from . import service

    # Follow GNU ld linker scripts up front so metadata/dependency analysis use
    # the resolved DSO, not the text script.
    old_input, _ = _normalize_binary_input(old_input)
    new_input, _ = _normalize_binary_input(new_input)

    result = service.run_compare(
        old_input,
        new_input,
        old_headers=old_headers,
        new_headers=new_headers,
        old_includes=old_includes,
        new_includes=new_includes,
        old_version=old_version,
        new_version=new_version,
        lang=lang,
        suppress=suppress,
        policy=policy,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        policy_file_path=policy_file_path,
        old_pdb_path=old_pdb_path,
        new_pdb_path=new_pdb_path,
        scope_to_public_surface=scope_to_public_surface,
        pattern_verdicts=pattern_verdicts,
        include_dependencies=include_dependencies,
        pack_policy_overrides=(
            dict(pack_application.policy_overrides) if pack_application else None
        ),
        pack_internal_namespaces=(
            pack_application.internal_namespaces if pack_application else None
        ),
        compile_context=compile_context,
    )
    record_release_resolved_config(result.diff, getattr(pack_application, "resolved_config", None))
    return result


_CompareReleaseCommonArgs = tuple[
    dict[str, Path],
    dict[str, Path],
    Path | None,
    Path | None,
    Callable[[Path, Path], Path | None],
    list[Path],
    list[Path],
    list[Path],
    list[Path],
    str,
    str,
    str,
    Path | None,
    str,
    Path | None,
    Path | None,
    bool,
    bool,
    bool,
    str | None,
    "SeverityConfig | None",
    "PackApplication | None",
    bool,
    bool,
    "CompileContext | None",
]


def _discover_files(
    input_dir: Path,
    lib_dir: Path,
    include_private: bool,
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
) -> list[Path]:
    """Discover library files from a directory or extracted package."""
    if is_package(input_dir):
        files = discover_shared_libraries(lib_dir, include_private=include_private)
        if not files:
            files = _collect_release_inputs(lib_dir)
    else:
        files = _collect_release_inputs(lib_dir)
    return files


def _compare_one_library(
    key: str,
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_debug_dir: Path | None,
    new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    output_dir: Path | None,
    scope_to_public_surface: bool = True,
    include_dependencies: bool = True,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    severity_config: SeverityConfig | None = None,
    pack_application: PackApplication | None = None,
    collect_diff_results: bool = False,
    need_full_snapshots: bool = False,
    compile_context: CompileContext | None = None,
) -> dict[str, object]:
    """Compare one library pair — suitable for parallel dispatch. Any
    exception yields an ERROR entry rather than aborting the release.

    The full :class:`DiffResult` is stashed under ``"_diff_result"``;
    callers needing it (bundle layer, JUnit) pop it before JSON-serialising.
    *collect_diff_results* additionally stashes ``"_bundle_key"`` plus
    either ``"_old_snapshot"``/``"_new_snapshot"`` (when
    *need_full_snapshots* -- JUnit/``--bundle-facts-out``) or the much
    smaller ``"_old_bundle_evidence"``/``"_new_bundle_evidence"`` (G38
    Phase 9's memory fix; see :class:`~abicheck.bundle_models.
    BundleSignatureEvidence`).

    *severity_config* is forwarded to the ``--output-dir`` JSON write below
    (Codex review): without it, that write always used the legacy
    exit-code scheme regardless of the release's own severity config.
    """
    old_path = old_map[key]
    new_path = new_map[key]
    try:
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        compare_result = _run_compare_pair(
            old_path,
            new_path,
            old_h,
            new_h,
            old_inc,
            new_inc,
            old_version,
            new_version,
            lang,
            suppress,
            policy,
            policy_file_path,
            old_pdb_path=old_dbg,
            new_pdb_path=new_dbg,
            scope_to_public_surface=scope_to_public_surface,
            include_dependencies=include_dependencies,
            contract_evaluation=contract_evaluation,
            contract_mode=contract_mode,
            pack_application=pack_application,
            compile_context=compile_context,
        )
        result = compare_result.diff
        v = result.verdict.value
        # compatible_additions historically counts *all* compatible changes
        # (additions + quality issues). Emit the quality subset separately so
        # downstream consumers (e.g. the PR-comment renderer) can gate the two
        # categories independently under the config's severity.quality_issues.
        from .checker_policy import ADDITION_KINDS

        n_quality = sum(1 for c in result.compatible if c.kind not in ADDITION_KINDS)
        entry: dict[str, object] = {
            "library": old_path.name,
            "verdict": v,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": len(result.compatible),
            "quality_issues": n_quality,
            "_diff_result": result,
            **({"coverage_warnings": list(result.coverage_warnings)} if result.coverage_warnings else {}),  # e.g. same-binary; never reached this entry before (Codex review)
        }
        if collect_diff_results:
            # See this function's own docstring (CodeRabbit review #798;
            # full- vs. compact-evidence split, G38 Phase 9).
            entry["_bundle_key"] = key
            if need_full_snapshots:
                entry["_old_snapshot"] = compare_result.old_snapshot
                entry["_new_snapshot"] = compare_result.new_snapshot
            else:
                entry["_old_bundle_evidence"] = BundleSignatureEvidence.from_snapshot(
                    compare_result.old_snapshot
                )
                entry["_new_bundle_evidence"] = BundleSignatureEvidence.from_snapshot(
                    compare_result.new_snapshot
                )
        if contract_evaluation:
            # ADR-049 Phase 7's orthogonal contract-coverage floor (0/1),
            # read off this library's own persisted contract context --
            # aggregated with max() into the release-level exit code in
            # _exit_compare_release, the same "raises a clean 0 to 1, never
            # lowers a real 2/4" rule a single-pair `compare` applies.
            from .contract_coverage_ledger import coverage_failures_for_context
            from .workflows.gate import coverage_exit_floor

            entry["contract_coverage_exit_contribution"] = coverage_exit_floor(result)
            # The *count* of failures is independent of the exit floor above
            # -- `contract.unresolved: warn` deliberately zeroes the floor
            # while the failures themselves stay real and unsuppressible
            # (AGENTS.md's contract_coverage_exit.py entry: "an acceptance
            # of incomplete assurance, not a way to hide it"). Without a
            # separate count, a release-level `warn`-accepted coverage gap
            # is invisible everywhere the release JSON is read from, since
            # this schema has no per-library `contract_coverage_failures`
            # array the way a single-pair `compare` report does (Codex
            # review, CLI-audit P2 follow-up).
            entry["contract_coverage_failure_count"] = len(
                coverage_failures_for_context(result.contract_context)
            )
        if scope_to_public_surface:
            # Per-library public-surface scoping outcome (ADR-024, issue #235),
            # aggregated into the release-level scope block by the formatter.
            entry["scope_resolved"] = result.scope_resolved
            entry["filtered_internal_count"] = result.out_of_surface_count
        if output_dir:
            lib_report_path = output_dir / f"{old_path.stem}.json"
            _safe_write_output(
                lib_report_path, to_json(result, severity_config=severity_config)
            )
        return entry
    except (ProfileMismatchError, ScopeMismatchError) as exc:
        # ADR-050 D2 — ordered before the generic except Exception below.
        # This library's old/new DSOs were not extracted under a comparable
        # profile/scope contract: a distinct, expected outcome (not an
        # abicheck bug), so it gets its own "not_comparable" verdict string
        # instead of falling into the same "ERROR"/exit-4 bucket a genuine
        # crash uses — see _RELEASE_VERDICT_ORDER's dedicated rank for it.
        kind = "profile_mismatch" if isinstance(exc, ProfileMismatchError) else "scope_mismatch"
        if output_dir:
            from .schemas import REPORT_SCHEMA_VERSION

            lib_report_path = output_dir / f"{old_path.stem}.json"
            doc = {
                "report_schema_version": REPORT_SCHEMA_VERSION,
                "library": old_path.name,
                "old_version": old_version,
                "new_version": new_version,
                "verdict": None,
                "reason": {"kind": kind, "message": str(exc)},
            }
            _safe_write_output(lib_report_path, json.dumps(doc, indent=2))
        return {
            "library": old_path.name,
            "verdict": "not_comparable",
            "reason": str(exc),
        }
    except (click.ClickException, click.UsageError) as exc:
        return {
            "library": old_path.name,
            "verdict": "ERROR",
            "error": exc.format_message(),
        }
    except Exception as exc:
        return {"library": old_path.name, "verdict": "ERROR", "error": str(exc)}


def _suppress_lockstep_soname_findings(
    library_results: list[dict[str, object]],
    worst_verdict: str,
    output_dir: Path | None,
    severity_config: SeverityConfig | None = None,
) -> int:
    """Drop ``SONAME_BUMP_UNNECESSARY`` when the release is a coordinated break.

    A library only earns ``SONAME_BUMP_UNNECESSARY`` when *it* had no breaking
    change yet its SONAME was bumped. In a multi-library release where a sibling
    or dependency suffered a genuine *binary* ABI break, bumping every member's
    SONAME in lockstep is the correct, intentional practice — so the per-library
    "unnecessary" signal is a false positive at the release level. Mutates the
    affected per-library results (and re-writes their JSON when ``output_dir`` is
    set) and returns the number of findings suppressed. *severity_config* is
    forwarded to that re-write's own ``to_json`` call (Codex review, fresh
    evidence): without it, a severity-aware release's per-library report file
    would revert to the legacy exit-code scheme's ``exit`` block on this
    second write, even though ``_compare_one_library``'s first write already
    used the severity-aware one — so which scheme a report's ``exit`` block
    reflects would depend on whether this suppression fired, not on the
    release's actual configuration.

    Only a binary-incompatible (``BREAKING``) finding justifies a SONAME bump; a
    source-only ``API_BREAK`` does not, so the warning is preserved in that case.
    """
    if worst_verdict != "BREAKING":
        return 0
    from .checker_policy import ChangeKind

    suppressed = 0
    for entry in library_results:
        result = entry.get("_diff_result")
        if not isinstance(result, DiffResult):
            continue
        unnecessary = [
            c for c in result.changes if c.kind == ChangeKind.SONAME_BUMP_UNNECESSARY
        ]
        if not unnecessary:
            continue
        result.changes = [
            c for c in result.changes if c.kind != ChangeKind.SONAME_BUMP_UNNECESSARY
        ]
        suppressed += len(unnecessary)
        # Recompute the cached per-library counts after the mutation.
        from .checker_policy import ADDITION_KINDS

        entry["breaking"] = len(result.breaking)
        entry["source_breaks"] = len(result.source_breaks)
        entry["risk_changes"] = len(result.risk)
        entry["compatible_additions"] = len(result.compatible)
        entry["quality_issues"] = sum(
            1 for c in result.compatible if c.kind not in ADDITION_KINDS
        )
        if output_dir is not None:
            lib_report_path = output_dir / f"{Path(str(entry['library'])).stem}.json"
            _safe_write_output(
                lib_report_path, to_json(result, severity_config=severity_config)
            )
    return suppressed


def _compare_release_libraries(
    matched_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_debug_dir: Path | None,
    new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    output_dir: Path | None,
    collect_diff_results: bool = False,
    *,
    need_full_snapshots: bool = False,
    jobs: int = 1,
    scope_to_public_surface: bool = True,
    include_dependencies: bool = True,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    pack_application: PackApplication | None = None,
    compile_context: CompileContext | None = None,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, AbiSnapshot]]]:
    """Compare each matched library pair and collect results.

    When *collect_diff_results* and *need_full_snapshots* are both True,
    ``(DiffResult, old_snapshot)`` pairs are collected and returned as the
    third element of the tuple (used by the JUnit output format).

    When *jobs* > 1, comparisons are dispatched in parallel via
    :func:`_compare_one_library` using a :class:`ThreadPoolExecutor` -- not
    a ``ProcessPoolExecutor``, as this docstring wrongly claimed before
    (Codex review, fresh evidence; see :func:`_compare_release_parallel`'s
    own docstring for why the distinction matters).
    """
    import os as _os

    effective_jobs = jobs if jobs > 0 else (_os.cpu_count() or 1)
    library_results: list[dict[str, object]] = []
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    worst_verdict = "NO_CHANGE"

    common_args = (
        old_map,
        new_map,
        old_debug_dir,
        new_debug_dir,
        resolve_debug_info,
        old_h,
        new_h,
        old_inc,
        new_inc,
        old_version,
        new_version,
        lang,
        suppress,
        policy,
        policy_file_path,
        output_dir,
        scope_to_public_surface,
        include_dependencies,
        contract_evaluation,
        contract_mode,
        severity_config,
        pack_application,
        collect_diff_results,
        need_full_snapshots,
        compile_context,
    )

    if effective_jobs > 1 and len(matched_keys) > 1:
        library_results.extend(
            _compare_release_parallel(
                matched_keys, common_args, old_map, effective_jobs
            ),
        )
    else:
        library_results.extend(
            _compare_release_sequential(matched_keys, common_args),
        )

    # Post-process all results: compute worst verdict, collect annotations,
    # and optionally collect diff_pairs (for JUnit).
    for entry in library_results:
        v = str(entry["verdict"])
        if v == "ERROR":
            if "error" in entry:
                click.echo(
                    f"Error comparing {entry['library']}: {entry['error']}", err=True
                )
        elif v == "not_comparable":
            if "reason" in entry:
                click.echo(
                    f"Not comparable: {entry['library']}: {entry['reason']}", err=True
                )
        if _RELEASE_VERDICT_ORDER.get(v, 0) > _RELEASE_VERDICT_ORDER.get(worst_verdict, 0):
            worst_verdict = v

    # Cross-library coupling: a coordinated SONAME bump across the release is not
    # "unnecessary" just because one member had no break of its own.
    suppressed_soname = _suppress_lockstep_soname_findings(
        library_results,
        worst_verdict,
        output_dir,
        severity_config,
    )
    if suppressed_soname:
        click.echo(
            f"Note: suppressed {suppressed_soname} 'soname_bump_unnecessary' "
            "finding(s) — the release contains coordinated ABI breaks, so "
            "lockstep SONAME bumps are justified.",
            err=True,
        )

    # collect_diff_results (JUnit / a secondary `--write junit=...` render)
    # used to need an independent re-run (`_collect_release_extras`) purely
    # to recover the old `AbiSnapshot` alongside each `DiffResult` -- the
    # primary pass above now stashes both directly in each library's own
    # `entry["_diff_result"]`/`entry["_old_snapshot"]`, so building the
    # pairs is a plain read, not a second comparison (CodeRabbit review,
    # PR #798): the old re-run's own failure handling silently *dropped* a
    # pair from the secondary report on a rerun error even when the
    # primary pass had already succeeded for it, which this can no longer
    # do since there is nothing left to fail. Annotations were fixed the
    # identical way earlier in this same PR (see `annotation_report_
    # entries`/`reporter_contract_blocks.add_annotations`); the Action
    # reads them straight off the JSON report.
    if collect_diff_results:
        for entry in library_results:
            diff = entry.get("_diff_result")
            old_snap = entry.get("_old_snapshot")
            if isinstance(diff, DiffResult) and isinstance(old_snap, AbiSnapshot):
                diff_pairs.append((diff, old_snap))

    return library_results, worst_verdict, diff_pairs


def _compare_release_parallel(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
    old_map: dict[str, Path],
    max_workers: int,
) -> list[dict[str, object]]:
    """Run per-library release comparisons in parallel.

    Results are collected by key and returned in *matched_keys* order so the
    report is deterministic regardless of completion timing (parallel is now the
    default via ``-j 0``); CI snapshots and downstream diffs depend on this.

    Uses a :class:`ThreadPoolExecutor` (real OS threads sharing this
    process's memory), *not* a ``ProcessPoolExecutor`` -- a stale claim in
    an earlier revision of this docstring said otherwise (Codex review,
    fresh evidence). That distinction matters for `policy_file.
    dedup_validate_overrides_warnings()`: a `ContextVar` set in the calling
    thread is *not* automatically visible to a new thread `ThreadPoolExecutor`
    spawns -- each worker thread starts with the `ContextVar`'s default value
    -- so submitting bare `_compare_one_library` calls would silently escape
    the caller's dedup scope and warn once per library even under the
    default (`--jobs 0`, auto-detected CPU count > 1) parallel path. Fixed by
    explicitly propagating a copy of the calling thread's
    `contextvars.Context` into each submitted call via ``Context.run``.

    Two subtleties this went through, both caught by a real (initially
    intermittent, then reliably reproducing) test failure rather than by
    inspection -- worth recording so a future edit here doesn't reintroduce
    either:

    1. ``copy_context()`` must be called in *this* (the calling) thread, at
       submission time -- not inside the function a worker thread executes.
       Calling it from within the submitted callable copies whatever context
       that already-new worker thread started with (the `ContextVar`
       default), not this thread's dedup scope, silently reproducing the
       exact bug this fix exists to close.
    2. Each submission needs its *own* fresh copy, not one `Context` object
       shared across tasks -- ``Context.run`` raises ``RuntimeError`` if the
       same `Context` object is entered from more than one thread
       concurrently.

    Every copy still shares the same mutable dedup ``set`` object the
    `ContextVar` points to (copying a context copies variable *bindings*,
    not the values they point to), so every worker's dedup check is against
    the one real, shared set regardless of which thread runs it -- guarded
    by `policy_file`'s own dedup lock against the resulting cross-thread
    race on that shared set (also caught by the same test failure).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from contextvars import Context, copy_context

    def _run_in_context(ctx: Context, key: str) -> dict[str, object]:
        # `ctx` was captured in the calling thread at submission time (see
        # point 1 in the docstring above) -- this closure has a concrete
        # signature (rather than `executor.submit(ctx.run, fn, *args)`
        # directly) so it stays checkable by mypy: `Context.run`'s own
        # ParamSpec-generic signature otherwise defeats `submit`'s overload
        # resolution against `_compare_one_library`'s real params.
        return ctx.run(_compare_one_library, key, *common_args)

    results_by_key: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            # copy_context() runs here, in the calling thread -- see point 1
            # above. A fresh copy per key -- see point 2.
            executor.submit(_run_in_context, copy_context(), key): key
            for key in matched_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results_by_key[key] = future.result()
            except Exception as exc:
                click.echo(f"Error comparing {old_map[key].name}: {exc}", err=True)
                results_by_key[key] = {
                    "library": old_map[key].name,
                    "verdict": "ERROR",
                    "error": str(exc),
                }
    return [results_by_key[key] for key in matched_keys if key in results_by_key]


def _compare_release_sequential(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
) -> list[dict[str, object]]:
    """Run per-library release comparisons sequentially."""
    return [_compare_one_library(key, *common_args) for key in matched_keys]


def _write_release_summary_file(
    output_dir: Path,
    worst_verdict: str,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    severity_config: SeverityConfig | None = None,
    fail_on_removed: bool = False, severity_exit_code: int | None = None, contract_coverage_exit_contribution: int = 0, bundle_result: BundleDiffResult | None = None, matrix_result: DiffResult | None = None,
) -> None:
    """Write per-library summary JSON to output directory.

    *severity_config*, when in effect, feeds the same effective-config
    digest ``_format_release_json`` (the primary release report) already
    stamps, via the one shared helper ``_release_summary_effective_config_
    block`` so the two can never independently drift (Codex review, PR
    #803). Also gains the same ``exit`` block that report does, via the
    same resolver (ADR-064 stage 1b, Codex review).
    """
    from .cli_compare_release_helpers import (
        _release_global_verdict,
        _release_summary_effective_config_block,
    )
    from .workflows.gate import resolve_release_exit_decision_for_report

    digest, fields = _release_summary_effective_config_block(severity_config)
    summary_data: dict[str, object] = {
        "verdict": worst_verdict,
        "libraries": library_results,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
        "effective_config_digest": digest,
        "effective_config_fields": fields,
        "exit": resolve_release_exit_decision_for_report(worst_verdict, fail_on_removed, removed_keys, severity_exit_code, contract_coverage_exit_contribution, library_results, _release_global_verdict(bundle_result, matrix_result)).to_dict(),
    }
    summary_path = output_dir / "summary.json"
    _safe_write_output(summary_path, json.dumps(summary_data, indent=2))
    click.echo(f"Per-library reports written to {output_dir}/", err=True)


def _collect_matrix_result(
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    policy: str,
    worst_verdict: str,
    *,
    suppress: Path | None = None,
    policy_file_path: Path | None = None,
    old_version: str = "",
    new_version: str = "",
    pack_application: PackApplication | None = None,
) -> tuple[DiffResult | None, str]:
    """Load probe-matrix snapshots, run them through the compare pipeline, fold.

    Returns (matrix_result, worst_verdict). When no matrix snapshots are
    given, matrix_result is None and the verdict is unchanged. The matrix
    findings are release-global build-configuration changes
    (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV,
    BEHAVIOURAL_DEFAULT_CHANGED).

    Rather than re-deriving a verdict, the changes are fed to
    :func:`checker.compare` as ``extra_changes`` over a pair of empty
    snapshots — exactly the path the single-pair ``compare`` command uses.
    This routes them through the *whole* pipeline uniformly: ``--suppress``
    rules, ``--policy-file`` per-kind overrides, and verdict composition all
    apply, so a suppression like ``cxx_standard_floor_raised`` or a policy
    override is honoured identically on both commands. The returned
    :class:`DiffResult` carries the post-suppression kept findings, which the
    report (JSON / markdown / JUnit) renders.

    *pack_application* (CLI cleanup phase two, "PR B" slice 1) folds the
    release's already-resolved ``--pack`` contribution into this pair's own
    ``PolicyFile`` too -- these matrix findings go through the same
    ``--policy-file`` per-kind overrides every other library does, so a
    pack overriding e.g. ``cxx_standard_floor_raised`` must apply here
    identically, not only to the per-library comparisons.
    """
    from .frontends.cli.runtime import _load_probe_matrix_changes

    matrix_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    if not matrix_changes:
        return None, worst_verdict

    from .model import AbiSnapshot
    from .service import compare_snapshots

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if pack_application is not None:
        from .pack_application import policy_file_with_packs

        pf = policy_file_with_packs(pf, pack_application, base_policy=policy)
    # Empty snapshots contribute no per-binary changes; the matrix findings
    # ride in as extra_changes and inherit the full post-processing pipeline.
    name = "<build-config matrix>"
    result = compare_snapshots(
        AbiSnapshot(library=name, version=old_version or "old"),
        AbiSnapshot(library=name, version=new_version or "new"),
        suppression=suppression,
        policy=policy,
        policy_file=pf,
        scope_to_public_surface=False,
        extra_changes=matrix_changes,
    )
    matrix_verdict = result.verdict.value
    if _RELEASE_VERDICT_ORDER.get(matrix_verdict, 0) > _RELEASE_VERDICT_ORDER.get(
        worst_verdict, 0
    ):
        worst_verdict = matrix_verdict
    return result, worst_verdict


def _finalize_release_output(
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
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]],
    bundle_result: BundleDiffResult | None,
    output: Path | None,
    output_dir: Path | None,
    fail_on_removed: bool,
    matrix_result: DiffResult | None = None,
    severity_exit_code: int | None = None,
    severity_config: SeverityConfig | None = None,
    contract_coverage_exit_contribution: int = 0,
    contract_coverage_failure_count: int = 0,
) -> None:
    """Write summary output, step summary, per-library dir report, then exit."""
    text = _format_release_summary(
        fmt,
        worst_verdict,
        old_dir,
        new_dir,
        library_results,
        removed_keys,
        added_keys,
        old_map,
        new_map,
        warning_msgs,
        diff_pairs=diff_pairs if fmt == "junit" else None,
        bundle_result=bundle_result,
        matrix_result=matrix_result,
        severity_config=severity_config,
        severity_exit_code=severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
        contract_coverage_failure_count=contract_coverage_failure_count,
        fail_on_removed=fail_on_removed,
    )
    _write_or_echo(output, text)

    # CLI cleanup phase two, PR E removed --annotate/--annotate-additions,
    # the flag that used to gate a $GITHUB_STEP_SUMMARY write here. This no
    # longer writes one at all -- see cli.py's _finalize_compare_result's
    # own, longer comment for why "unconditional in CI" (an earlier
    # revision of this comment) was itself a real regression through the
    # composite Action, which already writes its own job summary.

    if output_dir:
        _write_release_summary_file(
            output_dir, worst_verdict, library_results, removed_keys, added_keys,
            old_map, new_map, severity_config=severity_config,
            fail_on_removed=fail_on_removed, severity_exit_code=severity_exit_code,
            contract_coverage_exit_contribution=contract_coverage_exit_contribution, bundle_result=bundle_result, matrix_result=matrix_result,
        )

    # ADR-049 Phase 7's orthogonal contract-coverage axis, release/package
    # parity (CLI-audit P1, Codex review): a single-pair `compare` announces
    # a stderr notice whenever the rendered format doesn't already carry the
    # ledger (`contract_coverage_exit.announce_coverage_floor`), so a caller
    # reading only stderr (or `action/run.sh`'s own `_coverage_gated()`
    # stderr fallback, reached whenever no JSON report exists -- e.g. a
    # directory/package operand outside a `pull_request` event, where the
    # Action's PR-comment JSON rerun never fires) can still tell the axis
    # fired. The release path folds the same contribution into its exit code
    # unconditionally (`_exit_compare_release`) but, unlike single-pair
    # `compare`, never said so anywhere except `--format json`'s own
    # `contract_coverage_exit_contribution` field -- so a markdown-format
    # release run gave no visible reason for an exit code coverage alone
    # raised. Only `--format json` already states it; every other format
    # gets this one line.
    #
    # Gated on *failure count*, not exit contribution (Codex review,
    # CLI-audit P2 follow-up): `contract.unresolved: warn` deliberately
    # zeroes the exit contribution while the failures themselves stay real
    # -- an advisory (warn-accepted) coverage gap must still be announced,
    # exactly as single-pair `compare`'s own `coverage_failure_diagnostic`
    # speaks regardless of the floor (see its `floor == 0` "Accepted by
    # contract.unresolved=warn" wording).
    if contract_coverage_failure_count != 0 and fmt != "json":
        _affected = sorted(
            str(lib.get("library"))
            for lib in library_results
            if isinstance(lib, dict) and lib.get("contract_coverage_failure_count", 0)
        )
        if _affected:
            if contract_coverage_exit_contribution == 0:
                # Exact wording match with single-pair compare's own
                # `_coverage_message` (contract_coverage_exit.py) --
                # `=`, not `:` -- deliberately, so a consumer distinguishing
                # "accepted" from "genuinely gated" (e.g. action/run.sh's
                # `_coverage_gated()`) can match one phrase regardless of
                # which command produced the notice (Codex review).
                _effect = (
                    "Accepted by contract.unresolved=warn, so it "
                    "contributes 0 to the release exit code"
                )
            else:
                _effect = (
                    f"Contributes {contract_coverage_exit_contribution} to "
                    "the release exit code"
                )
            click.echo(
                "Contract coverage incomplete for the selected --contract "
                "domain in: "
                + ", ".join(_affected)
                + f". {_effect} (ADR-049 contract-coverage axis). See "
                "contract_coverage_failure_count in --format json output "
                "for per-library detail.",
                err=True,
            )

    _exit_compare_release(
        worst_verdict,
        fail_on_removed,
        removed_keys,
        severity_exit_code,
        contract_coverage_exit_contribution=contract_coverage_exit_contribution,
    )


def _validate_suppression_early(
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    strict_suppressions: bool,
    require_justification: bool,
) -> None:
    """Load and validate the suppression file before entering the per-library loop.

    Only invoked when the user passes a suppression file together with
    *strict_suppressions* or *require_justification*, so that stale or
    undocumented rules are rejected before any expensive per-library work.
    """
    if suppress is not None and (strict_suppressions or require_justification):
        _load_suppression_and_policy(
            suppress,
            policy,
            policy_file_path,
            strict_suppressions=strict_suppressions,
            require_justification=require_justification,
        )


# Cap on embedded per-library findings in release JSON — same rationale as
# `cli_scan_baseline._MAX_BASELINE_FINDINGS` / `stack_report._MAX_STACK_FINDINGS_PER_LIBRARY`:
# a bare count (``"breaking": 3``) leaves no way to identify which symbols
# broke without a separate `compare` run, but embedding every finding for
# every library in a large release could blow up the summary output.
_MAX_RELEASE_FINDINGS_PER_LIBRARY = 10


def _release_gating_buckets(
    diff: DiffResult,
    severity_config: SeverityConfig | None,
) -> list[tuple[str, list[Change]]]:
    """Return the named (bucket, changes) groups that gate *diff*'s exit code.

    Without *severity_config* (the legacy verdict-based exit-code scheme),
    only the three verdict buckets that ever gate the legacy exit code are
    used. With *severity_config* active, the release can instead exit
    non-zero because a category that's normally compatible (additions,
    quality issues) was promoted to ``error`` — e.g. ``severity.addition:
    error`` — so every category the active config gates to ``error`` is
    used instead (Codex review on #557: walking only the legacy buckets left
    a library reporting ``severity.exit_code: 1`` with an empty ``findings``
    list even though a specific addition/quality-issue finding was exactly
    what blocked the release).
    """
    if severity_config is not None:
        from .workflows.gate import categorize_changes, gate_decision_for_result

        kind_sets = diff._effective_kind_sets()
        # gate_decision_for_result (the single canonical gate-decision call
        # site, also used by reporter.py/sarif.py/html_report.py — ADR-061
        # D9) decides *which* categories are actually blocking;
        # categorize_changes supplies the change lists for them — a category
        # with no findings never contributes an (empty) bucket, matching how
        # JSON/SARIF's blocking_categories behave.
        gate = gate_decision_for_result(diff, severity_config)
        assert gate is not None  # severity_config is not None here
        categorized = categorize_changes(
            diff.changes,
            policy=diff.policy,
            kind_sets=kind_sets,
            policy_file=diff.policy_file,
        )
        cat_changes_by_name = {
            "abi_breaking": categorized.abi_breaking,
            "potential_breaking": categorized.potential_breaking,
            "quality_issues": categorized.quality_issues,
            "addition": categorized.addition,
        }
        return [(name, cat_changes_by_name[name]) for name in gate.blocking_categories]
    return [
        ("breaking", diff.breaking),
        ("api_break", diff.source_breaks),
        ("risk", diff.risk),
    ]


def _release_finding_dicts(
    diff: DiffResult,
    severity_config: SeverityConfig | None = None,
) -> list[dict[str, object]]:
    """Project a library's gating findings into small, capped dicts.

    Same shape as ``cli_scan_baseline._baseline_finding_dicts`` /
    ``stack_report._stack_finding_dicts``. Counts (not already-built dicts)
    decide the cap so a large diff never builds more dicts than the cap can
    ever keep. See :func:`_release_gating_buckets` for which findings this
    walks under a legacy vs. severity-aware exit-code scheme.
    """
    findings: list[dict[str, object]] = []
    for bucket_name, bucket_changes in _release_gating_buckets(diff, severity_config):
        remaining = _MAX_RELEASE_FINDINGS_PER_LIBRARY - len(findings)
        if remaining <= 0:
            break
        for c in bucket_changes[:remaining]:
            findings.append(
                {
                    "bucket": bucket_name,
                    "kind": c.kind.value,
                    "symbol": c.symbol,
                    "description": c.description,
                    "source_location": c.source_location,
                }
            )
    return findings


def _strip_diff_results_and_adjust_verdict(
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    worst_verdict: str,
    severity_config: SeverityConfig | None = None,
    *,
    needs_annotations: bool = True,
) -> str:
    """Remove un-serialisable ``_diff_result`` entries and adjust the worst verdict.

    Before the stashed :class:`DiffResult` objects are discarded, each
    library entry gets a capped ``findings`` list (kind/symbol/description/
    location) projected from it — otherwise the JSON summary is entirely
    count-centric (``"breaking": 3``) with no way to identify which symbols
    broke short of a separate `compare` run or the optional per-library
    ``--output-dir`` report file. *severity_config*, when the release's exit
    code is severity-aware, is forwarded to :func:`_release_finding_dicts` so
    a library gated by a promoted addition/quality-issue category (not one of
    the legacy breaking/api_break/risk buckets) still gets a matching
    finding, not an empty list next to a nonzero ``severity.exit_code``.
    Stripping the private keys (``_diff_result`` and friends) keeps the
    summary formatter free of Python-only objects. If any library was
    *removed*, the verdict is bumped to at least ``COMPATIBLE_WITH_RISK``.

    *needs_annotations* gates whether the uncapped ``annotations`` array
    (unlike ``findings`` above, deliberately unbounded -- see its own
    comment) is built at all (Codex review, fresh evidence): only a JSON
    render (primary ``--format json`` or a secondary ``--write
    json=...``) ever reads it, but every entry in ``library_results`` is
    held until the whole release finishes, so building it unconditionally
    grew peak memory by every library's full finding set even for a
    markdown/JUnit-only render that never reads it.

    Returns the (possibly updated) *worst_verdict* string.
    """
    for entry in library_results:
        if not isinstance(entry, dict):
            continue
        diff = entry.get("_diff_result")
        if isinstance(diff, DiffResult):
            total_gating = sum(
                len(cat_changes)
                for _, cat_changes in _release_gating_buckets(diff, severity_config)
            )
            findings = _release_finding_dicts(diff, severity_config)
            if findings:
                entry["findings"] = findings
                if total_gating > _MAX_RELEASE_FINDINGS_PER_LIBRARY:
                    entry["findings_truncated"] = True
            # CLI cleanup phase two, PR E: the uncapped, always-classified
            # counterpart to the capped `findings` list above -- the exact
            # same shape single-library `compare --format json` persists at
            # its own top-level `annotations` (schema 2.43,
            # `annotations.annotation_report_entries`), reused verbatim so
            # the two can never disagree. This is what lets the Action's own
            # renderer (`action/run.sh`'s `_emit_annotations`) read a
            # release-style operand's report the same way it reads a
            # single-library one, instead of needing an independent
            # per-library re-run this module used to perform
            # (`_collect_release_extras`, since removed) just to recover the
            # same DiffResult already sitting right here.
            if needs_annotations:
                from .annotations import annotation_report_entries

                entry["annotations"] = annotation_report_entries(
                    diff, severity_config=severity_config
                )
        entry.pop("_diff_result", None)
        entry.pop("_old_snapshot", None)
        entry.pop("_new_snapshot", None)
        entry.pop("_old_bundle_evidence", None)
        entry.pop("_new_bundle_evidence", None)
        entry.pop("_bundle_key", None)
    if removed_keys and _RELEASE_VERDICT_ORDER.get(
        worst_verdict, 0
    ) < _RELEASE_VERDICT_ORDER.get("COMPATIBLE_WITH_RISK", 0):
        worst_verdict = "COMPATIBLE_WITH_RISK"
    return worst_verdict


# NOTE: not registered on `main` — the user-facing `compare-release` command was
# removed (ADR-037 D7 clean removal). This stays a standalone Click command so
# `compare`'s directory/package dispatch can `ctx.invoke` it as the fan-out engine.
@click.command("compare-release")
@click.argument("old_dir", type=click.Path(exists=True, path_type=Path))
@click.argument("new_dir", type=click.Path(exists=True, path_type=Path))
# Per-side header/include/version for the internal (unregistered) release engine;
# the user-facing --header/--include collapse (ADR-040 L1) lives on `compare`.
@release_input_options
@lang_option
@output_options(
    ["json", "markdown", "junit"],
    output_help="Output file for summary report (default: stdout).",
)
@secondary_output_options(["json", "markdown", "junit"])
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write per-library reports.",
)
# Policy + suppression family (ADR-037 D3); strict/justification stay inline.
@policy_options
@click.option(
    "--strict-suppressions",
    is_flag=True,
    default=False,
    help="Fail with exit code 1 if any suppression rule has expired.",
)
@click.option(
    "--require-justification",
    is_flag=True,
    default=False,
    help="Require every suppression rule to have a non-empty 'reason' field.",
)
@click.option(
    "--fail-on-removed-library/--no-fail-on-removed-library",
    "fail_on_removed",
    default=False,
    help="Exit 8 when a library present in old_dir is absent in new_dir.",
)
@click.option(
    "--debug-info1",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Debug info package for old side (RPM/Deb/tar).",
)
@click.option(
    "--debug-info2",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Debug info package for new side (RPM/Deb/tar).",
)
@click.option(
    "--devel-pkg1",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Development package with headers for old side.",
)
@click.option(
    "--devel-pkg2",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Development package with headers for new side.",
)
@click.option(
    "--dso-only",
    is_flag=True,
    default=False,
    help="Only compare shared objects, skip executables.",
)
@click.option(
    "--include-private-dso",
    is_flag=True,
    default=False,
    help="Include private (non-public) shared objects from non-standard paths.",
)
@click.option(
    "--keep-extracted",
    is_flag=True,
    default=False,
    help="Keep extracted temporary files for debugging.",
)
@verbose_option
@click.option(
    "-j",
    "--jobs",
    "jobs",
    type=int,
    default=0,
    show_default=True,
    help="Number of parallel library comparisons (0 = auto-detect CPU count, the default).",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="ABI instantiation manifest (YAML/JSON) listing symbols the "
    "release publicly promises. See ADR-023.",
)
@click.option(
    "--bundle-system-providers",
    "bundle_system_providers",
    default="",
    help="Comma-separated extra sonames to treat as system-provided "
    "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list). "
    "Matched against the real DT_NEEDED soname either exactly or by its "
    "version-suffix-stripped stem (e.g. 'libmkl_core' matches a real "
    "'libmkl_core.so.2' DT_NEEDED entry); matching is case-sensitive.",
)
@click.option(
    "--bundle-cohort",
    "bundle_cohorts",
    multiple=True,
    metavar="PREFIX",
    help="Declare a co-versioned library cohort by name prefix (e.g. "
    "'libfoo_'). Repeatable. Enables the BUNDLE_SONAME_SKEW check, "
    "which flags when some members of the cohort bump their major SONAME "
    "while siblings lag.",
)
@click.option(
    "--no-bundle-analysis",
    "no_bundle_analysis",
    is_flag=True,
    default=False,
    help="Skip bundle-level cross-library analysis (debug/parity escape hatch). "
    "Bundle findings catch intra-bundle symbol removals, signature drift "
    "across DSO boundaries, type drift across siblings, provider "
    "migration, and manifest mismatches.",
)
@click.option(
    "--bundle-facts-out",
    "bundle_facts_out",
    type=click.Path(path_type=Path),
    default=None,
    help="Persist this run's OLD-side bundle facts (per-library snapshots "
    "plus the instantiation manifest, if any) to PATH (G38 Phase 2, "
    "ADR-023 amendment). A later comparison can load this file and pass "
    "it to abicheck.bundle_facts.compare_bundle_from_facts() (Python API; "
    "no CLI consumer yet) to get a bundle-level verdict from this stored "
    "baseline without reopening the old .so files. This is an additive "
    "output alongside the ordinary live-vs-live comparison this "
    "invocation already performs -- it does not change any finding or "
    "exit code. No-op combined with --no-bundle-analysis.",
)
@scope_options  # --scope-public-headers/--no- (ADR-037 D3)
@include_dependencies_option
@click.option(
    "--probe-matrix-old",
    "probe_matrix_old",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Old build-configuration matrix snapshot. "
    "When given with --probe-matrix-new, build-config findings "
    "(CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV, "
    "BEHAVIOURAL_DEFAULT_CHANGED) are folded into this release's "
    "verdict and report (G2: probe -> compare-release).",
)
@click.option(
    "--probe-matrix-new",
    "probe_matrix_new",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="New build-configuration matrix snapshot (pairs with --probe-matrix-old).",
)
# ── Severity (shared family, ADR-037 D3; mirrors `compare`) ───────────────────
@severity_options
def compare_release_cmd(
    old_dir: Path,
    new_dir: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    lang: str,
    fmt: str,
    output: Path | None,
    secondary_fmt: str | None,
    secondary_output: Path | None,
    output_dir: Path | None,
    suppress: Path | None,
    strict_suppressions: bool,
    require_justification: bool,
    policy: str,
    policy_file_path: Path | None,
    fail_on_removed: bool,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    dso_only: bool,
    include_private_dso: bool,
    keep_extracted: bool,
    verbose: bool,
    jobs: int,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...],
    no_bundle_analysis: bool,
    bundle_facts_out: Path | None,
    scope_public_headers: bool,
    include_dependencies: bool,
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    severity_preset: str | None,
    # Not Click options: `compare`'s directory/package fan-out `ctx.invoke`s
    # this engine with the *already-merged* per-category severity levels it
    # resolved from `.abicheck.yml` (the four `--severity-<category>` CLI
    # flags were removed -- see `cli_options.severity_options`). Plain
    # keyword parameters, so the fan-out can still state them without
    # re-exposing a user-facing flag on an unregistered internal command.
    severity_abi_breaking: str | None = None,
    severity_potential_breaking: str | None = None,
    severity_quality_issues: str | None = None,
    severity_addition: str | None = None,
    release_exit_code_scheme: str | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    # CLI cleanup phase two, PR B slice 1: `compare`'s directory/package
    # fan-out resolves any `--pack` once, ahead of dispatch
    # (resolve_release_pack_application), and hands over the pack's
    # policy/contract-surface contribution here -- same internal-parameter
    # shape as severity_abi_breaking et al. above. `None` (the default) is a
    # true no-op: every library is compared exactly as it was before this
    # parameter existed.
    pack_application: PackApplication | None = None,
    # `compare`'s directory/package fan-out resolves the both-sides L2
    # header-AST compile context once, ahead of dispatch (the same
    # `cli_options.resolve_compile_context` call the single-pair path uses),
    # and hands it over here -- same internal-parameter shape as
    # pack_application above. `None` (the default) is a true no-op.
    compile_context: CompileContext | None = None,
    # The dirs the project's `.abicheck.yml` `compile.include_dirs` appended
    # past the caller's raw `-I` roots (the suffix of `resolve_compile_
    # context`'s merged-includes return past its own `includes` input) --
    # forwarded separately from `includes` itself so they survive a per-
    # library-pair `--old-include`/`--new-include` override, which
    # otherwise fully replaces `includes` for that side
    # (`_prepare_compare_release_inputs`). `()` (the default) is a true
    # no-op.
    config_includes: tuple[Path, ...] = (),
) -> None:
    """Compare all libraries in two release directories or packages.

    OLD_DIR and NEW_DIR may each be a file, directory, or package
    (RPM, Deb, tar, conda, wheel). Package format is auto-detected.
    When directories are given, libraries are matched by filename stem.

    \b
    Exit codes (verdict-based, the default):
      0  All libraries: NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK
      2  At least one library: API_BREAK
      4  At least one library: BREAKING
      8  Library removed (only when --fail-on-removed-library)

    \b
    With a severity setting in effect, exit codes follow the severity-aware scheme
    aggregated across all libraries (and bundle/matrix findings):
      0  no error-level findings
      1  error in quality/addition categories only
      2  error in potential_breaking
      4  error in abi_breaking
    A removed library (--fail-on-removed-library) still exits 8, and a per-library
    comparison ERROR still floors the exit at 4, regardless of severity settings.

    \b
    Under --contract, each library's own ADR-049 Phase 7 contract-
    coverage floor (0/1) folds into the release exit code with max() -- the
    same orthogonal axis a single-pair `compare` applies: it can raise a
    clean 0 to 1 but never lowers a real 2/4/8.

    \b
    Examples:
      abicheck compare-release release-1.0/ release-2.0/ -H include/
      abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm
      abicheck compare-release libfoo_1.0.deb libfoo_1.1.deb
      abicheck compare-release sdk-2.0.tar.gz sdk-2.1.tar.gz
      abicheck compare-release pkg-v1.conda pkg-v2.conda
      abicheck compare-release old.whl new.whl
      abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm \\
          --debug-info1 libfoo-debuginfo-1.0.rpm \\
          --debug-info2 libfoo-debuginfo-1.1.rpm
    """

    from .workflows.extraction import (
        _is_elf_shared_object,
        detect_extractor,
        discover_shared_libraries,
        is_package,
        resolve_package_debug_info as resolve_debug_info,
    )

    _setup_verbosity(verbose)

    # CLI cleanup phase two, PR E: shared with `compare` so it can't drift.
    reject_incoherent_secondary_output(
        dry_run=False,
        output=output,
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )
    reject_bundle_facts_out_collision(bundle_facts_out, output, secondary_output)

    # Track temporary directory paths for cleanup
    _temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = tempfile.mkdtemp(prefix=prefix)
        _temp_dir_paths.append(path)
        return Path(path)

    def _do_extract(
        input_path: Path, debug_pkg: Path | None, devel_pkg: Path | None
    ) -> tuple[Path, Path | None, Path | None, Path | None]:
        return _extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            _make_temp_dir,
            is_package,
            detect_extractor,
        )

    # dedup_validate_overrides_warnings(): this whole release run reloads
    # the same --policy-file several times over -- the early strict-
    # suppression validation just below, the per-library fan-out (including
    # its default `--jobs 0` ThreadPoolExecutor parallel path -- see
    # _compare_release_parallel's own docstring for how the dedup scope
    # reaches those worker threads), and (when a probe matrix is given) the
    # matrix-result path all load it independently, so without this a
    # single risky override would log its validate_overrides() warning
    # once per load instead of once for the whole run (Codex review).
    from .policy_file import dedup_validate_overrides_warnings

    with dedup_validate_overrides_warnings():
        # Validate suppression file early (before per-library loop)
        _validate_suppression_early(
            suppress,
            policy,
            policy_file_path,
            strict_suppressions,
            require_justification,
        )

        try:
            (
                old_debug_dir,
                new_debug_dir,
                old_h,
                new_h,
                old_inc,
                new_inc,
                old_map,
                new_map,
                warning_msgs,
                matched_keys,
                removed_keys,
                added_keys,
            ) = _prepare_compare_release_inputs(
                old_dir,
                new_dir,
                debug_info1,
                debug_info2,
                devel_pkg1,
                devel_pkg2,
                include_private_dso,
                dso_only,
                headers,
                old_headers_only,
                new_headers_only,
                includes,
                old_includes_only,
                new_includes_only,
                config_includes,
                _do_extract,
                discover_shared_libraries,
                is_package,
                _is_elf_shared_object,
            )

            if fmt != "json":
                for msg in warning_msgs:
                    click.echo(msg, err=True)

            reject_bundle_facts_out_dir_collision(bundle_facts_out, output_dir, old_map)
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)

            # CLI cleanup phase two, "PR B" slice 2: fold a selected `kind:
            # gate` pack's gate.exit_code_scheme/gate.severity.<category>
            # into these same raw inputs, once, before every downstream
            # consumer below (this function's own severity_config, the
            # per-library JSON write inside _compare_release_libraries,
            # _compute_release_severity_exit_code,
            # _fold_release_global_severity) reads them — mirroring the
            # `.abicheck.yml`-only `severity_preset = "default"` reassignment
            # a few lines below, which the same downstream consumers already
            # rely on seeing applied exactly once. A no-op without --pack or
            # without a selected gate pack.
            (
                release_exit_code_scheme,
                severity_preset,
                severity_abi_breaking,
                severity_potential_breaking,
                severity_quality_issues,
                severity_addition,
            ) = apply_release_gate_pack(
                pack_application,
                release_exit_code_scheme=release_exit_code_scheme,
                severity_preset=severity_preset,
                severity_abi_breaking=severity_abi_breaking,
                severity_potential_breaking=severity_potential_breaking,
                severity_quality_issues=severity_quality_issues,
                severity_addition=severity_addition,
            )

            # Resolved before the compare pass (its inputs are plain CLI values, no
            # dependency on compare results) so persisted per-library annotations
            # (schema 2.43/2.44, computed inside _compare_release_libraries's
            # primary pass and read by the Action, not the CLI) reflect the same
            # severity-aware gate as the exit code below, instead of the legacy
            # kind-set mapping. Returns None when no severity setting was in
            # effect, or when compare's resolved config pins the legacy scheme
            # for set inputs.
            severity_config = _resolve_release_severity_config(
                severity_preset,
                severity_abi_breaking,
                severity_potential_breaking,
                severity_quality_issues,
                severity_addition,
            )
            if release_exit_code_scheme == "severity" and severity_config is None:
                # The resolved scheme is "severity" (e.g. .abicheck.yml's
                # exit_code_scheme: severity with no severity: block at all) but
                # no severity setting was ever in effect, so the raw-args resolution
                # above returned None. The single-file compare path never hits
                # this: its resolved_cfg.severity is unconditionally populated
                # (defaulting to PRESET_DEFAULT) and only *gated* by scheme, not
                # re-derived from raw flags. Mirror that here — including
                # reassigning severity_preset so the two downstream helpers below
                # that independently re-resolve from these same raw args
                # (_compute_release_severity_exit_code, _fold_release_global_severity)
                # agree with it, instead of also silently resolving None and
                # falling back to the legacy verdict-based exit (Codex review on
                # #549).
                from .workflows.gate import PRESET_DEFAULT

                severity_config = PRESET_DEFAULT
                severity_preset = "default"
            if release_exit_code_scheme == "legacy":
                severity_config = None

            # JUnit, bundle analysis, and annotations all reuse the
            # _diff_result (and, for JUnit, _old_snapshot) stashed in each
            # library entry from this single primary pass -- no independent
            # re-run.
            #
            # This fan-out reloads the same --policy-file once per library;
            # the enclosing dedup_validate_overrides_warnings() scope above
            # is what keeps a single risky override from logging its
            # validate_overrides() warning once per library (Codex review).
            library_results, worst_verdict, diff_pairs = _compare_release_libraries(
                matched_keys,
                old_map,
                new_map,
                old_debug_dir,
                new_debug_dir,
                resolve_debug_info,
                old_h,
                new_h,
                old_inc,
                new_inc,
                old_version,
                new_version,
                lang,
                suppress,
                policy,
                policy_file_path,
                output_dir,
                collect_diff_results=(
                    fmt == "junit"
                    or secondary_fmt == "junit"
                    or bundle_facts_out is not None
                    # G38 Phase 4 (_compare_one_library's docstring):
                    or not no_bundle_analysis
                ),
                # Phase 9: only JUnit/--bundle-facts-out need AbiSnapshot.
                need_full_snapshots=(
                    fmt == "junit"
                    or secondary_fmt == "junit"
                    or bundle_facts_out is not None
                ),
                jobs=jobs,
                scope_to_public_surface=scope_public_headers,
                include_dependencies=include_dependencies,
                severity_config=severity_config,
                contract_evaluation=contract_evaluation,
                contract_mode=contract_mode,
                pack_application=pack_application,
                compile_context=compile_context,
            )

            if bundle_facts_out is not None and not no_bundle_analysis:
                # Resolved here, not in the leaf write_bundle_facts_out() (see its docstring).
                def _resolve_stranded_library(old_path: Path) -> AbiSnapshot:
                    from .cli_resolve import _resolve_input
                    from .workflows import extraction

                    old_dbg = (
                        resolve_debug_info(old_path, old_debug_dir)
                        if old_debug_dir
                        else None
                    )
                    # old_h doubles as the public-header set, matching the
                    # normal compare path (else origin=UNKNOWN; Codex review).
                    pub_headers, pub_dirs = extraction.split_public_header_inputs(old_h)
                    try:
                        return _resolve_input(
                            old_path,
                            old_h,
                            old_inc,
                            old_version,
                            lang,
                            pdb_path=old_dbg,
                            compile=compile_context,
                            include_dependencies=include_dependencies,
                            public_headers=pub_headers,
                            public_header_dirs=pub_dirs,
                        )
                    except Exception as exc:
                        # Degrade rather than abort, but warn: lossy entry (Codex review).
                        click.echo(f"{old_path.name}: ELF-only ({exc})", err=True)
                        return AbiSnapshot(
                            library=old_path.name,
                            version="",
                            elf=extraction.parse_elf_metadata(old_path),
                        )

                write_bundle_facts_out(
                    bundle_facts_out,
                    diff_pairs,
                    manifest_path,
                    old_map,
                    resolve_stranded_library=_resolve_stranded_library,
                )

            # ADR-049 Phase 7's orthogonal contract-coverage floor, aggregated
            # across every library with max() -- one library's incomplete
            # evidence must still raise the release's exit code, the same rule
            # contract_coverage_exit.fold_coverage_exit applies to a single pair.
            # `0` (the default fold value) when --contract was never
            # given, or every library's own selected domain closed cleanly.
            contract_coverage_exit_contribution = max(
                (
                    contribution
                    for entry in library_results
                    if isinstance(entry, dict)
                    and isinstance(
                        contribution := entry.get(
                            "contract_coverage_exit_contribution", 0
                        ),
                        int,
                    )
                ),
                default=0,
            )
            # Summed, not max()'d: unlike the exit-code floor above (a 0/1
            # gate, where max is the right fold), this is a plain count of
            # how many failures exist across the whole release -- real even
            # when `contract.unresolved: warn` zeroed every library's own
            # exit contribution. Stays 0 (not omitted) for a run that never
            # passed --contract, same as the count field above.
            contract_coverage_failure_count = sum(
                count
                for entry in library_results
                if isinstance(entry, dict)
                and isinstance(
                    count := entry.get("contract_coverage_failure_count", 0), int
                )
            )

            # Compute the severity-aware exit code while per-library DiffResults
            # are still stashed (before _strip_diff_results_and_adjust_verdict).
            severity_exit_code = (
                None
                if release_exit_code_scheme == "legacy"
                else _compute_release_severity_exit_code(
                    library_results,
                    severity_preset,
                    severity_abi_breaking,
                    severity_potential_breaking,
                    severity_quality_issues,
                    severity_addition,
                )
            )

            bundle_result: BundleDiffResult | None = None
            if not no_bundle_analysis:
                bundle_result, worst_verdict = _collect_bundle_result(
                    library_results,
                    old_map,
                    new_map,
                    worst_verdict,
                    manifest_path=manifest_path,
                    bundle_system_providers=bundle_system_providers,
                    bundle_cohorts=bundle_cohorts,
                    policy=policy,
                    policy_file=resolve_bundle_policy_file(suppress, policy, policy_file_path, pack_application),
                )

            # Strip _diff_result from entries and bump verdict for removed libraries.
            worst_verdict = _strip_diff_results_and_adjust_verdict(
                library_results,
                removed_keys,
                worst_verdict,
                severity_config,
                needs_annotations=(fmt == "json" or secondary_fmt == "json"),
            )

            # Build-configuration matrix findings (G2: probe -> compare-release).
            # These are release-global, not per-library, so they fold into the
            # worst-of verdict and surface as their own report section.
            matrix_result, worst_verdict = _collect_matrix_result(
                probe_matrix_old,
                probe_matrix_new,
                policy,
                worst_verdict,
                suppress=suppress,
                policy_file_path=policy_file_path,
                old_version=old_version,
                new_version=new_version,
                pack_application=pack_application,
            )

            # Fold release-global bundle/matrix findings into the severity exit so a
            # clean-per-library release with a bundle/matrix break is not masked.
            if severity_exit_code is not None:
                severity_exit_code = _fold_release_global_severity(
                    severity_exit_code,
                    bundle_result,
                    matrix_result,
                    severity_preset,
                    severity_abi_breaking,
                    severity_potential_breaking,
                    severity_quality_issues,
                    severity_addition,
                )

            if secondary_output is not None:
                # CLI cleanup phase two, PR E: --write, now supported for a
                # directory/package (release) compare. Renders the second
                # format from the exact same already-computed
                # library_results/diff_pairs/bundle_result/matrix_result --
                # no second per-library comparison pass, mirroring how
                # single-pair `compare`'s own --write reuses its one already-
                # computed DiffResult (see run_compare's own secondary
                # _write_or_echo call).
                assert secondary_fmt is not None  # guaranteed by Click's callback
                secondary_text = _format_release_summary(
                    secondary_fmt,
                    worst_verdict,
                    old_dir,
                    new_dir,
                    library_results,
                    removed_keys,
                    added_keys,
                    old_map,
                    new_map,
                    warning_msgs,
                    diff_pairs=diff_pairs if secondary_fmt == "junit" else None,
                    bundle_result=bundle_result,
                    matrix_result=matrix_result,
                    severity_config=severity_config,
                    severity_exit_code=severity_exit_code,
                    contract_coverage_exit_contribution=contract_coverage_exit_contribution,
                    contract_coverage_failure_count=contract_coverage_failure_count,
                    fail_on_removed=fail_on_removed,
                )
                _write_or_echo(secondary_output, secondary_text)

            _finalize_release_output(
                fmt,
                worst_verdict,
                old_dir,
                new_dir,
                library_results,
                removed_keys,
                added_keys,
                old_map,
                new_map,
                warning_msgs,
                diff_pairs,
                bundle_result,
                output,
                output_dir,
                fail_on_removed,
                matrix_result=matrix_result,
                severity_exit_code=severity_exit_code,
                severity_config=severity_config,
                contract_coverage_exit_contribution=contract_coverage_exit_contribution,
                contract_coverage_failure_count=contract_coverage_failure_count,
            )
        finally:
            _cleanup_temp_dirs(_temp_dir_paths, keep_extracted)


def _prepare_compare_release_inputs(
    old_dir: Path,
    new_dir: Path,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    include_private_dso: bool,
    dso_only: bool,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    config_includes: tuple[Path, ...],
    extract_if_package: Callable[
        [Path, Path | None, Path | None],
        tuple[Path, Path | None, Path | None, Path | None],
    ],
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
    is_elf_shared_object: Callable[[Path], bool],
) -> tuple[
    Path | None,
    Path | None,
    list[Path],
    list[Path],
    list[Path],
    list[Path],
    dict[str, Path],
    dict[str, Path],
    list[str],
    list[str],
    list[str],
    list[str],
]:
    """Prepare inputs/maps/keys for compare-release command."""
    old_lib_dir, old_debug_dir, old_header_dir, old_symbols_file = extract_if_package(
        old_dir,
        debug_info1,
        devel_pkg1,
    )
    new_lib_dir, new_debug_dir, new_header_dir, new_symbols_file = extract_if_package(
        new_dir,
        debug_info2,
        devel_pkg2,
    )
    old_files = _discover_files(
        old_dir,
        old_lib_dir,
        include_private_dso,
        discover_shared_libraries,
        is_package,
    )
    new_files = _discover_files(
        new_dir,
        new_lib_dir,
        include_private_dso,
        discover_shared_libraries,
        is_package,
    )
    if dso_only:
        old_files = [f for f in old_files if is_elf_shared_object(f)]
        new_files = [f for f in new_files if is_elf_shared_object(f)]
    old_map, old_warns = _build_match_map(old_files)
    new_map, new_warns = _build_match_map(new_files)
    warning_msgs: list[str] = [
        f"Warning: {warning}" for warning in (old_warns + new_warns)
    ]
    debian_symbols_note = _debian_symbols_warning(old_symbols_file, new_symbols_file)
    if debian_symbols_note is not None:
        warning_msgs.append(debian_symbols_note)
    old_h, new_h = _resolve_release_headers(
        headers,
        old_headers_only,
        new_headers_only,
        old_header_dir,
        new_header_dir,
    )
    # config_includes (the project .abicheck.yml compile.include_dirs
    # suffix, already folded into `includes` by the caller) must survive a
    # per-library-pair --old-include/--new-include override, which
    # otherwise fully replaces `includes` for that side -- so it is
    # re-appended explicitly here rather than relied on via `includes`
    # (Codex review, fresh evidence).
    old_inc = (
        list(old_includes_only) + list(config_includes)
        if old_includes_only
        else list(includes)
    )
    new_inc = (
        list(new_includes_only) + list(config_includes)
        if new_includes_only
        else list(includes)
    )
    old_inc.extend(_discover_include_roots(old_header_dir))
    new_inc.extend(_discover_include_roots(new_header_dir))
    matched_keys, removed_keys, added_keys, old_map, new_map = _match_release_keys(
        old_dir,
        new_dir,
        old_map,
        new_map,
        old_files,
        new_files,
        is_package,
    )
    _collect_release_warnings(
        warning_msgs,
        matched_keys,
        removed_keys,
        added_keys,
        old_map,
        new_map,
    )
    return (
        old_debug_dir,
        new_debug_dir,
        old_h,
        new_h,
        old_inc,
        new_inc,
        old_map,
        new_map,
        warning_msgs,
        matched_keys,
        removed_keys,
        added_keys,
    )


# ── Suggest suppressions command ──────────────────────────────────────────────

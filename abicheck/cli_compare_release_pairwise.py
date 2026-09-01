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

"""``compare-release``'s per-pair/per-library comparison engine (split
from :mod:`abicheck.cli_compare_release`).

The single-pair comparison primitive (:func:`_run_compare_pair`), the
per-library comparison it feeds (:func:`_compare_one_library`), the
opt-in lockstep-SONAME suppression pass
(:func:`_suppress_lockstep_soname_findings`), and the release-wide
sequential/parallel dispatch over every matched library
(:func:`_compare_release_libraries`/:func:`_compare_release_parallel`/
:func:`_compare_release_sequential`).

Extracted purely to keep :mod:`abicheck.cli_compare_release` itself under
the AI-readiness 2000-line hard cap -- see this module's own sibling,
:mod:`abicheck.cli_compare_release_matrix` (matrix-result collection,
output finalization, gating, and input-discovery -- the *other* half of
what :func:`abicheck.cli_compare_release.compare_release_cmd` calls but
does not itself define), for the fuller rationale: `architecture/debt.yaml`
pins :mod:`abicheck.cli_compare_release` (and its pre-existing sibling
:mod:`abicheck.cli_compare_release_helpers`) at their exact adoption-time
line count, and a single combined engine module would itself have landed
over the AI-readiness 800-line production cap for a *new* file -- so the
per-pair engine and the matrix/output half are two separate, independently
mechanical extractions instead of one. A mechanical extraction (unchanged
function bodies). :mod:`abicheck.cli_compare_release` re-exports every
name here that an existing test or caller imports directly (``from
abicheck.cli_compare_release import ...``) for back-compat -- new code
should import from here directly.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .api_types import CompareResult
from .bundle_models import BundleSignatureEvidence
from .checker import DiffResult
from .cli import _normalize_binary_input, _safe_write_output
from .cli_compare_receipt import record_release_resolved_config
from .cli_compare_release_helpers import _RELEASE_VERDICT_ORDER
from .errors import ProfileMismatchError, ScopeMismatchError
from .model import AbiSnapshot
from .reporter import to_json

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig

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
    record_release_resolved_config(
        result.diff, getattr(pack_application, "resolved_config", None)
    )
    return result


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
            **(
                {"coverage_warnings": list(result.coverage_warnings)}
                if result.coverage_warnings
                else {}
            ),  # e.g. same-binary; never reached this entry before (Codex review)
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
        kind = (
            "profile_mismatch"
            if isinstance(exc, ProfileMismatchError)
            else "scope_mismatch"
        )
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
        if _RELEASE_VERDICT_ORDER.get(v, 0) > _RELEASE_VERDICT_ORDER.get(
            worst_verdict, 0
        ):
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

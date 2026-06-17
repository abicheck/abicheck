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

from .bundle import BundleDiffResult
from .checker import DiffResult
from .cli import (
    _build_match_map,
    _collect_release_inputs,
    _normalize_binary_input,
    _safe_write_output,
    _setup_verbosity,
    _write_or_echo,
    _write_release_step_summary,
    main,
)
from .cli_compare_release_helpers import (  # noqa: F401
    _RELEASE_VERDICT_ORDER,
    _cleanup_temp_dirs,
    _collect_bundle_result,
    _collect_release_warnings,
    _compute_release_severity_exit_code,
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
)
from .cli_params import POLICY_FILE_PARAM, _load_suppression_and_policy
from .model import AbiSnapshot
from .reporter import to_json

if TYPE_CHECKING:
    from .severity import SeverityConfig

# ---------------------------------------------------------------------------
# compare-release helpers
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
    scope_to_public_surface: bool = False,
    pattern_verdicts: bool = False,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Run compare for one old/new pair and return result + resolved snapshots.

    Routes through the single Tier-2 chokepoint (:func:`service.run_compare`,
    ADR-037 D1) rather than calling ``checker.compare`` directly — this is what
    keeps ``compare-release`` and ``compare`` on one classification path so a
    library gets the same verdict from either command (no ``scope_public``
    default drift).
    """
    from . import service

    # Follow GNU ld linker scripts up front so metadata/dependency analysis use
    # the resolved DSO, not the text script.
    old_input, _ = _normalize_binary_input(old_input)
    new_input, _ = _normalize_binary_input(new_input)

    return service.run_compare(
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
        policy_file_path=policy_file_path,
        old_pdb_path=old_pdb_path,
        new_pdb_path=new_pdb_path,
        scope_to_public_surface=scope_to_public_surface,
        pattern_verdicts=pattern_verdicts,
    )


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
    scope_to_public_surface: bool = False,
) -> dict[str, object]:
    """Compare one library pair — suitable for parallel dispatch.

    The entire per-library flow (debug info resolution, comparison, output
    writing) is wrapped so that *any* exception yields an ERROR entry
    instead of aborting the whole release comparison.

    The full :class:`DiffResult` is stashed in the returned dict under
    the ``"_diff_result"`` key. Callers that need the full diff (the
    bundle layer, JUnit aggregation) pop it from the entry before
    JSON-serialising — keeps the per-library compare a single-pass.
    """
    old_path = old_map[key]
    new_path = new_map[key]
    try:
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        result, _, _ = _run_compare_pair(
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
        )
        v = result.verdict.value
        # compatible_additions historically counts *all* compatible changes
        # (additions + quality issues). Emit the quality subset separately so
        # downstream consumers (e.g. the PR-comment renderer) can gate the two
        # categories independently under --severity-quality-issues.
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
        }
        if scope_to_public_surface:
            # Per-library public-surface scoping outcome (ADR-024, issue #235),
            # aggregated into the release-level scope block by the formatter.
            entry["scope_resolved"] = result.scope_resolved
            entry["filtered_internal_count"] = result.out_of_surface_count
        if output_dir:
            lib_report_path = output_dir / f"{old_path.stem}.json"
            _safe_write_output(lib_report_path, to_json(result))
        return entry
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
) -> int:
    """Drop ``SONAME_BUMP_UNNECESSARY`` when the release is a coordinated break.

    A library only earns ``SONAME_BUMP_UNNECESSARY`` when *it* had no breaking
    change yet its SONAME was bumped. In a multi-library release where a sibling
    or dependency suffered a genuine *binary* ABI break, bumping every member's
    SONAME in lockstep is the correct, intentional practice — so the per-library
    "unnecessary" signal is a false positive at the release level. Mutates the
    affected per-library results (and re-writes their JSON when ``output_dir`` is
    set) and returns the number of findings suppressed.

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
            _safe_write_output(lib_report_path, to_json(result))
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
    annotate: bool = False,
    annotate_additions: bool = False,
    jobs: int = 1,
    scope_to_public_surface: bool = False,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, AbiSnapshot]]]:
    """Compare each matched library pair and collect results.

    When *collect_diff_results* is True, ``(DiffResult, old_snapshot)``
    pairs are collected and returned as the third element of the tuple
    (used by the JUnit output format).

    When *jobs* > 1, comparisons are dispatched in parallel via
    :func:`_compare_one_library` using a :class:`ProcessPoolExecutor`.
    """
    import os as _os

    effective_jobs = jobs if jobs > 0 else (_os.cpu_count() or 1)
    library_results: list[dict[str, object]] = []
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    worst_verdict = "NO_CHANGE"
    all_annotations: list[tuple[int, str]] = []

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
    )
    if suppressed_soname:
        click.echo(
            f"Note: suppressed {suppressed_soname} 'soname_bump_unnecessary' "
            "finding(s) — the release contains coordinated ABI breaks, so "
            "lockstep SONAME bumps are justified.",
            err=True,
        )

    # collect_diff_results and annotate require re-running comparison for
    # affected libraries (only used for JUnit / GitHub annotations which
    # are sequential-only features)
    if collect_diff_results or annotate:
        extra_pairs, extra_annotations = _collect_release_extras(
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
            annotate_additions=annotate_additions,
            collect_diff_results=collect_diff_results,
            annotate=annotate,
            scope_to_public_surface=scope_to_public_surface,
        )
        diff_pairs.extend(extra_pairs)
        all_annotations.extend(extra_annotations)

    # Emit annotations once: sort globally across all libraries by severity,
    # then truncate to the cap.  This ensures the most important annotations
    # (errors) are always visible regardless of which library they came from.
    if all_annotations:
        from .annotations import format_annotations

        text = format_annotations(all_annotations)
        if text:
            click.echo(text, err=True)

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
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_by_key: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_compare_one_library, key, *common_args): key
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


def _collect_release_extras(
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
    *,
    annotate_additions: bool,
    collect_diff_results: bool,
    annotate: bool,
    scope_to_public_surface: bool = False,
) -> tuple[list[tuple[DiffResult, AbiSnapshot]], list[tuple[int, str]]]:
    """Collect optional re-run artifacts for JUnit and annotations."""
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    annotations: list[tuple[int, str]] = []
    for key in matched_keys:
        old_path = old_map[key]
        new_path = new_map[key]
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        try:
            result, old_snap, _ = _run_compare_pair(
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
            )
        except Exception as exc:
            click.echo(
                f"Warning: failed to re-run comparison for {old_path.name}: {exc}",
                err=True,
            )
            continue
        if collect_diff_results:
            diff_pairs.append((result, old_snap))
        if annotate:
            from .annotations import collect_annotations, is_github_actions

            if is_github_actions():
                annotations.extend(
                    collect_annotations(result, annotate_additions=annotate_additions),
                )
    return diff_pairs, annotations


def _write_release_summary_file(
    output_dir: Path,
    worst_verdict: str,
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> None:
    """Write per-library summary JSON to output directory."""
    summary_data: dict[str, object] = {
        "verdict": worst_verdict,
        "libraries": library_results,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
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
    """
    from .cli import _load_probe_matrix_changes

    matrix_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)
    if not matrix_changes:
        return None, worst_verdict

    from .model import AbiSnapshot
    from .service import compare_snapshots

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
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
    annotate: bool,
    fail_on_removed: bool,
    matrix_result: DiffResult | None = None,
    severity_exit_code: int | None = None,
    severity_config: SeverityConfig | None = None,
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
    )
    _write_or_echo(output, text)

    if annotate:
        _write_release_step_summary(text, fmt)

    if output_dir:
        _write_release_summary_file(
            output_dir,
            worst_verdict,
            library_results,
            removed_keys,
            added_keys,
            old_map,
            new_map,
        )

    _exit_compare_release(
        worst_verdict, fail_on_removed, removed_keys, severity_exit_code
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


def _strip_diff_results_and_adjust_verdict(
    library_results: list[dict[str, object]],
    removed_keys: list[str],
    worst_verdict: str,
) -> str:
    """Remove un-serialisable ``_diff_result`` entries and adjust the worst verdict.

    After bundle analysis the stashed :class:`DiffResult` objects are no
    longer needed.  Stripping them here keeps the summary formatter free of
    any Python-only objects.  Additionally, if any library was *removed*
    from the release and the verdict has not already been escalated, the
    verdict is bumped to at least ``COMPATIBLE_WITH_RISK``.

    Returns the (possibly updated) *worst_verdict* string.
    """
    for entry in library_results:
        if isinstance(entry, dict):
            entry.pop("_diff_result", None)
    if removed_keys and _RELEASE_VERDICT_ORDER.get(
        worst_verdict, 0
    ) < _RELEASE_VERDICT_ORDER.get("COMPATIBLE_WITH_RISK", 0):
        worst_verdict = "COMPATIBLE_WITH_RISK"
    return worst_verdict


@main.command("compare-release")
@click.argument("old_dir", type=click.Path(exists=True, path_type=Path))
@click.argument("new_dir", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-H",
    "--header",
    "headers",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Public header file or directory applied to both sides.",
)
@click.option(
    "-I",
    "--include",
    "includes",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Extra include directory for castxml.",
)
@click.option(
    "--old-include",
    "old_includes_only",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Include directory for old side only (overrides -I for old).",
)
@click.option(
    "--new-include",
    "new_includes_only",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Include directory for new side only (overrides -I for new).",
)
@click.option(
    "--old-header",
    "old_headers_only",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Header for old side only (overrides -H for old).",
)
@click.option(
    "--new-header",
    "new_headers_only",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Header for new side only (overrides -H for new).",
)
@click.option(
    "--old-version",
    "old_version",
    default="old",
    show_default=True,
    help="Version label for old side.",
)
@click.option(
    "--new-version",
    "new_version",
    default="new",
    show_default=True,
    help="Version label for new side.",
)
@click.option(
    "--lang",
    default="c++",
    show_default=True,
    type=click.Choice(["c++", "c"], case_sensitive=False),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown", "junit"]),
    default="markdown",
    show_default=True,
)
@click.option(
    "-o",
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output file for summary report (default: stdout).",
)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write per-library reports.",
)
@click.option(
    "--suppress",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Suppression file (YAML).",
)
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
    "--policy",
    "policy",
    type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
    default="strict_abi",
    show_default=True,
)
@click.option("--policy-file", "policy_file_path", type=POLICY_FILE_PARAM, default=None)
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
@click.option(
    "--annotate",
    is_flag=True,
    default=False,
    help="Emit GitHub Actions workflow command annotations to stdout. "
    "Only effective when GITHUB_ACTIONS=true.",
)
@click.option(
    "--annotate-additions",
    is_flag=True,
    default=False,
    help="Include additions/compatible changes as ::notice annotations "
    "(requires --annotate).",
)
@click.option("-v", "--verbose", is_flag=True, default=False)
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
    "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list).",
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
    "--scope-public-headers/--no-scope-public-headers",
    "scope_public_headers",
    default=True,
    show_default=True,
    help="Restrict findings to the public-header ABI surface (ADR-024). "
    "On by default (matches `compare`); use --no-scope-public-headers "
    "to report every finding.",
)
@click.option(
    "--probe-matrix-old",
    "probe_matrix_old",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Old build-configuration matrix snapshot (from 'abicheck probe run'). "
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
# ── Severity (mirrors `compare`) ──────────────────────────────────────────────
@click.option(
    "--severity-preset",
    "severity_preset",
    type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
    default=None,
    help="Severity preset: 'default', 'strict', or 'info-only'. "
    "When set (or any --severity-* option), exit codes follow the "
    "severity-aware scheme aggregated across all libraries.",
)
@click.option(
    "--severity-abi-breaking",
    "severity_abi_breaking",
    type=click.Choice(["error", "warning", "info"], case_sensitive=True),
    default=None,
    help="Severity for clear ABI/API incompatibilities (overrides preset).",
)
@click.option(
    "--severity-potential-breaking",
    "severity_potential_breaking",
    type=click.Choice(["error", "warning", "info"], case_sensitive=True),
    default=None,
    help="Severity for potential incompatibilities needing review (overrides preset).",
)
@click.option(
    "--severity-quality-issues",
    "severity_quality_issues",
    type=click.Choice(["error", "warning", "info"], case_sensitive=True),
    default=None,
    help="Severity for problematic behaviors (overrides preset).",
)
@click.option(
    "--severity-addition",
    "severity_addition",
    type=click.Choice(["error", "warning", "info"], case_sensitive=True),
    default=None,
    help="Severity for new public API additions (overrides preset).",
)
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
    annotate: bool,
    annotate_additions: bool,
    verbose: bool,
    jobs: int,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...],
    no_bundle_analysis: bool,
    scope_public_headers: bool,
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
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
    With any --severity-* option, exit codes follow the severity-aware scheme
    aggregated across all libraries (and bundle/matrix findings):
      0  no error-level findings
      1  error in quality/addition categories only
      2  error in potential_breaking
      4  error in abi_breaking
    A removed library (--fail-on-removed-library) still exits 8, and a per-library
    comparison ERROR still floors the exit at 4, regardless of severity settings.

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

    from .package import (
        _is_elf_shared_object,
        detect_extractor,
        discover_shared_libraries,
        is_package,
        resolve_debug_info,
    )

    _setup_verbosity(verbose)

    if annotate_additions and not annotate:
        raise click.UsageError("--annotate-additions requires --annotate")

    # Track temporary directory paths for cleanup
    _temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = tempfile.mkdtemp(prefix=prefix)
        _temp_dir_paths.append(path)
        return Path(path)

    def _do_extract(
        input_path: Path, debug_pkg: Path | None, devel_pkg: Path | None
    ) -> tuple[Path, Path | None, Path | None]:
        return _extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            _make_temp_dir,
            is_package,
            detect_extractor,
        )

    # Validate suppression file early (before per-library loop)
    _validate_suppression_early(
        suppress, policy, policy_file_path, strict_suppressions, require_justification
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
            _do_extract,
            discover_shared_libraries,
            is_package,
            _is_elf_shared_object,
        )

        if fmt != "json":
            for msg in warning_msgs:
                click.echo(msg, err=True)

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        # JUnit still re-runs pairs in _collect_release_extras because it
        # needs old AbiSnapshot too. Bundle analysis reuses the
        # _diff_result stashed in each library entry from the first pass.
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
            collect_diff_results=(fmt == "junit"),
            annotate=annotate,
            annotate_additions=annotate_additions,
            jobs=jobs,
            scope_to_public_surface=scope_public_headers,
        )

        # Compute the severity-aware exit code while per-library DiffResults
        # are still stashed (before _strip_diff_results_and_adjust_verdict).
        # Returns None when no --severity-* option was supplied, in which case
        # the legacy verdict-based exit is used downstream.
        severity_config = _resolve_release_severity_config(
            severity_preset,
            severity_abi_breaking,
            severity_potential_breaking,
            severity_quality_issues,
            severity_addition,
        )
        severity_exit_code = _compute_release_severity_exit_code(
            library_results,
            severity_preset,
            severity_abi_breaking,
            severity_potential_breaking,
            severity_quality_issues,
            severity_addition,
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
            )

        # Strip _diff_result from entries and bump verdict for removed libraries.
        worst_verdict = _strip_diff_results_and_adjust_verdict(
            library_results, removed_keys, worst_verdict
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
            annotate,
            fail_on_removed,
            matrix_result=matrix_result,
            severity_exit_code=severity_exit_code,
            severity_config=severity_config,
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
    extract_if_package: Callable[
        [Path, Path | None, Path | None], tuple[Path, Path | None, Path | None]
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
    old_lib_dir, old_debug_dir, old_header_dir = extract_if_package(
        old_dir,
        debug_info1,
        devel_pkg1,
    )
    new_lib_dir, new_debug_dir, new_header_dir = extract_if_package(
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
    old_h, new_h = _resolve_release_headers(
        headers,
        old_headers_only,
        new_headers_only,
        old_header_dir,
        new_header_dir,
    )
    old_inc = list(old_includes_only) if old_includes_only else list(includes)
    new_inc = list(new_includes_only) if new_includes_only else list(includes)
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

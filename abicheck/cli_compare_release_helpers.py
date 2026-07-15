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
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .checker import DiffResult
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .package import PackageExtractor
    from .severity import SeverityConfig


_RELEASE_VERDICT_ORDER: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 1,
    "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3,
    "BREAKING": 4,
    "ERROR": 5,
}


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
) -> BundleDiffResult | None:
    """Run bundle-level (ADR-023) analysis on a compare-release run.

    Reuses the per-library :class:`DiffResult`s already computed by
    :func:`_compare_release_libraries` — no second per-pair compare pass.

    Returns None when there is nothing to analyze (e.g. all libraries
    failed to dump). Errors during analysis are caught and reported as a
    warning rather than aborting; bundle analysis is additive.
    """
    from .bundle import (
        BundleDiffResult,
        build_bundle_snapshot,
        compare_bundle,
        load_manifest,
    )

    if not old_map and not new_map:
        return None
    try:
        old_snap = build_bundle_snapshot(dict(old_map))
        new_snap = build_bundle_snapshot(dict(new_map))
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

    system_extra: list[str] = [
        s.strip() for s in bundle_system_providers.split(",") if s.strip()
    ]
    try:
        return compare_bundle(
            old_snap,
            new_snap,
            per_lib_results,
            manifest=manifest,
            system_providers=system_extra or None,
            cohorts=list(bundle_cohorts) or None,
        )
    except Exception as exc:
        # Analysis-engine bugs should not block the per-library report;
        # surface as a warning. Future work: surface as a coverage_warning
        # in the JSON output so downstream CI can detect degradation.
        click.echo(f"Warning: bundle analysis raised: {exc}", err=True)
        return BundleDiffResult(old_root=old_snap.root, new_root=new_snap.root)


def _extract_if_package(
    input_path: Path,
    debug_pkg: Path | None,
    devel_pkg: Path | None,
    make_temp_dir: Callable[[str], Path],
    is_package: Callable[[Path], bool],
    detect_extractor: Callable[[Path], PackageExtractor | None],
) -> tuple[Path, Path | None, Path | None]:
    """Extract package to tempdir if needed, return (lib_dir, debug_dir, header_dir).

    When *input_path* is a plain directory (not a package archive), it is used
    as-is for lib_dir.  Side packages (*debug_pkg*, *devel_pkg*) are still
    extracted in that case so that standalone debug/devel packages paired with
    an already-extracted directory are not silently ignored.
    """
    # Default: treat input_path as an already-extracted library directory.
    lib_dir: Path = input_path
    debug_dir: Path | None = None
    header_dir: Path | None = None

    if is_package(input_path):
        extractor = detect_extractor(input_path)
        if extractor is None:
            raise click.ClickException(f"Unrecognized package format: {input_path}")
        target = make_temp_dir("abicheck_pkg_")
        result = extractor.extract(input_path, target)
        lib_dir = result.lib_dir
        debug_dir = result.debug_dir
        header_dir = result.header_dir

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

    return lib_dir, debug_dir, header_dir


def _collect_bundle_result(
    library_results: list[dict[str, object]],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    worst_verdict: str,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...] = (),
) -> tuple[BundleDiffResult | None, str]:
    """Extract stashed DiffResults, run bundle analysis, update worst verdict."""
    stashed_diffs: list[DiffResult] = []
    for entry in library_results:
        diff = entry.get("_diff_result") if isinstance(entry, dict) else None
        if isinstance(diff, DiffResult):
            stashed_diffs.append(diff)
    bundle_result = _run_bundle_analysis(
        old_map,
        new_map,
        stashed_diffs,
        manifest_path=manifest_path,
        bundle_system_providers=bundle_system_providers,
        bundle_cohorts=bundle_cohorts,
    )
    if bundle_result is not None:
        bv = bundle_result.bundle_verdict.value
        if _RELEASE_VERDICT_ORDER.get(bv, 0) > _RELEASE_VERDICT_ORDER.get(
            worst_verdict, 0
        ):
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


def _resolve_release_severity_config(
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> SeverityConfig | None:
    """Resolve the severity config, or None when no ``--severity-*`` was set."""
    if not any(
        v is not None
        for v in (
            severity_preset,
            severity_abi_breaking,
            severity_potential_breaking,
            severity_quality_issues,
            severity_addition,
        )
    ):
        return None
    from .severity import resolve_severity_config

    return resolve_severity_config(
        severity_preset,
        abi_breaking=severity_abi_breaking,
        potential_breaking=severity_potential_breaking,
        quality_issues=severity_quality_issues,
        addition=severity_addition,
    )


def _compute_release_severity_exit_code(
    library_results: list[dict[str, object]],
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> int | None:
    """Compute the severity-aware exit code aggregated across all libraries.

    Returns ``None`` when no ``--severity-*`` option was supplied (callers
    keep the legacy verdict-based exit). Otherwise returns the worst
    :func:`compute_exit_code` over the per-library changes. Each library is
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
    resolved_config = _resolve_release_severity_config(
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    )
    if resolved_config is None:
        return None

    from .severity import compute_exit_code

    worst = 0
    for entry in library_results:
        diff = entry.get("_diff_result") if isinstance(entry, dict) else None
        if isinstance(diff, DiffResult):
            code = compute_exit_code(
                diff.changes,
                resolved_config,
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
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> int:
    """Fold release-global (bundle + matrix) findings into the severity exit.

    The per-library aggregation in :func:`_compute_release_severity_exit_code`
    cannot see bundle-level findings or build-config matrix findings, which are
    computed later and update ``worst_verdict``. Without this, a release whose
    per-library diffs are clean but whose bundle/matrix analysis flags an
    error-level break would exit 0 under, e.g., the default preset. Returns the
    worst of *base_code* and the bundle/matrix severity codes.
    """
    config = _resolve_release_severity_config(
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    )
    if config is None:
        return base_code

    from .severity import compute_exit_code

    worst = base_code
    if bundle_result is not None and bundle_result.bundle_findings:
        # Bundle findings carry canonical (partitioned) ChangeKinds.
        bundle_changes = [f.to_change() for f in bundle_result.bundle_findings]
        worst = max(worst, compute_exit_code(bundle_changes, config))
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
) -> None:
    """Exit compare-release with ABI-compatible status code mapping.

    When *severity_exit_code* is not None, the severity-aware scheme is in
    effect: that code replaces the verdict-based 2/4 mapping, except that
    (a) a removed library still exits 8 in preference to the severity code, and
    (b) an operational ERROR verdict (a library failed to dump/extract/compare)
    still floors the exit at 4 — such failures produce no ``DiffResult.changes``
    so the severity aggregation cannot see them, and must never be downgraded.
    When None, the legacy verdict-based mapping is unchanged.
    """
    if severity_exit_code is not None:
        # Severity-aware scheme: removed-library 8 takes precedence over the
        # severity code, otherwise emit the aggregated severity exit code.
        if fail_on_removed and removed_keys:
            sys.exit(8)
        code = severity_exit_code
        if worst_verdict == "ERROR":
            code = max(code, 4)
        if code != 0:
            sys.exit(code)
        return
    # ERROR is a compare-release-specific operational-failure sentinel (not a
    # Verdict); it floors at 4. Otherwise the verdict→code mapping is the shared
    # canonical one, so compare and compare-release never disagree (C7).
    if worst_verdict == "ERROR":
        sys.exit(4)
    from .checker_policy import Verdict
    from .severity import legacy_exit_code

    code = (
        legacy_exit_code(Verdict[worst_verdict])
        if worst_verdict in Verdict.__members__
        else 0
    )
    if code != 0:
        sys.exit(code)
    if fail_on_removed and removed_keys:
        sys.exit(8)


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
    bundle_result: BundleDiffResult | None = None,
    matrix_result: DiffResult | None = None,
    severity_config: SeverityConfig | None = None,
    severity_exit_code: int | None = None,
) -> str:
    """Format the release comparison summary as JSON, markdown, or JUnit XML."""
    if fmt == "junit":
        return _format_release_junit(diff_pairs, matrix_result, library_results)
    if fmt == "json":
        return _format_release_json(
            worst_verdict,
            old_dir,
            new_dir,
            library_results,
            removed_keys,
            added_keys,
            old_map,
            new_map,
            warning_msgs,
            bundle_result,
            matrix_result,
            severity_config=severity_config,
            severity_exit_code=severity_exit_code,
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
) -> str:
    """Render the release summary as a JUnit XML report."""
    from .junit_report import to_junit_xml_multi

    pairs: list[tuple[DiffResult, AbiSnapshot | None]] = list(diff_pairs or [])
    # Release-global matrix findings ride in as their own synthetic
    # testsuite so CI dashboards reading the JUnit report see the failure.
    if matrix_result is not None:
        pairs.append((matrix_result, None))
    error_libs = [entry for entry in library_results if entry.get("verdict") == "ERROR"]
    return to_junit_xml_multi(
        pairs,
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
) -> str:
    """Render the release summary as a JSON document."""
    changed_libraries = [
        str(lib["library"])
        for lib in library_results
        if str(lib.get("verdict")) not in ("NO_CHANGE", "ERROR")
    ]
    summary: dict[str, object] = {
        "verdict": worst_verdict,
        "old_dir": str(old_dir),
        "new_dir": str(new_dir),
        "libraries": library_results,
        "changed_libraries": changed_libraries,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
        "warnings": warning_msgs,
    }
    # Severity config block (present only when --severity-* was active), mirroring
    # compare mode so downstream consumers (e.g. the PR-comment renderer) can see
    # which categories are gated to error and bucket findings accordingly.
    if severity_config is not None:
        summary["severity"] = {
            "config": {
                "abi_breaking": severity_config.abi_breaking.value,
                "potential_breaking": severity_config.potential_breaking.value,
                "quality_issues": severity_config.quality_issues.value,
                "addition": severity_config.addition.value,
            },
            "exit_code": severity_exit_code,
        }
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
    _VERDICT_EMOJI = {
        "NO_CHANGE": "✅",
        "COMPATIBLE": "✅",
        "COMPATIBLE_WITH_RISK": "⚠️",
        "API_BREAK": "⚠️",
        "BREAKING": "❌",
        "ERROR": "💥",
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
    lines += _release_md_changed_libraries(removed_keys, added_keys, old_map, new_map)
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


def _release_md_changed_libraries(
    removed_keys: list[str],
    added_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
) -> list[str]:
    """Markdown sections listing removed/added libraries."""
    lines: list[str] = []
    if removed_keys:
        lines += ["", "## ⚠️ Removed Libraries", ""]
        lines += [f"- `{old_map[k].name}`" for k in removed_keys]
    if added_keys:
        lines += ["", "## ℹ️ Added Libraries", ""]
        lines += [f"- `{new_map[k].name}`" for k in added_keys]
    return lines


def _release_md_bundle_findings(bundle_result: BundleDiffResult | None) -> list[str]:
    """Markdown section for cross-library (bundle) findings."""
    if bundle_result is None or not bundle_result.bundle_findings:
        return []
    lines = ["", "## 🔗 Bundle (Cross-Library) Findings", ""]
    for f in bundle_result.bundle_findings:
        # Library-scoped findings (bundle_library_added /
        # bundle_library_removed) carry the library name in `symbol`;
        # manifest/import findings carry the symbol. Both are non-empty in
        # practice, but guard against future finding shapes with no attribution.
        lines.append(
            f"- **{f.kind.value}**"
            + (f" — `{f.symbol}`" if f.symbol else "")
            + (f" (consumer: `{f.consumer_library}`)" if f.consumer_library else "")
            + (f" (provider: `{f.provider_library}`)" if f.provider_library else ""),
        )
        lines.append(f"  - {f.description}")
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

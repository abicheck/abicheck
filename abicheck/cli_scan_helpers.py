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

"""Pure helpers for :mod:`abicheck.cli_scan`.

Split out of the (large, near-cap) ``cli_scan`` module: these are click-free,
side-effect-free functions that ``_render_text`` and ``run_scan_core`` compose.
Keeping them here holds ``cli_scan.py`` under the 2000-line hard cap while
decomposing the two long methods into legible pieces.

No import cycle: this module imports only from :mod:`abicheck.buildsource`. The
render helpers take the ``ScanOutcome`` dataclass as ``Any`` rather than importing
it from :mod:`abicheck.cli_scan` (even under ``TYPE_CHECKING``), which would form a
cli_scan ↔ cli_scan_helpers cycle the import-cycles gate flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .buildsource.scan_levels import EvidenceDepth

if TYPE_CHECKING:
    from .buildsource.scan_levels import SourceMethod


# --- run_scan_core helpers ---------------------------------------------------


def scan_pattern_roots(
    headers: list[Path],
    sources: Path | None,
    eff_depth_enum: EvidenceDepth,
) -> list[Path]:
    """Roots the compiler-free pattern pre-scan (S3) walks for the given depth.

    The header roots are always scanned; the ``--sources`` tree is added only
    when the depth actually reaches source evidence (not BINARY/HEADERS).
    """
    pattern_roots: list[Path] = [*headers]
    if sources is not None and eff_depth_enum not in {
        EvidenceDepth.BINARY,
        EvidenceDepth.HEADERS,
    }:
        pattern_roots.append(sources)
    return pattern_roots


def l4_coverage_advisories(l4_cov: dict[str, Any]) -> list[str]:
    """Advisory notes derived from the L4 source-ABI coverage dict."""
    advisories: list[str] = []
    if l4_cov.get("scope_widened_to_full"):
        advisories.append(
            "headers-only source replay widened to all compile units because no "
            "include graph/public-header target ownership could narrow it. Provide "
            "depfile/include graph evidence or seed with --since/--changed-path to "
            "avoid full fanout."
        )
    uncovered = int(l4_cov.get("public_headers_uncovered", 0) or 0)
    if uncovered:
        advisories.append(
            f"headers-only source replay used the include graph and skipped full "
            f"fanout, but {uncovered} public header(s) were not reached by any "
            "selected TU; source-only coverage is partial for those headers."
        )
    exported = int(l4_cov.get("exported_symbols", 0) or 0)
    matched = int(l4_cov.get("matched_symbols", 0) or 0)
    parsed = int(l4_cov.get("compile_units_parsed", 0) or 0)
    if exported and parsed and matched == 0:
        advisories.append(
            f"L4 source replay parsed {parsed} TU(s) but matched 0/{exported} "
            "exported symbol(s); source-link evidence is degraded. Check mangled "
            "symbol matching/public-header roots before relying on source-only "
            "findings."
        )
    return advisories


def resolve_effective_allow_query(
    allow_build_query: bool,
    build_config: Path | None,
    collect_mode: str,
    level_explicit: bool,
    resolved: SourceMethod,
) -> tuple[bool, str | None]:
    """Resolve whether a trusted --config build.query is auto-enabled (ADR-037 D4).

    Returns ``(effective_allow_query, advisory_or_None)``.

    level-implies-query (ADR-037 D4): an explicit, *trusted* --config that
    defines a build.query, together with an *explicitly pinned* deep level
    (--source-method/--depth, level_explicit), is itself consent to run that
    query — making the user pass --allow-build-query as well for a level they
    explicitly asked for is needless friction. Trusted = an explicit --config
    path (build_config is not None here; an auto-discovered source-tree config
    is resolved later in embed_build_source and never reaches this gate), so
    this never runs an attacker-controlled command. Crucially it does NOT fire
    for the default mode preset (a plain `scan`/`--audit` with `--sources` whose
    collect_mode is already non-off) — only an explicit deep level counts, so a
    --config passed purely for project settings never silently runs a subprocess
    (Codex review). No-op when the config defines no query.
    """
    if not (
        not allow_build_query
        and build_config is not None
        and collect_mode != "off"
        and level_explicit
    ):
        return allow_build_query, None

    from .buildsource.inline import load_build_config

    try:
        _cfg = load_build_config(build_config)
    except Exception:  # malformed config surfaces later in the real load
        _cfg = None
    if _cfg is not None and _cfg.query:
        advisory = (
            f"level {resolved.value} with a trusted --config defining "
            "build.query: auto-enabled the query to collect L3+ evidence "
            "(equivalent to --allow-build-query). Pass --allow-build-query "
            "explicitly to silence this note."
        )
        return True, advisory
    return allow_build_query, None


# --- _render_text section helpers --------------------------------------------


def render_summary_lines(out: Any) -> list[str]:
    """The report header block: mode/level, risk, changed paths, advisories, POI."""
    lines: list[str] = []
    lines.append(f"abicheck scan — {out.mode} mode")
    lvl = f"  source-method={out.resolved_method}"
    if out.depth:
        lvl += f"  depth={out.depth}"
    lvl += f"  collect-mode={out.collect_mode}"
    if out.auto:
        lvl += "  (auto)"
    lines.append(lvl)
    matched = ", ".join(f"{k}×{v}" for k, v in sorted(out.risk.matched.items()))
    lines.append(
        f"  risk score={out.risk.total} "
        f"(auto→{out.risk.recommended_method})" + (f" [{matched}]" if matched else "")
    )
    lines.append(
        f"  changed paths: {out.changed_path_count} ({out.changed_path_source})"
    )
    for note in out.advisories:
        lines.append(f"  note: {note}")
    if out.stage_timings:
        timing = ", ".join(
            f"{name}={seconds:.2f}s"
            for name, seconds in sorted(out.stage_timings.items())
        )
        lines.append(f"  timings: {timing}")

    poi_counts = out.poi.get("counts_by_reason") or {}
    if poi_counts:
        focus = ", ".join(f"{k}×{v}" for k, v in sorted(poi_counts.items()))
        lines.append(
            f"  focus (POI): {out.poi.get('total', 0)} point(s) "
            f"[{focus}] → {len(out.poi.get('changed_paths') or [])} path(s), "
            f"{len(out.poi.get('symbols') or [])} symbol(s)"
        )
    return lines


def render_coverage_lines(out: Any) -> list[str]:
    """The always-present per-layer coverage table."""
    lines: list[str] = ["", "Coverage"]
    for row in out.coverage:
        lines.append(
            f"  {row['layer']:<18} {row['status']:<13} {row.get('detail', '')}"
        )
    return lines


def render_crosscheck_lines(out: Any) -> list[str]:
    """The cross-source / ABI-hygiene findings block (empty when none)."""
    if not out.crosscheck.get("counts_by_check"):
        return []
    lines: list[str] = [""]
    lines.append(
        "ABI-hygiene catalog (intra-version, advisory)"
        if out.audit
        else "Cross-source findings (advisory)"
    )
    for kind, n in sorted(out.crosscheck["counts_by_check"].items()):
        sev = out.crosscheck_severities.get(kind, "warning")
        lines.append(f"  [{sev}] {kind}: {n}")
    return lines


def render_pattern_lines(out: Any) -> list[str]:
    """The compiler-free pattern pre-scan facts block (empty when none)."""
    pat_counts = out.pattern.get("counts_by_kind") or {}
    if not pat_counts:
        return []
    lines: list[str] = ["", "Pattern pre-scan facts (advisory)"]
    for kind, n in sorted(pat_counts.items()):
        lines.append(f"  {kind}: {n}")
    return lines


def render_preprocessor_lines(out: Any) -> list[str]:
    """The S2 preprocessor pre-scan facts block (empty when none)."""
    pp_div = out.preprocessor.get("divergences") or []
    pp_leaks = out.preprocessor.get("leaks") or []
    if not (pp_div or pp_leaks):
        return []
    lines: list[str] = ["", "Preprocessor pre-scan facts (S2, advisory)"]
    for d in pp_div:
        lines.append(
            f"  macro divergence: {d['macro']} ({d['n_values']} values across TUs)"
        )
    for leak in pp_leaks:
        lines.append(
            f"  {leak['leak_class']}-header leak: "
            f"{leak['public_header']} → {leak['leaked_header']}"
        )
    return lines


def render_baseline_lines(out: Any) -> list[str]:
    """The baseline comparison summary block (empty without a baseline diff)."""
    if out.diff_summary is None:
        return []
    return [
        "",
        "Baseline comparison",
        f"  breaking={out.diff_summary['breaking']} "
        f"api_break={out.diff_summary['api_break']} "
        f"risk={out.diff_summary['risk']} "
        f"compatible={out.diff_summary['compatible']}",
    ]


def render_verdict_lines(out: Any) -> list[str]:
    """The always-present verdict / elapsed footer."""
    lines: list[str] = ["", f"Verdict: {out.verdict}"]
    if out.budget_s is not None:
        lines.append(f"Elapsed: {out.elapsed_s:.2f}s / budget {out.budget_s:.0f}s")
    return lines

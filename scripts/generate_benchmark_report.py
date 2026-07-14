#!/usr/bin/env python3
"""
Generate one authoritative benchmark report (JSON + Markdown) across
abicheck's L2 lane, L3-L5 source lane, and abidiff/ABICC, and optionally
verify it against the committed headline table in
``docs/reference/tool-comparison.md``.

This exists because the project's benchmark numbers have drifted from their
source of truth before: the doc's "Full-catalog benchmark" table was last
generated against a 170-case catalog, but ``examples/`` has since grown to
181 cases (PR #539) without the doc being regenerated. Nothing caught that
automatically. This script is the fix: a single reproducible report,
stamped with the git commit and ground-truth digest it was generated
against, plus a ``--check`` mode that fails loudly when the doc disagrees
with a freshly generated report instead of silently going stale again.

It is a thin wrapper around ``benchmark_comparison.py`` — all case
discovery, building, tool invocation, and accuracy/FP/FN math live there.
This script adds: Markdown rendering, wall-time + peak-RSS capture for the
whole run, frozen-vs-live cache-state reporting per tool, and the doc
drift check.

Usage:
    # Full catalog, whatever tools are available (frozen data fills the rest):
    python3 scripts/generate_benchmark_report.py

    # Fast local iteration on a couple of cases:
    python3 scripts/generate_benchmark_report.py --tools abicheck --cases case01 case02

    # Verify the committed doc table still matches reality (heading case-count
    # is always checked; per-lane numeric rows are only checked on a full run):
    python3 scripts/generate_benchmark_report.py --check

    # Regenerate the pinned competitor cache, then check:
    python3 scripts/generate_benchmark_report.py \\
        --tools abicheck abicheck_full abidiff abidiff_headers abicc_dumper abicc_xml \\
        --freeze abidiff abidiff_headers abicc_dumper abicc_xml --check
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - resource is POSIX-only
    resource = None  # type: ignore[assignment]

REPO_DIR = Path(__file__).parent.parent
DOC_PATH = REPO_DIR / "docs" / "reference" / "tool-comparison.md"

sys.path.insert(0, str(Path(__file__).parent))
import benchmark_comparison as bc  # noqa: E402

DEFAULT_JSON_OUT = bc.REPORT_DIR / "benchmark_report.json"
DEFAULT_MARKDOWN_OUT = bc.REPORT_DIR / "benchmark_report.md"

# Doc row label (markdown-stripped of `*`/`` ` ``) -> benchmark_comparison.py
# tool name. Keep in sync with the table in docs/reference/tool-comparison.md;
# parse_doc_table() below warns (rather than silently passing) when a label
# it expects can no longer be found.
LANE_DOC_LABELS: dict[str, str] = {
    "abicheck": "abicheck (L2, headers)",
    "abicheck_full": "abicheck (L3-L5, +sources)",
    "abidiff": "libabigail (`abidiff`)",
    "abidiff_headers": "libabigail + headers",
    "abicc_dumper": "ABICC (abi-dumper)",
    "abicc_xml": "ABICC (xml/legacy)",
}

DOC_HEADING_RE = re.compile(
    r"## Full-catalog benchmark \((?P<date>[^,]+), all (?P<count>\d+) cases\)"
)


def _peak_rss_mb() -> float | None:
    """Process peak RSS in MiB (self + terminated children), or None.

    Same technique as ``benchmark_scaling.py:_peak_rss_mb`` — summing
    ``RUSAGE_SELF`` and ``RUSAGE_CHILDREN`` is a conservative over-estimate
    (the two high-water marks need not coincide) but that's the safe
    direction for a reproducibility report, and it's the only way to see the
    native memory of castxml/abidiff/ABICC subprocesses at all.
    """
    if resource is None:
        return None
    self_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    child_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    divisor = 1024 if sys.platform == "darwin" else 1
    return round((self_rss + child_rss) / 1024 / divisor, 1)


def _clean_label(text: str) -> str:
    return text.strip().strip("*").replace("`", "").strip()


def _status_counts(results: list[dict], tool_name: str) -> dict[str, int]:
    """How many rows a tool reported as SKIP/ERROR/TIMEOUT/NO_SOURCE this run."""
    counts: dict[str, int] = {}
    for r in results:
        v = r.get(tool_name)
        if v in ("SKIP", "ERROR", "TIMEOUT", "NO_SOURCE"):
            counts[v] = counts.get(v, 0) + 1
    return counts


def cache_state_for(bc_args: argparse.Namespace, tool_names: list[str]) -> dict[str, str]:
    """Per tool: ``"live"`` if run this session, else its frozen provenance
    (``"frozen@<timestamp> (commit <sha>)"``), else ``"n/a"``.

    This is the reproducibility gap a plain accuracy table can't show: two
    reports with an identical-looking abidiff row might be one live run and
    one replaying a frozen cache from a different abicheck commit.
    """
    selected = bc._resolve_selected_tools(bc_args)
    frozen = bc._load_frozen(bc.FROZEN_COMPETITOR_PATH)
    state: dict[str, str] = {}
    for name in tool_names:
        if name in selected:
            state[name] = "live"
        elif frozen and name in frozen.get("tools", []):
            commit = str(frozen.get("git_commit", "?"))[:12]
            state[name] = f"frozen@{frozen.get('frozen_at', '?')} (commit {commit})"
        else:
            state[name] = "n/a"
    return state


def render_markdown(report: dict[str, Any], cache_state: dict[str, str]) -> str:
    """Render the reproducibility envelope + per-lane accuracy as Markdown.

    Mirrors the table shape in docs/reference/tool-comparison.md's
    "Full-catalog benchmark" section so this output can be pasted in directly
    once a run covers the full catalog with every tool.
    """
    gt_sha = (report.get("ground_truth_sha256") or "")[:12]
    commit = (report.get("git_commit") or "unknown")[:12]
    full = "yes" if report.get("full_catalog_run") else "no (partial run — see case list)"
    lines = [
        f"# Benchmark report — {report['generated_at']}",
        "",
        f"- abicheck version: `{report['abicheck_version']}`",
        f"- git commit: `{commit}`",
        f"- ground-truth sha256: `{gt_sha}`",
        f"- case count: {report['case_count']} (full-catalog run: {full})",
        f"- wall time: {report['wall_time_s']}s",
        f"- peak RSS: {report['peak_rss_mb']} MiB"
        if report.get("peak_rss_mb") is not None
        else "- peak RSS: n/a (resource module unavailable)",
        "",
        "## Tool versions",
        "",
    ]
    for name, version in report["tool_versions"].items():
        lines.append(f"- {name}: `{version or 'not found'}`")
    lines += [
        "",
        "## Accuracy (full-catalog denominator — SKIP/ERROR/TIMEOUT count as misses)",
        "",
        "| Tool | Cache state | Correct / Total | Accuracy | False positives | "
        "False negatives | Unsupported/error/timeout | Total time |",
        "|------|-------------|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]
    for name, cov in report["coverage_accuracy"].items():
        acc = report["accuracy"].get(name, {})
        status = report.get("status_counts", {}).get(name, {})
        status_str = ", ".join(f"{k}={v}" for k, v in sorted(status.items())) or "0"
        pct = f"{cov['pct']}%" if cov["pct"] is not None else "n/a"
        total_ms = acc.get("total_ms")
        total_s = f"{total_ms / 1000:.1f}s" if total_ms is not None else "n/a"
        lines.append(
            f"| {cov['label']} | {cache_state.get(name, 'n/a')} | "
            f"{cov['correct']} / {cov['total']} | {pct} | "
            f"{cov['false_positives']} | {cov['false_negatives']} | {status_str} | {total_s} |"
        )
    return "\n".join(lines) + "\n"


def parse_doc_table(text: str) -> dict[str, Any] | None:
    """Parse the committed 'Full-catalog benchmark' heading + table.

    Returns ``None`` if the heading anchor can't be found at all (wording
    changed — the caller should treat that as "doc drift check is blind"
    rather than silently reporting a clean match).
    """
    heading = DOC_HEADING_RE.search(text)
    if heading is None:
        return None
    label_to_tool = {_clean_label(v): k for k, v in LANE_DOC_LABELS.items()}
    rows: dict[str, dict[str, Any]] = {}
    in_table = False
    for line in text[heading.end():].splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        label = _clean_label(cells[0])
        tool = label_to_tool.get(label)
        if tool is None:
            continue
        in_table = True
        m_correct = re.match(r"(\d+)", _clean_label(cells[1]))
        m_pct = re.match(r"([\d.]+)%", _clean_label(cells[2]))
        m_fp = re.match(r"(\d+)", _clean_label(cells[3]))
        m_fn = re.match(r"(\d+)", cells[4].strip())
        if not (m_correct and m_pct and m_fp and m_fn):
            continue
        rows[tool] = {
            "correct": int(m_correct.group(1)),
            "pct": float(m_pct.group(1)),
            "false_positives": int(m_fp.group(1)),
            "false_negatives": int(m_fn.group(1)),
        }
    return {
        "date": heading.group("date").strip(),
        "case_count": int(heading.group("count")),
        "rows": rows,
    }


def diff_against_doc(report: dict[str, Any], doc_table: dict[str, Any] | None) -> list[str]:
    """Human-readable drift lines; empty means the doc matches this report."""
    if doc_table is None:
        return [
            f"could not find the 'Full-catalog benchmark (<date>, all N cases)' "
            f"heading in {DOC_PATH.relative_to(REPO_DIR)} — wording changed? "
            "update DOC_HEADING_RE in this script."
        ]
    drift: list[str] = []
    gt_count = len(bc._gt_data["verdicts"])
    if doc_table["case_count"] != gt_count:
        drift.append(
            f"doc heading says 'all {doc_table['case_count']} cases' but "
            f"examples/ground_truth.json currently has {gt_count} cases"
        )

    if not report.get("full_catalog_run"):
        print(
            "NOTE: this was a partial run (--cases and/or --suite pinned74) — "
            "only the heading case-count was checked, not per-lane numbers. "
            "Run the full catalog with every tool for a complete check.",
            file=sys.stderr,
        )
        return drift

    cov = report["coverage_accuracy"]
    skipped = [name for name in doc_table["rows"] if name not in cov]
    if skipped:
        print(
            f"NOTE: not verified this run (no fresh or frozen data for): {', '.join(skipped)}",
            file=sys.stderr,
        )
    for tool_name, doc_row in doc_table["rows"].items():
        if tool_name not in cov:
            continue
        fresh = cov[tool_name]
        label = LANE_DOC_LABELS.get(tool_name, tool_name)
        if fresh["correct"] != doc_row["correct"]:
            drift.append(
                f"{label}: doc says {doc_row['correct']}/{doc_table['case_count']} correct, "
                f"generated report says {fresh['correct']}/{fresh['total']}"
            )
        if fresh["pct"] is not None and abs(fresh["pct"] - doc_row["pct"]) >= 0.1:
            drift.append(
                f"{label}: doc says {doc_row['pct']}% accuracy, "
                f"generated report says {fresh['pct']}%"
            )
        if fresh["false_positives"] != doc_row["false_positives"]:
            drift.append(
                f"{label}: doc says {doc_row['false_positives']} false positives, "
                f"generated report says {fresh['false_positives']}"
            )
        if fresh["false_negatives"] != doc_row["false_negatives"]:
            drift.append(
                f"{label}: doc says {doc_row['false_negatives']} false negatives, "
                f"generated report says {fresh['false_negatives']}"
            )
    return drift


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a reproducible benchmark report, optionally checked "
        "against docs/reference/tool-comparison.md.",
    )
    p.add_argument("--tools", nargs="+", metavar="TOOL",
                   choices=["abicheck", "abicheck_full",
                            "abidiff", "abidiff_headers", "abicc_dumper", "abicc_xml"],
                   help="Run only selected tools (default: all — see benchmark_comparison.py).")
    p.add_argument("--cases", nargs="+", metavar="CASE",
                   help="Run only these case prefixes (e.g. case09 case16). Disables the "
                        "full-catalog numeric doc check (see --check).")
    p.add_argument("--suite", choices=["all", "pinned74"], default="all",
                   help="Case suite to run (default: all).")
    p.add_argument("--skip-abicc", action="store_true", help="Skip ABICC entirely.")
    p.add_argument("--timeout", type=int, default=None,
                   help="Per-tool-call timeout override (passed through to benchmark_comparison.py).")
    p.add_argument("--freeze", nargs="+", metavar="TOOL",
                   help="Persist named tools' results to the frozen-competitor cache "
                        "(see benchmark_comparison.py --freeze).")
    p.add_argument("--no-frozen", action="store_true",
                   help="Don't merge in previously-frozen competitor data.")
    p.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT,
                   help=f"Where to write the JSON report (default: {DEFAULT_JSON_OUT}).")
    p.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN_OUT,
                   help=f"Where to write the Markdown report (default: {DEFAULT_MARKDOWN_OUT}).")
    p.add_argument("--check", action="store_true",
                   help="After generating the report, diff it against the committed "
                        "table in docs/reference/tool-comparison.md and exit 1 on drift. "
                        "The heading case-count is always checked; per-lane numeric rows "
                        "are only checked on a full-catalog run (no --cases/pinned74).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    bc_args = bc.parse_args([])
    bc_args.tools = args.tools
    bc_args.cases = args.cases
    bc_args.suite = args.suite
    bc_args.skip_abicc = args.skip_abicc
    bc_args.freeze = args.freeze
    bc_args.no_frozen = args.no_frozen
    if args.timeout is not None:
        bc_args.timeout = args.timeout

    rss_before = _peak_rss_mb() or 0.0
    t0 = time.monotonic()
    results, active_tools, _selected = bc.run_suite(bc_args)
    wall_s = time.monotonic() - t0
    rss_after = _peak_rss_mb() or 0.0

    report = bc._collect_metadata(results, active_tools, bc_args.suite)
    report["wall_time_s"] = round(wall_s, 1)
    report["peak_rss_mb"] = max(rss_before, rss_after) if resource is not None else None
    report["full_catalog_run"] = bc_args.suite == "all" and not bc_args.cases
    report["case_names"] = [r["case"] for r in results]
    report["status_counts"] = {
        t.name: _status_counts(results, t.name) for t in active_tools
    }
    cache_state = cache_state_for(bc_args, [t.name for t in active_tools])
    report["cache_state"] = cache_state

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2))
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(report, cache_state)
    args.markdown_out.write_text(markdown)

    print(f"\n  JSON:     {args.json_out}")
    print(f"  Markdown: {args.markdown_out}\n")

    if not args.check:
        return 0

    doc_text = DOC_PATH.read_text() if DOC_PATH.is_file() else ""
    doc_table = parse_doc_table(doc_text)
    drift = diff_against_doc(report, doc_table)
    if drift:
        print("BENCHMARK DOC DRIFT DETECTED:", file=sys.stderr)
        for line in drift:
            print(f"  - {line}", file=sys.stderr)
        print(
            f"\nRegenerate {DOC_PATH.relative_to(REPO_DIR)}'s table from "
            f"{args.markdown_out.relative_to(REPO_DIR)} (full-catalog run required "
            "for the numeric rows).",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {DOC_PATH.relative_to(REPO_DIR)} matches the generated report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

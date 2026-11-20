"""Summaries of a ``run.competitors`` output directory: CSV, JSON, Markdown, SVG.

``python -m abiforge.run.competitor_report OUT`` rewrites ``pairs.csv``,
``summary.json``, ``table.md`` and ``figure.svg`` from ``pairs.jsonl``. Every
row of the table carries its denominator: the pairs that mode was asked to
judge in that configuration. Change classes (operators), unique pairs
(operator x seed x configuration) and runs (pair x mode) are counted apart.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from abiforge.run.competitor_score import CLAIMS, DECISION_RANK, OUTCOMES

MODE_LABEL = {
    "abicheck": "abicheck compare",
    "abidiff": "libabigail abidiff",
    "abicc-dump": "ABI Dumper + ABICC",
    "abicc-xml": "ABICC (XML descriptors)",
}


def load(out: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (out / "pairs.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def coverage(rows: list[dict]) -> dict[str, object]:
    from abiforge.model.edit_model import EDIT_MODEL
    from abiforge.operators import OPERATORS, matrix

    ran = [r for r in rows if r.get("status") == "ran"]
    ops_ran = {r["op"] for r in ran if r["op"] != "control_identity"}
    per_mode: dict[str, dict[str, int]] = {}
    for r in ran:
        for mode, m in r["modes"].items():
            pm = per_mode.setdefault(
                mode, {"classes": set(), "pairs": 0, "runs_answered": 0}
            )  # type: ignore[dict-item]
            pm["pairs"] += 1
            if m["decision_outcome"] not in ("error", "timeout", "unsupported"):
                pm["runs_answered"] += 1
                if r["op"] != "control_identity":
                    pm["classes"].add(r["op"])  # type: ignore[union-attr]
    return {
        "registry": {
            "operators_declared": len(OPERATORS),
            "edit_model_constructs": len(EDIT_MODEL),
            "edit_model_classes": sum(len(v) for v in EDIT_MODEL.values()),
            "operator_seed_pairs_declared": len(matrix()),
        },
        "this_run": {
            "cases_selected": len({r["case"] for r in rows}),
            "operators_selected": len({r["op"] for r in rows} - {"control_identity"}),
            "pairs_selected": len(rows),
            "pairs_infeasible": sum(
                str(r.get("status", "")).startswith("infeasible") for r in rows
            ),
            "pairs_build_failed": sum(
                str(r.get("status", "")).startswith("build_failed") for r in rows
            ),
            "pairs_built": len(ran),
            "operators_built": len(ops_ran),
            "pairs_with_independent_expectation": sum(
                1 for r in ran if r.get("expected")
            ),
            "pairs_with_detail_expectation": sum(
                1 for r in ran if r.get("details_expected", {}).get("sources")
            ),
        },
        "per_mode": {
            m: {
                "operators_answered": len(v["classes"]),
                "pairs_run": v["pairs"],
                "pairs_answered": v["runs_answered"],
            }  # type: ignore[arg-type]
            for m, v in sorted(per_mode.items())
        },
    }


def summarize(rows: list[dict]) -> dict[str, object]:
    table: dict[str, dict] = {}
    ran = [r for r in rows if r.get("status") == "ran"]
    for r in ran:
        control = r["expected_class"] == "unchanged"
        for mode, m in r["modes"].items():
            key = f"{r['config']}|{mode}"
            t = table.setdefault(
                key,
                {
                    "config": r["config"],
                    "mode": mode,
                    "pairs": 0,
                    "outcomes": Counter(),
                    "by_class": defaultdict(Counter),
                    "controls": 0,
                    "control_false_positive": 0,
                    "control_advisory": 0,
                    "details": {c: Counter() for c in CLAIMS},
                    "seconds": 0.0,
                },
            )
            t["pairs"] += 1
            t["outcomes"][m["decision_outcome"]] += 1
            t["by_class"][r["expected_class"]][m["decision_outcome"]] += 1
            t["seconds"] += m["run"]["seconds"]
            for c, v in m["details"].items():
                t["details"][c][v] += 1
            if control:
                t["controls"] += 1
                dec = m["observed"]["decision"]
                if dec and DECISION_RANK[dec] >= DECISION_RANK["API_BREAK"]:
                    t["control_false_positive"] += 1
                elif dec == "ADVISORY":
                    t["control_advisory"] += 1
    for t in table.values():
        t["outcomes"] = {o: t["outcomes"].get(o, 0) for o in OUTCOMES}
        t["by_class"] = {k: dict(v) for k, v in sorted(t["by_class"].items())}
        t["details"] = {c: dict(v) for c, v in t["details"].items()}
        t["seconds"] = round(t["seconds"], 2)
    return {"coverage": coverage(rows), "table": list(table.values())}


def write_csv(rows: list[dict], path: Path) -> None:
    cols = [
        "pair_id",
        "op",
        "seed",
        "config",
        "expected_class",
        "verdict_min",
        "verdict_max",
        "status",
        "mode",
        "decision",
        "decision_outcome",
        *[f"detail_{c}" for c in CLAIMS],
        "exit_codes",
        "seconds",
        "old_so_sha256",
        "new_so_sha256",
        "old_debug_sections",
        "new_debug_sections",
        "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            base = {
                "pair_id": r["pair_id"],
                "op": r["op"],
                "seed": r["seed"],
                "config": r["config"],
                "expected_class": r["expected_class"],
                "verdict_min": r["expected"]["verdict_min"],
                "verdict_max": r["expected"]["verdict_max"],
                "status": r.get("status"),
            }
            if r.get("status") != "ran":
                w.writerow(base)
                continue
            inp = r["inputs"]
            base |= {
                "old_so_sha256": inp["old"]["so_sha256"],
                "new_so_sha256": inp["new"]["so_sha256"],
                "old_debug_sections": inp["old"]["debug_sections"],
                "new_debug_sections": inp["new"]["debug_sections"],
            }
            for mode, m in r["modes"].items():
                w.writerow(
                    base
                    | {
                        "mode": mode,
                        "decision": m["observed"]["decision"],
                        "decision_outcome": m["decision_outcome"],
                        **{f"detail_{c}": v for c, v in m["details"].items()},
                        "exit_codes": " ".join(
                            str(s["exit"]) for s in m["run"]["steps"]
                        ),
                        "seconds": m["run"]["seconds"],
                        "reason": m["observed"]["reason"][:200],
                    }
                )


def markdown(summary: dict, meta: dict) -> str:
    cov = summary["coverage"]
    lines = [f"# Tool comparison: {meta.get('select')} selection", ""]
    reg, run = cov["registry"], cov["this_run"]
    lines += [
        "## Coverage (generator registry vs this run)",
        "",
        "| Count | Value |",
        "|---|---|",
        f"| change classes declared (operators in the registry) | {reg['operators_declared']} |",
        f"| edit-model classes (language model, {reg['edit_model_constructs']} constructs) | {reg['edit_model_classes']} |",
        f"| operator x seed pairs declared | {reg['operator_seed_pairs_declared']} |",
        f"| operators selected / built | {run['operators_selected']} / {run['operators_built']} |",
        f"| unique pairs selected (case x config) | {run['pairs_selected']} |",
        f"| pairs infeasible / build failed / built | {run['pairs_infeasible']} / {run['pairs_build_failed']} / {run['pairs_built']} |",
        f"| pairs with independent expected decision | {run['pairs_with_independent_expectation']} |",
        f"| pairs with expected explanation details | {run['pairs_with_detail_expectation']} |",
        "",
        "| Mode | pairs run | pairs answered | operators answered |",
        "|---|---|---|---|",
    ]
    for m, v in cov["per_mode"].items():
        lines.append(
            f"| {MODE_LABEL.get(m, m)} | {v['pairs_run']} | {v['pairs_answered']} | {v['operators_answered']} |"
        )
    lines += [
        "",
        "## Maintainer decision vs independent expectation",
        "",
        "Denominator n = built pairs that mode was run on in that configuration.",
        "",
        "| Config | Mode | n | correct | wrong | advisory | no signal | unsupported | error | timeout | controls | FP on controls | advisory on controls |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in summary["table"]:
        o = t["outcomes"]
        lines.append(
            f"| {t['config']} | {MODE_LABEL.get(t['mode'], t['mode'])} | {t['pairs']} | {o['correct']} | {o['wrong']} | "
            f"{o['advisory']} | {o['no_signal']} | {o['unsupported']} | {o['error']} | {o['timeout']} | "
            f"{t['controls']} | {t['control_false_positive']} | {t['control_advisory']} |"
        )
    lines += [
        "",
        "## Explanation details (match / applicable; `unsup` = format cannot express)",
        "",
        "| Config | Mode | " + " | ".join(CLAIMS) + " |",
        "|---|---|" + "---|" * len(CLAIMS),
    ]
    for t in summary["table"]:
        cells = []
        for c in CLAIMS:
            d = t["details"][c]
            n = sum(v for k, v in d.items() if k != "n/a")
            cells.append(
                "—"
                if not n
                else "unsup"
                if d.get("unsupported") == n
                else f"{d.get('match', 0)}/{n}"
            )
        lines.append(
            f"| {t['config']} | {MODE_LABEL.get(t['mode'], t['mode'])} | "
            + " | ".join(cells)
            + " |"
        )
    lines += [
        "",
        "## Pair-by-pair agreement between modes (equal totals are not equal answers)",
        "",
        "| Config | Mode A | Mode B | pairs | same decision | same outcome | same details | both correct |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g in summary.get("agreement", []):
        lines.append(
            f"| {g['config']} | {MODE_LABEL.get(g['a'], g['a'])} | {MODE_LABEL.get(g['b'], g['b'])} | {g['pairs']} | "
            f"{g.get('same_decision', 0)} | {g.get('same_outcome', 0)} | {g.get('same_details', 0)} | {g.get('both_correct', 0)} |"
        )
    env = meta.get("environment", {})
    lines += ["", "Environment: " + ", ".join(f"{k} {v}" for k, v in env.items()), ""]
    return "\n".join(lines)


def svg(summary: dict, title: str) -> str:
    """A stacked horizontal bar per (config, mode): decision outcomes over n pairs."""
    colors = {
        "correct": "#2e7d32",
        "wrong": "#c62828",
        "advisory": "#f9a825",
        "no_signal": "#9e9e9e",
        "unsupported": "#5c6bc0",
        "error": "#6d4c41",
        "timeout": "#000000",
    }
    rows = summary["table"]
    w, bar_w, left, h = 860, 520, 300, 30
    height = 70 + h * len(rows) + 60
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{height}" font-family="sans-serif" font-size="12">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="10" y="22" font-size="14" font-weight="bold">{title}</text>',
    ]
    for i, t in enumerate(rows):
        y = 45 + i * h
        out.append(
            f'<text x="{left - 8}" y="{y + 16}" text-anchor="end">{t["config"]} · {MODE_LABEL.get(t["mode"], t["mode"])} (n={t["pairs"]})</text>'
        )
        x = float(left)
        for o in OUTCOMES:
            n = t["outcomes"][o]
            if not n:
                continue
            ww = bar_w * n / max(t["pairs"], 1)
            out.append(
                f'<rect x="{x:.1f}" y="{y}" width="{ww:.1f}" height="{h - 8}" fill="{colors[o]}"><title>{o}: {n}</title></rect>'
            )
            if ww > 16:
                out.append(
                    f'<text x="{x + ww / 2:.1f}" y="{y + 16}" fill="white" text-anchor="middle">{n}</text>'
                )
            x += ww
    ly = 55 + h * len(rows)
    for j, o in enumerate(OUTCOMES):
        out.append(
            f'<rect x="{10 + j * 120}" y="{ly}" width="12" height="12" fill="{colors[o]}"/>'
            f'<text x="{26 + j * 120}" y="{ly + 11}">{o.replace("_", " ")}</text>'
        )
    out.append("</svg>")
    return "\n".join(out)


def agreement(rows: list[dict]) -> list[dict]:
    """Per configuration and mode pair: do equal totals hide different answers?

    Counts, over pairs both modes ran on, how often the decision, the decision
    outcome and the whole detail vector are identical pair by pair.
    """
    from itertools import combinations

    acc: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for r in rows:
        if r.get("status") != "ran":
            continue
        for a, b in combinations(sorted(r["modes"]), 2):
            ma, mb = r["modes"][a], r["modes"][b]
            c = acc[r["config"], a, b]
            c["pairs"] += 1
            c["same_decision"] += (
                ma["observed"]["decision"] == mb["observed"]["decision"]
            )
            c["same_outcome"] += ma["decision_outcome"] == mb["decision_outcome"]
            c["same_details"] += ma["details"] == mb["details"]
            c["both_correct"] += (
                ma["decision_outcome"] == mb["decision_outcome"] == "correct"
            )
    return [
        {"config": k[0], "a": k[1], "b": k[2], **dict(v)}
        for k, v in sorted(acc.items())
    ]


def divergences(rows: list[dict]) -> str:
    """Every pair a mode did not answer correctly: id, expectation, report excerpt."""
    lines = [
        "# Divergences from the independent expectation",
        "",
        "One entry per (pair, mode) whose decision outcome is not `correct`, or whose",
        "explanation missed an expected detail. Excerpts are the first lines of the raw report.",
        "",
    ]
    for r in rows:
        if r.get("status") != "ran":
            lines.append(f"- `{r['pair_id']}`: not run ({r.get('status')})")
            continue
        exp = r["expected"]
        for mode, m in sorted(r["modes"].items()):
            missed = [c for c, v in m["details"].items() if v == "miss"]
            if m["decision_outcome"] == "correct" and not missed:
                continue
            ob = m["observed"]
            lines.append(f"## `{r['pair_id']}` · {MODE_LABEL.get(mode, mode)}")
            lines.append(
                f"- expected: {exp['verdict_min']}..{exp['verdict_max'] or 'BREAKING'} "
                f"({r['expected_class']}; {exp['source']})"
            )
            lines.append(
                f"- tool decision: {ob['decision']} → **{m['decision_outcome']}**"
                + (f"; sides {ob['sides']}" if ob["sides"] else "")
                + (
                    f"; exit bits {[k for k, v in ob['status_bits'].items() if v]}"
                    if ob["status_bits"]
                    else ""
                )
            )
            if missed:
                de = r["details_expected"]
                lines.append(
                    f"- missed details: {missed} (expected { ({c: de.get({'entity': 'entities', 'offsets': 'offsets_moved'}.get(c, c)) for c in missed}) })"
                )
            if ob["reason"]:
                lines.append(f"- reason: {ob['reason'][:300]}")
            if ob["excerpt"]:
                lines.append("```text")
                lines += [e[:160] for e in ob["excerpt"][:8]]
                lines.append("```")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(out: Path) -> dict:
    rows = load(out)
    meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    summary = summarize(rows)
    summary["agreement"] = agreement(rows)
    summary["meta"] = {
        k: meta[k]
        for k in ("select", "modes", "configs", "compiler", "environment", "pilot_rule")
        if k in meta
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8"
    )
    write_csv(rows, out / "pairs.csv")
    (out / "table.md").write_text(markdown(summary, meta), encoding="utf-8")
    (out / "divergences.md").write_text(divergences(rows), encoding="utf-8")
    n_cases = summary["coverage"]["this_run"]["cases_selected"]
    (out / "figure.svg").write_text(
        svg(
            summary,
            f"Decision outcomes per pair, abiforge {meta.get('select')} selection ({n_cases} cases)",
        ),
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    write_reports(Path(sys.argv[1]).resolve())

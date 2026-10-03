#!/usr/bin/env python3
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

"""H6 / family F6: the real-library compatibility corpus gate.

Runs ``abicheck compare`` over every pair in ``data/compat_corpus.json`` (real
conda-forge release pairs with cited ground truth), writes one JSON result per
pair, and gates the run:

* a known-**compatible** pair fails on any BREAKING or API_BREAK finding
  that compatibility policy actually scored. A finding the report marks
  ``compatibility_evaluation_status: NOT_EVALUATED`` (contract relevance
  unproven, ``gate_contribution`` 0) is still counted, under
  ``<kind> (not evaluated)``, but as a non-breaking count: re-deriving a
  break from the kind alone overrules the product's own decision;
* a known-**incompatible** pair fails unless a BREAKING finding is reported
  (and every documented ``expected_break_kinds`` entry appears);
* a pair that could not be evaluated fails -- a run that compared nothing is
  never a clean pass;
* against ``data/compat_corpus_baseline.json`` (per-pair, per-kind counts of
  the *non-breaking* findings), a count moving by more than the tolerance
  fails, so a flood such as 500 new ``exported_not_public`` findings is
  visible even though none of them is BREAKING. With no baseline the drift
  half is report-only.

The gate (:func:`evaluate_gate`) is pure so ``tests/test_family_f6_corpus.py``
exercises it offline; only :func:`run_pair` touches the network.

Usage::

    python run_compat_corpus.py --out-dir corpus-results [--only T3_tbb_patch]
    python run_compat_corpus.py --out-dir corpus-results --write-baseline
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
VALID_DIR = SCRIPTS_DIR.parent
DEFAULT_CORPUS = VALID_DIR / "data" / "compat_corpus.json"
DEFAULT_BASELINE = VALID_DIR / "data" / "compat_corpus_baseline.json"
CONDA_FILE_URL = "https://conda.anaconda.org/conda-forge/{subdir}/{file}"

CORPUS_SCHEMA = "compat_corpus.v1"
BASELINE_SCHEMA = "compat_corpus_baseline.v1"
RESULT_SCHEMA = "compat_corpus_result.v1"
EXPECTATIONS = ("COMPATIBLE", "BREAKING")

#: A finding policy did not score (ADR-049 D9): recorded, never a break.
NOT_EVALUATED = "NOT_EVALUATED"
NOT_EVALUATED_SUFFIX = " (not evaluated)"
REQUIRED_PAIR_FIELDS = (
    "pair",
    "library",
    "pkg",
    "old_ver",
    "new_ver",
    "old_file",
    "new_file",
    "expected",
    "expected_break_kinds",
    "ground_truth_source",
)

DEFAULT_REL_TOLERANCE = 0.10
DEFAULT_ABS_TOLERANCE = 5


# --------------------------------------------------------------------------
# Corpus / baseline loading
# --------------------------------------------------------------------------


def validate_corpus(doc: Any) -> list[str]:
    """Return every schema problem in a corpus document (empty = valid)."""
    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("schema") != CORPUS_SCHEMA:
        return [f"corpus schema must be {CORPUS_SCHEMA!r}"]
    pairs = doc.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return ["corpus has no pairs"]
    seen: set[str] = set()
    for i, p in enumerate(pairs):
        name = p.get("pair", f"#{i}") if isinstance(p, dict) else f"#{i}"
        if not isinstance(p, dict):
            errors.append(f"{name}: not an object")
            continue
        for key in REQUIRED_PAIR_FIELDS:
            if key not in p:
                errors.append(f"{name}: missing {key}")
        if p.get("expected") not in EXPECTATIONS:
            errors.append(f"{name}: expected must be one of {EXPECTATIONS}")
        src = p.get("ground_truth_source")
        if not isinstance(src, str) or not src.strip():
            errors.append(f"{name}: ground_truth_source must cite a source")
        kinds = p.get("expected_break_kinds")
        if not isinstance(kinds, list):
            errors.append(f"{name}: expected_break_kinds must be a list")
        elif kinds and p.get("expected") != "BREAKING":
            errors.append(f"{name}: expected_break_kinds on a COMPATIBLE pair")
        rules = p.get("suppressions", [])
        if not isinstance(rules, list):
            errors.append(f"{name}: suppressions must be a list")
            rules = []
        for i_rule, rule in enumerate(rules):
            if not isinstance(rule, dict) or not all(
                isinstance(rule.get(k), str) and rule.get(k, "").strip()
                for k in ("reason", "source")
            ):
                errors.append(
                    f"{name}: suppressions[{i_rule}] needs a reason and a cited source"
                )
        if name in seen:
            errors.append(f"{name}: duplicate pair id")
        seen.add(name)
    return errors


def load_corpus(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_corpus(doc)
    if errors:
        raise ValueError("invalid corpus:\n  " + "\n  ".join(errors))
    return list(doc["pairs"])


def load_baseline(path: Path) -> dict[str, dict[str, int]] | None:
    """Per-pair non-breaking counts, or ``None`` when no baseline is recorded."""
    if not path.is_file():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != BASELINE_SCHEMA:
        raise ValueError(f"baseline schema must be {BASELINE_SCHEMA!r}")
    return {k: dict(v) for k, v in doc["pairs"].items()}


# --------------------------------------------------------------------------
# Classification of a compare report
# --------------------------------------------------------------------------


def _kind_category(kind: str) -> str:
    """``breaking`` / ``api_break`` / ``other`` for a ChangeKind value.

    Uses the registry-derived partitions rather than the report's own
    severity strings, so the gate's notion of "a break" is the product's.
    """
    from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind

    try:
        k = ChangeKind(kind)
    except ValueError:
        return "other"
    if k in BREAKING_KINDS:
        return "breaking"
    if k in API_BREAK_KINDS:
        return "api_break"
    return "other"


def summarize_report(report: dict) -> dict:
    """Reduce one ``abicheck compare`` JSON to verdict + counts by kind."""
    changes = report.get("changes") or report.get("findings") or []
    counts: dict[str, int] = {}
    for c in changes:
        if isinstance(c, dict) and isinstance(c.get("kind"), str):
            key = c["kind"]
            if c.get("compatibility_evaluation_status") == NOT_EVALUATED:
                key += NOT_EVALUATED_SUFFIX
            counts[key] = counts.get(key, 0) + 1
    verdict = report.get("verdict") or (report.get("summary") or {}).get("verdict")
    out: dict[str, Any] = {
        "verdict": verdict,
        "counts_by_kind": dict(sorted(counts.items())),
    }
    if not verdict:
        # abicheck reached no verdict: a refused comparison (``not_comparable``
        # -- e.g. a scope_fingerprint mismatch) writes a report with no
        # changes at all. Read as a result, that is "compared, found
        # nothing" -- a clean pass for a known-compatible pair, on a run
        # that compared nothing. It is that library's error instead, so the
        # pair reads "not evaluated" with abicheck's own reason.
        reason = report.get("reason") or {}
        outcome = (report.get("run_outcome") or {}).get("operational")
        kind = reason.get("kind") or outcome or "no verdict"
        out["error"] = f"abicheck reached no verdict ({kind})"
    return out


def pair_totals(result: dict) -> dict:
    """Aggregate a pair result's libraries into gate-relevant totals."""
    breaking = api_break = 0
    non_breaking: dict[str, int] = {}
    kinds_seen: set[str] = set()
    for lib in (result.get("libraries") or {}).values():
        for kind, n in (lib.get("counts_by_kind") or {}).items():
            kinds_seen.add(kind)
            cat = _kind_category(kind)
            if cat == "breaking":
                breaking += n
            elif cat == "api_break":
                api_break += n
            else:
                non_breaking[kind] = non_breaking.get(kind, 0) + n
    return {
        "breaking": breaking,
        "api_break": api_break,
        "non_breaking": dict(sorted(non_breaking.items())),
        "kinds_seen": sorted(kinds_seen),
    }


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass
class GateOutcome:
    failures: list[str] = field(default_factory=list)
    drift_notes: list[str] = field(default_factory=list)
    baseline_present: bool = False

    @property
    def passed(self) -> bool:
        return not self.failures


def _drift(
    old: dict[str, int], new: dict[str, int], rel: float, abs_: int
) -> list[str]:
    notes = []
    for kind in sorted(set(old) | set(new)):
        a, b = old.get(kind, 0), new.get(kind, 0)
        if abs(b - a) > max(abs_, rel * a):
            notes.append(f"{kind}: {a} -> {b}")
    return notes


def evaluate_gate(
    corpus: list[dict],
    results: dict[str, dict],
    baseline: dict[str, dict[str, int]] | None,
    *,
    rel_tolerance: float = DEFAULT_REL_TOLERANCE,
    abs_tolerance: int = DEFAULT_ABS_TOLERANCE,
) -> GateOutcome:
    """Judge per-pair ``results`` (keyed by pair id) against ground truth."""
    out = GateOutcome(baseline_present=baseline is not None)
    for entry in corpus:
        pid = entry["pair"]
        res = results.get(pid)
        if res is None or res.get("error") or not res.get("libraries"):
            why = (res or {}).get("error") or "no library was compared"
            out.failures.append(f"{pid}: not evaluated ({why})")
            continue
        tot = pair_totals(res)
        if entry["expected"] == "COMPATIBLE":
            if tot["breaking"] or tot["api_break"]:
                out.failures.append(
                    f"{pid}: known-compatible pair reported "
                    f"{tot['breaking']} BREAKING / {tot['api_break']} API_BREAK findings"
                )
        else:
            if not tot["breaking"]:
                out.failures.append(
                    f"{pid}: known-incompatible pair reported no BREAKING finding"
                )
            missing = sorted(
                set(entry["expected_break_kinds"]) - set(tot["kinds_seen"])
            )
            if missing:
                out.failures.append(f"{pid}: documented break kinds absent: {missing}")
        if baseline is not None:
            if pid not in baseline:
                out.drift_notes.append(f"{pid}: no baseline entry (report-only)")
                continue
            notes = _drift(
                baseline[pid], tot["non_breaking"], rel_tolerance, abs_tolerance
            )
            for n in notes:
                out.failures.append(f"{pid}: non-breaking count drift {n}")
    return out


def baseline_from_results(results: dict[str, dict]) -> dict:
    return {
        "schema": BASELINE_SCHEMA,
        "pairs": {
            pid: pair_totals(r)["non_breaking"]
            for pid, r in sorted(results.items())
            if not r.get("error") and r.get("libraries")
        },
    }


def render_summary(
    corpus: list[dict], results: dict[str, dict], gate: GateOutcome
) -> str:
    lines = [
        "## Real-library compatibility corpus (F6)",
        "",
        f"Gate: **{'PASS' if gate.passed else 'FAIL'}** -- baseline "
        + ("present" if gate.baseline_present else "absent (drift is report-only)"),
        "",
        "| pair | expected | BREAKING | API_BREAK | other findings |",
        "|---|---|---|---|---|",
    ]
    for e in corpus:
        r = results.get(e["pair"]) or {}
        if r.get("error") or not r.get("libraries"):
            lines.append(f"| {e['pair']} | {e['expected']} | - | - | not evaluated |")
            continue
        t = pair_totals(r)
        lines.append(
            f"| {e['pair']} | {e['expected']} | {t['breaking']} | {t['api_break']} "
            f"| {sum(t['non_breaking'].values())} |"
        )
    if gate.failures:
        lines += ["", "### Failures", ""] + [f"- {f}" for f in gate.failures]
    if gate.drift_notes:
        lines += ["", "### Notes", ""] + [f"- {n}" for n in gate.drift_notes]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Network half
# --------------------------------------------------------------------------


def evidence_args(entry: dict, work: Path) -> list[str]:
    """The evidence a maintainer would supply for this pair.

    Each package's own public headers (its ``include/`` tree) with
    ``--contract public`` when both sides ship one -- the realistic workflow,
    and the one that can tell an accidental export from a declared API --
    plus the pair's cited suppressions, written to a suppression file.
    """
    args: list[str] = []
    old_inc, new_inc = work / "old" / "include", work / "new" / "include"
    if old_inc.is_dir() and new_inc.is_dir():
        args += [
            "--header",
            f"old={old_inc}",
            "--header",
            f"new={new_inc}",
            "--contract",
            "public",
        ]
    rules = entry.get("suppressions") or []
    if rules:
        sup = work / "suppressions.yaml"
        sup.write_text(
            json.dumps(
                {
                    "version": 1,
                    "suppressions": [
                        {k: v for k, v in r.items() if k != "source"} for r in rules
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        args += ["--suppress", str(sup)]
    return args


def run_pair(
    entry: dict,
    work: Path,
    subdir: str = "linux-64",
    *,
    compare_timeout: float | None = None,
) -> dict:
    """Fetch, extract and compare every shared object common to both builds.

    *compare_timeout* bounds each ``abicheck compare`` (seconds). A compare
    that exceeds it is recorded as that library's error -- so the pair reads
    "not evaluated" and the gate fails on it -- rather than consuming the
    whole job: the first header-aware run spent 93 minutes on one protobuf
    pair and was cancelled before six other pairs started.
    """
    sys.path.insert(0, str(SCRIPTS_DIR))
    import conda_harness as ch

    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "pair": entry["pair"],
        "expected": entry["expected"],
        "libraries": {},
    }
    try:
        sides = {}
        for side in ("old", "new"):
            fname = entry[f"{side}_file"]
            pkg = work / f"{side}_{fname}"
            ch.fetch_file(
                CONDA_FILE_URL.format(subdir=subdir, file=fname), pkg, timeout=300
            )
            sides[side] = ch.extract_sos(pkg, work / side)
    except Exception as exc:  # noqa: BLE001 -- any fetch/extract failure is "not evaluated"
        result["error"] = f"fetch/extract failed: {exc}"
        return result
    extra = evidence_args(entry, work)
    result["evidence"] = list(extra)
    for name in sorted(set(sides["old"]) & set(sides["new"])):
        started = time.monotonic()
        try:
            report = ch.run_abicheck(
                sides["old"][name],
                sides["new"][name],
                entry["old_ver"],
                entry["new_ver"],
                extra,
                timeout=compare_timeout,
            )
        except subprocess.TimeoutExpired:
            result["libraries"][name] = {
                "verdict": None,
                "counts_by_kind": {},
                "error": f"compare timed out after {compare_timeout:.0f}s",
            }
            continue
        lib = (
            summarize_report(report)
            if report is not None
            else {"verdict": None, "counts_by_kind": {}, "error": "no report"}
        )
        lib["compare_s"] = round(time.monotonic() - started, 1)
        result["libraries"][name] = lib
    errors = sorted(
        {lib["error"] for lib in result["libraries"].values() if lib.get("error")}
    )
    if errors:
        result["error"] = "; ".join(errors)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument(
        "--only", action="append", default=[], help="run only this pair id (repeatable)"
    )
    ap.add_argument(
        "--library",
        action="append",
        default=[],
        help="run only the pairs of this corpus `library` (repeatable); the "
        "workflow's per-library matrix uses this",
    )
    ap.add_argument(
        "--compare-timeout",
        type=float,
        default=None,
        help="seconds allowed per `abicheck compare`; a compare that exceeds "
        "it is recorded as that pair's error (not evaluated)",
    )
    ap.add_argument(
        "--results-dir",
        type=Path,
        help="gate existing per-pair JSON instead of running",
    )
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--rel-tolerance", type=float, default=DEFAULT_REL_TOLERANCE)
    ap.add_argument("--abs-tolerance", type=int, default=DEFAULT_ABS_TOLERANCE)
    args = ap.parse_args(argv)

    corpus = load_corpus(args.corpus)
    if args.only:
        unknown = set(args.only) - {e["pair"] for e in corpus}
        if unknown:
            ap.error(f"unknown pair(s): {sorted(unknown)}")
        corpus = [e for e in corpus if e["pair"] in args.only]
    if args.library:
        unknown = set(args.library) - {e["library"] for e in corpus}
        if unknown:
            ap.error(f"unknown library(ies): {sorted(unknown)}")
        corpus = [e for e in corpus if e["library"] in args.library]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    for entry in corpus:
        pid = entry["pair"]
        if args.results_dir:
            p = args.results_dir / f"{pid}.json"
            results[pid] = (
                json.loads(p.read_text(encoding="utf-8"))
                if p.is_file()
                else {"error": "missing result file"}
            )
        else:
            print(f"== {pid}", file=sys.stderr)
            with tempfile.TemporaryDirectory() as tmp:
                results[pid] = run_pair(
                    entry, Path(tmp), compare_timeout=args.compare_timeout
                )
        results[pid]["totals"] = pair_totals(results[pid])
        (args.out_dir / f"{pid}.json").write_text(
            json.dumps(results[pid], indent=2) + "\n", encoding="utf-8"
        )

    if args.write_baseline:
        # Merge, so a run narrowed by --only keeps every other pair's entry.
        doc = baseline_from_results(results)
        existing = load_baseline(args.baseline) or {}
        doc["pairs"] = dict(sorted({**existing, **doc["pairs"]}.items()))
        args.baseline.write_text(
            json.dumps(doc, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline written to {args.baseline}", file=sys.stderr)

    baseline = load_baseline(args.baseline)
    gate = evaluate_gate(
        corpus,
        results,
        baseline,
        rel_tolerance=args.rel_tolerance,
        abs_tolerance=args.abs_tolerance,
    )
    summary = render_summary(corpus, results, gate)
    (args.out_dir / "gate.json").write_text(
        json.dumps(
            {
                "passed": gate.passed,
                "baseline_present": gate.baseline_present,
                "failures": gate.failures,
                "notes": gate.drift_notes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(summary)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

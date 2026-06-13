#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
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

"""abicheck field-evaluation runner.

Reads ``manifest.yaml``, scans each library's version pair with the installed
``abicheck`` (binary L0/L1 tier), and writes a schema'd results file under
``results/`` plus a regenerated ``REPORT.md``. The report is GENERATED — never
hand-edit it; edit the manifest and re-run. Raw downloads/snapshots live in the
gitignored cache, not in git.

Usage:
    python eval/runner.py                 # scan all, write results/<utc>.json + REPORT.md
    python eval/runner.py --only zlib,icu # subset
    python eval/runner.py --report-only   # regenerate REPORT.md from the latest results file
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import condafetch as cf  # local helper (eval/condafetch.py)

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML required: pip install pyyaml")

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"
WORK = Path(os.environ.get("ABICHECK_EVAL_CACHE", "/tmp/abicheck-eval")).parent / "abicheck-eval"
SNAP_DIR = WORK / "snap"
RESULT_SCHEMA = 1


def _abicheck_version() -> str:
    p = subprocess.run(["abicheck", "--version"], capture_output=True, text=True)
    return (p.stdout or p.stderr).strip()


def _defined_export_funcs(so: str) -> int:
    """Count defined (non-UND) GLOBAL/WEAK FUNC dynamic symbols."""
    p = subprocess.run(
        ["bash", "-c",
         f"readelf -W --dyn-syms '{so}' 2>/dev/null | "
         f"awk '$4==\"FUNC\" && $7!=\"UND\" && ($5==\"GLOBAL\"||$5==\"WEAK\")' | wc -l"],
        capture_output=True, text=True)
    try:
        return int(p.stdout.strip() or 0)
    except ValueError:
        return 0


def _pick_so(pkg: str, ver: str, so_stem: str | None) -> str:
    arch, _, _ = cf.download(pkg, ver)
    out = f"{cf.CACHE}/ex_{pkg}_{ver}"
    cf.extract(arch, out)
    sos = cf.find_sos(out)
    if not sos:
        raise RuntimeError(f"no .so in {pkg} {ver}")
    if so_stem:
        named = [s for s in sos if os.path.basename(s).startswith(so_stem)]
        if named:
            return max(named, key=os.path.getsize)
    # else: symbol-richest by defined exports
    return max(sos, key=_defined_export_funcs)


def _run(cmd: list[str]) -> tuple[float, subprocess.CompletedProcess]:
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return round(time.time() - t0, 3), p


def scan_one(entry: dict) -> dict:
    lib = entry["lib"]
    rec: dict = {"lib": lib, "conda_pkg": entry["conda_pkg"],
                 "old": str(entry["old"]), "new": str(entry["new"]),
                 "expect": entry.get("expect")}
    try:
        t0 = time.time()
        oso = _pick_so(entry["conda_pkg"], rec["old"], entry.get("so_stem"))
        nso = _pick_so(entry["conda_pkg"], rec["new"], entry.get("so_stem"))
        rec["fetch_s"] = round(time.time() - t0, 2)
        rec["old_so"] = os.path.basename(oso)
        rec["new_so"] = os.path.basename(nso)
        # P20 guard: never compare two different libraries from a multi-.so package
        if entry.get("so_stem") is None and rec["old_so"].split(".so")[0] != rec["new_so"].split(".so")[0]:
            rec["error"] = f"different libs picked: {rec['old_so']} vs {rec['new_so']} (set so_stem)"
            return rec
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        osnap = SNAP_DIR / f"{lib}_old.json"
        nsnap = SNAP_DIR / f"{lib}_new.json"
        td1, p1 = _run(["abicheck", "dump", oso, "-o", str(osnap)])
        td2, p2 = _run(["abicheck", "dump", nso, "-o", str(nsnap)])
        if p1.returncode or p2.returncode:
            rec["error"] = "dump failed: " + (p1.stderr or p2.stderr)[-200:]
            return rec
        rec["dump_s"] = round(td1 + td2, 3)
        rec["old_funcs"] = _defined_export_funcs(oso)
        rec["new_funcs"] = _defined_export_funcs(nso)
        rec["snap_kb"] = (osnap.stat().st_size + nsnap.stat().st_size) // 1024
        tc, pc = _run(["abicheck", "compare", str(osnap), str(nsnap), "--format", "json"])
        rec["compare_s"] = tc
        rec["legacy_rc"] = pc.returncode
        d = json.loads(pc.stdout)
        rec["verdict"] = d.get("verdict")
        rec["evidence_tier"] = d.get("evidence_tier")
        s = d.get("summary", {})
        for k in ("breaking", "source_breaks", "risk_changes", "compatible_additions", "total_changes"):
            rec[k] = s.get(k)
        rec["top_kinds"] = dict(
            collections.Counter(c.get("kind") for c in d.get("changes", [])).most_common(6))
        if rec["expect"] is not None:
            rec["verdict_matches_expected"] = (rec["verdict"] == rec["expect"])
    except Exception as e:  # noqa: BLE001 - record any failure as a row
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def run(manifest: dict, only: set[str] | None) -> dict:
    rows = []
    for entry in manifest["libraries"]:
        if only and entry["lib"] not in only:
            continue
        rec = scan_one(entry)
        status = rec.get("verdict") or rec.get("error", "?")
        flag = "" if rec.get("verdict_matches_expected", True) else "  !! EXPECTED " + str(rec.get("expect"))
        print(f"  {rec['lib']:14} {status}{flag}", file=sys.stderr)
        rows.append(rec)
    return {
        "result_schema": RESULT_SCHEMA,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "abicheck_version": _abicheck_version(),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "tier": "binary-L0L1",
        "results": rows,
    }


def write_results(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = payload["generated_utc"].replace(":", "").replace("-", "")
    out = RESULTS_DIR / f"{stamp}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def render_report(payload: dict) -> str:
    rows = payload["results"]
    ok = sum(1 for r in rows if r.get("verdict_matches_expected", True) and "error" not in r)
    verdicts = collections.Counter(r.get("verdict", "ERROR") for r in rows)
    L = []
    L.append("<!-- GENERATED by eval/runner.py — do not edit by hand. Edit manifest.yaml and re-run. -->")
    L.append("# abicheck field-evaluation report (binary tier)\n")
    L.append(f"- generated: `{payload['generated_utc']}`")
    L.append(f"- abicheck: `{payload['abicheck_version']}`")
    L.append(f"- host: `{payload['host']['platform']}`, Python {payload['host']['python']}")
    L.append(f"- libraries: **{len(rows)}** | verdict matches expected: **{ok}/{len(rows)}**")
    L.append(f"- verdict distribution: " + ", ".join(f"{v}×{n}" for v, n in sorted(verdicts.items())))
    L.append("")
    L.append("| lib | old→new | so | verdict | exp? | break/risk/add | total | funcs | dump s | cmp s | tier |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            L.append(f"| {r['lib']} | {r['old']}→{r['new']} | — | ERROR | | | | | | | {r['error'][:40]} |")
            continue
        exp = "✓" if r.get("verdict_matches_expected", True) else f"✗({r.get('expect')})"
        bra = f"{r.get('breaking')}/{r.get('risk_changes')}/{r.get('compatible_additions')}"
        L.append(
            f"| {r['lib']} | {r['old']}→{r['new']} | `{r.get('old_so','')}` | {r.get('verdict')} | {exp} "
            f"| {bra} | {r.get('total_changes')} | {r.get('old_funcs')}→{r.get('new_funcs')} "
            f"| {r.get('dump_s')} | {r.get('compare_s')} | {r.get('evidence_tier')} |")
    L.append("")
    L.append("> `funcs` = defined exported FUNC dynamic symbols (not raw readelf). "
             "Verdicts/counts are abicheck output. Reproduce: `python eval/runner.py`.")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated lib names")
    ap.add_argument("--report-only", action="store_true", help="regenerate REPORT.md from latest results")
    args = ap.parse_args()

    if args.report_only:
        payload = json.loads((RESULTS_DIR / "latest.json").read_text(encoding="utf-8"))
    else:
        manifest = yaml.safe_load((EVAL_DIR / "manifest.yaml").read_text(encoding="utf-8"))
        only = set(args.only.split(",")) if args.only else None
        payload = run(manifest, only)
        out = write_results(payload)
        print(f"results → {out}", file=sys.stderr)
    (EVAL_DIR / "REPORT.md").write_text(render_report(payload), encoding="utf-8")
    print(f"report  → {EVAL_DIR / 'REPORT.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()

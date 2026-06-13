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
import hashlib
import json
import os
import platform
import shutil
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
SRC_DIR = WORK / "src"        # source-tier git checkouts
BUILD_DIR = WORK / "build"    # source-tier configure output (compile DB)
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


# ── source tier (L3/L4/L5) ───────────────────────────────────────────────────
# For manifest entries carrying a `source:` block we clone the repo at each tag,
# configure it (cmake → compile_commands.json), dump the source ABI surface, and
# record L3 build / L4 source-ABI / L5 graph coverage. Gated on git+cmake (and
# clang for a non-partial L4); a missing tool or build failure is recorded as a
# skipped/errored row, never a crash — the binary tier stays the always-on lane.

#: External tools the source tier needs. git+cmake are required to produce a
#: compile DB at all; clang is what makes L4 (declarations/types) non-partial.
_SOURCE_REQUIRED = ("git", "cmake")


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _list_len(obj: object) -> int:
    return len(obj) if isinstance(obj, list) else 0


def _source_coverage(snap: dict) -> dict:
    """Count the embedded L3/L4/L5 facts in a ``dump --sources`` snapshot (pure).

    Reads the inline ``build_source`` payload (``BuildSourcePack.to_embedded_dict``)
    and returns per-layer counts plus the manifest coverage-status map, defending
    against a missing/partial payload so a configure-only tree still yields a row.
    """
    bs = snap.get("build_source") or {}
    be = bs.get("build_evidence") or {}
    surf = (bs.get("source_abi") or {}).get("reachable_source_surface") or {}
    sg = bs.get("source_graph") or {}
    cov = {
        row.get("layer"): row.get("status")
        for row in (bs.get("manifest") or {}).get("coverage", [])
        if isinstance(row, dict)
    }
    return {
        "l3_compile_units": _list_len(be.get("compile_units")),
        "l3_targets": _list_len(be.get("targets")),
        "l3_build_options": _list_len(be.get("build_options")),
        "l4_declarations": _list_len(surf.get("declarations")),
        "l4_types": _list_len(surf.get("types")),
        "l4_macros": _list_len(surf.get("macros")),
        "l5_nodes": _list_len(sg.get("nodes")),
        "l5_edges": _list_len(sg.get("edges")),
        "coverage_status": cov,
    }


def _checkout_key(repo: str, tag: str) -> str:
    """Short stable token identifying a (repo, tag) checkout, for the cache dir.

    Folding the repo+tag into the directory name means a manifest tag/repo bump
    (or a different side) lands in a *different* directory, so a cached checkout
    is never silently reused for a different revision (Codex review) — the dir
    name no longer omits the tag.
    """
    return hashlib.sha1(f"{repo}@{tag}".encode()).hexdigest()[:10]


def _git_clone_tag(repo: str, tag: str, dest: Path) -> None:
    """Shallow-clone *repo* at *tag* into *dest*.

    *dest* is keyed by (repo, tag) via :func:`_checkout_key`, so reuse is only
    ever of the same revision. A directory that exists but lacks a complete
    checkout (an interrupted clone leaves no ``.git``, or a stale partial) is
    wiped and re-cloned; a verified ``.git`` at the keyed path is reused.
    """
    if (dest / ".git").is_dir():
        return
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, repo, str(dest)],
        check=True, capture_output=True, text=True, timeout=600,
    )


def _cmake_configure(src_dir: Path, build_dir: Path, extra_args: list[str]) -> None:
    """Configure *src_dir* into *build_dir*, emitting compile_commands.json."""
    build_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["cmake", "-S", str(src_dir), "-B", str(build_dir),
         "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", *extra_args],
        check=True, capture_output=True, text=True, timeout=600,
    )


def _dump_sources(tree: Path, build_dir: Path, out: Path) -> tuple[float, subprocess.CompletedProcess]:
    return _run([
        "abicheck", "dump", "--sources", str(tree),
        "--build-info", str(build_dir),
        "--collect-mode", "source-target",
        "-o", str(out),
    ])


def _scan_source_side(entry: dict, which: str, tag: str) -> tuple[Path, dict, float]:
    """Clone+configure+dump one side; return (snapshot path, coverage, seconds)."""
    src = entry["source"]
    lib = entry["lib"]
    t0 = time.time()
    # Key the checkout + build dirs by (repo, tag) so a manifest revision bump
    # never reuses a stale checkout under the same name (Codex review).
    key = _checkout_key(src["repo"], tag)
    tree = SRC_DIR / f"{lib}_{which}_{key}"
    _git_clone_tag(src["repo"], tag, tree)
    cmake_src = tree / src["cmake_subdir"] if src.get("cmake_subdir") else tree
    build = BUILD_DIR / f"{lib}_{which}_{key}"
    _cmake_configure(cmake_src, build, list(src.get("cmake_args", [])))
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = SNAP_DIR / f"{lib}_src_{which}.json"
    secs, p = _dump_sources(tree, build, snap)
    if p.returncode or not snap.exists():
        raise RuntimeError(f"dump --sources failed ({which}): {(p.stderr or '')[-200:]}")
    cov = _source_coverage(json.loads(snap.read_text(encoding="utf-8")))
    return snap, cov, round(time.time() - t0 + secs, 2)


def scan_source_one(entry: dict) -> dict:
    """Source-tier (L3/L4/L5) scan for one manifest entry with a ``source:`` block."""
    src = entry["source"]
    rec: dict = {
        "lib": entry["lib"], "tier": "source",
        "old": str(src.get("tag_old", entry.get("old"))),
        "new": str(src.get("tag_new", entry.get("new"))),
        "repo": src.get("repo"),
    }
    try:
        osnap, ocov, ot = _scan_source_side(entry, "old", rec["old"])
        nsnap, ncov, nt = _scan_source_side(entry, "new", rec["new"])
        rec["old_coverage"] = ocov
        rec["new_coverage"] = ncov
        rec["build_s"] = round(ot + nt, 2)
        tc, pc = _run(["abicheck", "compare", str(osnap), str(nsnap), "--format", "json"])
        rec["compare_s"] = tc
        if pc.stdout:
            d = json.loads(pc.stdout)
            rec["verdict"] = d.get("verdict")
            rec["evidence_tier"] = d.get("evidence_tier")
            s = d.get("summary", {})
            for k in ("source_breaks", "risk_changes", "total_changes"):
                rec[k] = s.get(k)
        else:
            rec["error"] = "compare produced no output: " + (pc.stderr or "")[-160:]
    except subprocess.CalledProcessError as e:
        rec["error"] = f"{e.cmd[0] if e.cmd else 'cmd'} failed: {(e.stderr or str(e))[-200:]}"
    except Exception as e:  # noqa: BLE001 - record any failure as a row
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def _source_entries(manifest: dict, only: set[str] | None) -> list[dict]:
    return [
        e for e in manifest["libraries"]
        if e.get("source") and not (only and e["lib"] not in only)
    ]


def run(manifest: dict, only: set[str] | None, tiers: set[str]) -> dict:
    rows: list[dict] = []
    if "binary" in tiers:
        for entry in manifest["libraries"]:
            if only and entry["lib"] not in only:
                continue
            rec = scan_one(entry)
            status = rec.get("verdict") or rec.get("error", "?")
            flag = "" if rec.get("verdict_matches_expected", True) else "  !! EXPECTED " + str(rec.get("expect"))
            print(f"  {rec['lib']:14} {status}{flag}", file=sys.stderr)
            rows.append(rec)

    source_rows: list[dict] = []
    if "source" in tiers:
        missing = [t for t in _SOURCE_REQUIRED if not _have(t)]
        entries = _source_entries(manifest, only)
        if missing:
            print(f"  source tier skipped: missing {', '.join(missing)}", file=sys.stderr)
            source_rows = [
                {"lib": e["lib"], "tier": "source", "error": f"skipped: missing {', '.join(missing)}"}
                for e in entries
            ]
        else:
            if not _have("clang"):
                print("  note: clang absent — L4 (decls/types) will be partial", file=sys.stderr)
            for entry in entries:
                rec = scan_source_one(entry)
                status = rec.get("verdict") or rec.get("error", "?")
                cov = rec.get("new_coverage") or {}
                cu = cov.get("l3_compile_units", "?")
                decls = cov.get("l4_declarations", "?")
                print(f"  {rec['lib']:14} src {status}  (L3 cu={cu}, L4 decls={decls})", file=sys.stderr)
                source_rows.append(rec)

    payload = {
        "result_schema": RESULT_SCHEMA,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "abicheck_version": _abicheck_version(),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "tier": "+".join(sorted(tiers)),
        "results": rows,
    }
    if "source" in tiers:
        payload["source_results"] = source_rows
    return payload


def drift_rows(payload: dict) -> list[dict]:
    """Binary-tier rows that failed the regression guard (verdict drift or error).

    Pure (no I/O): the CI gate (`--fail-on-drift`) and tests both call this so
    "what counts as a failure" lives in one place.
    """
    return [
        r for r in payload.get("results", [])
        if "error" in r or not r.get("verdict_matches_expected", True)
    ]


def write_results(payload: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = payload["generated_utc"].replace(":", "").replace("-", "")
    out = RESULTS_DIR / f"{stamp}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def _render_binary_section(rows: list[dict]) -> list[str]:
    ok = sum(1 for r in rows if r.get("verdict_matches_expected", True) and "error" not in r)
    verdicts = collections.Counter(r.get("verdict", "ERROR") for r in rows)
    L = ["## Binary tier (L0/L1)\n"]
    L.append(f"- libraries: **{len(rows)}** | verdict matches expected: **{ok}/{len(rows)}**")
    L.append("- verdict distribution: " + ", ".join(f"{v}×{n}" for v, n in sorted(verdicts.items())))
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
    return L


def _render_source_section(rows: list[dict]) -> list[str]:
    """Render the L3/L4/L5 source-tier table (coverage on the *new* side)."""
    scanned = [r for r in rows if "error" not in r]
    L = ["## Source tier (L3 build / L4 source-ABI / L5 graph)\n"]
    L.append(f"- entries: **{len(rows)}** | scanned: **{len(scanned)}** "
             f"(rest skipped/errored — needs git+cmake, clang for full L4)")
    L.append("")
    L.append("| lib | old→new | verdict | L3 units | L4 decls | L4 types | L4 macros | L5 n/e | build s | cmp s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if "error" in r:
            L.append(f"| {r['lib']} | {r.get('old','')}→{r.get('new','')} | SKIP/ERR "
                     f"| | | | | | | {r['error'][:40]} |")
            continue
        c = r.get("new_coverage", {})
        ne = f"{c.get('l5_nodes','?')}/{c.get('l5_edges','?')}"
        L.append(
            f"| {r['lib']} | {r['old']}→{r['new']} | {r.get('verdict','-')} "
            f"| {c.get('l3_compile_units','?')} | {c.get('l4_declarations','?')} "
            f"| {c.get('l4_types','?')} | {c.get('l4_macros','?')} | {ne} "
            f"| {r.get('build_s','?')} | {r.get('compare_s','?')} |")
    L.append("")
    L.append("> Coverage = embedded `build_source` fact counts on the new side. "
             "L4 needs clang (decls/types); a configure-only tree may show partial "
             "L4 if generated headers are absent. Reproduce: `python eval/runner.py --tier source`.")
    L.append("")
    return L


def render_report(payload: dict) -> str:
    rows = payload.get("results", [])
    source_rows = payload.get("source_results", [])
    L = []
    L.append("<!-- GENERATED by eval/runner.py — do not edit by hand. Edit manifest.yaml and re-run. -->")
    L.append("# abicheck field-evaluation report\n")
    L.append(f"- generated: `{payload['generated_utc']}`")
    L.append(f"- abicheck: `{payload['abicheck_version']}`")
    L.append(f"- host: `{payload['host']['platform']}`, Python {payload['host']['python']}")
    L.append(f"- tiers: `{payload.get('tier', 'binary')}`")
    L.append("")
    if rows:
        L.extend(_render_binary_section(rows))
    if source_rows:
        L.extend(_render_source_section(source_rows))
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated lib names")
    ap.add_argument("--tier", choices=["binary", "source", "both"], default="binary",
                    help="which tier(s) to scan: binary L0/L1 (default), source L3/L4/L5, or both")
    ap.add_argument("--report-only", action="store_true", help="regenerate REPORT.md from latest results")
    ap.add_argument("--fail-on-drift", action="store_true",
                    help="exit non-zero if any binary-tier verdict drifts from its "
                         "manifest `expect` (or a scan errored) — the CI regression gate")
    args = ap.parse_args()

    if args.report_only:
        payload = json.loads((RESULTS_DIR / "latest.json").read_text(encoding="utf-8"))
    else:
        manifest = yaml.safe_load((EVAL_DIR / "manifest.yaml").read_text(encoding="utf-8"))
        only = set(args.only.split(",")) if args.only else None
        tiers = {"binary", "source"} if args.tier == "both" else {args.tier}
        payload = run(manifest, only, tiers)
        out = write_results(payload)
        print(f"results → {out}", file=sys.stderr)
    (EVAL_DIR / "REPORT.md").write_text(render_report(payload), encoding="utf-8")
    print(f"report  → {EVAL_DIR / 'REPORT.md'}", file=sys.stderr)

    if args.fail_on_drift:
        drift = drift_rows(payload)
        if drift:
            libs = ", ".join(
                f"{r['lib']}({r.get('verdict') or r.get('error', '?')[:30]})" for r in drift
            )
            print(f"FAIL: binary-tier drift/errors on {len(drift)} lib(s): {libs}", file=sys.stderr)
            sys.exit(1)
        print("OK: all binary-tier verdicts match expected", file=sys.stderr)


if __name__ == "__main__":
    main()

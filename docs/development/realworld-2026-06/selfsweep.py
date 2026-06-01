#!/usr/bin/env python3
"""Self-comparison robustness + false-positive sweep.

For a diverse sample of real system shared libraries, compare each library
against ITSELF. Ground truth: identical bytes => zero real ABI changes.
Any of the following is a finding worth reporting:
  * non-zero exit / exception / crash            -> robustness bug
  * verdict not in {NO_CHANGE, COMPATIBLE}        -> correctness bug
  * any breaking/source/risk change              -> false positive
  * quality-only findings                        -> note (expected class)
"""
from __future__ import annotations

import json
import random
import subprocess
import time
from collections import Counter
from pathlib import Path

REPORTS = Path("/tmp/val/reports")
SEARCH = ["/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"]


def collect() -> list[Path]:
    """Stratified sample: 20 smallest, 20 largest, 60 random-middle libraries."""
    seen: dict[str, Path] = {}
    for root in SEARCH:
        for p in Path(root).glob("*.so*"):
            if p.is_file() and not p.is_symlink():
                # one per soname stem
                stem = p.name.split(".so")[0]
                seen.setdefault(stem, p)
    libs = sorted(seen.values(), key=lambda x: x.stat().st_size)
    random.seed(42)
    # stratified: smallest 20, largest 20, random 60 from middle
    small = libs[:20]
    large = libs[-20:]
    mid = libs[20:-20]
    rand = random.sample(mid, min(60, len(mid)))
    chosen = list({p: None for p in (small + large + rand)})
    return chosen


def main() -> None:
    libs = collect()
    results = []
    kind_global: Counter = Counter()
    for i, lib in enumerate(libs):
        tag = f"self_{lib.name}"
        out_json = REPORTS / "self" / f"{tag}.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["abicheck", "compare", str(lib), str(lib),
               "--format", "json", "-o", str(out_json)]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            rc = proc.returncode
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            rc = -999
            stderr = "TIMEOUT>300s"
        dt = round(time.time() - t0, 2)
        rec: dict = {"tag": tag, "path": str(lib),
                     "size": lib.stat().st_size, "rc": rc, "elapsed_s": dt}
        traceback = "Traceback" in stderr
        rec["python_traceback"] = traceback
        if out_json.exists():
            try:
                d = json.loads(out_json.read_text())
                rec["verdict"] = d.get("verdict")
                s = d.get("summary", {})
                rec["summary"] = s
                kinds = Counter(c.get("kind") for c in d.get("changes", []))
                rec["kinds"] = dict(kinds)
                kind_global.update(kinds)
                rec["breaking"] = s.get("breaking", 0)
                rec["source_breaks"] = s.get("source_breaks", 0)
                rec["risk"] = s.get("risk_changes", 0)
            except Exception as e:  # noqa: BLE001
                rec["parse_error"] = repr(e)
        else:
            rec["no_output"] = True
            rec["stderr_tail"] = stderr.splitlines()[-4:]
        results.append(rec)
        flag = ""
        if rc != 0:
            flag = " !! NONZERO/CRASH"
        elif rec.get("breaking") or rec.get("source_breaks") or rec.get("risk"):
            flag = " !! FALSE-POSITIVE-BREAK"
        elif traceback:
            flag = " !! TRACEBACK"
        print(f"[{i+1}/{len(libs)}] {lib.name} sz={rec['size']//1024}K "
              f"rc={rc} verdict={rec.get('verdict')} "
              f"changes={rec.get('summary',{}).get('total_changes')} t={dt}s{flag}",
              flush=True)

    summary = {
        "total": len(results),
        "nonzero_rc": sum(1 for r in results if r["rc"] != 0),
        "tracebacks": sum(1 for r in results if r.get("python_traceback")),
        "no_output": sum(1 for r in results if r.get("no_output")),
        "non_compatible_verdict": sum(
            1 for r in results
            if r.get("verdict") not in (None, "NO_CHANGE", "COMPATIBLE")),
        "fp_breaks": sum(1 for r in results
                         if r.get("breaking") or r.get("source_breaks") or r.get("risk")),
        "kind_histogram": dict(kind_global),
    }
    (REPORTS / "selfsweep_results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results) + "\n")
    (REPORTS / "selfsweep_summary.json").write_text(json.dumps(summary, indent=2))
    print("\nSUMMARY:", json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

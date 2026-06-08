#!/usr/bin/env python3
"""abicheck real-world validation harness.

For each curated version pair, find every shared object present in BOTH the old
and new package (matched by logical library name, e.g. libtbb / libcrypto), then
run `abicheck compare` capturing JSON, exit code, wall time, and stderr.
Aggregates into results.json for the report.

Paths resolve from the checkout: the manifest is read from and results written to
``validation/data/`` next to this script. The extracted-libraries directory is NOT
committed (binaries are large); point the harness at it via the
``ABICHECK_VALIDATION_LIBS`` environment variable (default: ``validation/libs/ex``
relative to the repo). Each package extracts to ``<libs>/<pkg_stem>/lib/*.so*`` —
see ``data/manifest.json`` for the conda-forge files to fetch and extract.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VALID_DIR = Path(__file__).resolve().parent.parent          # validation/
DATA = VALID_DIR / "data"
EX = os.environ.get("ABICHECK_VALIDATION_LIBS", str(VALID_DIR / "libs" / "ex"))
OUT = str(DATA)
MANIFEST = DATA / "manifest.json"
os.makedirs(f"{OUT}/runs", exist_ok=True)  # per-library JSONs (gitignored)

if not MANIFEST.is_file():
    sys.exit(f"manifest not found: {MANIFEST}")
if not os.path.isdir(EX):
    sys.exit(
        f"extracted-libraries dir not found: {EX}\n"
        "Fetch+extract the packages in data/manifest.json (binaries are not "
        "committed), then set ABICHECK_VALIDATION_LIBS to their parent directory."
    )

def logical_name(path: str) -> str:
    b = os.path.basename(path)
    stem = b.split(".so")[0]
    return re.sub(r"-(?:\d+\.)+\d+$", "", stem)

def real_sos(pkgdir: str) -> dict[str, str]:
    """logical_name -> path for every real (non-symlink) ELF .so in pkgdir/lib."""
    out = {}
    for p in glob.glob(f"{EX}/{pkgdir}/lib/*.so*"):
        if os.path.islink(p):
            continue
        try:
            with open(p, "rb") as f:
                if f.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        out[logical_name(p)] = p
    return out

def has_dwarf(path: str) -> bool:
    r = subprocess.run(["readelf", "-S", path], capture_output=True, text=True)
    return "debug_info" in r.stdout

def run(cmd: list[str]) -> tuple[int, str, str, float]:
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr, time.time() - t

manifest = json.load(open(MANIFEST))
results = []
for m in manifest:
    old_dir = m["old_file"].replace(".conda", "").replace(".tar.bz2", "")
    new_dir = m["new_file"].replace(".conda", "").replace(".tar.bz2", "")
    old = real_sos(old_dir)
    new = real_sos(new_dir)
    common = sorted(set(old) & set(new))
    for lname in common:
        op, np = old[lname], new[lname]
        mode = ("dwarf" if has_dwarf(op) else "sym") + "->" + ("dwarf" if has_dwarf(np) else "sym")
        tag = f"{m['pair']}__{lname}"
        jpath = f"{OUT}/runs/{tag}.json"
        cmd = ["abicheck", "compare", op, np,
               "--old-version", m["old_ver"], "--new-version", m["new_ver"],
               "--format", "json", "-o", jpath, "--recommend"]
        rc, so, se, dt = run(cmd)
        data = None
        if os.path.exists(jpath):
            try:
                data = json.load(open(jpath))
            except Exception as e:
                se += f"\n[harness] json parse failed: {e}"
        rec = {
            "tag": tag, "pair": m["pair"], "library": m["library"], "logical": lname,
            "old_ver": m["old_ver"], "new_ver": m["new_ver"],
            "expectation": m["expectation"], "note": m["note"],
            "mode": mode, "old_path": op, "new_path": np,
            "exit_code": rc, "seconds": round(dt, 2),
            "stderr": se.strip(),
        }
        if data:
            summ = data.get("summary", {}) if isinstance(data, dict) else {}
            rec["verdict"] = data.get("verdict") or summ.get("verdict")
            rec["counts"] = {
                k: summ.get(k) for k in
                ("total", "breaking", "api_break", "compatible", "risk",
                 "added", "removed", "changed")
                if k in summ
            }
            rec["release_recommendation"] = data.get("release_recommendation")
        results.append(rec)
        print(f"{tag:48} {mode:11} exit={rc} {dt:5.1f}s verdict={rec.get('verdict')}")

json.dump(results, open(f"{OUT}/results.json", "w"), indent=2)
print(f"\n{len(results)} comparisons -> {OUT}/results.json")

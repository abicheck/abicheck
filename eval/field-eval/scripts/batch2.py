#!/usr/bin/env python3
"""Iteration-2 binary scan: larger products. Picks the symbol-richest real .so."""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import time

import condafetch as cf

LIBS = [
    ("icu",         "icu",         "75.1",   "78.3"),
    ("hdf5",        "hdf5",        "1.8.20", "2.1.0"),
    ("protobuf",    "libprotobuf", "6.34.1", "7.35.1"),
    ("glib",        "libglib",     "2.86.4", "2.88.1"),
    ("openssl",     "openssl",     "3.6.1",  "4.0.1"),
    ("gmp",         "gmp",         "6.2.1",  "6.3.0"),
    ("flac",        "libflac",     "1.4.3",  "1.5.0"),
    ("openblas",    "libopenblas", "0.3.8",  "0.3.9"),
]

def run(cmd):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return round(time.time() - t0, 3), p

def exported_dynfunc_count(path):
    """Count defined exported dynamic FUNC/IFUNC symbols in a shared object."""
    p = subprocess.run(["readelf", "--dyn-syms", "-W", path], capture_output=True, text=True)
    if p.returncode:
        return 0
    count = 0
    for line in p.stdout.splitlines():
        cols = line.split()
        if len(cols) < 8 or not cols[0].endswith(":"):
            continue
        typ, bind, vis, ndx = cols[3], cols[4], cols[5], cols[6]
        if (
            typ in {"FUNC", "IFUNC"}
            and bind in {"GLOBAL", "WEAK"}
            and vis in {"DEFAULT", "PROTECTED"}
            and ndx != "UND"
        ):
            count += 1
    return count

def best_so(pkg, ver):
    """Extract pkg@ver, return the real .so with the most exported dynsyms."""
    arch, dl, sz = cf.download(pkg, ver)
    out = f"/tmp/scan/pkgs/ex_{pkg}_{ver}"
    ext = cf.extract(arch, out)
    sos = cf.find_sos(out)
    best, bestn = None, -1
    for s in sos:
        n = exported_dynfunc_count(s)
        if n > bestn:
            best, bestn = s, n
    return best, bestn, round(dl, 2), round(ext, 2), sz // 1024

def so_pair_key(path):
    """Stable library identity for pairing old/new DSOs from multi-lib packages."""
    name = os.path.basename(path)
    name = re.sub(r"\.so(?:\..*)?$", "", name)
    name = re.sub(r"-r?\d+(?:\.\d+)*$", "", name)
    return name

def main():
    out = []
    for disp, pkg, ov, nv in LIBS:
        rec = {"lib": disp, "pkg": pkg, "old_ver": ov, "new_ver": nv}
        try:
            t0 = time.time()
            oso, on, odl, oext, osz = best_so(pkg, ov)
            nso, nn, ndl, next_, nsz = best_so(pkg, nv)
            rec["fetch_s"] = round(time.time() - t0, 2)
            rec["dl_mb"] = round((osz + nsz) / 1024, 1)
            rec["so"] = os.path.basename(oso)
            rec["new_so"] = os.path.basename(nso)
            rec["old_funcs"] = on
            rec["new_funcs"] = nn
            if so_pair_key(oso) != so_pair_key(nso):
                rec["error"] = f"selected different DSOs: {os.path.basename(oso)} vs {os.path.basename(nso)}"
                print(json.dumps(rec))
                out.append(rec)
                json.dump(out, open("/tmp/scan/results2.json", "w"), indent=2)
                continue
            os_ = f"/tmp/scan/snap/{disp}_old.json"
            ns_ = f"/tmp/scan/snap/{disp}_new.json"
            for stale in (os_, ns_):  # don't read a prior run's snapshot if dump fails
                if os.path.exists(stale):
                    os.remove(stale)
            td1, p1 = run(["abicheck", "dump", oso, "-o", os_])
            td2, p2 = run(["abicheck", "dump", nso, "-o", ns_])
            if p1.returncode or p2.returncode:
                rec["error"] = "dump failed: " + (p1.stderr or p2.stderr)[-200:]
                print(json.dumps(rec))
                out.append(rec)
                json.dump(out, open("/tmp/scan/results2.json", "w"), indent=2)
                continue
            rec["dump_s"] = round(td1 + td2, 2)
            rec["snap_mb"] = round((os.path.getsize(os_) + os.path.getsize(ns_)) / 1048576, 2)
            tc, pc = run(["abicheck", "compare", os_, ns_, "--format", "json"])
            rec["compare_s"] = tc
            d = json.loads(pc.stdout)
            s = d.get("summary", {})
            rec["verdict"] = d.get("verdict")
            rec["tier"] = d.get("evidence_tier")
            rec["breaking"] = s.get("breaking")
            rec["risk"] = s.get("risk_changes")
            rec["additions"] = s.get("compatible_additions")
            rec["total"] = s.get("total_changes")
            rec["top_kinds"] = dict(collections.Counter(c.get("kind") for c in d.get("changes", [])).most_common(5))
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(rec))
        out.append(rec)
        json.dump(out, open("/tmp/scan/results2.json", "w"), indent=2)

if __name__ == "__main__":
    main()

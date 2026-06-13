#!/usr/bin/env python3
"""Batch L0 binary ABI scan across a dozen conda-forge libraries.

For each (display, candidate conda pkgs, old_ver, new_ver): download+extract both
sides, pick the main .so (largest real shared object), dump both, compare. Record
verdict, change kinds, symbol counts, and per-step wall time into results.json.
"""
from __future__ import annotations
import json, os, time, subprocess, sys
import condafetch as cf

# (display, [conda pkg candidates], old_ver, new_ver)
LIBS = [
    ("zlib",          ["libzlib"],       "1.2.13", "1.3.1"),
    ("zstd",          ["zstd", "libzstd"], "1.5.5", "1.5.7"),
    ("xz/liblzma",    ["liblzma"],        "5.6.4", "5.8.3"),
    ("bzip2",         ["bzip2", "libbzip2"], "1.0.6", "1.0.8"),
    ("lz4",           ["lz4-c", "liblz4"], "1.9.3", "1.10.0"),
    ("libpng",        ["libpng"],         "1.6.55", "1.6.58"),
    ("libjpeg-turbo", ["libjpeg-turbo"],  "3.0.0", "3.1.4.1"),
    ("pcre2",         ["pcre2"],          "10.44", "10.47"),
    ("libsodium",     ["libsodium"],      "1.0.18", "1.0.22"),
    ("c-ares",        ["c-ares"],         "1.34.3", "1.34.6"),
    ("libssh2",       ["libssh2"],        "1.10.0", "1.11.1"),
    ("snappy",        ["snappy"],         "1.1.10", "1.2.2"),
    # libwebp runtime .so ships in the libwebp-base split package (see FINDINGS P01);
    # matches the recorded data/results.json row.
    ("libwebp",       ["libwebp-base"],   "1.4.0", "1.6.0"),
    ("libuv",         ["libuv"],          "1.49.2", "1.52.1"),
]

def get_side(candidates, ver):
    """Try each candidate pkg, return (so_path, pkg, dl_s, ext_s, size_kb) for the
    largest real .so found."""
    last = None
    for pkg in candidates:
        try:
            arch, dl_t, size = cf.download(pkg, ver)
        except SystemExit as e:
            last = str(e); continue
        out = f"/tmp/scan/pkgs/ex_{pkg}_{ver}"
        ext_t = cf.extract(arch, out)
        sos = cf.find_sos(out)
        if sos:
            sos.sort(key=os.path.getsize)
            return sos[-1], pkg, dl_t, ext_t, size // 1024
        last = f"no .so in {pkg} {ver}"
    raise RuntimeError(f"{candidates} {ver}: {last}")

def run(cmd):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    return time.time() - t0, p

def sym_count(snap_path):
    d = json.load(open(snap_path))
    for k in ("symbols", "functions", "exported_symbols"):
        v = d.get(k)
        if isinstance(v, (list, dict)):
            return len(v)
    # nested
    return sum(len(d.get(k, [])) for k in ("functions", "variables") if isinstance(d.get(k), list))

def main():
    results = []
    for display, cands, ov, nv in LIBS:
        rec = {"lib": display, "old_ver": ov, "new_ver": nv}
        try:
            t_dl = time.time()
            oso, opkg, odl, oext, osz = get_side(cands, ov)
            nso, npkg, ndl, next_, nsz = get_side(cands, nv)
            rec["pkg"] = opkg
            rec["fetch_s"] = round(time.time() - t_dl, 2)
            rec["dl_s"] = round(odl + ndl, 2)
            rec["pkg_kb"] = osz + nsz
            rec["old_so"] = os.path.basename(oso); rec["new_so"] = os.path.basename(nso)
            osnap = f"/tmp/scan/snap/{display.replace('/','_')}_old.json"
            nsnap = f"/tmp/scan/snap/{display.replace('/','_')}_new.json"
            td1, p1 = run(["abicheck", "dump", oso, "-o", osnap])
            td2, p2 = run(["abicheck", "dump", nso, "-o", nsnap])
            if p1.returncode or p2.returncode:
                rec["error"] = "dump failed: " + (p1.stderr or p2.stderr)[-200:]
                results.append(rec); print(json.dumps(rec)); continue
            rec["dump_s"] = round(td1 + td2, 3)
            rec["old_syms"] = sym_count(osnap); rec["new_syms"] = sym_count(nsnap)
            rec["snap_kb"] = (os.path.getsize(osnap) + os.path.getsize(nsnap)) // 1024
            tc, pc = run(["abicheck", "compare", osnap, nsnap, "--format", "json"])
            rec["compare_s"] = round(tc, 3)
            rec["legacy_rc"] = pc.returncode
            d = json.loads(pc.stdout)
            rec["verdict"] = d.get("verdict")
            rec["evidence_tier"] = d.get("evidence_tier")
            s = d.get("summary", {})
            rec["breaking"] = s.get("breaking"); rec["risk"] = s.get("risk_changes")
            rec["source_breaks"] = s.get("source_breaks"); rec["additions"] = s.get("compatible_additions")
            rec["total_changes"] = s.get("total_changes")
            import collections
            kinds = collections.Counter(c.get("kind") for c in d.get("changes", []))
            rec["kinds"] = dict(kinds)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        results.append(rec)
        print(json.dumps(rec))
        json.dump(results, open("/tmp/scan/results.json", "w"), indent=2)
    json.dump(results, open("/tmp/scan/results.json", "w"), indent=2)

if __name__ == "__main__":
    main()

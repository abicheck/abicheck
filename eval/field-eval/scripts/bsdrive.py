#!/usr/bin/env python3
"""Build/source-data stage timer: clone -> cmake configure -> L3 -> L3+L4+L5.

Records wall time for each stage and the payload sizes/counts so we can answer
"how long does build data vs source+graph take" with a real range.
"""
from __future__ import annotations
import json, os, subprocess, sys, time

def t(cmd, **kw):
    t0 = time.time(); p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return round(time.time() - t0, 2), p

def drive(name, repo, tag, so, src_subdir_for_cmake=".", cmake_extra=None):
    root = f"/tmp/scan/src/{name}"
    rec = {"lib": name, "tag": tag}
    if not os.path.isdir(root):
        os.makedirs(os.path.dirname(root), exist_ok=True)  # git clone needs the parent
        ct, p = t(["git", "clone", "--depth", "1", "--branch", tag, repo, root])
        rec["clone_s"] = ct
        if p.returncode:
            rec["error"] = "clone: " + p.stderr[-200:]; return rec
    else:
        rec["clone_s"] = 0.0
    cmake_src = os.path.join(root, src_subdir_for_cmake)
    bld = os.path.join(root, "_bld")
    cm = ["cmake", "-S", cmake_src, "-B", bld, "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
    if cmake_extra: cm += cmake_extra
    ct, p = t(cm)
    rec["cmake_configure_s"] = ct
    cdb = os.path.join(bld, "compile_commands.json")
    if not os.path.exists(cdb):
        rec["error"] = "no compile_commands.json: " + p.stderr[-300:]; return rec
    rec["tus"] = len(json.load(open(cdb)))
    L3 = f"/tmp/scan/snap/{name}.L3.json"; L345 = f"/tmp/scan/snap/{name}.L345.json"
    t3, p3 = t(["abicheck", "dump", so, "--sources", root, "--build-info", bld,
                "--collect-mode", "build", "-o", L3])
    rec["L3_s"] = t3
    t5, p5 = t(["abicheck", "dump", so, "--sources", root, "--build-info", bld,
                "--collect-mode", "source-target", "-o", L345])
    rec["L3L4L5_s"] = t5
    rec["source_graph_s"] = round(t5 - t3, 2)
    if p5.returncode:
        rec["error"] = "L4 dump: " + p5.stderr[-200:]
    # payload counts
    try:
        bs = json.load(open(L345))["build_source"]
        sg = bs.get("source_graph", {})
        rec["graph_nodes"] = len(sg.get("nodes", []))
        rec["graph_edges"] = len(sg.get("edges", []))
        rec["snap_kb"] = os.path.getsize(L345) // 1024
    except Exception as e:
        rec["payload_err"] = str(e)
    return rec

JOBS = [
    ("zstd", "https://github.com/facebook/zstd.git", "v1.5.7",
     "/tmp/scan/pkgs/ex_zstd_1.5.7/lib/libzstd.so.1.5.7", "build/cmake", None),
    ("snappy", "https://github.com/google/snappy.git", "1.2.2",
     "/tmp/scan/pkgs/ex_snappy_1.2.2/lib/libsnappy.so.1.2.2", ".",
     ["-DSNAPPY_BUILD_TESTS=OFF", "-DSNAPPY_BUILD_BENCHMARKS=OFF"]),
]

if __name__ == "__main__":
    out = []
    for name, repo, tag, so, sub, extra in JOBS:
        r = drive(name, repo, tag, so, sub, extra)
        print(json.dumps(r))
        out.append(r)
        json.dump(out, open("/tmp/scan/bs_results.json", "w"), indent=2)

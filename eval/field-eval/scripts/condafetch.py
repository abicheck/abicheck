#!/usr/bin/env python3
"""Minimal conda-forge package fetcher: list versions, download, extract .so/headers.

No conda needed. Uses anaconda.org API + direct CDN download. Handles .conda (zip of
zstd tarballs) and legacy .tar.bz2.
"""
from __future__ import annotations
import json, os, sys, time, zipfile, tarfile, subprocess, urllib.request, shutil

API = "https://api.anaconda.org/package/conda-forge/{}"
CDN = "https://conda.anaconda.org/conda-forge/linux-64/{}"
CACHE = "/tmp/scan/pkgs"
os.makedirs(CACHE, exist_ok=True)  # urlretrieve won't create parents

def _get(url, dest):
    t0 = time.time()
    urllib.request.urlretrieve(url, dest)
    return time.time() - t0, os.path.getsize(dest)

def list_files(pkg, subdir="linux-64"):
    p = f"{CACHE}/{pkg}.api.json"
    if not os.path.exists(p):
        urllib.request.urlretrieve(API.format(pkg), p)
    d = json.load(open(p))
    fs = [f for f in d["files"] if f["attrs"].get("subdir") == subdir]
    # newest build per (version): sort by version then build_number
    return fs

def pick(pkg, version, subdir="linux-64"):
    """Pick the highest build_number .conda (fallback .tar.bz2) for a version."""
    fs = [f for f in list_files(pkg, subdir) if f["version"] == version]
    if not fs:
        raise SystemExit(f"no files for {pkg} {version} in {subdir}")
    conda = [f for f in fs if f["basename"].endswith(".conda")]
    pool = conda or fs
    pool.sort(key=lambda f: (f["attrs"].get("build_number", 0), f["basename"]))
    return pool[-1]

def download(pkg, version, subdir="linux-64"):
    f = pick(pkg, version, subdir)
    base = os.path.basename(f["basename"])
    dest = f"{CACHE}/{base}"
    dl_t = 0.0; size = os.path.getsize(dest) if os.path.exists(dest) else 0
    if not os.path.exists(dest):
        dl_t, size = _get(CDN.format(base), dest)
    return dest, dl_t, size

def extract(archive, outdir):
    """Extract a .conda or .tar.bz2 into outdir. Returns extract seconds."""
    t0 = time.time()
    os.makedirs(outdir, exist_ok=True)
    if archive.endswith(".conda"):
        with zipfile.ZipFile(archive) as z:
            inner = [n for n in z.namelist() if n.startswith("pkg-") and n.endswith(".tar.zst")]
            tmp = outdir + "/_inner.tar.zst"
            with z.open(inner[0]) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        subprocess.run(["tar", "--zstd", "-xf", tmp, "-C", outdir], check=True)
        os.remove(tmp)
    else:
        with tarfile.open(archive, "r:bz2") as t:
            t.extractall(outdir)
    return time.time() - t0

def find_sos(root):
    """Real (non-symlink) shared objects: *.so, *.so.N.M, *.dylib."""
    out = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if ".so" in fn or fn.endswith(".dylib"):
                p = os.path.join(dp, fn)
                if os.path.islink(p):
                    continue
                # skip linker scripts / tiny stubs masquerading as .so
                if os.path.getsize(p) < 256:
                    continue
                out.append(p)
    return out

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "versions":
        fs = list_files(sys.argv[2])
        import collections
        c = collections.Counter(f["version"] for f in fs)
        for v in sorted(c, key=lambda s: [int(x) if x.isdigit() else x for x in s.replace('-', '.').split('.')]):
            print(v, c[v])
    elif cmd == "fetch":
        pkg, ver = sys.argv[2], sys.argv[3]
        arch, dl_t, size = download(pkg, ver)
        out = f"/tmp/scan/pkgs/ex_{pkg}_{ver}"
        ex_t = extract(arch, out)
        sos = find_sos(out)
        print(json.dumps({"archive": os.path.basename(arch), "dl_s": round(dl_t,2),
                          "size_kb": size//1024, "extract_s": round(ex_t,2),
                          "sos": [os.path.relpath(s, out) for s in sos]}, indent=2))

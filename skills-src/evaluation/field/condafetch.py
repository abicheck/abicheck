#!/usr/bin/env python3
"""Minimal conda-forge package fetcher: list versions, download, extract .so/headers.

No conda needed. Uses anaconda.org API + direct CDN download. Handles .conda
(zip of zstd tarballs; requires external zstd for GNU tar) and legacy .tar.bz2.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

API = "https://api.anaconda.org/package/conda-forge/{}"
CDN = "https://conda.anaconda.org/conda-forge/linux-64/{}"
CACHE = os.environ.get(
    "ABICHECK_EVAL_CACHE", os.path.join(tempfile.gettempdir(), "abicheck-eval", "pkgs")
)
os.makedirs(CACHE, exist_ok=True)  # urlretrieve won't create parents


def _retrieve(url, dest):
    """Download *url* to *dest*, refusing anything but HTTPS.

    Every URL here is built from the HTTPS API/CDN constants, but pinning the
    scheme keeps a crafted package/version string from smuggling in a
    ``file://``/``ftp://`` fetch (bandit B310).
    """
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-HTTPS download: {url}")
    urllib.request.urlretrieve(url, dest)  # noqa: S310  # nosec B310 - scheme pinned above

def _ensure_within(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    if os.path.commonpath([root, path]) != root:
        raise RuntimeError(f"unsafe archive member path: {path}")

def _validate_tar_member(member, outdir):
    _ensure_within(outdir, os.path.join(outdir, member.name))
    if member.ischr() or member.isblk() or member.isfifo():
        raise RuntimeError(f"unsupported special archive member: {member.name}")
    if member.issym() or member.islnk():
        if os.path.isabs(member.linkname):
            raise RuntimeError(f"unsafe absolute archive link: {member.name}")
        link_base = os.path.dirname(os.path.join(outdir, member.name))
        _ensure_within(outdir, os.path.join(link_base, member.linkname))

def _safe_extract_tar(tf, outdir):
    for member in tf.getmembers():
        _validate_tar_member(member, outdir)
    # Every member is validated above (containment, no device/FIFO specials, no
    # escaping links). The `data` filter is defence in depth on top of that; its
    # `filter` kwarg postdates some 3.10.x patch releases, hence the fallback.
    try:
        tf.extractall(outdir, filter="data")  # nosec B202 - members validated above
    except TypeError:
        tf.extractall(outdir)  # noqa: S202  # nosec B202 - members validated above

def _require_zstd():
    if shutil.which("zstd") is None:
        raise RuntimeError(
            "extracting .conda archives requires the external 'zstd' executable; "
            "install zstd or provide a legacy .tar.bz2 package"
        )

def _get(url, dest):
    t0 = time.time()
    _retrieve(url, dest)
    return time.time() - t0, os.path.getsize(dest)

REPODATA = "https://conda.anaconda.org/conda-forge/{}/repodata.json.zst"
_REPODATA_INDEX: dict = {}


def repodata_files(repodata, subdir):
    """Index a channel ``repodata.json`` dict into the anaconda.org API file shape.

    Pure: returns ``{name: [{"basename", "version", "attrs": {...}}]}`` so
    :func:`pick` works unchanged whichever source listed the files.
    """
    out: dict = {}
    for key in ("packages", "packages.conda"):
        for fn, rec in (repodata.get(key) or {}).items():
            out.setdefault(rec["name"], []).append({
                "basename": f"{subdir}/{fn}",
                "version": rec["version"],
                "attrs": {"subdir": rec.get("subdir", subdir),
                          "build_number": rec.get("build_number", 0)},
            })
    # ``removed`` lists archives pulled from the index but still served by the
    # CDN (e.g. libprotobuf 6.34.1); parse name-version-build from the filename.
    for fn in repodata.get("removed") or []:
        stem = fn.removesuffix(".conda").removesuffix(".tar.bz2")
        parts = stem.rsplit("-", 2)
        if len(parts) != 3 or stem == fn:
            continue
        name, version, build = parts
        bn = build.rsplit("_", 1)[-1]
        out.setdefault(name, []).append({
            "basename": f"{subdir}/{fn}",
            "version": version,
            "attrs": {"subdir": subdir, "build_number": int(bn) if bn.isdigit() else 0,
                      "removed": True},
        })
    return out


def _repodata_index(subdir):
    """Channel repodata for *subdir* (CDN), cached on disk + in memory.

    Fallback for hosts whose proxy blocks ``api.anaconda.org`` but allows the
    ``conda.anaconda.org`` CDN the archives come from anyway.
    """
    if subdir not in _REPODATA_INDEX:
        _require_zstd()
        zst = f"{CACHE}/repodata-{subdir}.json.zst"
        if not os.path.exists(zst):
            _retrieve(REPODATA.format(subdir), zst)
        raw = subprocess.run(["zstd", "-dc", zst], check=True, capture_output=True).stdout
        _REPODATA_INDEX[subdir] = repodata_files(json.loads(raw), subdir)
    return _REPODATA_INDEX[subdir]


def list_files(pkg, subdir="linux-64"):
    p = f"{CACHE}/{pkg}.api.json"
    if not os.path.exists(p):
        try:
            _retrieve(API.format(pkg), p)
        except OSError:  # API host unreachable/blocked: use the CDN's repodata
            return list(_repodata_index(subdir).get(pkg, []))
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    fs = [f for f in d["files"] if f["attrs"].get("subdir") == subdir]
    # newest build per (version): sort by version then build_number
    return fs


#: Suffixes conda-forge uses for the split package that carries a library's
#: ``include/`` tree when the runtime package does not (``tbb`` → ``tbb-devel``).
DEVEL_SUFFIXES = ("", "-devel", "-dev", "-headers", "-include", "-static")


def devel_candidates(pkg):
    """Package names that may ship *pkg*'s headers, most likely first (pure).

    The runtime package itself comes first (zstd/lz4-c ship headers inline),
    then the ``-devel``/``-dev``/``-headers``/``-include``/``-static`` splits, then the same
    for the name with a ``lib`` prefix stripped (``libzlib`` → ``zlib``).
    """
    stems = [pkg]
    if pkg.startswith("lib") and len(pkg) > 3:
        stems.append(pkg[3:])
    out = []
    for stem in stems:
        for suf in DEVEL_SUFFIXES:
            if stem + suf not in out:
                out.append(stem + suf)
    return out


def fetch_extract(pkg, version, subdir="linux-64"):
    """Download + extract one package version; return its extract dir."""
    arch, _, _ = download(pkg, version, subdir)
    out = os.path.join(CACHE, f"ex_{pkg}_{version}")
    if not os.path.isdir(out) or not os.listdir(out):
        extract(arch, out)
    return out


def resolve_headers_pkg(pkg, version, candidates=None, subdir="linux-64"):
    """First candidate package at *version* whose extract has an ``include/`` dir.

    Returns ``(package, include_root)``; raises ``LookupError`` naming every
    candidate tried when none ships headers for that version.
    """
    tried = []
    for cand in candidates or devel_candidates(pkg):
        if not any(f["version"] == version for f in list_files(cand, subdir)):
            tried.append(f"{cand} (no {version})")
            continue
        inc = os.path.join(fetch_extract(cand, version, subdir), "include")
        if os.path.isdir(inc):
            return cand, inc
        tried.append(f"{cand} (no include/)")
    raise LookupError(f"no header package for {pkg} {version}: tried " + ", ".join(tried))

def pick(pkg, version, subdir="linux-64"):
    """Pick the highest build_number .conda (fallback .tar.bz2) for a version."""
    fs = [f for f in list_files(pkg, subdir) if f["version"] == version]
    if not fs:
        raise SystemExit(f"no files for {pkg} {version} in {subdir}")
    conda = [f for f in fs if f["basename"].endswith(".conda")]
    pool = conda or fs
    # a live build always beats one the index has since removed
    pool.sort(key=lambda f: (not f["attrs"].get("removed", False),
                             f["attrs"].get("build_number", 0), f["basename"]))
    return pool[-1]

def download(pkg, version, subdir="linux-64"):
    f = pick(pkg, version, subdir)
    base = os.path.basename(f["basename"])
    dest = f"{CACHE}/{base}"
    dl_t = 0.0
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    if not os.path.exists(dest):
        dl_t, size = _get(CDN.format(base), dest)
    return dest, dl_t, size

def extract(archive, outdir):
    """Extract a .conda or .tar.bz2 into outdir. Returns extract seconds."""
    t0 = time.time()
    shutil.rmtree(outdir, ignore_errors=True)  # no stale files from a prior build/extract
    os.makedirs(outdir, exist_ok=True)
    if archive.endswith(".conda"):
        _require_zstd()
        with zipfile.ZipFile(archive) as z:
            inner = [n for n in z.namelist() if n.startswith("pkg-") and n.endswith(".tar.zst")]
            if not inner:
                raise RuntimeError(f"no pkg-*.tar.zst payload in {archive}")
            tmp = outdir + "/_inner.tar.zst"
            with z.open(inner[0]) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
        subprocess.run(["tar", "--zstd", "-xf", tmp, "-C", outdir], check=True)
        os.remove(tmp)
    else:
        with tarfile.open(archive, "r:bz2") as t:
            _safe_extract_tar(t, outdir)
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
        import re
        def _vkey(s):
            # homogeneous (int, str) tuples per component so mixed numeric/suffix
            # versions (1.0.2 vs 1.1.1w) stay comparable under sorted()
            return [(int(p), "") if p.isdigit() else (0, p)
                    for p in re.split(r"(\d+)", s) if p]
        for v in sorted(c, key=_vkey):
            print(v, c[v])
    elif cmd == "fetch":
        pkg, ver = sys.argv[2], sys.argv[3]
        arch, dl_t, size = download(pkg, ver)
        out = os.path.join(CACHE, f"ex_{pkg}_{ver}")
        ex_t = extract(arch, out)
        sos = find_sos(out)
        print(json.dumps({"archive": os.path.basename(arch), "dl_s": round(dl_t,2),
                          "size_kb": size//1024, "extract_s": round(ex_t,2),
                          "sos": [os.path.relpath(s, out) for s in sos]}, indent=2))

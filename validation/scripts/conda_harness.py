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

"""Shared conda-forge fetch → extract → ``abicheck compare`` engine.

This is the source-agnostic core behind the validation harness: given a version
pair (from *any* expectation source — a hand-curated manifest, the
abi-laboratory tracker oracle, or a future cross-checker), it resolves each
version to a conda-forge package, downloads and extracts the shared objects,
and runs ``abicheck compare`` to obtain a verdict. The verdict is then scored
against the source's expectation by the caller (``validate.py``).

Nothing here knows *where the expectation came from* — that is the whole point
of the unification: one engine, many sources.

Pure resolution/extraction helpers are unit-tested offline
(``tests/test_conda_harness.py``); only the network/conda/abicheck calls touch
the outside world.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
VALID_DIR = SCRIPTS_DIR.parent  # validation/

ANACONDA_API = "https://api.anaconda.org/package/conda-forge/{pkg}"
CONDA_CHANNEL = "https://conda.anaconda.org/conda-forge/"
USER_AGENT = "abicheck-validation/1.0 (+https://github.com/napetrov/abicheck)"

# Reuse the verdict normalisation/ranking from the scoring module.
sys.path.insert(0, str(SCRIPTS_DIR))
from fetch_tracker_oracle import load_results_map  # noqa: E402


def conda_download_url(basename: str) -> str:
    """Build a conda-forge download URL from an anaconda.org ``basename``.

    The API ``basename`` already carries the subdir (e.g.
    ``linux-64/libxml2-2.9.4-4.tar.bz2``), so it appends directly to the
    channel root.
    """
    return CONDA_CHANNEL + basename.lstrip("/")


def build_number(basename: str) -> int:
    """Best-effort conda build number from a filename (for picking newest)."""
    stem = re.sub(r"\.(conda|tar\.bz2)$", "", basename.rsplit("/", 1)[-1])
    m = re.search(r"_(\d+)$", stem) or re.search(r"-(\d+)$", stem)
    return int(m.group(1)) if m else -1


def select_conda_basename(
    api_json: dict, version: str, subdir: str = "linux-64"
) -> str | None:
    """Pick the newest build's basename for ``version`` in ``subdir``.

    Returns ``None`` if the version is not published for that subdir. ``.tar.bz2``
    and ``.conda`` are both eligible; ties break toward the highest build number,
    then lexicographically (stable, deterministic).
    """
    candidates = [
        f["basename"]
        for f in api_json.get("files", [])
        if f.get("version") == version and f.get("attrs", {}).get("subdir") == subdir
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda b: (build_number(b), b))


def logical_name(path: str) -> str:
    """Strip ``.so`` suffix and any embedded version to a logical library name."""
    base = path.rsplit("/", 1)[-1]
    stem = base.split(".so")[0]
    return re.sub(r"-(?:\d+\.)+\d+$", "", stem)


def query_conda(pkg: str, timeout: float = 30.0) -> dict:
    """Fetch the anaconda.org file listing for a conda-forge package."""
    req = urllib.request.Request(
        ANACONDA_API.format(pkg=pkg), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed api host
        return json.loads(resp.read())


def fetch_file(url: str, dest: Path, timeout: float = 60.0) -> None:
    """Download ``url`` to ``dest`` (fixed conda-forge host)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed conda host
        dest.write_bytes(resp.read())


def safe_extractall(tf: tarfile.TarFile, into: Path) -> None:
    """tarfile.extractall with the ``data`` filter when the runtime supports it.

    The ``filter`` kwarg predates some Python 3.10.x patch releases; conda
    packages are a trusted source, so fall back to a plain extract there.
    """
    try:
        tf.extractall(into, filter="data")
    except TypeError:
        tf.extractall(into)  # noqa: S202


def extract_tar_zst(zst_path: Path, into: Path) -> None:
    """Extract a ``.tar.zst`` into ``into``.

    Prefers pure-Python backends so the loop runs without a system zstd binary:
    the ``zstandard`` package, then the 3.14+ stdlib ``compression.zstd``; falls
    back to ``tar --zstd`` if only the CLI tool is present. Raises ``RuntimeError``
    when no backend is available (the caller turns that into a skipped pair).
    """
    try:
        import zstandard
    except ImportError:
        zstandard = None  # type: ignore[assignment]
    if zstandard is not None:
        with zst_path.open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            with tarfile.open(fileobj=reader, mode="r|") as tf:
                safe_extractall(tf, into)
        return

    try:
        from compression import zstd  # type: ignore[import-not-found]  # Python 3.14+
    except ImportError:
        zstd = None  # type: ignore[assignment]
    if zstd is not None:
        with (
            zstd.ZstdFile(zst_path, "rb") as fh,
            tarfile.open(fileobj=fh, mode="r|") as tf,
        ):
            safe_extractall(tf, into)
        return

    if shutil.which("tar"):
        subprocess.run(
            ["tar", "--zstd", "-xf", str(zst_path), "-C", str(into)], check=True
        )
        return

    raise RuntimeError(
        f"cannot extract {zst_path.name}: no zstd backend available "
        "(pip install zstandard, or install zstd + GNU tar)"
    )


def extract_sos(pkg: Path, into: Path) -> dict[str, str]:
    """Extract shared objects from a conda package; return logical_name -> path.

    Handles ``.tar.bz2`` natively and ``.conda`` (a zip of zstd tarballs) via a
    pure-Python zstd backend, with a ``tar --zstd`` fallback. Only real
    (non-symlink) ``lib/*.so*`` files are kept.
    """
    into.mkdir(parents=True, exist_ok=True)
    if pkg.name.endswith(".tar.bz2"):
        with tarfile.open(pkg, "r:bz2") as tf:
            safe_extractall(tf, into)
    elif pkg.name.endswith(".conda"):
        with zipfile.ZipFile(pkg) as zf:
            inner = next(
                (
                    n
                    for n in zf.namelist()
                    if n.startswith("pkg-") and n.endswith(".tar.zst")
                ),
                None,
            )
            if inner is None:
                return {}
            zf.extract(inner, into)
        extract_tar_zst(into / inner, into)
    else:
        return {}

    out: dict[str, str] = {}
    for so in into.glob("lib/*.so*"):
        if so.is_symlink() or not so.is_file():
            continue
        try:
            with so.open("rb") as fh:
                if fh.read(4) != b"\x7fELF":
                    continue  # skip GNU ld linker scripts / other non-ELF .so files
        except OSError:
            continue
        out[logical_name(so.name)] = str(so)
    return out


def abicheck_verdict(old: str, new: str, old_ver: str, new_ver: str) -> str | None:
    """Run ``abicheck compare`` on two .so files and return its verdict."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        "abicheck",
        "compare",
        old,
        new,
        "--old-version",
        old_ver,
        "--new-version",
        new_ver,
        "--format",
        "json",
        "-o",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(Path(out_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    summ = data.get("summary", {}) if isinstance(data, dict) else {}
    return data.get("verdict") or summ.get("verdict")


def resolve_pair(pair: dict, api: dict, subdir: str) -> tuple[str, str] | None:
    """Resolve a pair to (old_basename, new_basename), or ``None`` if unavailable.

    A source may pin exact conda filenames (``old_file``/``new_file``, e.g. the
    curated manifest); otherwise the newest build for each version is resolved
    from the anaconda.org listing.
    """
    if pair.get("old_file") and pair.get("new_file"):
        return f"{subdir}/{pair['old_file']}", f"{subdir}/{pair['new_file']}"
    ob = select_conda_basename(api, pair["old_ver"], subdir)
    nb = select_conda_basename(api, pair["new_ver"], subdir)
    if not ob or not nb:
        return None
    return ob, nb


def evaluate_pair(
    pair: dict, api: dict, subdir: str, tmp: Path, idx: int
) -> str | None:
    """Fetch, extract, and run abicheck for one pair; return the verdict.

    Source-agnostic: ``pair`` only needs ``old_ver``/``new_ver``/``pair`` (and may
    carry ``old_file``/``new_file`` to pin exact builds). Returns ``None`` when the
    pair can't be evaluated (not on conda-forge, fetch/extract error, no shared
    object common to both versions, or abicheck produced no verdict). Prints a
    per-pair line on success and a diagnostic on hard errors. The loop index
    ``idx`` gives every attempt a fresh extraction slot so a skipped pair can't
    leak stale ``.so`` files into the next.
    """
    pid = pair["pair"]
    resolved = resolve_pair(pair, api, subdir)
    if resolved is None:
        return None  # version not on conda-forge; stays UNCOMPARABLE upstream
    ob, nb = resolved

    try:
        # Preserve the real extension so extract_sos can dispatch on it.
        op = tmp / f"old_{idx}_{Path(ob).name}"
        npath = tmp / f"new_{idx}_{Path(nb).name}"
        fetch_file(conda_download_url(ob), op)
        fetch_file(conda_download_url(nb), npath)
        old_sos = extract_sos(op, tmp / f"old_{idx}")
        new_sos = extract_sos(npath, tmp / f"new_{idx}")
    except (
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
        RuntimeError,
    ) as exc:
        print(f"  {pid}: fetch/extract failed: {exc}", file=sys.stderr)
        return None

    common = sorted(set(old_sos) & set(new_sos))
    if not common:
        return None
    # Take the most-breaking verdict across the shared objects in the pair.
    ov, nv = pair["old_ver"], pair["new_ver"]
    verdicts = [
        v
        for name in common
        if (v := abicheck_verdict(old_sos[name], new_sos[name], ov, nv)) is not None
    ]
    if not verdicts:
        return None
    verdict = load_results_map([{"pair": pid, "verdict": v} for v in verdicts])[pid]
    print(
        f"  {pid}: abicheck={verdict} expected={pair.get('expected_verdict')} "
        f"({','.join(common)})"
    )
    return verdict

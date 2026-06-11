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

"""End-to-end abi-laboratory parity loop: harvest → fetch → compare.

This closes the loop opened by ``fetch_tracker_oracle.py``. Given a harvested
oracle (``validation/data/tracker_oracle/<lib>.json``), for each consecutive
version pair whose *both* versions exist on conda-forge it:

  1. resolves each version to a conda-forge package via the anaconda.org API,
  2. downloads and extracts the shared objects,
  3. runs ``abicheck compare`` on the matched ``.so``,
  4. scores the resulting verdict against the tracker's published verdict
     (reusing ``compare_to_results`` from ``fetch_tracker_oracle``).

It prints the agreement rate and every divergence — ``ABICHECK_WEAKER`` rows
are likely false negatives and the highest-value signal. A full report is
written to ``validation/data/tracker_parity/<lib>.json`` (gitignored).

Binaries are fetched on demand and never committed. Pure resolution helpers
(``conda_download_url`` / ``select_conda_basename``) are unit-tested offline;
only ``main`` touches the network, conda, or abicheck.

Usage:
    # harvest the oracle first (see fetch_tracker_oracle.py), then:
    python validation/scripts/run_tracker_parity.py libxml2
    python validation/scripts/run_tracker_parity.py zstd --pkg zstd --max-pairs 5
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
VALID_DIR = SCRIPTS_DIR.parent  # validation/
ORACLE_DIR = VALID_DIR / "data" / "tracker_oracle"
PARITY_DIR = VALID_DIR / "data" / "tracker_parity"

ANACONDA_API = "https://api.anaconda.org/package/conda-forge/{pkg}"
CONDA_CHANNEL = "https://conda.anaconda.org/conda-forge/"
USER_AGENT = "abicheck-tracker-parity/1.0 (+https://github.com/napetrov/abicheck)"

# Reuse the scoring + results-coercion logic from the harvester.
sys.path.insert(0, str(SCRIPTS_DIR))
from fetch_tracker_oracle import compare_to_results, load_results_map  # noqa: E402


def conda_download_url(basename: str) -> str:
    """Build a conda-forge download URL from an anaconda.org ``basename``.

    The API ``basename`` already carries the subdir (e.g.
    ``linux-64/libxml2-2.9.4-4.tar.bz2``), so it appends directly to the
    channel root.
    """
    return CONDA_CHANNEL + basename.lstrip("/")


def _build_number(basename: str) -> int:
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
    return max(candidates, key=lambda b: (_build_number(b), b))


def _logical_name(path: str) -> str:
    """Strip ``.so`` suffix and any embedded version to a logical library name."""
    base = path.rsplit("/", 1)[-1]
    stem = base.split(".so")[0]
    return re.sub(r"-(?:\d+\.)+\d+$", "", stem)


def _fetch(url: str, dest: Path, timeout: float = 60.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # fixed conda-forge host
        dest.write_bytes(resp.read())


def _extract_sos(pkg: Path, into: Path) -> dict[str, str]:
    """Extract shared objects from a conda package; return logical_name -> path.

    Handles ``.tar.bz2`` natively and ``.conda`` (zip of zstd tarballs) via the
    system ``tar --zstd``. Only real (non-symlink) ``lib/*.so*`` files are kept.
    """
    into.mkdir(parents=True, exist_ok=True)
    if pkg.name.endswith(".tar.bz2"):
        with tarfile.open(pkg, "r:bz2") as tf:
            tf.extractall(into, filter="data")
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
        subprocess.run(
            ["tar", "--zstd", "-xf", str(into / inner), "-C", str(into)], check=True
        )
    else:
        return {}

    out: dict[str, str] = {}
    for so in into.glob("lib/*.so*"):
        if so.is_symlink() or not so.is_file():
            continue
        out[_logical_name(so.name)] = str(so)
    return out


def _abicheck_verdict(old: str, new: str, old_ver: str, new_ver: str) -> str | None:
    """Run ``abicheck compare`` and return its verdict, or None on failure."""
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


def _verdict_for_pair(
    pair: dict, api: dict, subdir: str, tmp: Path, idx: int
) -> str | None:
    """Resolve, fetch, extract, and run abicheck for one oracle pair.

    Returns the (conservatively aggregated) abicheck verdict, or ``None`` when
    the pair can't be evaluated (version not on conda-forge, fetch/extract
    error, no shared object common to both versions, or abicheck produced no
    verdict). Prints a per-pair line on success and a diagnostic on hard errors.
    """
    ov, nv, pid = pair["old_ver"], pair["new_ver"], pair["pair"]
    ob = select_conda_basename(api, ov, subdir)
    nb = select_conda_basename(api, nv, subdir)
    if not ob or not nb:
        return None  # version not on conda-forge; stays UNCOMPARABLE upstream

    try:
        # Preserve the real extension so _extract_sos can dispatch on it.
        op = tmp / f"old_{idx}_{Path(ob).name}"
        npath = tmp / f"new_{idx}_{Path(nb).name}"
        _fetch(conda_download_url(ob), op)
        _fetch(conda_download_url(nb), npath)
        old_sos = _extract_sos(op, tmp / f"old_{idx}")
        new_sos = _extract_sos(npath, tmp / f"new_{idx}")
    except (OSError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f"  {pid}: fetch/extract failed: {exc}", file=sys.stderr)
        return None

    common = sorted(set(old_sos) & set(new_sos))
    if not common:
        return None
    # Take the most-breaking verdict across the shared objects in the pair.
    verdicts = [
        v
        for name in common
        if (v := _abicheck_verdict(old_sos[name], new_sos[name], ov, nv)) is not None
    ]
    if not verdicts:
        return None
    verdict = load_results_map([{"pair": pid, "verdict": v} for v in verdicts])[pid]
    print(
        f"  {pid}: abicheck={verdict} oracle={pair['expected_verdict']} ({','.join(common)})"
    )
    return verdict


def main(argv: list[str] | None = None) -> int:
    """Run the harvest→fetch→compare parity loop for one library."""
    ap = argparse.ArgumentParser(
        description="Score abicheck against the abi-laboratory oracle on conda-forge binaries."
    )
    ap.add_argument(
        "library",
        help="tracker library slug (must already be harvested into tracker_oracle/)",
    )
    ap.add_argument(
        "--pkg", help="conda-forge package name if it differs from the tracker slug"
    )
    ap.add_argument(
        "--subdir", default="linux-64", help="conda subdir (default: linux-64)"
    )
    ap.add_argument(
        "--max-pairs",
        type=int,
        default=0,
        help="limit number of pairs (0 = all usable)",
    )
    args = ap.parse_args(argv)

    lib = args.library
    pkg = args.pkg or lib
    oracle_path = ORACLE_DIR / f"{lib}.json"
    if not oracle_path.is_file():
        print(
            f"no oracle for {lib}: run fetch_tracker_oracle.py {lib} first",
            file=sys.stderr,
        )
        return 1

    oracle = json.loads(oracle_path.read_text())
    req = urllib.request.Request(
        ANACONDA_API.format(pkg=pkg), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            api = json.loads(resp.read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"failed to query conda-forge for {pkg}: {exc}", file=sys.stderr)
        return 1

    results: dict[str, str] = {}
    done = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for pair in oracle["pairs"]:
            if args.max_pairs and done >= args.max_pairs:
                break
            verdict = _verdict_for_pair(pair, api, args.subdir, tmp, done)
            if verdict is None:
                continue
            results[pair["pair"]] = verdict
            done += 1

    report = compare_to_results(oracle, results)
    PARITY_DIR.mkdir(parents=True, exist_ok=True)
    out = PARITY_DIR / f"{lib}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    c = report["counts"]
    rate = report["agreement_rate"]
    print(
        f"\n[{lib}] ran {done} pairs | comparable={report['comparable_pairs']} "
        f"agreement={'n/a' if rate is None else f'{rate:.1%}'} "
        f"match={c['MATCH']} stricter={c['ABICHECK_STRICTER']} "
        f"weaker={c['ABICHECK_WEAKER']} -> {out}"
    )
    for row in report["rows"]:
        if row["status"] == "ABICHECK_WEAKER":
            print(
                f"  WEAKER (likely FN): {row['pair']} "
                f"oracle={row['expected_verdict']} abicheck={row['abicheck_verdict']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

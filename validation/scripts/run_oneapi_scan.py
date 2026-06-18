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
"""Binary-tier oneAPI scan driver (validation/oneapi-scan-2026-06.md).

For each planned ``(lib, pair)`` it downloads the **pinned** conda-forge / Intel
artifacts, dumps the old side and runs ``abicheck scan --source-method s0``,
recording verdict / coverage / DWARF presence / SONAME / wall time to
``data/oneapi_scan_2026-06.json``. Network + ``abicheck`` on PATH required; this
is a slow real-world lane, not a unit test.

Reproducibility: each pair pins the exact ``old_file``/``new_file`` build
basename (like ``data/manifest.json``), so a rebuild publishing a higher build
number for the same version cannot silently change which artifacts are scanned.
"""

from __future__ import annotations

import atexit
import glob
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conda_harness as ch  # noqa: E402

# A fresh per-run temp dir (portable; no /tmp hardcode or concurrent-run
# collision), cleaned up at exit. Downloaded artifacts and per-pair scan JSONs
# are scratch; only the aggregated results land in data/.
WORK = pathlib.Path(tempfile.mkdtemp(prefix="oneapi_run_"))
atexit.register(shutil.rmtree, WORK, ignore_errors=True)
SUBDIR = "linux-64"

# Pinned exact build basenames per pair (reproducibility — see module docstring).
PAIRS = [
    {
        "lib": "oneTBB",
        "pkg": "tbb",
        "channel": "conda-forge",
        "old": "2021.12.0",
        "new": "2021.13.0",
        "so_glob": "libtbb.so.12*",
        "expectation": "COMPATIBLE",
        "note": "minor, same SONAME",
        "old_file": "tbb-2021.12.0-h84d6215_4.conda",
        "new_file": "tbb-2021.13.0-hb700be7_6.conda",
    },
    {
        "lib": "oneTBB",
        "pkg": "tbb",
        "channel": "conda-forge",
        "old": "2021.13.0",
        "new": "2023.0.0",
        "so_glob": "libtbb.so.12*",
        "expectation": "COMPATIBLE",
        "note": "wide gap, internal removals (scope test)",
        "old_file": "tbb-2021.13.0-hb700be7_6.conda",
        "new_file": "tbb-2023.0.0-hab88423_2.conda",
    },
    {
        "lib": "oneDNN",
        "pkg": "onednn",
        "channel": "conda-forge",
        "old": "3.11",
        "new": "3.12",
        "so_glob": "libdnnl.so*",
        "expectation": "COMPATIBLE",
        "note": "minor, libdnnl.so.3 (tbb variant)",
        "old_file": "onednn-3.11-tbb_h2a4fcdb_0.conda",
        "new_file": "onednn-3.12-tbb_h2a4fcdb_0.conda",
    },
    {
        "lib": "oneDNN",
        "pkg": "onednn",
        "channel": "conda-forge",
        "old": "2.7.2",
        "new": "3.0",
        "so_glob": "libdnnl.so*",
        "expectation": "BREAKING",
        "note": "SONAME .so.2 -> .so.3 (tbb variant)",
        "old_file": "onednn-2.7.2-tbb_hb007830_0.conda",
        "new_file": "onednn-3.0-tbb_h7022a57_0.conda",
    },
    {
        "lib": "oneDAL",
        "pkg": "dal",
        "channel": "conda-forge",
        "old": "2025.0.0",
        "new": "2025.1.0",
        "so_glob": "libonedal_core.so*",
        "expectation": "COMPATIBLE",
        "note": "minor, same SONAME",
        "old_file": "dal-2025.0.0-h9289deb_961.conda",
        "new_file": "dal-2025.1.0-h9289deb_124.conda",
    },
    {
        "lib": "oneDAL",
        "pkg": "dal",
        "channel": "conda-forge",
        "old": "2024.7.0",
        "new": "2025.0.0",
        "so_glob": "libonedal_core.so*",
        "expectation": "BREAKING",
        "note": "SONAME .so.2 -> .so.3",
        "old_file": "dal-2024.7.0-h58b1d36_15.conda",
        "new_file": "dal-2025.0.0-h9289deb_961.conda",
    },
    {
        "lib": "oneCCL",
        "pkg": "oneccl-devel",
        "channel": "intel",
        "old": "2021.12.0",
        "new": "2021.13.0",
        "so_glob": "libccl.so*",
        "expectation": "COMPATIBLE",
        "note": "minor, libccl.so.1",
        "old_file": "oneccl-devel-2021.12.0-intel_309.tar.bz2",
        "new_file": "oneccl-devel-2021.13.0-intel_299.tar.bz2",
    },
]


def real_so(extract_dir: pathlib.Path, so_glob: str) -> str | None:
    """The fully-versioned (non-symlink) .so matching *so_glob*."""
    matches = [
        x
        for x in sorted(glob.glob(str(extract_dir / "lib" / so_glob)))
        if not pathlib.Path(x).is_symlink() and ".so" in pathlib.Path(x).name
    ]
    matches.sort(key=len, reverse=True)
    return matches[0] if matches else None


def dt_soname(so_path: str) -> str:
    """The ELF ``DT_SONAME`` (e.g. ``libtbb.so.12``), or the filename if absent.

    The on-disk file is versioned more deeply than its SONAME
    (``libtbb.so.12.13``), so the SONAME — not the path — is what distinguishes a
    stable-SONAME minor from a real SONAME bump.
    """
    out = subprocess.run(
        ["readelf", "-d", so_path], capture_output=True, text=True
    ).stdout
    m = re.search(r"\(SONAME\).*\[(.*?)\]", out)
    return m.group(1) if m else pathlib.Path(so_path).name


def run() -> list[dict]:
    apis: dict[tuple[str, str], dict] = {}
    results: list[dict] = []
    for spec in PAIRS:
        pkg, chan = spec["pkg"], spec["channel"]
        api = apis.setdefault((pkg, chan), ch.query_conda(pkg, channel=chan))
        row = {
            k: spec[k]
            for k in (
                "lib",
                "pkg",
                "channel",
                "old",
                "new",
                "expectation",
                "note",
                "old_file",
                "new_file",
            )
        }
        ob = f"{SUBDIR}/{spec['old_file']}"
        nb = f"{SUBDIR}/{spec['new_file']}"
        old_dir = WORK / f"{spec['lib']}_{spec['old']}"
        new_dir = WORK / f"{spec['lib']}_{spec['new']}"
        for bn, dd in [(ob, old_dir), (nb, new_dir)]:
            local = WORK / pathlib.Path(bn).name
            ch.fetch_file(ch.conda_download_url(bn, api, channel=chan), local)
            ch.extract_sos(local, dd)
        old_so = real_so(old_dir, spec["so_glob"])
        new_so = real_so(new_dir, spec["so_glob"])
        if not old_so or not new_so:
            row["status"] = "NO_SO"
            results.append(row)
            continue
        # SONAME from ELF (DT_SONAME) distinguishes stable-SONAME minors from
        # bumps; sofile_* keeps the deeper on-disk filename for provenance.
        row["soname_old"] = dt_soname(old_so)
        row["soname_new"] = dt_soname(new_so)
        row["sofile_old"] = pathlib.Path(old_so).name
        row["sofile_new"] = pathlib.Path(new_so).name
        row["dwarf_old"] = ch.has_dwarf(old_so)
        row["dwarf_new"] = ch.has_dwarf(new_so)
        base = WORK / f"{spec['lib']}_{spec['old']}.abi.json"
        try:
            subprocess.run(
                ["abicheck", "dump", old_so, "-o", str(base)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            row["status"] = "DUMP_FAILED"
            row["detail"] = (exc.stderr or "")[-300:]
            results.append(row)
            continue
        out = WORK / f"{spec['lib']}_{spec['old']}_{spec['new']}_s0.json"
        out.unlink(missing_ok=True)  # never parse a stale report from a prior run
        start = time.monotonic()
        proc = subprocess.run(
            [
                "abicheck",
                "scan",
                "--binary",
                new_so,
                "--baseline",
                str(base),
                "--source-method",
                "s0",
                "--format",
                "json",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        row["wall_s"] = round(time.monotonic() - start, 2)
        row["exit"] = proc.returncode
        # A BREAKING scan exits non-zero (2/4) but still writes JSON; gate on a
        # freshly-written report, not the exit code.
        if not out.exists():
            row["status"] = "SCAN_FAILED"
            row["detail"] = proc.stderr[-300:]
            results.append(row)
            continue
        try:
            scan = json.loads(out.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            row["status"] = "SCAN_FAILED"
            row["detail"] = (proc.stderr or str(exc))[-300:]
            results.append(row)
            continue
        cov = {r["layer"]: r["status"] for r in scan.get("coverage", [])}
        row["verdict"] = scan.get("verdict")
        row["L0"] = cov.get("L0_binary")
        row["L1"] = cov.get("L1_debug")
        row["diff"] = scan.get("diff") or {}
        row["status"] = "OK"
        results.append(row)
    return results


if __name__ == "__main__":
    rows = run()
    dest = (
        pathlib.Path(__file__).resolve().parent.parent
        / "data"
        / "oneapi_scan_2026-06.json"
    )
    dest.write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(
            f"{r['status']:11} {r['lib']:7} {r['old']}->{r['new']} "
            f"verdict={r.get('verdict')} soname={r.get('soname_old')}->"
            f"{r.get('soname_new')} wall={r.get('wall_s')}s"
        )
    print("wrote", dest)

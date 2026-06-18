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

Fetches each planned ``(lib, pair)`` from conda-forge / the Intel channel, dumps
the old side and runs ``abicheck scan --source-method s0``, recording verdict /
coverage / DWARF presence / SONAME / wall time to
``data/oneapi_scan_2026-06.json``. Network + ``abicheck`` on PATH required; this
is a slow real-world lane, not a unit test.
"""

from __future__ import annotations

import glob
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conda_harness as ch  # noqa: E402

WORK = pathlib.Path("/tmp/oneapi_run")

# (lib, pkg, channel, old, new, so_glob, expectation, note)
PAIRS = [
    (
        "oneTBB",
        "tbb",
        "conda-forge",
        "2021.12.0",
        "2021.13.0",
        "libtbb.so.12*",
        "COMPATIBLE",
        "minor, same SONAME",
    ),
    (
        "oneTBB",
        "tbb",
        "conda-forge",
        "2021.13.0",
        "2023.0.0",
        "libtbb.so.12*",
        "COMPATIBLE",
        "wide gap, internal symbol removals (scope test)",
    ),
    (
        "oneDNN",
        "onednn",
        "conda-forge",
        "3.11",
        "3.12",
        "libdnnl.so*",
        "COMPATIBLE",
        "minor, libdnnl.so.3",
    ),
    (
        "oneDNN",
        "onednn",
        "conda-forge",
        "2.7.2",
        "3.0",
        "libdnnl.so*",
        "BREAKING",
        "SONAME .so.2 -> .so.3",
    ),
    (
        "oneDAL",
        "dal",
        "conda-forge",
        "2025.0.0",
        "2025.1.0",
        "libonedal_core.so*",
        "COMPATIBLE",
        "minor, same SONAME",
    ),
    (
        "oneDAL",
        "dal",
        "conda-forge",
        "2024.7.0",
        "2025.0.0",
        "libonedal_core.so*",
        "BREAKING",
        "SONAME .so.2 -> .so.3",
    ),
    (
        "oneCCL",
        "oneccl-devel",
        "intel",
        "2021.12.0",
        "2021.13.0",
        "libccl.so*",
        "COMPATIBLE",
        "minor, libccl.so.1",
    ),
]


def variant_ok(pkg: str, basename: str) -> bool:
    """Hold oneDNN's threading variant constant (compare like-for-like)."""
    return pkg != "onednn" or "tbb_" in basename


def pick(api: dict, version: str, pkg: str) -> str | None:
    """Newest linux-64 build basename for *version* (variant-filtered)."""
    cands = [
        f["basename"]
        for f in api.get("files", [])
        if f.get("version") == version
        and f.get("attrs", {}).get("subdir") == "linux-64"
        and variant_ok(pkg, f["basename"])
    ]
    if not cands:
        return None
    return max(cands, key=lambda b: (ch.build_number(b), b))


def real_so(extract_dir: pathlib.Path, so_glob: str) -> str | None:
    """The fully-versioned (non-symlink) .so matching *so_glob*."""
    matches = [
        x
        for x in sorted(glob.glob(str(extract_dir / "lib" / so_glob)))
        if not pathlib.Path(x).is_symlink() and ".so" in pathlib.Path(x).name
    ]
    matches.sort(key=len, reverse=True)
    return matches[0] if matches else None


def run() -> list[dict]:
    WORK.mkdir(exist_ok=True)
    apis: dict[tuple[str, str], dict] = {}
    results: list[dict] = []
    for lib, pkg, chan, ov, nv, so_glob, exp, note in PAIRS:
        api = apis.setdefault((pkg, chan), ch.query_conda(pkg, channel=chan))
        row = {
            "lib": lib,
            "pkg": pkg,
            "channel": chan,
            "old": ov,
            "new": nv,
            "expectation": exp,
            "note": note,
        }
        ob, nb = pick(api, ov, pkg), pick(api, nv, pkg)
        if not ob or not nb:
            row["status"] = "UNAVAILABLE"
            results.append(row)
            continue
        row["old_file"] = pathlib.Path(ob).name
        row["new_file"] = pathlib.Path(nb).name
        old_dir, new_dir = WORK / f"{lib}_{ov}", WORK / f"{lib}_{nv}"
        for bn, dd in [(ob, old_dir), (nb, new_dir)]:
            local = WORK / pathlib.Path(bn).name
            ch.fetch_file(ch.conda_download_url(bn, api, channel=chan), local)
            ch.extract_sos(local, dd)
        old_so, new_so = real_so(old_dir, so_glob), real_so(new_dir, so_glob)
        if not old_so or not new_so:
            row["status"] = "NO_SO"
            results.append(row)
            continue
        row["soname_old"] = pathlib.Path(old_so).name
        row["soname_new"] = pathlib.Path(new_so).name
        row["dwarf_new"] = ch.has_dwarf(new_so)
        base = WORK / f"{lib}_{ov}.abi.json"
        subprocess.run(
            ["abicheck", "dump", old_so, "-o", str(base)],
            check=True,
            capture_output=True,
        )
        out = WORK / f"{lib}_{ov}_{nv}_s0.json"
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
        scan = json.loads(out.read_text())
        cov = {r["layer"]: r["status"] for r in scan.get("coverage", [])}
        row["verdict"] = scan.get("verdict")
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
            f"verdict={r.get('verdict')} wall={r.get('wall_s')}s"
        )
    print("wrote", dest)

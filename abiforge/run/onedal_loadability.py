# SPDX-License-Identifier: Apache-2.0
"""Compare the ELF loader and abicheck deps compare for the four oneDAL CPU DSOs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

COMPONENTS = ("core", "thread", "parameters", "main")


def run(prefix, out):
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"loadability requires an empty output directory: {out}")
    baseline = out / "baseline"
    baseline.mkdir(parents=True, exist_ok=True)
    lib = prefix / "lib"
    names = [
        f"libonedal{'_' + name if name != 'main' else ''}.so.3" for name in COMPONENTS
    ]
    source = out / "client.c"
    source.write_text("int main(void) { return 0; }\n")
    exe = baseline / "client"
    subprocess.run(
        [
            "cc",
            source,
            "-Wl,--no-as-needed",
            *(lib / n for n in names),
            f"-Wl,-rpath-link,{lib}",
            "-o",
            exe,
        ],
        check=True,
    )
    linked = subprocess.check_output(
        ["ldd", exe], env={**os.environ, "LD_LIBRARY_PATH": str(lib)}, text=True
    )
    sources = {
        Path(t).name: Path(t)
        for line in linked.splitlines()
        for t in line.split()
        if t.startswith("/") and Path(t).is_file()
    }
    if not set(names) <= sources.keys():
        raise ValueError("could not resolve all four CPU providers")
    results = []
    for absent in (None, *names):
        image = out / (absent or "baseline")
        directory = image / "lib"
        directory.mkdir(parents=True, exist_ok=True)
        for name, path in sources.items():
            target = directory / name
            if name != absent and not target.exists():
                # A regular file also gives the checker's sysroot resolver a local target.
                shutil.copyfile(path, target)
                target.chmod(0o755)
        if absent:
            (directory / absent).unlink(missing_ok=True)
            shutil.copyfile(exe, image / "client")
            (image / "client").chmod(0o755)
        env = {**os.environ, "LD_LIBRARY_PATH": str(directory), "LD_BIND_NOW": "1"}
        actual = subprocess.run(
            [image / "client"], env=env, capture_output=True, text=True
        )
        if absent is None:
            control = out / "control"
            control.mkdir(exist_ok=True)
            shutil.copytree(directory, control / "lib", dirs_exist_ok=True)
            shutil.copy2(exe, control / "client")
            image = control
        report = image / "report.json"
        report.unlink(missing_ok=True)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "deps",
                "compare",
                "client",
                "--root",
                f"old={baseline}",
                "--root",
                f"new={image}",
                "--ld-library-path",
                "/lib",
                "-o",
                f"json={report}",
            ],
            capture_output=True,
        )
        if not report.is_file():
            raise RuntimeError(proc.stderr.decode(errors="replace")[-1000:])
        result = json.loads(report.read_text())
        loadability = result["verdict"]["loadability"]
        matches = (
            (
                actual.returncode == 127
                and absent in actual.stderr
                and loadability == "fail"
            )
            if absent
            else (actual.returncode == 0 and loadability == "pass")
        )
        results.append(
            dict(
                absent=absent,
                loader_exit=actual.returncode,
                loadability=loadability,
                matches=matches,
            )
        )
    (out / "receipt.json").write_text(json.dumps(results, indent=2) + "\n")
    return all(row["matches"] for row in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    raise SystemExit(not run(args.prefix.resolve(), args.out.resolve()))

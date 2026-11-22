"""Compare a precompiled Linux client's loader outcome with abicheck deps compare.

Run with python -m abiforge.run.provider_campaign OUTPUT_DIRECTORY.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    (root / "value.c").write_text("int value(void) { return 17; }")
    (root / "anchor.c").write_text("int anchor(void) { return 0; }")
    (root / "client.c").write_text(
        "int value(void); int main(void){return value()!=17;}"
    )
    for tag in ("V1", "V2"):
        (root / tag).write_text(f"{tag} {{ global: value; anchor; local: *; }};")
    rows = []
    for case in ("old", "reachable", "missing", "wrong_version", "unreachable"):
        image = root / case
        lib = image / "lib"
        lib.mkdir(parents=True, exist_ok=True)

        def build(name, source, tag, *extra):
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-nostdlib",
                    "-s",
                    root / source,
                    f"-Wl,--version-script={root / tag}",
                    *extra,
                    "-o",
                    lib / name,
                ],
                check=True,
            )

        if case == "old":
            build("libapi.so", "value.c", "V1")
            subprocess.run(
                [
                    "gcc",
                    root / "client.c",
                    "-L" + str(lib),
                    "-lapi",
                    "-o",
                    image / "client",
                ],
                check=True,
            )
        else:
            build("libowner.so", "value.c", "V2" if case == "wrong_version" else "V1")
            link = (
                []
                if case == "unreachable"
                else ["-L" + str(lib), "-Wl,--no-as-needed", "-lowner"]
            )
            build("libapi.so", "anchor.c", "V1", *link)
            if case == "missing":
                (lib / "libowner.so").unlink()
            shutil.copy2(root / "old/client", image / "client")
        # Supply the interpreter and libc actually used by this Linux host.
        for line in subprocess.check_output(
            ["ldd", str(root / "old/client")],
            env={**os.environ, "LD_LIBRARY_PATH": str(root / "old/lib")},
            text=True,
        ).splitlines():
            for token in line.split():
                if (
                    token.startswith("/")
                    and Path(token).is_file()
                    and Path(token).name != "libapi.so"
                ):
                    if Path(token).resolve() != (lib / Path(token).name).resolve():
                        shutil.copy2(token, lib / Path(token).name)
        actual = subprocess.run(
            [image / "client"],
            env={**os.environ, "LD_LIBRARY_PATH": str(lib), "LD_BIND_NOW": "1"},
            capture_output=True,
        )
        if (actual.returncode == 0) != (case in ("old", "reachable")):
            raise RuntimeError((case, actual.stderr))
        if case == "old":
            continue
        report = image / "stack.json"
        report.unlink(missing_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck.cli",
                "deps",
                "compare",
                "client",
                "--root",
                "old=" + str(root / "old"),
                "--root",
                "new=" + str(image),
                "--ld-library-path",
                "/lib",
                "-o",
                "json=" + str(report),
            ],
            capture_output=True,
        )
        result = json.loads(report.read_text())
        if result["verdict"]["loadability"] != (
            "pass" if actual.returncode == 0 else "fail"
        ):
            raise AssertionError((case, result["verdict"]))
        rows.append(
            [
                case,
                actual.returncode,
                result["verdict"]["loadability"],
                result["verdict"]["abi_risk"],
            ]
        )
    with (root / "results.csv").open("w") as f:
        writer = csv.writer(f)
        writer.writerow(["case", "client_exit", "loadability", "abi_risk"])
        writer.writerows(rows)


if __name__ == "__main__":
    run(Path(sys.argv[1]))

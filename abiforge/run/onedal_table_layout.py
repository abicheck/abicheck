# SPDX-License-Identifier: Apache-2.0
"""Replay frozen host and SYCL clients across releases and a data_layout enum mutation."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2] / "research/onedal-sycl-usm/layout_consumer.cpp"
)
HEADER = Path("oneapi/dal/table/common.hpp")


def check_layout(old, out):
    cli = [sys.executable, "-m", "abicheck"]
    env = {**os.environ, "ABICHECK_AST_FRONTEND": "clang"}
    for variant in ("old", "extend", "swap"):
        directory = out / variant
        subprocess.run(
            [
                *cli,
                "dump",
                directory / "liblayout.so",
                "-H",
                directory / HEADER,
                "-I",
                old / "include",
                "-o",
                out / f"{variant}.json.gz",
            ],
            check=True,
            env=env,
        )
    findings = {}
    for right in ("old", "extend", "swap"):
        report = out / f"compare-{right}.json"
        result = subprocess.run(
            [
                *cli,
                "compare",
                out / "old.json.gz",
                out / f"{right}.json.gz",
                "-o",
                f"json={report}",
            ],
            env=env,
        )
        data = json.loads(report.read_text())
        if right == "extend" and not any(
            c["kind"] == "enum_member_added" and c["symbol"] == "data_layout::reserved"
            for c in data["changes"]
        ):
            raise ValueError("missing appended enumerator in L2 report")
        members = sorted(
            c["symbol"]
            for c in data["changes"]
            if c["kind"]
            in ("enum_member_value_changed", "enum_last_member_value_changed")
            and "data_layout" in c["symbol"]
        )
        findings[right] = dict(
            verdict=data["verdict"], members=members, exit=result.returncode
        )
    return findings


def run(old, minor, compiler, out, selector):
    out.mkdir(parents=True, exist_ok=True)
    original = (old / "include" / HEADER).read_text()
    values = "enum class data_layout { unknown, row_major, column_major };"
    if original.count(values) != 1:
        raise ValueError("review the SDK data_layout declaration")
    source = out / "provider.cpp"
    source.write_text(
        '#include <oneapi/dal/table/common.hpp>\nextern "C" oneapi::dal::data_layout column_layout() { return oneapi::dal::data_layout::column_major; }\n'
    )
    for variant, text in (
        ("old", original),
        (
            "extend",
            original.replace(
                values,
                "enum class data_layout { unknown, row_major, column_major, reserved };",
            ),
        ),
        (
            "swap",
            original.replace(
                values, "enum class data_layout { unknown, column_major, row_major };"
            ),
        ),
    ):
        inc = out / variant
        header = inc / HEADER
        header.parent.mkdir(exist_ok=True, parents=True)
        header.write_text(text)
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-shared",
                "-fPIC",
                "-I",
                inc,
                "-I",
                old / "include",
                source,
                "-o",
                inc / "liblayout.so",
            ],
            check=True,
        )
    rows = []
    for mode in ("host", "sycl"):
        device = mode == "sycl"
        suffix = "_dpc" if device else ""
        providers = [
            f"libonedal{part}.so.3"
            for part in (suffix, "_parameters" + suffix, "_core", "_thread")
        ]
        client = out / mode
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                *(["-fsycl", "-DONEDAL_DATA_PARALLEL"] if device else []),
                "-I",
                old / "include",
                SOURCE,
                "-L",
                out / "old",
                "-llayout",
                "-L",
                old / "lib",
                "-Wl,--no-as-needed",
                *(f"-l:{name}" for name in providers),
                "-o",
                client,
            ],
            check=True,
        )
        for sdk in (old, minor):
            for variant in ("old", "extend", "swap"):
                env = {
                    **os.environ,
                    "ONEAPI_DEVICE_SELECTOR": selector,
                    "LD_LIBRARY_PATH": os.pathsep.join(
                        [
                            str(out / variant),
                            str(sdk / "lib"),
                            os.environ.get("LD_LIBRARY_PATH", ""),
                        ]
                    ),
                }
                linked = subprocess.check_output(["ldd", client], env=env, text=True)
                for name, directory in (
                    ("liblayout.so", out / variant),
                    *((name, sdk / "lib") for name in providers),
                ):
                    if f"{name} => {directory / name} (" not in linked:
                        raise ValueError(f"unexpected provider for {name}")
                result = subprocess.run([client], env=env)
                rows.append(
                    dict(
                        mode=mode,
                        package=sdk.name,
                        provider=variant,
                        exit=result.returncode,
                    )
                )
    findings = check_layout(old, out)
    receipt = {"clients": rows, "selector": selector, "checker": findings}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if findings["old"] != {"verdict": "NO_CHANGE", "members": [], "exit": 0}:
        return False
    if findings["extend"] != {"verdict": "COMPATIBLE", "members": [], "exit": 0}:
        return False
    if findings["swap"] != {
        "verdict": "BREAKING",
        "members": ["data_layout::column_major", "data_layout::row_major"],
        "exit": 4,
    }:
        return False
    return all(r["exit"] == (42 if r["provider"] == "swap" else 0) for r in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "compiler", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--selector", default="opencl:cpu")
    args = parser.parse_args()
    raise SystemExit(
        not run(
            args.old.resolve(),
            args.minor.resolve(),
            args.compiler.resolve(),
            args.out.resolve(),
            args.selector,
        )
    )

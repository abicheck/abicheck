# SPDX-License-Identifier: Apache-2.0
"""Replay frozen host and SYCL clients across releases and table enum mutations."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2] / "research/onedal-sycl-usm/layout_consumer.cpp"
)
HEADER = Path("oneapi/dal/table/common.hpp")


def sparse_width_changes(report):
    """Sizes from enum findings are bytes; record layout findings use bits."""
    relevant = {
        ("enum_underlying_size_changed", "sparse_indexing"),
        ("type_size_changed", "sparse_request"),
        ("type_alignment_changed", "sparse_request"),
        ("type_field_offset_changed", "sparse_request"),
    }
    return {
        c["kind"]: [c["old_value"], c["new_value"]]
        for c in report["changes"]
        if (c["kind"], c["symbol"]) in relevant
    }


def check_layout(old, out, enum_name, variants, frontend):
    cli = [sys.executable, "-m", "abicheck"]
    env = {**os.environ, "ABICHECK_AST_FRONTEND": frontend}
    for variant in variants:
        directory = out / variant
        subprocess.run(
            [
                *cli,
                "dump",
                directory / "liblayout.so",
                "-H",
                directory / HEADER,
                *(
                    ["-H", directory / "sparse_indexing_request.hpp", "-I", directory]
                    if enum_name == "sparse_indexing"
                    else []
                ),
                "-I",
                old / "include",
                "-o",
                out / f"{variant}.json.gz",
            ],
            check=True,
            env=env,
        )
    findings = {}
    for right in variants:
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
            c["kind"] == "enum_member_added" and c["symbol"] == f"{enum_name}::reserved"
            for c in data["changes"]
        ):
            raise ValueError("missing appended enumerator in L2 report")
        members = sorted(
            c["symbol"]
            for c in data["changes"]
            if c["kind"]
            in ("enum_member_value_changed", "enum_last_member_value_changed")
            and c["symbol"].startswith(enum_name + "::")
        )
        findings[right] = dict(
            verdict=data["verdict"], members=members, exit=result.returncode
        )
        if right == "narrow":
            findings[right]["layout_changes"] = sparse_width_changes(data)
    return findings


def expected_layout(findings, variants, enum_name, first, second, rows):
    if findings["old"] != {"verdict": "NO_CHANGE", "members": [], "exit": 0}:
        return False
    if findings["extend"] != {"verdict": "COMPATIBLE", "members": [], "exit": 0}:
        return False
    if findings["swap"] != {
        "verdict": "BREAKING",
        "members": sorted(f"{enum_name}::{member}" for member in (first, second)),
        "exit": 4,
    }:
        return False
    if "narrow" in variants and findings["narrow"] != {
        "verdict": "BREAKING",
        "exit": 4,
        "members": [],
        "layout_changes": {
            "enum_underlying_size_changed": ["4", "1"],
            "type_size_changed": ["64", "16"],
            "type_alignment_changed": ["32", "8"],
            "type_field_offset_changed": ["32", "8"],
        },
    }:
        return False
    return all(
        r["exit"] == (42 if r["provider"] in ("swap", "narrow") else 0) for r in rows
    )


def run(old, minor, compiler, out, selector, enum_name="data_layout", frontend="clang"):
    out.mkdir(parents=True, exist_ok=True)
    original = (old / "include" / HEADER).read_text()
    if enum_name == "sparse_indexing":
        prefix, first, second = "", "zero_based", "one_based"
        implementation = (
            '#include "sparse_indexing_request.hpp"\n'
            'extern "C" void request_zero_based(sparse_request* request) { request->indexing = oneapi::dal::sparse_indexing::zero_based; }\n'
        )
        consumer = SOURCE.with_name("sparse_indexing_consumer.cpp")
    elif enum_name == "data_layout":
        prefix, first, second = "unknown, ", "row_major", "column_major"
        implementation = (
            "#include <oneapi/dal/table/common.hpp>\n"
            'extern "C" oneapi::dal::data_layout column_layout() { return oneapi::dal::data_layout::column_major; }\n'
        )
        consumer = SOURCE
    else:
        raise ValueError(f"unknown enum: {enum_name}")

    def declaration(members):
        return f"enum class {enum_name} {{ {prefix}{members} }};"

    values = declaration(f"{first}, {second}")
    if original.count(values) != 1:
        raise ValueError(f"review the SDK {enum_name} declaration")
    source = out / "provider.cpp"
    source.write_text(implementation)
    variants = {
        "old": values,
        "extend": declaration(f"{first}, {second}, reserved"),
        "swap": declaration(f"{second}, {first}"),
    }
    if enum_name == "sparse_indexing":
        variants["narrow"] = values.replace(" {", " : unsigned char {")
    for variant, replacement in variants.items():
        inc = out / variant
        header = inc / HEADER
        header.parent.mkdir(exist_ok=True, parents=True)
        header.write_text(original.replace(values, replacement))
        if variant == "narrow":
            for name in ("table_iface.hpp", "csr_access_iface.hpp"):
                relative = HEADER.parent / "detail" / name
                forward = (old / "include" / relative).read_text()
                if forward.count("enum class sparse_indexing;") != 1:
                    raise ValueError(
                        f"review sparse_indexing forward declaration in {relative}"
                    )
                target = inc / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    forward.replace(
                        "enum class sparse_indexing;",
                        "enum class sparse_indexing : unsigned char;",
                    )
                )
        if enum_name == "sparse_indexing":
            shutil.copyfile(
                consumer.with_name("sparse_indexing_request.hpp"),
                inc / "sparse_indexing_request.hpp",
            )
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
                consumer,
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
            for variant in variants:
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
    findings = check_layout(old, out, enum_name, variants, frontend)
    receipt = {"clients": rows, "selector": selector, "checker": findings}
    if enum_name == "sparse_indexing":
        receipt["frontend"] = frontend
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return expected_layout(findings, variants, enum_name, first, second, rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "compiler", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--selector", default="opencl:cpu")
    parser.add_argument(
        "--enum", choices=("data_layout", "sparse_indexing"), default="data_layout"
    )
    parser.add_argument("--frontend", choices=("clang", "castxml"), default="clang")
    args = parser.parse_args()
    raise SystemExit(
        not run(
            args.old.resolve(),
            args.minor.resolve(),
            args.compiler.resolve(),
            args.out.resolve(),
            args.selector,
            args.enum,
            args.frontend,
        )
    )

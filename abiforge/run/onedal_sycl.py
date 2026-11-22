"""Generate and judge a oneDAL queue-overload withdrawal on a real SYCL SDK.

python -m abiforge.run.onedal_sycl OUT --prefix CONDA_PREFIX
Requires Linux, icpx, dal, dal-include and a SYCL device with shared USM.
The fixture is a consumer-facing bridge, never a modified oneDAL release.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

from abiforge.checkers import abicheck_cli

# Each expression exercises the real host or queue overload. All return the
# same observable value for one float equal to 7.
_OPERATIONS = {
    "array_wrap": "d::array<float>::wrap(ARGS p, 1).get_data()[0]",
    "table_wrap": "d::homogen_table::wrap(ARGS p, 1, 1).get_row_count() + 6",
    "row_pull": "d::row_accessor<const float>{d::homogen_table::wrap(ARGS p, 1, 1)}.pull(ARGS d::range{0, 1}).get_data()[0]",
    "column_pull": "d::column_accessor<const float>{d::homogen_table::wrap(ARGS p, 1, 1)}.pull(ARGS 0).get_data()[0]",
    "array_empty": "d::array<float>::empty(ARGS 7 ALLOC).get_count()",
    "array_zeros": "7 + d::array<float>::zeros(ARGS 1 ALLOC).get_data()[0]",
    "array_full": "d::array<float>::full(ARGS 1, *p ALLOC).get_data()[0]",
}
_PREAMBLE = """#include <oneapi/dal/array.hpp>
#include <oneapi/dal/table/homogen.hpp>
#include <oneapi/dal/table/column_accessor.hpp>
#include <oneapi/dal/table/row_accessor.hpp>
namespace d = oneapi::dal;
"""


def generate(directory: Path, queues: bool) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # The public bridge needs an incomplete queue for reference declarations.
    # Including actual SDK headers in the implementation checks ABI identity.
    header = [
        "#pragma once",
        "namespace sycl { inline namespace _V1 { class queue; } }",
    ]
    exports = []
    source = [_PREAMBLE, '#include "api.hpp"']
    for name, expression in _OPERATIONS.items():
        for with_queue in (False, True):
            args = "sycl::queue& q, " if with_queue else ""
            signature = f"int {name}({args}float* p)"
            body = expression.replace("ARGS ", "q, " if with_queue else "").replace(
                " ALLOC", ", sycl::usm::alloc::shared" if with_queue else ""
            )
            if queues or not with_queue:
                header.append(signature + ";")
                suffix = "RN4sycl3_V15queueEPf" if with_queue else "Pf"
                exports.append(f"_Z{len(name)}{name}{suffix};")

            source.append(f"{signature} {{ return static_cast<int>({body}); }}")
    (directory / "exports.map").write_text(
        "{ global: " + " ".join(exports) + " local: *; };\n"
    )
    (directory / "api.hpp").write_text("\n".join(header) + "\n")
    (directory / "bridge.cpp").write_text("\n".join(source) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--prefix", type=Path, required=True)
    args = parser.parse_args()
    root, prefix = args.out.resolve(), args.prefix.resolve()
    old, new = root / "old", root / "withdrawn"
    generate(old, True)
    generate(new, False)
    lib = prefix / "lib"
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = os.pathsep.join([str(lib), env.get("LD_LIBRARY_PATH", "")])
    env.setdefault("OCL_ICD_VENDORS", str(prefix / "etc/OpenCL/vendors"))
    cc = [
        str(prefix / "bin/icpx"),
        "-std=c++17",
        "-fsycl",
        "-DONEDAL_DATA_PARALLEL",
        "-I",
        str(prefix / "include"),
    ]
    links = [
        "-L",
        str(lib),
        "-l:libonedal_dpc.so.3",
        "-Wl,--no-as-needed",
        "-l:libonedal_parameters_dpc.so.3",
        "-l:libonedal_core.so.3",
        "-l:libonedal_thread.so.3",
    ]

    def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(argv, env=env, text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError(f"{argv}: {result.stderr}")
        return result

    receipt = {}
    for side in (old, new):
        run(
            [
                *cc,
                "-shared",
                "-fPIC",
                str(side / "bridge.cpp"),
                f"-Wl,--version-script={side / 'exports.map'}",
                *links,
                "-o",
                str(side / "libbridge.so"),
            ]
        )
    for queue in (False, True):
        call_args = "q, p" if queue else "p"
        init = (
            "sycl::queue q; auto p = sycl::malloc_shared<float>(1, q);"
            if queue
            else "float storage; auto p = &storage;"
        )
        calls = " && ".join(f"{op}({call_args}) == 7" for op in _OPERATIONS)
        release = "sycl::free(p, q);" if queue else ""
        consumer = root / ("queue.cpp" if queue else "cpu.cpp")
        consumer.write_text(
            '#include <sycl/sycl.hpp>\n#include "api.hpp"\n'
            f"int main() {{ {init} *p = 7; bool ok = {calls}; {release} return !ok; }}\n"
        )
        exe = consumer.with_suffix("")
        run(
            [
                *cc,
                "-I",
                str(old),
                str(consumer),
                "-L",
                str(old),
                "-lbridge",
                *links,
                "-o",
                str(exe),
            ]
        )
        for side in (old, new):
            result = run(
                [
                    "env",
                    f"LD_LIBRARY_PATH={side}:{env['LD_LIBRARY_PATH']}",
                    str(exe),
                ],
                check=False,
            )
            rebuild = run(
                [*cc, "-I", str(side), "-fsyntax-only", str(consumer)], check=False
            )
            withdrawn = queue and side == new
            receipt[f"{consumer.stem}/{side.name}"] = {
                "run": result.returncode,
                "rebuild": rebuild.returncode,
                "loader_error": result.stderr.strip(),
                "matches": (
                    (
                        "undefined symbol" in result.stderr
                        and result.returncode != 0
                        and rebuild.returncode != 0
                        and "no matching function" in rebuild.stderr
                    )
                    if withdrawn
                    else result.returncode == rebuild.returncode == 0
                ),
            }
    config = root / "compiler.yml"
    config.write_text(
        json.dumps(
            {
                "compile": {
                    "frontend": "clang",
                    "compiler": cc[0],
                    "options": ["-std=c++17", "-fsycl"],
                    "defines": ["ONEDAL_DATA_PARALLEL"],
                }
            }
        )
    )
    for side in (old, new):
        stem = f"compare-{side.name}"
        report_path = root / f"{stem}.json"
        report_path.unlink(missing_ok=True)
        check = abicheck_cli.run(
            old / "libbridge.so",
            side / "libbridge.so",
            root,
            headers=([old / "api.hpp"], [side / "api.hpp"]),
            stem=stem,
            config=config,
        )
        if check.steps[-1].exit not in abicheck_cli.ANSWER_EXITS:
            raise RuntimeError(check.steps[-1].stderr_tail)
        report = abicheck_cli.parse(report_path.read_text())
        kinds = Counter(c["kind"] for c in report["changes"])
        affected = [
            sym
            for c in report["changes"]
            if c["kind"] == "sycl_overload_set_removed"
            for sym in c["affected_symbols"]
        ]
        expected = {f"_Z{len(name)}{name}RN4sycl3_V15queueEPf" for name in _OPERATIONS}
        receipt[stem] = {
            "verdict": report["verdict"],
            "kinds": kinds,
            "affected": affected,
            "matches": (
                report["verdict"] == "NO_CHANGE" and not kinds and not affected
                if side == old
                else report["verdict"] == "BREAKING"
                and Counter(affected) == Counter(expected)
                and kinds == {"sycl_overload_set_removed": 1}
            ),
        }
    print(json.dumps(receipt, indent=2))
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return int(not all(row["matches"] for row in receipt.values()))


if __name__ == "__main__":
    raise SystemExit(main())

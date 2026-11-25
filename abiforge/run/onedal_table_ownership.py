# SPDX-License-Identifier: Apache-2.0
"""Replay oneDAL table ownership mutants against frozen SDK clients on Linux."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import subprocess
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[2] / "research/onedal-coverage/product"
MODES = {"baseline": 0, "forward": 0, "borrow": 11, "extra_owner": 12}


def build(old, out, rows):
    flags = ["g++", "-O1", "-std=c++17", f"-I{old}/include", f"-I{old}/include/dal"]
    (out / "return_getters.inc").write_text(
        "".join(
            f'CHECK_GETTER("{r["case"]}", {r["result_type"]}, {r["slot"]}, {"false" if r["access"] == "direct" else "true"})\n'
            for r in rows
        )
    )
    client = out / "client"
    subprocess.run(
        [
            *flags,
            f"-I{out}",
            PRODUCT / "return_client.cpp",
            old / "lib/libonedal_core.so.3",
            old / "lib/libonedal_thread.so.3",
            f"-Wl,-rpath-link,{old}/lib",
            "-o",
            client,
        ],
        check=True,
    )
    imports = {
        line.split()[-1]
        for line in subprocess.check_output(
            ["nm", "-D", "--undefined-only", client], text=True
        ).splitlines()
    }
    source = [
        '#include "daal.h"',
        "#include <cstdlib>",
        "#include <cstring>",
        "#include <dlfcn.h>",
        "using namespace daal::algorithms;",
        "using namespace daal::data_management;",
    ]
    for i, r in enumerate(rows):
        symbol = r["symbol"]
        if symbol not in imports:
            raise ValueError(f"missing client import {symbol}")
        params = f"const {r['result_type']} *self, {r['id_type']} id"
        args = "self, id"
        if r["access"] == "alpha":
            cv = "" if r["result_type"].startswith("brownboost::") else "const "
            params = f"{cv}{r['result_type']} *self"
            args = "self"
        elif r["access"] not in ("direct", "block"):
            params += f", {r['access']} index"
            args += ", index"
        source.append(f'''NumericTablePtr adapter{i}({params}) asm("{symbol}");
NumericTablePtr adapter{i}({params}) {{
    using Function = NumericTablePtr (*)({params});
    auto next = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "{symbol}"));
    if (!next) std::abort();
    auto result = next({args});
    if (std::strcmp(std::getenv("ONEDAL_CASE"), "{r["case"]}")) return result;
    const char *mode = std::getenv("ONEDAL_MODE");
    if (!std::strcmp(mode, "borrow")) return NumericTablePtr(NumericTablePtr(), result.get());
    if (!std::strcmp(mode, "extra_owner")) {{ static NumericTablePtr extra; extra = result; }}
    return result;
}}''')
    cpp = out / "adapters.cpp"
    cpp.write_text("\n".join(source))
    exports = out / "adapters.map"
    exports.write_text(
        "{ global: " + "; ".join(r["symbol"] for r in rows) + "; local: *; };"
    )
    so = out / "adapters.so"
    subprocess.run(
        [
            *flags,
            "-shared",
            "-fPIC",
            "-s",
            cpp,
            f"-Wl,--version-script={exports}",
            "-ldl",
            "-o",
            so,
        ],
        check=True,
    )
    return client, so


def run(old, new, out):
    if old.name == new.name:
        raise ValueError(
            "release prefixes need distinct package names for the ownership receipt"
        )
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in ("return-getters.csv", "table-accessors.csv"):
        with (PRODUCT / name).open() as stream:
            rows.extend(csv.DictReader(stream))
    for row in rows:
        if row["access"] == "indexed":
            row["access"] = "size_t"
    with gzip.open(PRODUCT / "ledger.csv.gz", "rt") as stream:
        expected = {
            r["symbol"]
            for r in csv.DictReader(stream)
            if r["kind"] == "by_value_return"
            and r["detail"] == "data_management::NumericTablePtr"
            and r["providers"]
        }
    if len(rows) != len(expected) or {r["symbol"] for r in rows} != expected:
        raise ValueError("table accessor manifest differs from the product ledger")
    client, adapter = build(old, out, rows)
    results = []
    for prefix in (old, new):
        for row in rows:
            for mode, expected in MODES.items():
                env = {
                    **os.environ,
                    "LD_LIBRARY_PATH": str(prefix / "lib"),
                    "ONEDAL_CASE": row["case"],
                    "ONEDAL_MODE": mode,
                }
                env.pop("LD_PRELOAD", None)
                if mode != "baseline":
                    env["LD_PRELOAD"] = str(adapter)
                proc = subprocess.run(
                    [client, row["case"]], env=env, capture_output=True, text=True
                )
                results.append(
                    dict(
                        package=prefix.name,
                        symbol=row["symbol"],
                        mode=mode,
                        exit=proc.returncode,
                        expected=expected,
                    )
                )
    by_case = {}
    for row in results:
        by_case.setdefault((row["symbol"], row["package"]), {})[row["mode"]] = row[
            "exit"
        ]
    with (out / "table-ownership.csv").open("w") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("symbol", "package", *MODES))
        for (symbol, package), modes in sorted(by_case.items()):
            writer.writerow((symbol, package, *(modes[m] for m in MODES)))
    (out / "receipt.json").write_text(json.dumps(results, indent=2) + "\n")
    return all(row["exit"] == row["expected"] for row in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "new", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    raise SystemExit(
        not run(args.old.resolve(), args.new.resolve(), args.out.resolve())
    )

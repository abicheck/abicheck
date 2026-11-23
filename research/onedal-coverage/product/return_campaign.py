"""Generate and run oneDAL by-value return probes on Linux x86-64 (Itanium ABI)."""

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
with (HERE / "return-getters.csv").open() as stream:
    GETTERS = list(csv.DictReader(stream))
STATUS = "_ZN4daal10algorithms10interface113AlgorithmImplILNS_11ComputeModeE1EE14computeNoThrowEv"

# The adapters return the SDK's C++ type by value, including its ABI return slot.
SIGNATURES = {
    **{
        row["case"]: (
            row["symbol"],
            "NumericTablePtr",
            f"const {row['result_type']} *self, {row['id_type']} id",
            "self, id",
        )
        for row in GETTERS
    },
    "statistics": (
        "_ZN4daal15data_management10interface112NumericTable23allocateBasicStatisticsEv",
        "Status",
        "NumericTable *self",
        "self",
    ),
    "status": (STATUS, "Status", "AlgorithmImpl<batch> *self", "self"),
}
TABLE_EDITS = {
    "forward": ("return result;", 0),
    "borrow": ("return NumericTablePtr(NumericTablePtr(), result.get());", 11),
    "extra_owner": (
        "static NumericTablePtr extra; extra = result; return result;",
        12,
    ),
}
EDITS = {
    **{row["case"]: TABLE_EDITS for row in GETTERS},
    "statistics": {
        "forward": ("return result;", 0),
        "reject_success": ("return Status(ErrorNullByteInjection);", 30),
        "drop_sum": (
            "self->basicStatistics.set(NumericTable::sum, NumericTablePtr()); return result;",
            31,
        ),
    },
    "status": {
        "forward": ("return result;", 0),
        "swallow_error": ("result.clear(); return result;", 20),
        "reject_success": (
            "return result.ok() ? Status(ErrorNullByteInjection) : result;",
            23,
        ),
    },
}


def build_adapter(directory, family, name, body, flags):
    symbol, result, params, args = SIGNATURES[family]
    source = directory / f"{family}-{name}.cpp"
    source.write_text(f'''#include "daal.h"
#include <cstdlib>
#include <dlfcn.h>
using namespace daal;
using namespace daal::services;
using namespace daal::algorithms;
using namespace daal::data_management;
{result} replacement({params}) asm("{symbol}");
{result} replacement({params}) {{
    using Function = {result} (*)({params});
    auto next = reinterpret_cast<Function>(dlsym(RTLD_NEXT, "{symbol}"));
    if (!next) std::abort();
    auto result = next({args});
    {body}
}}
''')
    exports = directory / f"{family}.map"
    exports.write_text(f"{{ global: {symbol}; local: *; }};")
    output = source.with_suffix(".so")
    subprocess.run(
        [
            "g++",
            *flags,
            "-shared",
            "-fPIC",
            "-s",
            source,
            "-ldl",
            f"-Wl,--version-script={exports}",
            "-o",
            output,
        ],
        check=True,
    )
    return output


def run(old, new, output):
    output.mkdir(parents=True, exist_ok=True)
    flags = ["-std=c++17", "-O1", f"-I{old}/include", f"-I{old}/include/dal"]
    flags.append(f"-I{output}")
    (output / "return_getters.inc").write_text(
        "".join(
            f'CHECK_GETTER("{row["case"]}", {row["result_type"]}, {row["slot"]})\n'
            for row in GETTERS
        )
    )
    client = output / "client"
    subprocess.run(
        [
            "g++",
            *flags,
            HERE / "return_client.cpp",
            old / "lib/libonedal_core.so.3",
            old / "lib/libonedal_thread.so.3",
            f"-Wl,-rpath-link,{old}/lib",
            "-o",
            client,
        ],
        check=True,
    )
    imports = subprocess.check_output(
        ["nm", "-D", "--undefined-only", client], text=True
    )
    rows = []
    scan_rows = []
    for family, variants in EDITS.items():
        symbol = SIGNATURES[family][0]
        if symbol not in {
            line.split()[-1] for line in imports.splitlines() if line.strip()
        }:
            raise RuntimeError(f"Missing client import: {symbol}")
        adapters = {
            name: build_adapter(output, family, name, body, flags)
            for name, (body, _) in variants.items()
        }
        for name, candidate in adapters.items():
            report = output / f"{family}-{name}.json"
            report.unlink(missing_ok=True)
            command = [
                sys.executable,
                "-m",
                "abicheck.cli",
                "compare",
                str(adapters["forward"]),
                str(candidate),
                "-o",
                f"json={report}",
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            if not report.is_file():
                raise RuntimeError((command, completed.returncode, completed.stderr))
            data = json.loads(report.read_text())
            kinds = sorted({c["kind"] for c in data["changes"]})
            scan_rows.append(
                (
                    family,
                    name,
                    "adapter_elf",
                    variants[name][1],
                    data["verdict"],
                    " ".join(kinds),
                )
            )
        for prefix in (old, new):
            for name in ("baseline", *variants):
                env = {
                    **os.environ,
                    "LD_LIBRARY_PATH": str(prefix / "lib"),
                    "LD_BIND_NOW": "1",
                }
                env.pop("LD_PRELOAD", None)
                if name in adapters:
                    env["LD_PRELOAD"] = str(adapters[name])
                observed = subprocess.run(
                    [client, family], env=env, capture_output=True, text=True
                )
                expected = 0 if name == "baseline" else variants[name][1]
                if observed.returncode != expected:
                    raise RuntimeError(
                        (
                            prefix,
                            family,
                            name,
                            observed.returncode,
                            observed.stdout,
                            observed.stderr,
                        )
                    )
                rows.append(
                    (
                        prefix.name,
                        family,
                        symbol,
                        name,
                        observed.returncode,
                        observed.stdout.strip().replace("\n", " "),
                    )
                )
    with (output / "return-witnesses.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            ["environment", "family", "symbol", "variant", "client_exit", "observation"]
        )
        writer.writerows(rows)
    with (output / "return-scans.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            ["family", "variant", "lane", "client_exit", "checker", "findings"]
        )
        writer.writerows(scan_rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "new", "output"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run(args.old.resolve(), args.new.resolve(), args.output.resolve())

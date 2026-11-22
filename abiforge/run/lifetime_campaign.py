"""Generate same-layout C++ lifetime mutants and run the original consumer.

Run with python -m abiforge.run.lifetime_campaign OUTPUT_DIRECTORY on Linux.
"""

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

HEADER = """
struct Value {
    int slot;
    Value();
    Value(const Value&);
    ~Value();
    int read() const;
};
Value make();
int deletions();
"""
PROVIDER = """
#include "value.hpp"
static int refs, releases;
Value::Value() : slot(17) { ++refs; }
Value::Value(const Value& v) : slot(v.slot) { COPY }
Value::~Value() { if (--refs == 0) { RELEASE } }
int Value::read() const { return refs > 0 ? slot : -1; }
Value make() { return Value(); }
int deletions() { return releases; }
"""
CLIENT = """
#include "value.hpp"
#include <cstdio>
int main() {
    int observed;
    {
        Value original = make();
        {
            Value copy(original);
            if (copy.read() != 17) return 2;
        }
        observed = original.read();
    }
    std::printf("%d %d\\n", observed, deletions());
}
"""
CASES = {
    "old": ("++refs;", "++releases;", (17, 1)),
    "compatible": ("refs += 1;", "releases += 1;", (17, 1)),
    "early_release": ("", "++releases;", (-1, 1)),
    "missing_release": ("++refs;", "", (17, 0)),
    "double_release": ("++refs;", "releases += 2;", (17, 2)),
}


def run(root: Path) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    header = root / "value.hpp"
    header.write_text(HEADER)
    (root / "client.cpp").write_text(CLIENT)
    rows = []
    for name, (copy, release, expected) in CASES.items():
        case = root / name
        case.mkdir(exist_ok=True)
        source = case / "value.cpp"
        source.write_text(PROVIDER.replace("COPY", copy).replace("RELEASE", release))
        lib = case / "libvalue.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-shared",
                "-fPIC",
                "-s",
                "-I",
                root,
                source,
                "-o",
                lib,
            ],
            check=True,
        )
        if name == "old":
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    root / "client.cpp",
                    "-L",
                    case,
                    "-lvalue",
                    "-o",
                    root / "client",
                ],
                check=True,
            )
        output = subprocess.check_output(
            [root / "client"],
            env={**os.environ, "LD_LIBRARY_PATH": str(case)},
            text=True,
        )
        observed = tuple(map(int, output.split()))
        if observed != expected:
            raise AssertionError((name, observed, expected))
        for lane, options in [
            ("elf", []),
            (
                "headers",
                ["--header", "old=" + str(header), "--header", "new=" + str(header)],
            ),
        ]:
            report = case / (lane + ".json")
            report.unlink(missing_ok=True)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "abicheck.cli",
                    "compare",
                    root / "old/libvalue.so",
                    lib,
                    *options,
                    "-o",
                    "json=" + str(report),
                ],
                capture_output=True,
            )
            data = json.loads(report.read_text())
            rows.append((name, *observed, lane, data["verdict"]))
    with (root / "results.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case", "surviving_value", "deleter_calls", "lane", "verdict"))
        writer.writerows(rows)


if __name__ == "__main__":
    run(Path(sys.argv[1]))

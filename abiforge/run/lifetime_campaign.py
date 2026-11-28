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
    Value& operator=(const Value&);
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
Value& Value::operator=(const Value& other) {
    ASSIGN
    slot = other.slot;
    return *this;
}
Value::~Value() { if (--refs == 0) { RELEASE } }
int Value::read() const { return refs > 0 ? slot : -1; }
Value make() { return Value(); }
int deletions() { return releases; }
"""
CLIENT = """
#include "value.hpp"
#include <cstdio>
int main() {
    int observed, after_assignment;
    {
        Value original = make();
        const Value& alias = original;
        original = alias;
        after_assignment = deletions();
        {
            Value copy(original);
            if (copy.read() != 17) return 2;
        }
        observed = original.read();
    }
    std::printf("%d %d %d\\n", observed, deletions(), after_assignment);
}
"""
# All handles share one tracked resource. Releasing its last reference runs
# the deleter, even when an assignment immediately restores the count.
RETAIN_FIRST = "++refs; if (--refs == 0) ++releases;"
RELEASE_FIRST = "if (--refs == 0) ++releases; ++refs;"
CASES = {
    "old": ("++refs;", "++releases;", RETAIN_FIRST, (17, 1)),
    "compatible": ("refs += 1;", "releases += 1;", RETAIN_FIRST, (17, 1)),
    "early_release": ("", "++releases;", RETAIN_FIRST, (-1, 1)),
    "missing_release": ("++refs;", "", RETAIN_FIRST, (17, 0)),
    "double_release": ("++refs;", "releases += 2;", RETAIN_FIRST, (17, 2)),
    "self_assignment_release": ("++refs;", "++releases;", RELEASE_FIRST, (17, 2)),
}


def run(root: Path) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    header = root / "value.hpp"
    header.write_text(HEADER)
    (root / "client.cpp").write_text(CLIENT)
    rows = []
    for name, (copy, release, assign, expected) in CASES.items():
        case = root / name
        case.mkdir(exist_ok=True)
        source = case / "value.cpp"
        source.write_text(
            PROVIDER.replace("COPY", copy)
            .replace("RELEASE", release)
            .replace("ASSIGN", assign)
        )
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
        if observed != (*expected, int(name == "self_assignment_release")):
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
            comparison = subprocess.run(
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
                text=True,
            )
            if not report.is_file():
                raise RuntimeError(
                    f"{name}/{lane}: checker exited {comparison.returncode} without a report: {comparison.stderr}"
                )
            data = json.loads(report.read_text())
            rows.append((name, *observed[:2], lane, data["verdict"], observed[2]))
    with (root / "results.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            (
                "case",
                "surviving_value",
                "deleter_calls",
                "lane",
                "verdict",
                "releases_after_assignment",
            )
        )
        writer.writerows(rows)


if __name__ == "__main__":
    run(Path(sys.argv[1]))

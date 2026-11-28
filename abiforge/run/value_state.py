"""Frozen C++ client for state propagation through return, copy, assignment and reset."""

import os
import subprocess
from pathlib import Path

HEADER = """struct Value {
    int state;
    explicit Value(int);
    Value(const Value&);
    Value& operator=(const Value&);
    void reset();
};
Value evaluate(bool fail);
"""
PROVIDER = """#include "api.hpp"
Value::Value(int n) : state(n) {}
Value::Value(const Value& from) : state(from.state) {}
Value& Value::operator=(const Value& from) { state = from.state; return *this; }
void Value::reset() { state = 0; }
Value evaluate(bool fail) { return Value(fail ? 7 : 0); }
"""
CLIENT = """#include "api.hpp"
#include <cstdio>
int main() {
    auto ok = evaluate(false);
    auto error = evaluate(true);
    Value source(7), copy(source), assigned(0);
    assigned = source;
    source.reset();
    std::printf("%d %d %d %d %d\\n", ok.state, error.state, copy.state, assigned.state, source.state);
}
"""
# Each edit changes one observation; the public declaration and layout stay fixed.
EDITS = {
    "baseline": None,
    "equivalent": ("fail ? 7 : 0", "7 * int(fail)"),
    "reject_success": ("fail ? 7 : 0", "7"),
    "swallow_error": ("fail ? 7 : 0", "0"),
    "discard_copy": ("state(from.state)", "state(0)"),
    "discard_assignment": ("state = from.state;", ""),
    "ignore_reset": ("state = 0;", ""),
}


def run(root: Path) -> dict[str, tuple[int, ...]]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "api.hpp").write_text(HEADER)
    client_source = root / "client.cpp"
    client_source.write_text(CLIENT)
    executable = root / "client"
    results = {}
    for name, edit in EDITS.items():
        directory = root / name
        directory.mkdir(exist_ok=True)
        source = directory / "value.cpp"
        source.write_text(PROVIDER.replace(*edit) if edit else PROVIDER)
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-shared",
                "-fPIC",
                "-I",
                root,
                source,
                "-o",
                directory / "libvalue.so",
            ],
            check=True,
        )
        if name == "baseline":
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    client_source,
                    "-L",
                    directory,
                    "-lvalue",
                    "-o",
                    executable,
                ],
                check=True,
            )
        observed = subprocess.check_output(
            [executable],
            text=True,
            env={**os.environ, "LD_LIBRARY_PATH": str(directory)},
        )
        results[name] = tuple(map(int, observed.split()))
    return results

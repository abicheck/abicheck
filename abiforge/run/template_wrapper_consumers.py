"""Generate two C++ consumer binding modes and independently hide each export."""

import subprocess
from pathlib import Path


def build_cases(tmp_path: Path):
    header = tmp_path / "value.hpp"
    header.write_text(
        "int read_impl(int);\n"
        "template<class T> T read_value(T n) { return read_impl(n); }\n"
    )
    source = tmp_path / "value.cpp"
    source.write_text(
        '#include "value.hpp"\n'
        "int read_impl(int n) { return n + 1; }\n"
        "template int read_value<int>(int);\n"
    )
    wrapper = "_Z10read_valueIiET_S0_"
    implementation = "_Z9read_impli"
    providers = {}
    for case, public in (
        ("both", [wrapper, implementation]),
        ("wrapper_only", [wrapper]),
        ("implementation_only", [implementation]),
    ):
        directory = tmp_path / case
        directory.mkdir()
        exports = directory / "exports.map"
        exports.write_text("{ global: " + "; ".join(public) + "; local: *; };\n")
        library = directory / "libvalue.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                source,
                f"-Wl,--version-script={exports}",
                "-o",
                library,
            ],
            check=True,
        )
        providers[case] = library

    clients = {}
    for name, declaration, required in (
        ("local_wrapper", "", implementation),
        ("exported_wrapper", "extern template int read_value<int>(int);", wrapper),
    ):
        client_source = tmp_path / f"{name}.cpp"
        client_source.write_text(
            '#include "value.hpp"\n'
            + declaration
            + "\nint main() { return read_value(6) == 7 ? 0 : 1; }\n"
        )
        client = tmp_path / name
        subprocess.run(
            [
                "g++",
                "-O2",
                client_source,
                "-L",
                providers["both"].parent,
                "-lvalue",
                "-o",
                client,
            ],
            check=True,
        )
        clients[client] = required

    return providers, clients, wrapper, implementation

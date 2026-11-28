"""An SDK wrapper can be imported or instantiated in the downstream binary."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_template_wrapper_and_implementation_have_distinct_consumers(tmp_path):
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

    for case, library in providers.items():
        report = tmp_path / f"{case}.json"
        compared = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                providers["both"],
                library,
                *(arg for client in clients for arg in ("--used-by", client)),
                "-o",
                f"json={report}",
            ]
        )
        data = json.loads(report.read_text())
        assert compared.returncode == (0 if case == "both" else 4)
        rows = {row["app"]: row for row in data["used_by"]}
        for client, required in clients.items():
            missing = (case == "wrapper_only" and required == implementation) or (
                case == "implementation_only" and required == wrapper
            )
            run = subprocess.run(
                [client],
                env={
                    **os.environ,
                    "LD_LIBRARY_PATH": str(library.parent),
                    "LD_BIND_NOW": "1",
                },
                capture_output=True,
                text=True,
            )
            assert run.returncode == (127 if missing else 0), run.stderr
            if missing:
                assert required in run.stderr
            row = rows[str(client)]
            assert row["required_symbol_count"] == 1
            assert row["missing_symbols"] == ([required] if missing else [])
            assert (row["verdict"] == "BREAKING") == missing

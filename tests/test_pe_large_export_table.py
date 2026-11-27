# SPDX-License-Identifier: Apache-2.0
"""Compare the recorded linker contract with the public PE metadata reader."""

import shutil
import subprocess

import pytest

from abicheck.pe_metadata import parse_pe_metadata


@pytest.mark.skipif(
    not shutil.which("clang") or not shutil.which("lld-link"),
    reason="needs clang and lld-link to build a Windows DLL",
)
def test_large_dll_keeps_exact_long_spellings_and_name_ordinals(tmp_path):
    source = tmp_path / "entry.c"
    source.write_text("int entry(void) { return 1; }\n")
    obj = tmp_path / "entry.obj"
    subprocess.run(
        ["clang", "--target=x86_64-pc-windows-msvc", "-c", source, "-o", obj],
        check=True,
    )
    names = [f"entry_{i:05d}" for i in range(8250)]
    prefix = "long_export_" + "x" * 500
    names.extend([prefix, prefix + "_left", prefix + "_right"])
    definition = tmp_path / "exports.def"
    definition.write_text(
        "EXPORTS\n"
        + "".join(f"{name}=entry @{ordinal}\n" for ordinal, name in enumerate(names, 1))
    )
    dll = tmp_path / "large.dll"
    subprocess.run(
        [
            "lld-link",
            "/dll",
            "/noentry",
            "/nodefaultlib",
            f"/def:{definition}",
            f"/out:{dll}",
            obj,
        ],
        check=True,
    )
    exports = parse_pe_metadata(dll).exports
    assert len(exports) == len(names)
    assert {(exp.name, exp.ordinal) for exp in exports} == {
        (name, ordinal) for ordinal, name in enumerate(names, 1)
    }

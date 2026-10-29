"""The initial value of an exported const integral global, read from the binary.

A header that declares ``extern const int k;`` carries no initializer, so the
definition in a ``.cpp`` reaches abicheck only as bytes in the object. Oracle
for the value: the compiler's own probe of the same source (the test builds the
library and a program that prints ``k``), not an abicheck helper.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.extract.elf_data_initializer import integral_shape
from abicheck.model import AbiSnapshot, Variable


@pytest.mark.parametrize(
    ("spelling", "shape"),
    [
        ("int", (4, True)),
        ("const int", (4, True)),
        ("unsigned int", (4, False)),
        ("unsigned long long", (8, False)),
        ("long int", (8, True)),
        ("uint8_t", (1, False)),
        ("const int64_t", (8, True)),
        ("bool", (1, False)),
        # A pointer, an enum, a class, and an unknown typedef are not read.
        ("const char *", None),
        ("const char * const", None),
        ("Color", None),
        ("std::string", None),
        ("size_t", None),
    ],
)
def test_integral_shape_is_size_and_signedness_or_none(
    spelling: str, shape: object
) -> None:
    assert integral_shape(spelling) == shape


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("g++") is None,
    reason="g++ ELF fixture",
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", "1"), ("-7", "-7"), ("300", "300")],
)
def test_exported_const_int_value_matches_compiler(
    tmp_path: Path, value: str, expected: str
) -> None:
    from abicheck.extract.elf_data_initializer import populate_const_integral_values

    source = tmp_path / "lib.cpp"
    source.write_text(f"extern const int kv;\nconst int kv = {value};\n")
    so = tmp_path / "lib.so"
    built = subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", str(source), "-o", str(so)],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr

    probe = tmp_path / "probe.cpp"
    probe.write_text(
        'extern const int kv;\n#include <cstdio>\nint main() { std::printf("%d\\n", kv); }\n'
    )
    exe = tmp_path / "probe"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            str(probe),
            str(so),
            "-o",
            str(exe),
            f"-Wl,-rpath,{tmp_path}",
        ],
        check=True,
    )
    compiler_value = subprocess.run(
        [str(exe)], capture_output=True, text=True
    ).stdout.strip()
    assert compiler_value == expected

    snap = AbiSnapshot(library="lib.so", version="1")
    snap.declarations.variables.append(
        Variable(name="kv", mangled="kv", type="int", is_const=True)
    )
    populate_const_integral_values(snap, so)
    assert snap.declarations.variables[0].value == compiler_value


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("g++") is None,
    reason="g++ ELF fixture",
)
def test_header_value_and_non_integral_are_left_alone(tmp_path: Path) -> None:
    """A value the header already gave is kept, and a pointer is never read."""
    from abicheck.extract.elf_data_initializer import populate_const_integral_values

    source = tmp_path / "lib.cpp"
    source.write_text(
        "extern const int kv;\nconst int kv = 5;\nextern const char *const name;\n"
        'const char *const name = "abc";\n'
    )
    so = tmp_path / "lib.so"
    built = subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", str(source), "-o", str(so)],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr

    snap = AbiSnapshot(library="lib.so", version="1")
    snap.declarations.variables.extend(
        [
            Variable(name="kv", mangled="kv", type="int", is_const=True, value="99"),
            Variable(name="name", mangled="name", type="const char *", is_const=True),
        ]
    )
    populate_const_integral_values(snap, so)
    kept, pointer = snap.declarations.variables
    assert kept.value == "99"  # header-derived value is never overwritten
    assert pointer.value is None  # a pointer is not an integer; unknown stays unknown

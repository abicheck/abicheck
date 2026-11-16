"""``method_deleted``'s DWARF expectation follows the producer's DWARF.

``abiforge/model/producer_dwarf.py`` keys whether a producer records a
``= delete`` member (``DW_AT_deleted``) on its family and major version. The
oracle here is ``readelf`` run on a fresh object file, never the table: each
installed producer is re-measured, and the table and the operator's expectation
must both agree with the measurement. A compiler change therefore fails this
test instead of drifting the nightly silently. A producer that is not installed
is skipped, one at a time.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.model.config import STANDARD_BY_NAME, Config, Flags, Toolchain
from abiforge.model.producer_dwarf import DW_AT_DELETED_BY_PRODUCER
from abiforge.operators import OP_BY_NAME

_SOURCE = (
    "struct Calc { int m(int a); int n(int) = delete; };\n"
    "int use(Calc& c) { return c.m(1); }\n"
)


def _driver(family: str, version: int) -> str:
    return f"{'g++' if family == 'gcc' else 'clang++'}-{version}"


def _records_deleted(driver: str, workdir: Path) -> bool:
    src = workdir / "d.cpp"
    obj = workdir / "d.o"
    src.write_text(_SOURCE)
    subprocess.run(
        [driver, "-std=c++17", "-g", "-O0", "-c", str(src), "-o", str(obj)],
        check=True,
        capture_output=True,
    )
    dump = subprocess.run(
        ["readelf", "--debug-dump=info", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return "DW_AT_deleted" in dump


@pytest.mark.parametrize(
    ("family", "version", "measured"),
    [(f, v, m) for (f, v), m in DW_AT_DELETED_BY_PRODUCER.items()],
)
def test_table_matches_the_installed_producer(
    family: str, version: int, measured: bool, tmp_path: Path
) -> None:
    driver = _driver(family, version)
    if shutil.which(driver) is None:
        pytest.skip(f"{driver} is not installed")
    assert _records_deleted(driver, tmp_path) is measured


@pytest.mark.parametrize(
    ("family", "version"),
    [
        ("gcc", 12),
        ("gcc", 13),
        ("gcc", 14),
        ("clang", 16),
        ("clang", 17),
        ("clang", 18),
        ("clang", 19),
    ],
)
def test_every_corpus_producer_is_measured(family: str, version: int) -> None:
    assert (family, version) in DW_AT_DELETED_BY_PRODUCER


@pytest.mark.parametrize(
    ("family", "version", "measured"),
    [(f, v, m) for (f, v), m in DW_AT_DELETED_BY_PRODUCER.items()],
)
def test_operator_expectation_follows_the_producer(
    family: str, version: int, measured: bool
) -> None:
    producer = Toolchain(
        family, version, _driver(family, version), _driver(family, version)
    )
    config = Config(
        std=STANDARD_BY_NAME["c++17"],
        producer=producer,
        flags=Flags(),
        evidence="dwarf",
    )
    expected = OP_BY_NAME["method_deleted"].expect(config).expected
    # SONAME_BUMP_RECOMMENDED is a derived kind, not part of the DWARF question.
    want = {"FUNC_DELETED_DWARF"} if measured else {"FUNC_REMOVED"}
    assert expected & {"FUNC_DELETED_DWARF", "FUNC_REMOVED"} == want

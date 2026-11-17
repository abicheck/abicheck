"""``FUNC_DELETED_ELF_FALLBACK`` on real builds: the namesake loss, and its negatives.

A declared C++ function whose bare-name C-linkage replacement stays in the
dynamic table is the one real-build shape the ELF-fallback detector fires on:
``compare`` reports ``FUNC_DELETED_ELF_FALLBACK`` (BREAKING). With hidden
visibility the replacement is not exported, and the loss is
``FUNC_VISIBILITY_CHANGED``. A declared function the new library does not
export at all, with no namesake, is also ``FUNC_VISIBILITY_CHANGED``.

Each pair is built from the abiforge seed's own sources with a real compiler.
The export table is read with ``readelf --dyn-syms`` as the oracle, never the
abicheck extractor, so a detector test cannot agree with itself.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import dump

# ELF shared objects and ``readelf --dyn-syms``: Windows builds DLLs, which
# export nothing under these names, so the module is Linux-only.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF dynamic-table oracle"),
]

_CLANG_CANDIDATES = ("clang++-19", "clang++-18", "clang++-17", "clang++")
_BARE = "kdl_gone"
_MANGLED = "_Z8kdl_gonei"


def _clang() -> str:
    for cc in _CLANG_CANDIDATES:
        if shutil.which(cc):
            return cc
    pytest.skip("no clang++ on PATH")
    raise AssertionError("unreachable")


def _build(cc: str, source: Path, out: Path, visibility: str) -> Path:
    subprocess.run(
        [
            cc,
            "-std=c++17",
            "-O0",
            f"-fvisibility={visibility}",
            "-fPIC",
            "-shared",
            str(source),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _dynamic_names(lib: Path) -> set[str]:
    """The names in the library's dynamic table, read by ``readelf`` (the oracle)."""
    if shutil.which("readelf") is None:
        pytest.skip("readelf is not installed")
    out = subprocess.run(
        ["readelf", "--dyn-syms", "-W", str(lib)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[6] != "UND":
            names.add(parts[7].split("@")[0])
    return names


def _seed_pair(tmp: Path) -> tuple[Path, Path, Path]:
    """The namesake's old header and sources, from the abiforge seed."""
    from abiforge.model.seeds import SEED_BY_NAME

    roles = SEED_BY_NAME["cxx_more"].roles
    old_header, old_source = roles["k.del"]
    old_h = tmp / "old.h"
    old_h.write_text(old_header, encoding="utf-8")
    old_c = tmp / "old.cpp"
    old_c.write_text(old_source, encoding="utf-8")
    return old_h, old_c, tmp


def _kinds(old_lib: Path, old_h: Path, new_lib: Path, new_h: Path):
    old = dump(old_lib, headers=[old_h])
    new = dump(new_lib, headers=[new_h])
    result = compare(old, new)
    kinds = {c.kind for c in result.changes}
    return kinds, result.verdict


def _namesake_cell(visibility: str, tmp_path: Path):
    """The operator's real cell for the namesake, built by the abiforge harness."""
    from abiforge.model.config import STANDARD_BY_NAME, Config, Flags, Toolchain
    from abiforge.model.seeds import SEED_BY_NAME
    from abiforge.operators import OP_BY_NAME
    from abiforge.run.cells import CellContext, run_cell

    cc = _clang()
    major = int(cc.rsplit("-", 1)[1]) if "-" in cc else 0
    if not major:
        pytest.skip("need a versioned clang++ (clang++-NN) for the real cell")
    config = Config(
        std=STANDARD_BY_NAME["c++17"],
        producer=Toolchain("clang", major, f"clang-{major}", cc),
        flags=Flags(opt="O0", visibility=visibility),
        evidence="headers",
    )
    cell = run_cell(
        OP_BY_NAME["declared_function_export_became_c_namesake"],
        SEED_BY_NAME["cxx_more"],
        config,
        CellContext.at(tmp_path),
    )
    if cell.status in ("unavailable", "infeasible"):
        pytest.skip(cell.reason)
    return cell


def test_the_namesake_loss_is_func_deleted_elf_fallback(tmp_path: Path) -> None:
    cell = _namesake_cell("default", tmp_path)
    assert "FUNC_DELETED_ELF_FALLBACK" in cell.kinds, sorted(cell.kinds)
    assert cell.verdict == "BREAKING", cell.verdict


def test_hidden_visibility_makes_the_namesake_a_visibility_change(
    tmp_path: Path,
) -> None:
    cell = _namesake_cell("hidden", tmp_path)
    assert "FUNC_VISIBILITY_CHANGED" in cell.kinds, sorted(cell.kinds)
    assert "FUNC_DELETED_ELF_FALLBACK" not in cell.kinds, sorted(cell.kinds)


def test_a_declared_function_with_no_namesake_is_not_the_fallback(
    tmp_path: Path,
) -> None:
    """Negative: the new library drops the function and exports no namesake.

    The header still declares ``kdl_gone`` and the library exports neither
    spelling, so the loss is visibility, not a namesake fallback.
    """
    cc = _clang()
    old_h, old_c, _ = _seed_pair(tmp_path)
    plain_c = tmp_path / "new_plain.cpp"
    plain_c.write_text("int kdl_keep(int a) { return a; }\n", encoding="utf-8")
    new_h = tmp_path / "new.h"
    new_h.write_text(old_h.read_text(encoding="utf-8"), encoding="utf-8")
    old_lib = _build(cc, old_c, tmp_path / "libold.so", "default")
    new_lib = _build(cc, plain_c, tmp_path / "libnew.so", "default")

    # Oracle: neither spelling of the function is in the new dynamic table.
    names = _dynamic_names(new_lib)
    assert _BARE not in names
    assert _MANGLED not in names

    kinds, verdict = _kinds(old_lib, old_h, new_lib, new_h)
    assert ChangeKind.FUNC_VISIBILITY_CHANGED in kinds
    assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds
    assert verdict == Verdict.BREAKING

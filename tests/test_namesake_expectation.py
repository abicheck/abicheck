"""``declared_function_export_became_c_namesake``'s expectation follows the export.

The C-linkage replacement is exported under the bare name only without
``-fvisibility=hidden``. The oracle is the library's dynamic symbol table read
with ``readelf --dyn-syms`` on a fresh build of the operator's own new source,
never the expectation layer. Default visibility exports the namesake, so the
loss is ``FUNC_DELETED_ELF_FALLBACK``; hidden visibility does not, so the loss
is ``FUNC_VISIBILITY_CHANGED``. Each variant also runs the real cell, which
asserts that abicheck emits the kind the expectation names.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.model.config import STANDARD_BY_NAME, Config, Flags, Toolchain
from abiforge.model.expect import PASSING_CLASSES
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators import OP_BY_NAME
from abiforge.run.cells import CellContext, run_cell

_OP = "declared_function_export_became_c_namesake"
_KINDS = {"FUNC_DELETED_ELF_FALLBACK", "FUNC_VISIBILITY_CHANGED"}
_CLANG = "clang++-17"


def _config(visibility: str) -> Config:
    producer = Toolchain("clang", 17, "clang-17", _CLANG)
    return Config(
        std=STANDARD_BY_NAME["c++17"],
        producer=producer,
        flags=Flags(opt="O0", visibility=visibility),
        evidence="headers",
    )


def _exports_bare_name(visibility: str, workdir: Path) -> bool:
    """Build the operator's new k.del source and read its dynamic table."""
    new_source = OP_BY_NAME[_OP].edit(dict(SEED_BY_NAME["cxx_more"].roles))["k.del"]
    if isinstance(new_source, tuple):
        new_source = new_source[1]
    src = workdir / "new.cpp"
    lib = workdir / "libnew.so"
    src.write_text(new_source)
    subprocess.run(
        [
            _CLANG,
            "-std=c++17",
            "-O0",
            f"-fvisibility={visibility}",
            "-fPIC",
            "-shared",
            str(src),
            "-o",
            str(lib),
        ],
        check=True,
        capture_output=True,
    )
    dyn = subprocess.run(
        ["readelf", "--dyn-syms", "-W", str(lib)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return any(line.rstrip().endswith(" kdl_gone") for line in dyn.splitlines())


@pytest.mark.parametrize(
    ("visibility", "kind"),
    [("default", "FUNC_DELETED_ELF_FALLBACK"), ("hidden", "FUNC_VISIBILITY_CHANGED")],
)
def test_expectation_selects_the_kind_the_export_table_implies(
    visibility: str, kind: str, tmp_path: Path
) -> None:
    if shutil.which(_CLANG) is None:
        pytest.skip(f"{_CLANG} is not installed")
    exported = _exports_bare_name(visibility, tmp_path)
    assert exported is (visibility == "default")
    expected = OP_BY_NAME[_OP].expect(_config(visibility)).expected
    assert expected & _KINDS == {kind}


@pytest.mark.parametrize("visibility", ["default", "hidden"])
def test_the_real_cell_passes_for_both_visibility_variants(
    visibility: str, tmp_path: Path
) -> None:
    if shutil.which(_CLANG) is None:
        pytest.skip(f"{_CLANG} is not installed")
    cell = run_cell(
        OP_BY_NAME[_OP],
        SEED_BY_NAME["cxx_more"],
        _config(visibility),
        CellContext.at(tmp_path),
    )
    if cell.status in ("unavailable", "infeasible"):
        pytest.skip(cell.reason)
    assert cell.cls in PASSING_CLASSES, cell.describe()

"""A build-flag edit is observable only where its flag is recorded (WS-6 A2.10).

A layout change made by a build flag (``-fpack-struct``) lives in neither the
binary nor a distributed header, so the stripped ``{B,H}`` configuration cannot
see it. An operator declares that with ``EditOp.carrier``. This table is
hand-written from the edit's meaning, not from any checker's answer: the build
flags are the carriers, and the header-source edits are not.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import default_config
from abiforge.model.observable import min_level_cells
from abiforge.model.seeds import SEEDS, Seed
from abiforge.operators import OPERATORS, matrix
from abiforge.run.toolchains import toolchain

PRODUCER = toolchain("gcc", 13)
CARRIERS = frozenset({"D", "BD"})

#: operator name -> whether its edit is a build flag (carried by D or BD only).
BUILD_FLAG_EDITS = {
    "rebuild_pack_struct": True,
    # A source-level packing (attribute or pragma) lives in the header text.
    "record_became_packed": False,
}


def _op(name):
    return next(op for op in OPERATORS if op.name == name)


def _seeds(op) -> list[Seed]:
    return [s for s in SEEDS if op.applies_to(s)]


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_declared_carrier_matches_the_table(name) -> None:
    op = _op(name)
    assert bool(op.carrier) is BUILD_FLAG_EDITS[name], name
    if op.carrier:
        assert op.carrier == CARRIERS


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_stripped_headers_cannot_show_the_edit_exactly_when_it_is_a_build_flag(
    name,
) -> None:
    op = _op(name)
    for seed in _seeds(op)[:2]:
        base = default_config(seed.lang, PRODUCER)
        stripped = base.with_(evidence="headers", stripped=True)
        dwarf = base.with_(evidence="dwarf", stripped=False)
        assert op.expect(stripped).observable is (not BUILD_FLAG_EDITS[name]), seed.name
        assert op.expect(dwarf).observable is True, seed.name


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_minimal_cells_of_a_build_flag_edit_include_a_carrier(name) -> None:
    op = _op(name)
    for seed in _seeds(op)[:2]:
        base = default_config(seed.lang, PRODUCER)
        cells, _ = min_level_cells(op, base)
        at_min = [c for c in cells if c.role == "at-minimum"]
        assert at_min, seed.name
        for cell in at_min:
            if BUILD_FLAG_EDITS[name]:
                assert cell.atoms & CARRIERS, (seed.name, sorted(cell.atoms))
            else:
                assert cell.atoms, seed.name


def test_every_matrix_pair_of_a_carrier_operator_has_a_floor_cell() -> None:
    """No build-flag edit is silently left without a cell (plan B2)."""
    for op, seed in matrix():
        if not op.carrier:
            continue
        base = default_config(seed.lang, PRODUCER)
        if op.requires(base) is not None or not op.applies_to(seed):
            continue
        cells, unsupported = min_level_cells(op, base)
        assert cells or unsupported, (op.name, seed.name)


# --- the compiler is the oracle for the layout a build flag moves ----------

#: Packed and aligned record shapes; the same header text under each flag set.
SHAPES = {
    "mixed": "struct rec { char tag; double weight; int id; };",
    "pair": "struct rec { char a; int b; };",
    "tail": "struct rec { int x; char y; };",
    "aligned": "struct rec { char tag; _Alignas(16) double w; };",
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_same_header_has_a_different_layout_under_the_build_flag(
    tmp_path, shape
) -> None:
    """``-fpack-struct=1`` moves ``sizeof``/``alignof`` with the header text unchanged.

    The probe is compiled by gcc under each flag set; the header is one string.
    A stripped ``{B,H}`` side parses the header without the flag, so its layout
    is the unpacked one: the two differ, which is why the edit needs its carrier.
    """
    import shutil
    import subprocess

    if shutil.which("gcc") is None:
        pytest.skip("needs gcc")
    probe = tmp_path / "probe.c"
    probe.write_text(
        SHAPES[shape]
        + "\n#include <stdio.h>\n#include <stddef.h>\n"
        + 'int main(void){printf("%zu %zu\\n",sizeof(struct rec),_Alignof(struct rec));return 0;}\n',
        encoding="utf-8",
    )

    def layout(*flags: str) -> str:
        exe = tmp_path / ("p" + "".join(flags).replace("=", "").replace("-", "_"))
        subprocess.run(
            ["gcc", *flags, str(probe), "-o", str(exe)], check=True, capture_output=True
        )
        return subprocess.run(
            [str(exe)], check=True, capture_output=True, text=True
        ).stdout

    unpacked = layout()
    packed = layout("-fpack-struct=1")
    assert packed != unpacked, shape
    # alignof falls to 1 under -fpack-struct=1 for every shape: the header carries
    # the same declaration, so only the flag tells the two layouts apart.
    assert packed.split()[1] == "1", shape

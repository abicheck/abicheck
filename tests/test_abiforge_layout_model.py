"""The union/bit-field layout model (abiforge/model/layout_model.py) against two oracles.

Bug class: an expected layout row taken from an assumption (the research
campaign's scoped ``#pragma pack``) instead of the generator's real build
(``-fpack-struct``) — gcc rounds an unnamed ``:0`` barrier to the next byte
under ``-fpack-struct=1`` but to the next 32-bit unit under the pragma. Every
record state an operator builds is measured by a compiler probe (byte images,
``sizeof``/``_Alignof``) and by raw DWARF, at both packings, O0 and O2, on
every installed producer, and must equal the model exactly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abiforge.model.layout_model import Layout, layout
from abiforge.operators.fragments import MODELLED_STATES
from abiforge.run.layout_probe import dwarf, probe

pytestmark = pytest.mark.integration

_PRODUCERS = [p for p in ("gcc", "clang") if shutil.which(p)]
_STATES = sorted(dict(MODELLED_STATES).items())


def _model(records, pack, producer) -> Layout:
    known: dict[str, Layout] = {}
    for r in records:
        known[r.spelling] = layout(r, pack=pack, producer=producer, known=known)
    return known[records[-1].spelling]


def test_some_producer_is_installed() -> None:
    assert _PRODUCERS, "integration lane without gcc or clang"


@pytest.mark.parametrize("producer", _PRODUCERS)
@pytest.mark.parametrize("pack", [None, 1], ids=["default", "pack1"])
def test_model_matches_compiler_probe_and_raw_dwarf(
    producer: str, pack, tmp_path: Path
) -> None:
    checked = 0
    for name, records in _STATES:
        want = _model(records, pack, producer)
        work = tmp_path / name.replace(":", "_")
        for opt in ("O0", "O2"):
            got = probe(producer, records, pack=pack, work=work / f"p{opt}", opt=opt)
            assert (got.size, got.align) == (want.size, want.align), (name, opt)
            for member, off in got.offsets.items():
                assert want.offsets[member] == off, (name, member, opt)
            assert got.widths == want.widths, (name, opt)
        dw = dwarf(producer, records, pack=pack, work=work / "dwarf")
        assert dw.size == want.size, name
        assert dw.align is None  # DWARF records no alignment for these records
        assert dw.offsets == want.offsets, name
        assert dw.widths == want.widths, name
        checked += 1
    assert checked == len(_STATES) > 30


def test_gcc_pack_flag_and_pragma_differ_on_a_barrier(tmp_path: Path) -> None:
    """Why the generator's flag is modelled, not the research pragma."""
    if "gcc" not in _PRODUCERS:
        pytest.skip("gcc is not installed")
    from abiforge.model.fragments import bitfield_record

    rec = bitfield_record("x", 7, "before")
    flag = probe("gcc", (rec,), pack=1, work=tmp_path / "flag")
    assert flag.offsets["b"] == 8  # next byte
    src = tmp_path / "pragma.c"
    src.write_text(
        "#include <stdio.h>\n#include <string.h>\n#pragma pack(push, 1)\n"
        + rec.render()
        + "\n#pragma pack(pop)\nint main(void){struct Bits_x v; unsigned char m[sizeof v];"
        "int i; memset(&v,0,sizeof v); v.b=~0u; memcpy(m,&v,sizeof v);"
        'for(i=0;i<(int)(8*sizeof v);i++) if((m[i/8]>>(i%8))&1){printf("%d\\n",i);break;}return 0;}\n'
    )
    import subprocess

    subprocess.run(["gcc", "-w", str(src), "-o", str(tmp_path / "pragma")], check=True)
    out = subprocess.run(
        [str(tmp_path / "pragma")], capture_output=True, text=True, check=True
    )
    assert int(out.stdout) == 32  # next unit

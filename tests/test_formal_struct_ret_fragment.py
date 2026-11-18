"""Same-class struct returns (E14b, ``formal/RET_FRAGMENT.md`` "Struct member
layouts").

``formal/lean/AbiLean/StructRetFragment.lean`` proves, within its own model of
``int``/``long``/``double`` struct layouts and for every nonempty old member
list, that the model's image comparison equals the member-read oracle and
that intact holds iff the old member layout is a sub-layout of the new one,
both or neither in memory, memory not grown; it also enumerates the 1190
pairs. The Python implementation is not proved: these tests check it, and
the export, against independent oracles. These tests check
what Lean cannot reach, each against an oracle that is not the code under
test:

* the exported structs, layouts and shapes against the experiment's own
  psABI classifier (``formal/experiments/struct_ret_fragment.py``);
* the exported pair list against the experiment's ``space()`` and the
  generator checksum against its spelling;
* Lean's verdict against execution on gcc and clang
  (``formal/struct_ret_fragment_observed.json``) and against abicheck's
  ``assess_call_interface`` on every pair.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"
EXPORT = json.loads(
    (FORMAL / "lean" / "struct_ret_fragment_cases.json").read_text(encoding="utf-8")
)
OBSERVED = json.loads(
    (FORMAL / "struct_ret_fragment_observed.json").read_text(encoding="utf-8")
)


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "struct_ret_fragment.py")
ROWS: list[list] = EXPORT["rows"]
LEAN_INTACT = {(o, n) for o, n, ok in ROWS if ok}


def _fnv1a64(texts: list[str]) -> int:
    h = 14695981039346656037
    for t in texts:
        for b in t.encode("utf-8"):
            h = ((h ^ b) * 1099511628211) % (1 << 64)
    return h


def test_structs_layouts_and_shapes_match_the_experiment() -> None:
    assert [s[0] for s in EXPORT["structs"]] == FRAG.structs()
    for seq, size, shape, layout in EXPORT["structs"]:
        members, want_size = FRAG.layout(seq)
        assert size == want_size, seq
        assert shape == FRAG.shape(seq), seq
        assert layout == [[n[0], off] for n, _c, off in members], seq


def test_pairs_are_the_experiment_space_in_order() -> None:
    pairs = [(o, n) for o, n, _ok in ROWS]
    assert pairs == FRAG.space()
    assert len(pairs) == len(set(pairs)) == EXPORT["space_size"] == 1190


def test_generator_checksum_pins_the_rows() -> None:
    texts = [f"{o}\n{n}\n" for o, n, _ok in ROWS]
    assert str(_fnv1a64(texts)) == EXPORT["gen_fnv1a64"]
    texts[len(texts) // 2] += " "
    assert str(_fnv1a64(texts)) != EXPORT["gen_fnv1a64"]


@pytest.mark.parametrize("run", ["gcc/oracle", "clang/oracle"])
def test_lean_verdict_equals_execution(run: str) -> None:
    observed = {tuple(p) for p in OBSERVED["runs"][run]["intact_pairs"]}
    assert OBSERVED["cases"] == len(ROWS)
    assert LEAN_INTACT == observed
    assert len(LEAN_INTACT) == 12  # Lean `intact_pairs`


def test_lean_verdict_equals_abicheck_on_every_pair() -> None:
    ci = pytest.importorskip("abicheck.compare.call_interface")
    from abicheck.model.call_interface import SYSV_AMD64, ArgClass, Member, RetShape

    shapes = {
        "int1": RetShape.INT1,
        "int2": RetShape.INT2,
        "sse1": RetShape.SSE1,
        "sse2": RetShape.SSE2,
        "memory": RetShape.MEMORY,
    }
    cls = {
        "i": (32, ArgClass.INTEGER),
        "l": (64, ArgClass.INTEGER),
        "d": (64, ArgClass.SSE),
    }
    sigs = {}
    for seq, size, shape, layout in EXPORT["structs"]:
        members = tuple(Member(off * 8, *cls[t]) for t, off in layout)
        sigs[seq] = ci.CallSignature(
            (), shapes[shape], False, "", ret_members=members, ret_size=size * 8
        )
    got = {
        (o, n): ci.assess_call_interface(sigs[o], sigs[n], SYSV_AMD64).status
        for o, n, _ok in ROWS
    }
    want = {
        (o, n): ci.AxisStatus.PRESERVED if ok else ci.AxisStatus.BROKEN
        for o, n, ok in ROWS
    }
    assert got == want

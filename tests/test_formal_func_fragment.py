"""The closed function-signature fragment (``formal/FUNC_FRAGMENT.md``).

The Lean module ``formal/lean/AbiLean/FuncFragment.lean`` proves the abstract
statements (enumeration = space, generator bijective, when an edit keeps the
SysV call interface). These tests check what Lean cannot reach, each against
an oracle that is not the code under test:

* the exported case list against the space predicate, enumerated here
  independently with ``itertools``;
* the C generator (``formal/experiments/func_fragment.py``) against that space
  and against Lean's ``gen``;
* the exported call compatibility against an independent re-implementation of
  the SysV register assignment written in this file, and the report contract
  against an independent positional matcher;
* (``integration``) the execution oracle (an old caller calling the new
  library) and abicheck's report against the export, for the reduced set (and
  the full set under ``slow``).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import platform
import shutil
import sys
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"
EXPORT = FORMAL / "lean" / "func_fragment_cases.json"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "func_fragment.py")
RAW = json.loads(EXPORT.read_text(encoding="utf-8"))
DATA = FRAG.load_export()
ROWS: list[dict] = DATA["rows"]
CLASSES: list[dict] = DATA["classes"]
TYPES = ("int", "long", "double", "void *")

Case = tuple[tuple[str, ...], str, int, str]


def _case(r: dict) -> Case:
    return tuple(r["base"]), r["op"], r["pos"], r["type"]


def _space() -> set[Case]:
    out: set[Case] = set()
    for n in range(1, 4):
        for base in itertools.product(TYPES, repeat=n):
            out |= {(base, "ins", p, t) for p in range(n + 1) for t in TYPES}
            out |= {(base, "rem", p, "") for p in range(n)}
            out |= {
                (base, "set", p, t) for p in range(n) for t in TYPES if t != base[p]
            }
    return out


def _new(case: Case) -> list[tuple[str, str]]:
    base, op, pos, ty = case
    old = [(f"p{j}", t) for j, t in enumerate(base)]
    if op == "ins":
        return old[:pos] + [("ins", ty)] + old[pos:]
    if op == "rem":
        return old[:pos] + old[pos + 1 :]
    return old[:pos] + [(f"p{pos}", ty)] + old[pos + 1 :]


# Reference SysV register assignment for scalar arguments (independent of Lean):
# INTEGER arguments take rdi, rsi, rdx, rcx, r8, r9 in order; SSE ones xmm0..7.
_INT_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")


def _registers(types: list[str]) -> list[str]:
    ints = iter(_INT_REGS)
    sse = (f"xmm{i}" for i in range(8))
    return [next(sse) if t == "double" else next(ints) for t in types]


#: How the value sits in its register: what a reader of each type expects.
_WIDTH = {
    "int": "32-bit integer",
    "long": "64-bit integer",
    "void *": "64-bit integer",
    "double": "64-bit float",
}


def _call_compatible(case: Case) -> bool:
    old = [(f"p{j}", t) for j, t in enumerate(case[0])]
    new = _new(case)
    if any(n == "ins" for n, _ in new):
        return False
    old_reg = dict(zip((n for n, _ in old), _registers([t for _, t in old])))
    new_reg = dict(zip((n for n, _ in new), _registers([t for _, t in new])))
    old_ty = dict(old)
    return all(
        old_reg[n] == new_reg[n] and _WIDTH[old_ty[n]] == _WIDTH[t] for n, t in new
    )


def _contract(case: Case) -> list[str]:
    """Reference positional report contract (independent of Lean)."""
    old = [(f"p{j}", t) for j, t in enumerate(case[0])]
    new = _new(case)
    out = ["func_params_changed:"]
    for k, ((on, ot), (nn, nt)) in enumerate(zip(old, new)):
        if ot.endswith("*") != nt.endswith("*"):
            out.append(f"param_pointer_level_changed:{k}")
        elif ot == nt and on != nn:
            out.append(f"param_renamed:{k}")
    return out


# ------------------------------------------------------------------ 1. space


def test_export_is_exactly_the_space_without_duplicates() -> None:
    cases = [_case(r) for r in ROWS]
    assert len(cases) == len(set(cases)) == DATA["space_size"] == 2160
    assert set(cases) == _space()


# -------------------------------------------------------------- 2. generator


def test_generator_is_a_bijection_onto_the_space() -> None:
    programs = FRAG.generate()
    decoded = [FRAG.decode(p) for p in programs]
    assert None not in decoded
    assert len(set(programs)) == len(programs) == len(set(decoded))
    assert set(decoded) == _space()


def test_generator_realizes_leans_gen() -> None:
    by_case = {FRAG.decode(p): p for p in FRAG.generate()}
    ordered = [by_case[_case(r)] for r in ROWS]
    assert str(FRAG.fnv1a64(ordered)) == RAW["gen_fnv1a64"]
    for cls in CLASSES:
        p = by_case[_case(ROWS[cls["rep"]])]
        assert (p.old_body, p.new_body) == (cls["rep_old_body"], cls["rep_new_body"])


def test_checksum_detects_a_one_character_change() -> None:
    by_case = {FRAG.decode(p): p for p in FRAG.generate()}
    ordered = [by_case[_case(r)] for r in ROWS]
    i = len(ordered) // 2
    bad = [
        *ordered[:i],
        FRAG.Program(ordered[i].old_body, ordered[i].new_body + " "),
        *ordered[i + 1 :],
    ]
    assert str(FRAG.fnv1a64(bad)) != RAW["gen_fnv1a64"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("int p0", "int p0"),  # no edit
        ("int p0", "long p0, long p1"),  # two edits
        ("int p1", "int ins, int p1"),  # misnumbered
        ("int p0, long p1", "long p1, int p0"),  # reorder
        ("int p0", "float ins, int p0"),  # type outside the space
        ("int p0, int p1, int p2, int p3", "int p0, int p1, int p2"),  # 4 parameters
        ("int p0", "int ins,  int p0"),  # not the generator's spelling
        ("void", "int ins"),  # a function with no parameters is not an old case
    ],
)
def test_decode_rejects_programs_outside_the_space(old: str, new: str) -> None:
    assert FRAG.decode(FRAG.Program(old, new)) is None


# ----------------------------------------------------------------- 3. oracles


def test_call_compatibility_matches_reference_register_assignment() -> None:
    for r in ROWS:
        assert r["call"] == _call_compatible(_case(r)), r["id"]


def test_class_findings_follow_the_positional_contract() -> None:
    for r in ROWS:
        cls = CLASSES[r["class"]]
        assert cls["findings"] == _contract(_case(r)), r["id"]
        assert cls["verdict"] == "breaking"  # Lean `every_case_breaking`
        assert cls["call_compatible"] == r["call"]


def test_compatibility_theorems_on_the_space() -> None:
    """Lean `ins_never_compatible`, `set_compatible_iff`, `rem_compatible_iff`
    and `trailing_removal_compatible`, re-checked case by case."""
    cls = {"int": "I", "long": "I", "void *": "I", "double": "S"}
    for r in ROWS:
        base, op, pos, ty = _case(r)
        if op == "ins":
            want = False
        elif op == "set":
            want = _WIDTH[base[pos]] == _WIDTH[ty]
        else:
            want = all(cls[u] != cls[base[pos]] for u in base[pos + 1 :])
            if pos == len(base) - 1:
                assert r["call"], r["id"]
        assert r["call"] == want, r["id"]


def test_call_compatible_edits_are_reported_as_breaking() -> None:
    """The finding this fragment documents (FUNC_FRAGMENT.md "Findings"): 240
    edits keep the binary call interface, and the report contract -- which
    abicheck meets -- still calls every one of them breaking."""
    compatible = [r for r in ROWS if r["call"]]
    assert len(compatible) == 240
    assert {CLASSES[r["class"]]["verdict"] for r in compatible} == {"breaking"}
    assert {r["op"] for r in compatible} == {"rem", "set"}


# --------------------------------------------------------- 4./5. reduction


def test_classes_partition_the_space_by_the_reference_key() -> None:
    key_of_class: dict[int, tuple] = {}
    class_of_key: dict[tuple, int] = {}
    for r in ROWS:
        k = (tuple(_contract(_case(r))[1:]), _call_compatible(_case(r)))
        assert key_of_class.setdefault(r["class"], k) == k, r["id"]
        assert class_of_key.setdefault(k, r["class"]) == r["class"], r["id"]
    assert len(class_of_key) == len(CLASSES) == 34


def test_reduced_set_covers_every_class_exactly_once() -> None:
    reps = [r for r in ROWS if r["rep"]]
    assert sorted(r["class"] for r in reps) == list(range(len(CLASSES)))
    assert {r["class"] for r in ROWS} == {r["class"] for r in reps}


# ------------------------------------------------- execution + abicheck

_X86_64 = pytest.mark.skipif(
    not sys.platform.startswith("linux") or platform.machine() != "x86_64",
    reason="x86-64 System V fragment",
)


def _tools() -> tuple[str, ...]:
    if shutil.which("castxml") is None or shutil.which("abicheck") is None:
        pytest.skip("castxml and abicheck required")
    ccs = tuple(cc for cc in ("gcc", "clang") if shutil.which(cc))
    if not ccs:
        pytest.skip("gcc or clang required")
    return ccs


def _assert_all_agree(res: dict, n: int) -> None:
    for cc, r in res["compilers"].items():
        assert (r["call_agree"], r["call_disagree"]) == (n, 0), (cc, r)
        assert (r["abicheck_agree"], r["abicheck_disagree"]) == (n, 0), (cc, r)
        assert r["stray_findings"] == 0, (cc, r)


@pytest.mark.integration
@_X86_64
def test_reduced_set_execution_and_abicheck_match_oracles(tmp_path: Path) -> None:
    reps = [r for r in ROWS if r["rep"]]
    _assert_all_agree(FRAG.check_batch(tmp_path, reps, CLASSES, _tools()), len(reps))


@pytest.mark.integration
@pytest.mark.slow
@_X86_64
def test_full_space_execution_and_abicheck_match_oracles(tmp_path: Path) -> None:
    _assert_all_agree(FRAG.check_batch(tmp_path, ROWS, CLASSES, _tools()), len(ROWS))

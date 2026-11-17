"""The closed return-type fragment (``formal/RET_FRAGMENT.md``).

The Lean module ``formal/lean/AbiLean/RetFragment.lean`` proves the abstract
statements (enumeration = space, generator bijective, when a return-type
change keeps the SysV call interface). These tests check what Lean cannot
reach, each against an oracle that is not the code under test:

* the exported case list against the space predicate, enumerated here
  independently with ``itertools``;
* the C generator (``formal/experiments/ret_fragment.py``) against that space
  and against Lean's ``gen``;
* the exported argument placement and call compatibility against an
  independent re-implementation of the SysV register and return assignment
  written in this file, and the report contract against the kinds abicheck's
  catalog defines for a return-type change;
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
EXPORT = FORMAL / "lean" / "ret_fragment_cases.json"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "ret_fragment.py")
RAW = json.loads(EXPORT.read_text(encoding="utf-8"))
DATA = FRAG.load_export()
ROWS: list[dict] = DATA["rows"]
CLASSES: list[dict] = DATA["classes"]
PARAMS = ("int", "long", "double", "void *")
RETS = (
    "void",
    "int",
    "long",
    "double",
    "void *",
    "struct s2l",
    "struct s2d",
    "struct s3l",
)

Case = tuple[tuple[str, ...], str, str]


def _case(r: dict) -> Case:
    return tuple(r["params"]), r["old"], r["new"]


def _space() -> set[Case]:
    return {
        (ps, o, n)
        for k in range(3)
        for ps in itertools.product(PARAMS, repeat=k)
        for o in RETS
        for n in RETS
        if o != n
    }


# Reference SysV assignment (independent of Lean). A struct over 16 bytes is
# MEMORY: the caller passes its address as a hidden first INTEGER argument.
_INT_REGS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")
_SIZE = {"struct s2l": 16, "struct s2d": 16, "struct s3l": 24}


def _sret(t: str) -> bool:
    return _SIZE.get(t, 0) > 16


def _registers(params: tuple[str, ...], sret: bool) -> list[str]:
    ints = iter(_INT_REGS[1:] if sret else _INT_REGS)
    sse = (f"xmm{i}" for i in range(8))
    return [next(sse) if t == "double" else next(ints) for t in params]


#: Where, and in what form, a reader of each return type finds the result.
_RESULT = {
    "void": None,
    "int": ("rax", "32-bit integer"),
    "long": ("rax", "64-bit integer"),
    "void *": ("rax", "64-bit integer"),
    "double": ("xmm0", "64-bit float"),
    "struct s2l": ("rax:rdx", "two 64-bit integers"),
    "struct s2d": ("xmm0:xmm1", "two 64-bit floats"),
    "struct s3l": ("caller memory", "three 64-bit integers"),
}


def _args_intact(case: Case) -> bool:
    ps, o, n = case
    return _registers(ps, _sret(o)) == _registers(ps, _sret(n))


def _call_compatible(case: Case) -> bool:
    _, o, n = case
    if not _args_intact(case):
        return False
    if o == "void":
        return not _sret(n)  # nothing read, but no write through rdi either
    return _RESULT[o] == _RESULT[n]


def _contract(case: Case) -> list[str]:
    """Reference report contract (independent of Lean)."""
    _, o, n = case
    out = ["func_return_changed"]
    if o.endswith("*") != n.endswith("*"):
        out.append("return_pointer_level_changed")
    if o.startswith("struct") and n.startswith("struct") and _sret(o) != _sret(n):
        out.append("struct_return_convention_changed")
    return out


# ------------------------------------------------------------------ 1. space


def test_export_is_exactly_the_space_without_duplicates() -> None:
    cases = [_case(r) for r in ROWS]
    assert len(cases) == len(set(cases)) == DATA["space_size"] == 1176
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
        assert (p.old, p.new) == (cls["rep_old"], cls["rep_new"])


def test_checksum_detects_a_one_character_change() -> None:
    by_case = {FRAG.decode(p): p for p in FRAG.generate()}
    ordered = [by_case[_case(r)] for r in ROWS]
    i = len(ordered) // 2
    bad = [
        *ordered[:i],
        FRAG.Program(ordered[i].old, ordered[i].new + " "),
        *ordered[i + 1 :],
    ]
    assert str(FRAG.fnv1a64(bad)) != RAW["gen_fnv1a64"]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("int (void)", "int (void)"),  # no edit
        ("int (int p0)", "long (long p0)"),  # parameter edited too
        ("int (int p1)", "long (int p1)"),  # misnumbered
        ("float (void)", "int (void)"),  # type outside the space
        (
            "int (int p0, int p1, int p2)",
            "long (int p0, int p1, int p2)",
        ),  # 3 parameters
        ("int (int  p0)", "long (int  p0)"),  # not the generator's spelling
        ("struct s4 (void)", "int (void)"),  # struct outside the space
    ],
)
def test_decode_rejects_programs_outside_the_space(old: str, new: str) -> None:
    assert FRAG.decode(FRAG.Program(old, new)) is None


# ----------------------------------------------------------------- 3. oracles


def test_argument_placement_matches_reference_register_assignment() -> None:
    for r in ROWS:
        assert r["args"] == _args_intact(_case(r)), r["id"]


def test_call_compatibility_matches_reference_model() -> None:
    for r in ROWS:
        assert r["call"] == _call_compatible(_case(r)), r["id"]


def test_class_findings_follow_the_contract() -> None:
    for r in ROWS:
        cls = CLASSES[r["class"]]
        assert cls["findings"] == _contract(_case(r)), r["id"]
        assert cls["verdict"] == "breaking"  # Lean `every_case_breaking`
        assert (cls["call_compatible"], cls["args_intact"]) == (r["call"], r["args"])


def test_compatibility_theorems_on_the_space() -> None:
    """Lean `args_intact_iff`, `call_compatible_iff` and `params_irrelevant`,
    re-checked case by case."""
    by_change: dict[tuple[str, str], set[bool]] = {}
    for r in ROWS:
        ps, o, n = _case(r)
        assert r["args"] == (_sret(o) == _sret(n) or all(t == "double" for t in ps))
        want = (o == "void" and n != "struct s3l") or (
            o != "void" and _RESULT[o] == _RESULT[n]
        )
        assert r["call"] == want, r["id"]
        by_change.setdefault((o, n), set()).add(r["call"])
    assert all(len(v) == 1 for v in by_change.values())


def test_call_compatible_changes_are_reported_as_breaking() -> None:
    """The finding this fragment documents (RET_FRAGMENT.md "Findings"): 168
    return-type changes keep the binary call interface -- a `void` function
    starting to return a value in registers (126), `long` <-> `void *` (42) --
    and the report contract, which abicheck meets, calls every one breaking."""
    compatible = [r for r in ROWS if r["call"]]
    assert len(compatible) == 168
    assert {CLASSES[r["class"]]["verdict"] for r in compatible} == {"breaking"}
    assert sum(r["old"] == "void" for r in compatible) == 126
    assert {(r["old"], r["new"]) for r in compatible if r["old"] != "void"} == {
        ("long", "void *"),
        ("void *", "long"),
    }


# --------------------------------------------------------- 4./5. reduction


def test_classes_partition_the_space_by_the_reference_key() -> None:
    key_of_class: dict[int, tuple] = {}
    class_of_key: dict[tuple, int] = {}
    for r in ROWS:
        c = _case(r)
        k = (tuple(_contract(c)), _call_compatible(c), _args_intact(c))
        assert key_of_class.setdefault(r["class"], k) == k, r["id"]
        assert class_of_key.setdefault(k, r["class"]) == r["class"], r["id"]
    assert len(class_of_key) == len(CLASSES) == 8


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
    res = FRAG.check_batch(tmp_path, ROWS, CLASSES, _tools())
    _assert_all_agree(res, len(ROWS))
    for cc, r in res["compilers"].items():
        # The sentinel decides the 21 cases where a new sret function writes
        # through an rdi the caller never set (no INTEGER argument).
        assert r["sentinel_written"] == 21, (cc, r)

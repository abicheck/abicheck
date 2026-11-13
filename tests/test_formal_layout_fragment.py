"""The closed layout fragment (``formal/FRAGMENT.md``): space, generator, oracle, reduction.

The Lean module ``formal/lean/AbiLean/Fragment.lean`` proves the abstract
statements (enumeration = space, generator bijective, equivalence condition).
These tests check the parts Lean cannot reach, each against an oracle that is
not the code under test:

* the exported case list against the space predicate, enumerated here
  independently with ``itertools``;
* the C generator (``formal/experiments/layout_fragment.py``), which has its
  own enumeration, against that space and against Lean's ``gen``;
* the exported layout oracle against an independent re-implementation of the
  SysV sequential layout rule written in this file;
* the reduced set against every equivalence class;
* (``integration``) gcc and clang layouts and abicheck's findings against the
  oracle, for the reduced set (and the full set under ``slow``).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import platform
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "layout_fragment.py")
RAW = json.loads((FORMAL / "lean" / "fragment_cases.json").read_text(encoding="utf-8"))
DATA = FRAG.load_export()
ROWS: list[dict] = DATA["rows"]
CLASSES: list[dict] = DATA["classes"]
SIZES = (1, 2, 4, 8)


def _case(r: dict) -> tuple[tuple[int, ...], int, int]:
    return tuple(r["base"]), r["pos"], r["ins"]


def _space() -> set[tuple[tuple[int, ...], int, int]]:
    """The space predicate InSpace, enumerated independently of Lean and of the generator."""
    return {
        (base, pos, ins)
        for n in range(1, 5)
        for base in itertools.product(SIZES, repeat=n)
        for pos in range(n + 1)
        for ins in SIZES
    }


def _layout(sizes: list[int]) -> tuple[list[int], int, int]:
    """Reference SysV layout for scalars with alignment = size (independent of Lean)."""
    offs, end = [], 0
    for s in sizes:
        end = -(-end // s) * s
        offs.append(end)
        end += s
    align = max(sizes)
    return offs, -(-end // align) * align, align


# ------------------------------------------------------------------ 1. space


def test_export_is_exactly_the_space_without_duplicates() -> None:
    cases = [_case(r) for r in ROWS]
    assert len(cases) == len(set(cases)) == DATA["space_size"] == 6368
    assert set(cases) == _space()
    assert [r["id"] for r in ROWS] == list(range(len(ROWS)))


# -------------------------------------------------------------- 2. generator


def test_generator_is_a_bijection_onto_the_space() -> None:
    programs = FRAG.generate()
    decoded = [FRAG.decode(p) for p in programs]
    assert None not in decoded  # every generated pair falls in some case
    assert len(set(programs)) == len(programs)  # no program generated twice
    assert len(set(decoded)) == len(decoded)  # ...and no two in the same case
    assert set(decoded) == _space()  # every case has a generated program


def test_generator_realizes_leans_gen() -> None:
    """The C text the generator emits is the rendering of Lean's `gen`, case by case.

    Lean exports an FNV-1a-64 checksum of `gen c` rendered for every case in
    `space` order, and the full text for each class representative. The
    generator's own programs, decoded and put in Lean's order, must give the
    same checksum (a mismatch in any one case's text changes it).
    """
    by_case = {FRAG.decode(p): p for p in FRAG.generate()}
    ordered = [by_case[_case(r)] for r in ROWS]
    assert str(FRAG.fnv1a64(ordered)) == RAW["gen_fnv1a64"]
    for cls in CLASSES:
        p = by_case[_case(ROWS[cls["rep"]])]
        assert (p.old_body, p.new_body) == (cls["rep_old_body"], cls["rep_new_body"])


def test_checksum_detects_a_one_character_change() -> None:
    """Negative control for the checksum comparison above."""
    by_case = {FRAG.decode(p): p for p in FRAG.generate()}
    ordered = [by_case[_case(r)] for r in ROWS]
    i = len(ordered) // 2
    bad = (
        ordered[:i]
        + [FRAG.Program(ordered[i].old_body, ordered[i].new_body.replace("ins", "inz"))]
        + ordered[i + 1 :]
    )
    assert str(FRAG.fnv1a64(bad)) != RAW["gen_fnv1a64"]


def test_export_stays_under_the_large_file_threshold() -> None:
    """AgentReady flags checked-in files over 1,000,000 bytes (PR readiness gate)."""
    assert (FORMAL / "lean" / "fragment_cases.json").stat().st_size < 1_000_000


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("char f0;", "char f0;"),  # no inserted field
        ("char f0;", "char ins; char ins; char f0;"),  # two inserted fields
        ("char f1;", "char ins; char f1;"),  # old field misnumbered
        ("char f0; int f1;", "char ins; int f1; char f0;"),  # old fields reordered
        ("char f0;", "float ins; char f0;"),  # type outside the size classes
        ("long double f0;", "char ins; long double f0;"),  # 16-byte type, excluded
        (
            "char f0; char f1; char f2; char f3; char f4;",
            "char ins; char f0; char f1; char f2; char f3; char f4;",
        ),  # 5 fields
        ("char f0;", "char ins;  char f0;"),  # not the generator's spelling
        ("", "char ins;"),  # empty struct
    ],
)
def test_decode_rejects_programs_outside_the_space(old: str, new: str) -> None:
    assert FRAG.decode(FRAG.Program(old, new)) is None


# ----------------------------------------------------------------- 3. oracle


def test_oracle_layout_matches_reference_layout_rule() -> None:
    for r in ROWS:
        base, pos, ins = _case(r)
        new = list(base[:pos]) + [ins] + list(base[pos:])
        assert list(r["old"]) == list(_layout(list(base))), r["id"]
        assert list(r["new"]) == list(_layout(new)), r["id"]


def _key(r: dict) -> tuple[tuple[int, ...], bool, bool]:
    """(moved old fields, sizeof changed, alignment changed), from the reference layout."""
    base, pos, ins = _case(r)
    (oo, os_, oa) = _layout(list(base))
    (no, ns, na) = _layout(list(base[:pos]) + [ins] + list(base[pos:]))
    moved = tuple(j for j in range(len(base)) if no[j if j < pos else j + 1] != oo[j])
    return moved, os_ != ns, oa != na


def test_moved_fields_are_an_interval_from_the_insertion_point() -> None:
    """Lean `movedFields_interval`, re-checked on the reference layout."""
    for r in ROWS:
        moved = _key(r)[0]
        assert moved == tuple(range(r["pos"], r["pos"] + len(moved))), r["id"]


def test_class_findings_follow_from_the_key() -> None:
    """Each class's expected findings and verdict, rebuilt from its key by the
    rule stated in FRAGMENT.md (not by the exporter)."""
    assert len(CLASSES) == 32
    for cls in CLASSES:
        moved, size, align = cls["key"]
        want = ["type_field_added_compatible:ins"]
        want += [f"type_field_offset_changed:f{j}" for j in moved]
        want += ["type_size_changed:"] * size + ["type_alignment_changed:"] * align
        assert cls["findings"] == want
        assert cls["verdict"] == (
            "breaking" if moved or size or align else "compatible"
        )


# --------------------------------------------------------- 4./5. reduction


def test_classes_partition_the_space_by_the_reference_key() -> None:
    """Two cases share a class iff their independently computed keys are equal."""
    key_of_class: dict[int, tuple] = {}
    class_of_key: dict[tuple, int] = {}
    for r in ROWS:
        k = _key(r)
        assert key_of_class.setdefault(r["class"], k) == k, r["id"]
        assert class_of_key.setdefault(k, r["class"]) == r["class"], r["id"]
    assert len(class_of_key) == len(CLASSES)
    for cls in CLASSES:
        moved, size, align = cls["key"]
        assert key_of_class[cls["class"]] == (tuple(moved), size, align)


def test_reduced_set_covers_every_class_exactly_once() -> None:
    reps = [r for r in ROWS if r["rep"]]
    assert sorted(r["class"] for r in reps) == list(range(len(CLASSES)))
    assert {r["class"] for r in ROWS} == {r["class"] for r in reps}
    for cls in CLASSES:
        rep = ROWS[cls["rep"]]
        assert rep["rep"] and rep["class"] == cls["class"]
    assert len(ROWS) / len(reps) == 199  # 6368 / 32


def test_operation_alone_does_not_determine_the_class() -> None:
    """Lean `opName_not_enough`: merging by (length, position, inserted size) is unsound."""
    by_op: dict[tuple, set[int]] = defaultdict(set)
    for r in ROWS:
        by_op[(len(r["base"]), r["pos"], r["ins"])].add(r["class"])
    mixed = {op for op, cs in by_op.items() if len(cs) > 1}
    assert len(mixed) > len(by_op) // 2
    # the theorem's witness: {char; int} vs {int; int}, insert char at 1
    a = next(r for r in ROWS if _case(r) == ((1, 4), 1, 1))
    b = next(r for r in ROWS if _case(r) == ((4, 4), 1, 1))
    assert CLASSES[a["class"]]["verdict"] == "compatible"
    assert CLASSES[b["class"]]["verdict"] == "breaking"


# ------------------------------------------------------- compilers + abicheck

_LP64 = pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or platform.machine() not in ("x86_64", "aarch64")
    or sys.maxsize <= 2**32,
    reason="Linux ELF, LP64 SysV fragment",
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
        assert (r["layout_agree"], r["layout_disagree"]) == (2 * n, 0), (cc, r)
        assert (r["abicheck_agree"], r["abicheck_disagree"]) == (n, 0), (cc, r)


@pytest.mark.integration
@_LP64
def test_reduced_set_compilers_and_abicheck_match_oracle(tmp_path: Path) -> None:
    reps = [r for r in ROWS if r["rep"]]
    _assert_all_agree(FRAG.check_batch(tmp_path, reps, CLASSES, _tools()), len(reps))


@pytest.mark.integration
@_LP64
def test_per_case_exit_code_matches_oracle_verdict(tmp_path: Path) -> None:
    """One compatible and one breaking representative, each in its own run."""
    picks = [ROWS[c["rep"]] for c in CLASSES]
    picks = [
        next(r for r in picks if CLASSES[r["class"]]["verdict"] == v)
        for v in ("compatible", "breaking")
    ]
    res = FRAG.check_per_case(tmp_path, picks, CLASSES, _tools(), jobs=2)
    assert res["bad"] == []


@pytest.mark.integration
@pytest.mark.slow
@_LP64
def test_full_space_compilers_and_abicheck_match_oracle(tmp_path: Path) -> None:
    _assert_all_agree(FRAG.check_batch(tmp_path, ROWS, CLASSES, _tools()), len(ROWS))

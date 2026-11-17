"""The closed enum fragment (``formal/ENUM_FRAGMENT.md``): space, generator, oracle, reduction.

The Lean module ``formal/lean/AbiLean/EnumFragment.lean`` proves the abstract
statements (enumeration = space, generator bijective, revalued enumerators
form an interval, equivalence condition). These tests check the parts Lean
cannot reach, each against an oracle that is not the code under test:

* the exported case list against the space predicate, enumerated here
  independently with ``itertools``;
* the C generator (``formal/experiments/enum_fragment.py``), which has its own
  enumeration, against that space and against Lean's ``gen``;
* the exported values against an independent re-implementation of C11
  6.7.2.2's value rule written in this file;
* the reduced set against every equivalence class;
* (``integration``) gcc and clang values and abicheck's findings against the
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
EXPORT = FORMAL / "lean" / "enum_fragment_cases.json"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "enum_fragment.py")
RAW = json.loads(EXPORT.read_text(encoding="utf-8"))
DATA = FRAG.load_export()
ROWS: list[dict] = DATA["rows"]
CLASSES: list[dict] = DATA["classes"]
INITS = ("imp", "0", "2", "-1")

Case = tuple[tuple[str, ...], str, int, str]


def _case(r: dict) -> Case:
    return tuple(r["base"]), r["op"], r["pos"], r["init"]


def _space() -> set[Case]:
    """The space predicate InSpace, enumerated independently of Lean and of the generator."""
    out: set[Case] = set()
    for n in range(1, 5):
        for base in itertools.product(INITS, repeat=n):
            out |= {(base, "ins", p, i) for p in range(n + 1) for i in INITS}
            if n >= 2:  # an enumerator list is never empty
                out |= {(base, "rem", p, "") for p in range(n)}
            out |= {
                (base, "set", p, i) for p in range(n) for i in INITS if i != base[p]
            }
    return out


def _values(inits: list[str]) -> list[int]:
    """Reference C11 6.7.2.2p3 rule (independent of Lean): `= k` is k, no
    initializer is the previous value + 1, the first such is 0."""
    out, prev = [], -1
    for i in inits:
        prev = prev + 1 if i == "imp" else int(i)
        out.append(prev)
    return out


def _edit(case: Case) -> tuple[list[str], list[str]]:
    """(new names, new initializers) of a case."""
    base, op, pos, init = case
    names, inits = [f"e{j}" for j in range(len(base))], list(base)
    if op == "ins":
        names.insert(pos, "ins")
        inits.insert(pos, init)
    elif op == "rem":
        del names[pos], inits[pos]
    else:
        inits[pos] = init
    return names, inits


def _key(case: Case) -> tuple[bool, tuple[int, ...], tuple[int, ...]]:
    """(added, removed old enumerators, revalued old enumerators), from the reference rule."""
    base = case[0]
    names, inits = _edit(case)
    old = dict(zip([f"e{j}" for j in range(len(base))], _values(list(base))))
    new = dict(zip(names, _values(inits)))
    removed = tuple(j for j in range(len(base)) if f"e{j}" not in new)
    revalued = tuple(
        j for j in range(len(base)) if f"e{j}" in new and new[f"e{j}"] != old[f"e{j}"]
    )
    return "ins" in names, removed, revalued


# ------------------------------------------------------------------ 1. space


def test_export_is_exactly_the_space_without_duplicates() -> None:
    cases = [_case(r) for r in ROWS]
    assert len(cases) == len(set(cases)) == DATA["space_size"] == 11372
    assert set(cases) == _space()
    assert [r["id"] for r in ROWS] == list(range(len(ROWS)))


# -------------------------------------------------------------- 2. generator


def test_generator_is_a_bijection_onto_the_space() -> None:
    programs = FRAG.generate()
    decoded = [FRAG.decode(p) for p in programs]
    assert None not in decoded
    assert len(set(programs)) == len(programs)
    assert len(set(decoded)) == len(decoded)
    assert set(decoded) == _space()


def test_generator_realizes_leans_gen() -> None:
    """The text the generator emits is the rendering of Lean's `gen`, for every
    case (checksum in `space` order) and verbatim for each representative."""
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
    bad = [
        *ordered[:i],
        FRAG.Program(ordered[i].old_body, ordered[i].new_body + " "),
        *ordered[i + 1 :],
    ]
    assert str(FRAG.fnv1a64(bad)) != RAW["gen_fnv1a64"]


def test_export_stays_under_the_large_file_threshold() -> None:
    """AgentReady flags checked-in files over 1,000,000 bytes (PR readiness gate)."""
    assert EXPORT.stat().st_size < 1_000_000


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("e0", "e0"),  # no edit
        ("e0", ""),  # removing the only enumerator: an empty enum is ill-formed
        ("e0 = 2", "e0 = 2"),  # "set" to the same initializer is not an edit
        ("e0", "ins, ins, e0"),  # two inserted enumerators
        ("e1", "ins, e1"),  # old enumerator misnumbered
        ("e0, e1", "e1, e0"),  # reorder
        ("e0", "ins = 3, e0"),  # initializer outside the domain
        ("e0, e1", "e0 = 2, e1 = 2"),  # two initializers changed
        ("e0, e1, e2, e3, e4", "e0, e1, e2, e3"),  # 5 enumerators
        ("e0", "ins,  e0"),  # not the generator's spelling
    ],
)
def test_decode_rejects_programs_outside_the_space(old: str, new: str) -> None:
    assert FRAG.decode(FRAG.Program(old, new)) is None


# ----------------------------------------------------------------- 3. oracle


def test_oracle_values_match_reference_value_rule() -> None:
    for r in ROWS:
        case = _case(r)
        assert r["old"] == _values(list(case[0])), r["id"]
        assert r["new"] == _values(_edit(case)[1]), r["id"]


def test_class_findings_follow_from_the_key() -> None:
    """Each class's expected findings and verdict, rebuilt from its key by the
    rule stated in ENUM_FRAGMENT.md (not by the exporter)."""
    assert len(CLASSES) == 32
    for cls in CLASSES:
        added, removed, revalued = cls["key"]
        want = ["enum_member_added:ins"] * added
        want += [f"enum_member_removed:e{j}" for j in removed]
        want += [f"enum_member_value_changed:e{j}" for j in revalued]
        assert cls["findings"] == want
        if removed or revalued:
            assert cls["verdict"] == "breaking"
        else:
            assert cls["verdict"] == ("compatible" if added else "no_change")


def test_revalued_enumerators_are_an_interval_from_the_edit() -> None:
    """Lean `changed_interval`, re-checked on the reference rule."""
    for r in ROWS:
        start = r["pos"] + (1 if r["op"] == "rem" else 0)
        revalued = _key(_case(r))[2]
        assert revalued == tuple(range(start, start + len(revalued))), r["id"]


def test_insert_or_remove_never_revalues_an_explicit_enumerator() -> None:
    """Lean `explicit_never_shifted`, re-checked on the reference rule."""
    for r in ROWS:
        if r["op"] != "set":
            for j in _key(_case(r))[2]:
                assert r["base"][j] == "imp", r["id"]


def test_append_is_always_compatible() -> None:
    """Lean `append_compatible`."""
    for r in ROWS:
        if r["op"] == "ins" and r["pos"] == len(r["base"]):
            assert CLASSES[r["class"]]["verdict"] == "compatible", r["id"]


# --------------------------------------------------------- 4./5. reduction


def test_classes_partition_the_space_by_the_reference_key() -> None:
    """Two cases share a class iff their independently computed keys are equal."""
    key_of_class: dict[int, tuple] = {}
    class_of_key: dict[tuple, int] = {}
    for r in ROWS:
        k = _key(_case(r))
        assert key_of_class.setdefault(r["class"], k) == k, r["id"]
        assert class_of_key.setdefault(k, r["class"]) == r["class"], r["id"]
    assert len(class_of_key) == len(CLASSES)
    for cls in CLASSES:
        added, removed, revalued = cls["key"]
        assert key_of_class[cls["class"]] == (added, tuple(removed), tuple(revalued))


def test_reduced_set_covers_every_class_exactly_once() -> None:
    reps = [r for r in ROWS if r["rep"]]
    assert sorted(r["class"] for r in reps) == list(range(len(CLASSES)))
    assert {r["class"] for r in ROWS} == {r["class"] for r in reps}
    for cls in CLASSES:
        rep = ROWS[cls["rep"]]
        assert rep["rep"] and rep["class"] == cls["class"]
    assert len(ROWS) / len(reps) == 11372 / 32


def test_edit_alone_does_not_determine_the_class() -> None:
    """Lean `op_not_enough`: merging by (length, edit, position, initializer) is unsound."""
    by_op: dict[tuple, set[int]] = defaultdict(set)
    for r in ROWS:
        by_op[(len(r["base"]), r["op"], r["pos"], r["init"])].add(r["class"])
    assert sum(len(cs) > 1 for cs in by_op.values()) > len(by_op) // 2
    a = next(r for r in ROWS if _case(r) == (("0",), "ins", 0, "imp"))
    b = next(r for r in ROWS if _case(r) == (("imp",), "ins", 0, "imp"))
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
        assert (r["values_agree"], r["values_disagree"]) == (2 * n, 0), (cc, r)
        assert (r["abicheck_agree"], r["abicheck_disagree"]) == (n, 0), (cc, r)
        assert r["stray_findings"] == 0, (cc, r)


@pytest.mark.integration
@_LP64
def test_reduced_set_compilers_and_abicheck_match_oracle(tmp_path: Path) -> None:
    reps = [r for r in ROWS if r["rep"]]
    _assert_all_agree(FRAG.check_batch(tmp_path, reps, CLASSES, _tools()), len(reps))


@pytest.mark.integration
@_LP64
def test_per_case_exit_code_matches_oracle_verdict(tmp_path: Path) -> None:
    """One representative per verdict, each in its own run (exit 0, 0, 4)."""
    picks = [ROWS[c["rep"]] for c in CLASSES]
    picks = [
        next(r for r in picks if CLASSES[r["class"]]["verdict"] == v)
        for v in ("no_change", "compatible", "breaking")
    ]
    res = FRAG.check_per_case(tmp_path, picks, CLASSES, _tools(), jobs=2)
    assert res["bad"] == []


@pytest.mark.integration
@pytest.mark.slow
@_LP64
def test_full_space_compilers_and_abicheck_match_oracle(tmp_path: Path) -> None:
    _assert_all_agree(FRAG.check_batch(tmp_path, ROWS, CLASSES, _tools()), len(ROWS))

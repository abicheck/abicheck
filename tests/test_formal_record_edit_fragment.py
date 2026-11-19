# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""E16, the record-edit fragments (``formal/RECORD_EDIT_FRAGMENT.md``).

``formal/lean/AbiLean/RecordEditFragment.lean`` proves the enumeration is the
space (no duplicates, 9252 cases), the abstract generator is a bijection onto
its image, and the layout facts. These tests check what Lean cannot reach,
each against an oracle that is not the code under test:

* the export against the space predicate, enumerated here with ``itertools``;
* the C generator (its own enumeration) against that space and Lean's ``gen``;
* the exported layouts against a SysV layout rule written in this file;
* the exported findings against rules written in this file from the catalog
  contract (removed/retyped field, moved fields, size, alignment);
* the consumer byte model against hand-computed bytes;
* (``integration``) gcc/clang layouts, abicheck's typed findings in the
  header, DWARF and combined lanes, and the old-consumer observation, for the
  class representatives; per-case exit codes; the full space under ``slow``.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import platform
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"
EXPORT = FORMAL / "lean" / "record_edit_fragment_cases.json"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


FRAG = _load(FORMAL / "experiments" / "record_edit_fragment.py")
DATA = FRAG.load_export()
ROWS: list[dict] = DATA["rows"]
CLASSES: list[dict] = DATA["classes"]

#: Written here, independently of the harness: (spelling, size = alignment).
TY = [
    ("char", 1),
    ("short", 2),
    ("int", 4),
    ("double", 8),
    ("unsigned int", 4),
    ("float", 4),
]


def _space() -> set[tuple[tuple[int, ...], tuple[int, int, int]]]:
    out = set()
    for n in range(1, 5):
        for base in itertools.product(range(4), repeat=n):
            if n >= 2:
                out |= {(base, (0, k, 0)) for k in range(n)}
            out |= {(base, (1, i, j)) for i, j in itertools.combinations(range(n), 2)}
            out |= {
                (base, (2, k, t)) for k in range(n) for t in range(6) if t != base[k]
            }
    return out


def _new_fields(
    base: tuple[int, ...], edit: tuple[int, int, int]
) -> list[tuple[int, int]]:
    """``(old index, type)`` of the new struct, in declaration order."""
    fs = list(enumerate(base))
    op, a, b = edit
    if op == 0:
        del fs[a]
    elif op == 1:
        fs[a], fs[b] = fs[b], fs[a]
    else:
        fs[a] = (a, b)
    return fs


def _layout(types: list[int]) -> tuple[list[int], int, int]:
    offs, end = [], 0
    for t in types:
        s = TY[t][1]
        end = -(-end // s) * s
        offs.append(end)
        end += s
    align = max(TY[t][1] for t in types)
    return offs, -(-end // align) * align, align


def _findings(base: tuple[int, ...], edit: tuple[int, int, int]) -> list[str]:
    old = list(enumerate(base))
    new = _new_fields(base, edit)
    ooff, osize, oal = _layout([t for _, t in old])
    noff, nsize, nal = _layout([t for _, t in new])
    nat = {j: o for (j, _), o in zip(new, noff)}
    out = []
    if edit[0] == 0:
        out.append(f"type_field_removed:f{edit[1]}")
    if edit[0] == 2:
        out.append(f"type_field_type_changed:f{edit[1]}")
    out += [
        f"type_field_offset_changed:f{j}"
        for (j, _), o in zip(old, ooff)
        if j in nat and nat[j] != o
    ]
    if osize != nsize:
        out.append("type_size_changed:")
    if oal != nal:
        out.append("type_alignment_changed:")
    return sorted(out)


# ------------------------------------------------------------------ space


def test_export_is_exactly_the_space_without_duplicates() -> None:
    cases = [(tuple(r["base"]), tuple(r["edit"])) for r in ROWS]
    assert len(cases) == len(set(cases)) == DATA["space_size"] == 9252
    assert set(cases) == _space()
    assert Counter(e[0] for _, e in cases) == {0: 1248, 1: 1744, 2: 6260}


def test_generator_is_a_bijection_onto_the_space() -> None:
    progs = FRAG.generate()
    decoded = [FRAG.decode(p) for p in progs]
    assert None not in decoded
    assert len(set(decoded)) == len(progs) == 9252
    assert set(decoded) == _space()


def test_generator_realizes_leans_gen() -> None:
    progs = FRAG.generate()
    pos = {(tuple(r["base"]), tuple(r["edit"])): r["id"] for r in ROWS}
    ordered = [None] * len(progs)
    for p in progs:
        ordered[pos[FRAG.decode(p)]] = p
    assert FRAG.fnv1a64(ordered) == int(DATA["gen_fnv1a64"])
    tampered = list(ordered)
    tampered[17] = FRAG.Program(tampered[17].old_body, tampered[17].new_body + " ")
    assert FRAG.fnv1a64(tampered) != int(DATA["gen_fnv1a64"])


def test_export_stays_under_the_large_file_threshold() -> None:
    assert EXPORT.stat().st_size < 1_000_000


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("char f0;", "char f0;"),  # no edit
        ("char f0; int f1;", "int f1;  "),  # not the generator's spelling
        ("char f0; int f1;", "int f0; char f1;"),  # retype both = not one edit
        ("char f0; int f1; short f2;", "short f2; int f1; char f0; "),
        ("char f1;", "short f1;"),  # names must start at f0
        ("float f0;", "int f0;"),  # base types exclude float
        ("char f0;", ""),  # removing the only field is outside the space
        ("char f0; int f1;", "char f0;"),  # fine: removal ...
    ],
)
def test_decode_rejects_programs_outside_the_space(old: str, new: str) -> None:
    got = FRAG.decode(FRAG.Program(old, new))
    if (old, new) == ("char f0; int f1;", "char f0;"):
        assert got == ((0, 2), (0, 1, 0))  # ... the one valid pair, as a control
    else:
        assert got is None


# ----------------------------------------------------------------- oracle


def test_oracle_layout_matches_reference_layout_rule() -> None:
    for r in ROWS:
        base, edit = tuple(r["base"]), tuple(r["edit"])
        assert r["old"] == list(_layout(list(base))), r["id"]
        assert r["new"] == list(_layout([t for _, t in _new_fields(base, edit)])), r[
            "id"
        ]


def test_class_findings_follow_from_the_contract() -> None:
    for r in ROWS:
        cls = CLASSES[r["class"]]
        assert sorted(cls["findings"]) == _findings(
            tuple(r["base"]), tuple(r["edit"])
        ), r["id"]
        assert cls["verdict"] == "breaking"


def test_classes_partition_by_observable_and_reps_cover_each_once() -> None:
    by_obs: dict[tuple[str, ...], set[int]] = {}
    for r in ROWS:
        by_obs.setdefault(
            tuple(_findings(tuple(r["base"]), tuple(r["edit"]))), set()
        ).add(r["class"])
    assert all(len(v) == 1 for v in by_obs.values())
    assert len(by_obs) == len(CLASSES) == 88
    reps = [r for r in ROWS if r["rep"]]
    assert sorted(r["class"] for r in reps) == list(range(len(CLASSES)))


def test_layout_facts_the_lean_module_states() -> None:
    """Spot re-checks of ``swap_align_unchanged``, ``swap_moves_one``,
    ``swap_can_keep_one`` and ``same_layout_retype_exists`` with this file's rule."""
    swaps = [r for r in ROWS if r["edit"][0] == 1]
    assert all(r["old"][2] == r["new"][2] for r in swaps)
    assert all(
        any(
            f.startswith("type_field_offset_changed")
            for f in CLASSES[r["class"]]["findings"]
        )
        for r in swaps
    )
    keep = next(r for r in swaps if r["base"] == [0, 0, 1] and r["edit"] == [1, 1, 2])
    assert CLASSES[keep["class"]]["findings"] == [
        "type_field_offset_changed:f1",
        "type_size_changed:",
    ]
    same = [
        r
        for r in ROWS
        if r["edit"][0] == 2
        and r["old"][1:] == r["new"][1:]
        and r["old"][0] == r["new"][0]
    ]
    assert same and all(
        CLASSES[r["class"]]["findings"] == [f"type_field_type_changed:f{r['edit'][1]}"]
        for r in same
    )


def test_same_size_retypes_are_classified() -> None:
    """The handover's explicit classification: same-size integer signedness
    (int -> unsigned int) and integer <-> floating (int <-> float) retypes keep
    every offset and the size, so only the retype itself reports them."""
    kinds = Counter()
    for r in ROWS:
        op, k, t = r["edit"]
        if op != 2 or TY[r["base"][k]][1] != TY[t][1]:
            continue
        pair = (TY[r["base"][k]][0], TY[t][0])
        kinds[pair] += 1
        assert r["old"] == r["new"], r["id"]
    assert set(kinds) == {("int", "unsigned int"), ("int", "float")}


def test_consumer_byte_model_matches_hand_computed_bytes() -> None:
    """``{int f0}``, f0: int -> float, x86-64 little endian: the old provider
    writes int -2 (fe ff ff ff); the new one writes -2.5f (0x c0200000)."""
    row = next(r for r in ROWS if r["base"] == [2] and r["edit"] == [2, 0, 5])
    assert FRAG.consumer_model(row, "old") == {0: "feffffff"}
    assert FRAG.consumer_model(row, "new") == {0: "000020c0"}
    rem = next(r for r in ROWS if r["base"] == [0, 2] and r["edit"] == [0, 0, 0])
    # {char f0; int f1} minus f0: f1 moves to 0; the old reader sees f1's low
    # byte where f0 was and sentinel bytes where f1 was.
    assert FRAG.consumer_model(rem, "new") == {0: "fd", 1: "5a5a5a5a"}


def test_write_direction_sees_what_the_read_direction_cannot() -> None:
    """``{char f0}``, f0: char -> short. Reading, the old consumer sees the low
    byte of the new short: (char)V again, so the read direction never shows a
    widening. Writing, it stores one byte and leaves the sentinel 0x5a in the
    second; the new provider reads short 0x5afe = 23294 instead of -2."""
    row = next(r for r in ROWS if r["base"] == [0] and r["edit"] == [2, 0, 1])
    for rnd in range(FRAG.ROUNDS):
        assert FRAG.consumer_model(row, "old", rnd) == FRAG.consumer_model(
            row, "new", rnd
        )
    assert FRAG.write_model(row, "old", 0) == {0: -2}
    assert FRAG.write_model(row, "new", 0) == {0: 0x5AFE}
    # int -> unsigned int: same bytes both ways; the provider reads them as a
    # different number (-2 vs 4294967294): a reinterpretation, not a move.
    sgn = next(r for r in ROWS if r["base"] == [2] and r["edit"] == [2, 0, 4])
    assert FRAG.consumer_model(sgn, "old", 0) == FRAG.consumer_model(sgn, "new", 0)
    assert (FRAG.write_model(sgn, "old", 0), FRAG.write_model(sgn, "new", 0)) == (
        {0: -2},
        {0: 2**32 - 2},
    )


def test_probe_values_convert_like_c() -> None:
    assert FRAG.probe_value(0, 0, 1) == 112  # 70000 mod 256
    assert FRAG.probe_value(0, 1, 1) == 70000 % 65536
    assert FRAG.probe_value(0, 4, 0) == 2**32 - 2
    assert FRAG.probe_value(1, 3, 0) == -3.5


COMP = _load(FORMAL / "experiments" / "record_edit_competitors.py")
_LP64_EARLY = pytest.mark.skipif(
    not sys.platform.startswith("linux") or platform.machine() != "x86_64",
    reason="Linux ELF, x86-64 LP64 SysV fragment",
)


@pytest.mark.parametrize(
    "body", ["{}", '{"changes": [1]}', '{"changes": {}}', "{trunc"]
)
def test_one_unreadable_report_does_not_stop_the_others(
    tmp_path: Path, body: str
) -> None:
    for side in ("old", "new"):
        (tmp_path / f"lib{side}_gcc_g.so").write_bytes(b"")
    (tmp_path / "compare_gcc_dwarf.libs.json").write_text(
        json.dumps(FRAG.lib_hashes(tmp_path, "gcc"))
    )
    (tmp_path / "compare_gcc_dwarf.json").write_text(body)
    (tmp_path / "abidiff_gcc.txt").write_text(
        "  in pointed to type 'struct s0' at new.h:1:1:\n    type size changed from 8 to 16 (in bits)\n"
    )
    status = {"abidiff": {"ok": True}, "abicc": {"ok": False}}
    parsed, errors = COMP._parse_all(tmp_path, "gcc", status)
    assert parsed["abicheck"] is None and errors["abicheck"].startswith("parse_error")
    assert parsed["abidiff"]["s0"]["size"] is True
    assert parsed["abicc"] is None and errors["abicc"] == "error_or_unavailable"


def test_main_scores_the_valid_tool_and_writes_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through ``main``: abicheck's report is malformed, abidiff's is
    valid; the summary is written, abicheck's cell names the error, abidiff
    is scored."""
    row = next(r for r in ROWS if r["rep"] and r["edit"][0] == 0)
    for side in ("old", "new"):
        (tmp_path / f"lib{side}_gcc_g.so").write_bytes(side.encode())
    (tmp_path / "compare_gcc_dwarf.libs.json").write_text(
        json.dumps(FRAG.lib_hashes(tmp_path, "gcc"))
    )
    (tmp_path / "built_rows.json").write_text(json.dumps([row["id"]]))
    (tmp_path / "compare_gcc_dwarf.json").write_text('{"changes": {}}')
    (tmp_path / "abidiff_gcc.txt").write_text(
        f"  in pointed to type 'struct s{row['id']}' at new.h:1:1:\n"
    )
    monkeypatch.setattr(
        COMP,
        "run_tools",
        lambda out, cc: {"abidiff": {"ok": True}, "abicc": {"ok": False}},
    )
    assert COMP.main([str(tmp_path), "--compilers", "gcc"]) == 0
    tools = json.loads((tmp_path / "competitors.json").read_text())["compilers"]["gcc"][
        "tools"
    ]
    assert tools["abicheck"]["status"].startswith("parse_error")
    assert tools["abicc"]["status"] == "error_or_unavailable"
    assert tools["abidiff"]["claims"]["flagged"] == {"match": 1}


@pytest.mark.parametrize("xml", ["<not_a_report/>", "<report/>", "<report"])
def test_abicc_output_that_is_not_a_report_is_a_parse_error(
    tmp_path: Path, xml: str
) -> None:
    (tmp_path / "abicc_gcc.xml").write_text(xml)
    status = {"abidiff": {"ok": False}, "abicc": {"ok": True}}
    parsed, errors = COMP._parse_all(tmp_path, "gcc", status)
    assert parsed["abicc"] is None and errors["abicc"].startswith("parse_error")


def test_a_report_without_its_library_record_is_not_scored(tmp_path: Path) -> None:
    """A report left by a batch that rebuilt nothing after it, or by an older
    harness, carries no sidecar: unavailable, never scored."""
    for side in ("old", "new"):
        (tmp_path / f"lib{side}_gcc_g.so").write_bytes(side.encode())
    (tmp_path / "compare_gcc_dwarf.json").write_text('{"changes": []}')
    status = {"abidiff": {"ok": False}, "abicc": {"ok": False}}
    parsed, errors = COMP._parse_all(tmp_path, "gcc", status)
    assert parsed["abicheck"] is None and "no library record" in errors["abicheck"]


@pytest.mark.integration
@_LP64_EARLY
def test_a_narrower_batch_in_the_same_directory_drops_the_old_dwarf_report(
    tmp_path: Path,
) -> None:
    """Directory reuse: a full-lane batch, then an hdr-only batch over the
    same directory. The second rebuilds the libraries and must not leave the
    first batch's dwarf report (or its record) behind."""
    if not (shutil.which("gcc") and shutil.which("castxml")):
        pytest.skip("gcc and castxml required")
    picks = [r for r in ROWS if r["rep"]][:2]
    FRAG.check_batch(tmp_path, picks, CLASSES, ("gcc",), ("dwarf",), [])
    assert (tmp_path / "compare_gcc_dwarf.libs.json").exists()
    FRAG.check_batch(tmp_path, picks, CLASSES, ("gcc",), ("hdr",), [])
    assert not (tmp_path / "compare_gcc_dwarf.json").exists()
    assert not (tmp_path / "compare_gcc_dwarf.libs.json").exists()


@pytest.mark.parametrize("rebuilt", ["old", "new"])
def test_report_for_another_library_pair_is_stale(tmp_path: Path, rebuilt: str) -> None:
    """The batch records the libraries' hashes; rebuilding either one after
    the report (an interrupted batch) makes the report stale."""
    for side in ("old", "new"):
        (tmp_path / f"lib{side}_gcc_g.so").write_bytes(side.encode())
    (tmp_path / "compare_gcc_dwarf.libs.json").write_text(
        json.dumps(FRAG.lib_hashes(tmp_path, "gcc"))
    )
    (tmp_path / "compare_gcc_dwarf.json").write_text('{"changes": []}')
    status = {"abidiff": {"ok": False}, "abicc": {"ok": False}}
    assert (
        COMP._parse_all(tmp_path, "gcc", status)[0]["abicheck"] == {}
    )  # control: current
    (tmp_path / f"lib{rebuilt}_gcc_g.so").write_bytes(b"rebuilt")
    parsed, errors = COMP._parse_all(tmp_path, "gcc", status)
    assert parsed["abicheck"] is None and errors["abicheck"].startswith("stale")


# ------------------------------------------------------ compilers + abicheck

_LP64 = pytest.mark.skipif(
    not sys.platform.startswith("linux") or platform.machine() != "x86_64",
    reason="Linux ELF, x86-64 LP64 SysV fragment",
)


def _tools() -> tuple[str, ...]:
    if shutil.which("castxml") is None or shutil.which("abicheck") is None:
        pytest.skip("castxml and abicheck required")
    ccs = tuple(cc for cc in ("gcc", "clang") if shutil.which(cc))
    if not ccs:
        pytest.skip("gcc or clang required")
    return ccs


def _assert_all_agree(res: dict, n: int, compilers: tuple[str, ...]) -> None:
    assert set(res["compilers"]) == set(compilers)
    for cc, r in res["compilers"].items():
        assert set(r["lanes"]) == set(FRAG.LANES), cc
        assert (r["layout_agree"], r["layout_disagree"]) == (2 * n, 0), cc
        for lane, v in r["lanes"].items():
            assert v["outcomes"] == {"agree": n}, (cc, lane, v["examples"][:3])
            assert v["controls_with_findings"] == 0, (cc, lane, v["control_examples"])
        cons = r["consumer"]["counts"]
        assert cons["model_ok"] == n and "unexplained" not in cons, (cc, r["consumer"])


@pytest.mark.integration
@_LP64
def test_reduced_set_matches_oracle_on_every_lane(tmp_path: Path) -> None:
    reps = [r for r in ROWS if r["rep"]]
    ccs = _tools()
    res = FRAG.check_batch(tmp_path, reps, CLASSES, ccs, FRAG.LANES, FRAG.controls())
    _assert_all_agree(res, len(reps), ccs)


@pytest.mark.integration
@_LP64
def test_per_case_exit_code_is_four(tmp_path: Path) -> None:
    """One case per operation, each its own build and compare per lane."""
    picks = [
        next(r for r in ROWS if r["rep"] and r["edit"][0] == op) for op in (0, 1, 2)
    ]
    ccs = _tools()
    res = FRAG.check_per_case(tmp_path, picks, CLASSES, ccs, FRAG.LANES, jobs=3)
    want = {f"{cc}/{lane}" for cc in ccs for lane in FRAG.LANES}
    assert set(res["cells"]) == want
    assert all(cell == {"agree exit_ok=True": 3} for cell in res["cells"].values()), res
    assert res["bad"] == []


@pytest.mark.integration
@pytest.mark.slow
@_LP64
def test_full_space_matches_oracle_on_every_lane(tmp_path: Path) -> None:
    ccs = _tools()
    res = FRAG.check_batch(tmp_path, ROWS, CLASSES, ccs, FRAG.LANES, FRAG.controls())
    _assert_all_agree(res, len(ROWS), ccs)

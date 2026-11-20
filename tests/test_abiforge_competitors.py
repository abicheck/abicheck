"""abiforge's third-party tool adapters and the comparison runner.

The parsers are checked on real reports (``tests/data/abiforge_competitors``,
written by ``abidiff`` 2.4.0, ABICC 2.3 and abi-dumper 1.2 on abiforge pairs),
the decision mapping on the tools' documented status semantics, and the
runner end to end where the tools are installed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from abiforge.checkers import abicc, abidiff
from abiforge.model.expect import Expectation
from abiforge.run import competitor_score as score
from abiforge.run.competitor_truth import (
    _decls,
    entities,
    expected_class,
    members,
    records,
)

DATA = Path(__file__).with_name("data") / "abiforge_competitors"


def _text(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


# ------------------------------------------------------------- abidiff


@pytest.mark.parametrize("code", range(16))
def test_abidiff_status_bits_decode_every_value(code: int) -> None:
    bits = abidiff.decode_status(code)
    assert sum(bits.values()) == bin(code).count("1")
    assert bits["incompatible_change"] == bool(code & 8)
    assert bits["abi_change"] == bool(code & 4)


def test_abidiff_layout_change_is_advisory_not_a_break() -> None:
    """A record change sets only bit 2: the report explains it, abidiff does not judge it."""
    rep = abidiff.parse(_text("abidiff_struct_append.txt"))
    assert rep.changed_functions == {"fill_rec", "make_rec"}
    assert rep.types["rec"].size == (192, 256)
    assert rep.types["rec"].added == {"extra"}
    assert abidiff.decision(4, rep) == "ADVISORY"


def test_abidiff_changed_variable_is_named() -> None:
    rep = abidiff.parse(_text("abidiff_var_const.txt"))
    assert rep.changed_variables == {"g_obj"}
    assert not rep.changed_functions


def test_abidiff_symbol_only_addition_is_compatible() -> None:
    """A stripped library: the added symbol is listed without debug info."""
    rep = abidiff.parse(_text("abidiff_elf_only_added.txt"))
    assert rep.added_functions == {"add3"}
    assert abidiff.decision(4, rep) == "COMPATIBLE"


def test_abidiff_incompatible_bit_decides_break() -> None:
    rep = abidiff.parse(_text("abidiff_soname.txt"))
    assert rep.soname_changed
    assert abidiff.decision(12, rep) == "BREAKING"
    assert abidiff.decision(0, abidiff.parse("")) == "NO_CHANGE"


# ---------------------------------------------------------------- ABICC


def test_abicc_binary_and_source_reports_stay_separate() -> None:
    sides = abicc.parse(_text("abicc_dump_struct_swap.xml"))
    assert set(sides) == {"binary", "source"}
    assert abicc.side_decision(sides["binary"]) == "INCOMPATIBLE"
    assert abicc.side_decision(sides["source"]) == "COMPATIBLE"
    assert abicc.decision(sides) == "BREAKING"
    assert abicc.decision({"source": sides["source"]}) == "COMPATIBLE"


def test_abicc_descriptor_report_names_the_record_change() -> None:
    sides = abicc.parse(_text("abicc_xml_struct_remove.xml"))
    b = sides["binary"]
    assert b.headers_checked == ["edit_seed.h"] and b.libs_checked == ["libedit.so"]
    rec = b.types["rec"]
    assert rec.size == (192, 128)
    assert "tag" in rec.targets("Removed_")
    assert rec.affected == {"fill_rec", "make_rec"}


def test_abicc_constant_change_is_not_an_incompatibility() -> None:
    sides = abicc.parse(_text("abicc_xml_constant.xml"))
    assert "INCOMPATIBLE" not in {abicc.side_decision(s) for s in sides.values()}


def test_abicc_input_descriptor_is_not_an_output_report() -> None:
    """The input descriptor and the output report are different XML formats."""
    desc = abicc.descriptor("1.0", [Path("/x/a.h")], [Path("/x/liba.so")], "-std=c17")
    assert (
        "<headers>\n/x/a.h\n</headers>" in desc
        and "<libs>\n/x/liba.so\n</libs>" in desc
    )
    with pytest.raises(ValueError):
        abicc.parse(f"<descriptor>{desc}</descriptor>")


# ------------------------------------------------------ decision outcome


def _ob(decision: str | None, status: str = "ok") -> score.Observed:
    return score.Observed(decision=decision, status=status)


#: The abicheck mode name (a name, not an argv: kept out of tuple-literal form).
ABICHECK = "abicheck"
BREAK = Expectation(verdict_min="BREAKING")
SOURCE = Expectation(verdict_min="API_BREAK", verdict_max="API_BREAK")
COMPAT = Expectation(verdict_min="COMPATIBLE", verdict_max="COMPATIBLE")
NONE = Expectation(verdict_min="NO_CHANGE", verdict_max="NO_CHANGE")


@pytest.mark.parametrize(
    ("mode", "ob", "exp", "outcome"),
    [
        (ABICHECK, _ob("BREAKING"), BREAK, "correct"),
        ("abidiff", _ob("ADVISORY"), BREAK, "advisory"),
        ("abidiff", _ob("NO_CHANGE"), BREAK, "no_signal"),
        ("abicc-xml", _ob("API_BREAK"), BREAK, "wrong"),
        ("abidiff", _ob("ADVISORY"), SOURCE, "unsupported"),
        ("abidiff", _ob("BREAKING"), SOURCE, "wrong"),
        ("abicc-xml", _ob("ADVISORY"), SOURCE, "advisory"),
        ("abicc-xml", _ob("API_BREAK"), SOURCE, "correct"),
        (ABICHECK, _ob("BREAKING"), COMPAT, "wrong"),
        (ABICHECK, _ob("NO_CHANGE"), NONE, "correct"),
        (ABICHECK, _ob("ADVISORY"), NONE, "wrong"),
        ("abidiff", _ob(None, "error"), BREAK, "error"),
        ("abidiff", _ob(None, "timeout"), BREAK, "timeout"),
        ("abicc-dump", _ob(None, "unsupported"), BREAK, "unsupported"),
    ],
)
def test_decision_outcomes(
    mode: str, ob: score.Observed, exp: Expectation, outcome: str
) -> None:
    assert score.decision_outcome(mode, ob, exp) == outcome


def test_expected_class_from_bounds() -> None:
    assert expected_class(BREAK) == "abi_break"
    assert expected_class(SOURCE) == "source_break"
    assert expected_class(COMPAT) == "compatible"
    assert expected_class(NONE) == "unchanged"
    assert expected_class(Expectation(verdict_min="COMPATIBLE_WITH_RISK")) == "risk"


# ----------------------------------------------------------- truth model


def test_header_model_reads_members_in_order() -> None:
    assert list(members(" int id; double weight; char tag[8]; ")) == [
        "id",
        "weight",
        "tag",
    ]
    assert records("struct point { int y; int x; };") == {
        "point": {"y": "int y", "x": "int x"}
    }
    assert entities("int add3(int a);") == {"add3"}
    assert entities("extern const int g_obj;") == {"g_obj"}
    changed = _decls("struct rec make_rec(int id);") ^ _decls(
        "struct rec make_rec(int id);\nint add3(int a);"
    )
    assert changed == {"int add3(int a);"}


def test_pilot_selection_is_deterministic_and_covers_every_class() -> None:
    from abiforge.run.competitors import CONTROL, PILOT_CLASSES, pilot_cases

    first, second = pilot_cases(), pilot_cases()
    assert first == second
    classes = {c.cls for c in first}
    assert set(PILOT_CLASSES) <= classes
    assert any(c.op == "glibc_floor_raised" for c in first)
    assert any(c.op == CONTROL for c in first)


# ---------------------------------------------------------- end to end

_TOOLS = ("abidiff", "abi-dumper", "abi-compliance-checker", "gcc", "readelf")


@pytest.mark.integration
@pytest.mark.skipif(
    any(shutil.which(t) is None for t in _TOOLS),
    reason="needs abidiff, abi-dumper, ABICC, gcc",
)
def test_runner_gives_every_mode_the_same_pair(tmp_path: Path) -> None:
    from abiforge.run import competitors

    assert (
        competitors.main(
            [str(tmp_path), "--select", "struct_swap_fields", "--jobs", "2"]
        )
        == 0
    )
    rows = [
        json.loads(line) for line in (tmp_path / "pairs.jsonl").read_text().splitlines()
    ]
    swap = [
        r for r in rows if r["op"] == "struct_swap_fields" and r["seed"] == "c_core"
    ]
    assert {r["config"] for r in swap} == set(competitors.CONFIGS)
    for r in swap:
        assert r["status"] == "ran"
        assert r["details_expected"]["offsets_moved"] == ["x", "y"]  # compiler probe
        for argv in (s["argv"] for m in r["modes"].values() for s in m["run"]["steps"]):
            assert any(
                r["inputs"]["new"]["so"] in a or "desc.xml" in a or ".dump" in a
                for a in argv
            )
        stripped = r["config"] == "stripped+headers"
        assert (r["inputs"]["old"]["debug_sections"] == 0) is stripped
        assert r["modes"]["abicc-xml"]["inputs_verified"]["received_header"] is True
        assert r["modes"]["abicheck"]["decision_outcome"] == "correct"
        if stripped:
            assert r["modes"]["abicc-dump"]["decision_outcome"] == "unsupported"
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["coverage"]["this_run"]["pairs_built"] == len(rows)

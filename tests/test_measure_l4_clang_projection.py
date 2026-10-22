"""Tests for ``scripts/measure_l4_clang_projection.py``.

The pure comparison logic (pairing, per-field comparison, corpus aggregation) is
checked against expectations written out by hand here, not against the helper's
own output. The integration test runs the real clang extractor on one small TU
and is marked ``integration``.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "scripts" / "measure_l4_clang_projection.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("measure_l4_clang_projection", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = _load_tool()


def _fn(
    qualified: str, *, mangled: str = "", value: str = "", sig: str = "sha256:s"
) -> object:
    return M.EntityView(
        kind="function",
        qualified_name=qualified,
        fields={
            "qualified_name": qualified,
            "mangled_name": mangled,
            "visibility": "public_header",
            "api_relevant": "True",
            "value": value,
            "signature_hash": sig,
        },
    )


def _typedef(name: str, target: str) -> object:
    return M.EntityView(
        kind="typedef",
        qualified_name=name,
        fields={
            "qualified_name": name,
            "mangled_name": "",
            "visibility": "public_header",
            "api_relevant": "True",
            "value": target,
            "type_hash": "sha256:t",
        },
    )


@pytest.mark.parametrize(
    ("qualified", "bare"),
    [
        ("f", "f"),
        ("ns::f", "f"),
        ("A::B::m", "m"),
        ("Tpl<a::b>::m", "m"),
        ("ns::Tpl<int, std::string>::run", "run"),
        ("A::operator<", "operator<"),
    ],
)
def test_bare_name_is_last_top_level_component(qualified: str, bare: str) -> None:
    assert M.bare_name(qualified) == bare


def test_pair_entities_pairs_overloads_in_order_and_reports_leftovers() -> None:
    left = [_fn("f", mangled="_Z1fi"), _fn("f", mangled="_Z1fd")]
    right = [_fn("f", mangled="_Z1fi"), _fn("f", mangled="_Z1fd"), _fn("g")]
    pairs, left_only, right_only = M.pair_entities(left, right)
    assert [
        (lv.fields["mangled_name"], rv.fields["mangled_name"]) for lv, rv in pairs
    ] == [
        ("_Z1fi", "_Z1fi"),
        ("_Z1fd", "_Z1fd"),
    ]
    assert left_only == []
    assert [e.qualified_name for e in right_only] == ["g"]


def test_pair_entities_never_pairs_across_kinds() -> None:
    left = [_fn("T")]
    right = [_typedef("T", "int")]
    pairs, left_only, right_only = M.pair_entities(left, right)
    assert pairs == []
    assert len(left_only) == 1 and len(right_only) == 1


def test_compare_views_records_each_field_and_leaves_out_of_scope_uncounted() -> None:
    # Projection carries the bare member name; the current side the qualified one.
    current = [_fn("Processor::run", mangled="_ZN9Processor3runEv", value="")]
    projected = [
        _fn("run", mangled="_ZN9Processor3runEv", value=""),
        _fn("unrelated_system_decl", mangled="unrelated"),
    ]
    comp = M.compare_views("tu", current, projected)

    qn = comp.fields[("function", "qualified_name")]
    assert qn.compared == 1
    assert qn.differing == [
        {"name": "Processor::run", "current": "Processor::run", "projected": "run"}
    ]
    mn = comp.fields[("function", "mangled_name")]
    assert (mn.compared, mn.both_empty, mn.differing) == (1, 0, [])

    fn_presence = comp.presence["function"]
    assert fn_presence.left == 1
    assert fn_presence.right == 2
    assert fn_presence.right_out_of_scope == 1
    assert fn_presence.paired == 1
    assert fn_presence.right_only == []


def test_compare_views_counts_blanked_projected_mangled_names() -> None:
    # A projection that blanks mangled_name where the current side has one.
    current = [_fn("distance", mangled="_Z8distance5PointS_")]
    projected = [_fn("distance", mangled="")]
    comp = M.compare_views("tu", current, projected)
    assert comp.mangled["current_function_entities"] == 1
    assert comp.mangled["current_blank_mangled"] == 0
    assert comp.mangled["projected_in_scope_function_entities"] == 1
    assert comp.mangled["projected_blank_mangled"] == 1
    mn = comp.fields[("function", "mangled_name")]
    assert mn.differing == [
        {"name": "distance", "current": "_Z8distance5PointS_", "projected": ""}
    ]


def test_both_empty_pairs_are_counted_separately_from_equality() -> None:
    comp = M.compare_views("tu", [_typedef("T", "int")], [_typedef("T", "int")])
    mn = comp.fields[("typedef", "mangled_name")]
    assert (mn.compared, mn.both_empty, mn.differing) == (1, 1, [])
    val = comp.fields[("typedef", "value")]
    assert (val.compared, val.both_empty, val.differing) == (1, 0, [])


def test_aggregate_drops_a_field_that_differs_on_any_tu() -> None:
    # Equal on tu1 and differing on tu2: the corpus status is "differs", so
    # ADR-063 A3 drops the field for the whole revision pair.
    agree = M.compare_views("tu1", [_typedef("T", "int")], [_typedef("T", "int")])
    disagree = M.compare_views("tu2", [_typedef("U", "int")], [_typedef("U", "long")])
    rows = {(r.kind, r.field): r for r in M.aggregate([agree, disagree])}
    value = rows[("typedef", "value")]
    assert value.status == "differs"
    assert value.compared == 2
    assert value.differing == 1
    assert value.tus_differing == ["tu2"]


def test_aggregate_statuses_are_equal_equal_empty_and_vacuous() -> None:
    comp = M.compare_views(
        "tu1",
        [_typedef("T", "int")],
        [_typedef("T", "int")],
    )
    rows = {(r.kind, r.field): r for r in M.aggregate([comp])}
    assert rows[("typedef", "value")].status == "equal"
    assert rows[("typedef", "mangled_name")].status == "equal_empty"
    # No function pairs at all: nothing was compared, so nothing is "equal".
    assert rows[("function", "value")].status == "vacuous"
    assert rows[("function", "value")].compared == 0


def test_aggregate_presence_totals_include_out_of_scope() -> None:
    comp = M.compare_views(
        "tu1",
        [_fn("a")],
        [_fn("a"), _fn("zz1"), _fn("zz2")],
    )
    totals = M.aggregate_presence([comp])
    assert totals["function"] == {
        "current": 1,
        "projected": 3,
        "projected_out_of_scope": 2,
        "paired": 1,
        "tus_with_unpaired": [],
    }


def test_corpus_cases_selects_one_level_cpp_translation_units(tmp_path: Path) -> None:
    (tmp_path / "caseA").mkdir()
    (tmp_path / "caseA" / "app.cpp").write_text("")
    (tmp_path / "caseA" / "v1.h").write_text("")
    (tmp_path / "caseA" / "nested").mkdir()
    (tmp_path / "caseA" / "nested" / "deep.cpp").write_text("")
    (tmp_path / "caseB").mkdir()
    (tmp_path / "caseB" / "v1.cpp").write_text("")
    found = [p.relative_to(tmp_path).as_posix() for p in M.corpus_cases(tmp_path)]
    assert found == ["caseA/app.cpp", "caseB/v1.cpp"]


@pytest.mark.integration
def test_measure_tu_on_real_clang_shows_the_recorded_projection_gaps(
    tmp_path: Path,
) -> None:
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    extractor = ClangSourceExtractor()
    if not extractor.available():
        pytest.skip("clang not installed")
    (tmp_path / "pt.hpp").write_text(
        textwrap.dedent(
            """
            #pragma once
            namespace ns {
            struct Pt { int x; };
            typedef int scalar_t;
            int dist(Pt a, Pt b);
            }
            """
        )
    )
    tu = tmp_path / "pt.cpp"
    tu.write_text(
        '#include "pt.hpp"\nint ns::dist(ns::Pt a, ns::Pt b) { return a.x - b.x; }\n'
    )

    comp = M.measure_tu(tu, extractor)

    # The record and typedef sides both project; the projection's bare record
    # name pairs with the current side's qualified name, so the qualified-name
    # field differs. This is the gap Phase 4 has to close before the field can be pinned.
    record_presence = comp.presence["record"]
    assert record_presence.paired == 1
    rq = comp.fields[("record", "qualified_name")]
    assert rq.compared == 1
    assert rq.differing == [{"name": "ns::Pt", "current": "ns::Pt", "projected": "Pt"}]
    # The projection leaves origin unset, so visibility differs on the same record.
    rv = comp.fields[("record", "visibility")]
    assert rv.differing and rv.differing[0]["projected"] == "unknown"

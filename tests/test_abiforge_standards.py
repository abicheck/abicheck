"""The language-standards axis (plan §5.2): declared ranges, applicability and generation.

The expectations in the parametrized cases below are written from the cited
standards (ISO/IEC 14882, ISO/IEC 9899, WG21/WG14 papers), not read off the
range table, so a wrong range fails here rather than agreeing with itself.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import (
    FEATURES,
    STANDARD_BY_NAME,
    STANDARDS,
    StdRange,
    Toolchain,
    default_config,
    has_feature,
    in_standards,
)
from abiforge.model.space import Point, feature_standards, infeasible
from abiforge.operators import OP_BY_NAME, OPERATORS
from abiforge.run import tiers

TC = Toolchain("gcc", 14, "gcc", "g++")


def _cfg(std_name: str):
    std = STANDARD_BY_NAME[std_name]
    return default_config(std.lang, TC).with_(std=std)


# --- independent cases, from the standards -----------------------------------


@pytest.mark.parametrize(
    ("op", "std", "defined"),
    [
        # [[no_unique_address]] is C++20 (P0840R2): not in C++17, in C++20.
        ("no_unique_address_added", "c++17", False),
        ("no_unique_address_added", "c++20", True),
        # Concepts are C++20 (P0734R0).
        ("concept_constraint_added", "c++17", False),
        ("concept_constraint_added", "c++20", True),
        # Dynamic exception specifications: in C++11 and C++14, removed in C++17
        # (P0003R5). A C++17 library cannot be built with the edit's throw(...).
        ("func_exception_spec_widened", "c++11", True),
        ("func_exception_spec_widened", "c++14", True),
        ("func_exception_spec_widened", "c++17", False),
        ("func_exception_spec_widened", "c++20", False),
        ("func_exception_spec_widened", "c++23", False),
        # Inline variables are C++17 (P0386R2); C17 has no inline variables.
        ("object_became_inline_variable", "c++14", False),
        ("object_became_inline_variable", "c++17", True),
        ("object_became_inline_variable", "c17", False),
    ],
)
def test_an_edit_is_defined_only_in_the_standards_it_is(
    op: str, std: str, defined: bool
) -> None:
    assert (OP_BY_NAME[op].requires(_cfg(std)) is None) is defined


@pytest.mark.parametrize(
    ("construct", "std", "defined"),
    [
        ("no_unique_address", "c++17", False),
        ("no_unique_address", "c++20", True),
        ("concept", "c++17", False),
        ("concept", "c++20", True),
        # _BitInt is C23 (ISO/IEC 9899:2024).
        ("bit_int", "c17", False),
        ("bit_int", "c23", True),
    ],
)
def test_a_construct_is_infeasible_before_its_standard(
    construct: str, std: str, defined: bool
) -> None:
    lang = STANDARD_BY_NAME[std].lang
    point = Point(construct, lang, std, "baseline", "gcc", "gcc", "headers.castxml")
    why = infeasible(point)
    if defined:
        assert why is None, why
    else:
        assert why is not None and "which" in why


# --- the range type ----------------------------------------------------------


def test_a_range_includes_both_of_its_ends() -> None:
    r = StdRange(
        "c++", "c++11", "N3337 [except.spec]", last="c++14", end_cite="P0003R5"
    )
    got = [r.contains(STANDARD_BY_NAME[s]) for s in ("c++11", "c++14", "c++17")]
    assert got == [True, True, False]


def test_no_range_restricts_an_edit_to_no_standard() -> None:
    assert in_standards((), STANDARD_BY_NAME["c99"])


def test_a_language_no_range_names_is_never_in() -> None:
    # C has no inline variables; a C++17 range says nothing about C17.
    ranges = (StdRange("c++", "c++17", "P0386R2 (inline variables), [dcl.inline]"),)
    assert not in_standards(ranges, STANDARD_BY_NAME["c17"])
    assert in_standards(ranges, STANDARD_BY_NAME["c++17"])


@pytest.mark.parametrize(
    "make",
    [
        lambda: StdRange("c++", "c++17", ""),  # a range without its citation
        lambda: StdRange("c++", "c++11", "x", last="c++14"),  # removal, no citation
        lambda: StdRange("c", "c++17", "x"),  # standard of another language
        lambda: StdRange("c++", "c++14", "x", last="c++11", end_cite="y"),  # ends first
    ],
)
def test_a_malformed_range_is_rejected(make) -> None:
    with pytest.raises(ValueError):
        make()


def test_an_edit_needing_consumer_side_introduction_only_gates_on_introduction() -> (
    None
):
    # A consumer compiles the edit's header; one newer than a removal still
    # compiles it (the header guards the removed spelling), so only a consumer
    # older than the introduction cannot.
    dyn = OP_BY_NAME["func_exception_spec_widened"]
    assert dyn.consumer_gap(STANDARD_BY_NAME["c++17"]) is None
    inline = OP_BY_NAME["object_became_inline_variable"]
    assert inline.consumer_gap(STANDARD_BY_NAME["c++11"]) is not None
    assert inline.consumer_gap(STANDARD_BY_NAME["c++17"]) is None


# --- the feature table and its citations --------------------------------------


@pytest.mark.parametrize("feature", sorted(FEATURES))
def test_a_feature_range_agrees_with_the_feature_table(feature: str) -> None:
    ranges = feature_standards(feature)
    assert all(r.last is None for r in ranges), "no feature is removed in this table"
    for std in STANDARDS:
        assert in_standards(ranges, std, introduced_only=True) == has_feature(
            std, feature
        ), (feature, std.name)


# --- generation --------------------------------------------------------------


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # The tier code asks the real driver which -std spellings it accepts; the
    # range logic is independent of that, so every spelling is accepted here.
    def accepts(_tc: object, std: object) -> str:
        return f"-std={std.name}"  # type: ignore[attr-defined]

    monkeypatch.setattr(tiers, "std_flag", accepts)
    monkeypatch.setattr(tiers, "std_flag_for_driver", accepts)


@pytest.mark.parametrize("lang", ["c", "c++"])
def test_a_weekly_cell_starts_in_the_edits_own_standards(
    lang: str, fake_probe: None
) -> None:
    for op in OPERATORS:
        if not any(op.standard_ok(s) for s in tiers.stds(lang)):
            continue
        for cfg in tiers._weekly_configs(lang, TC, (), op):
            assert op.standard_ok(cfg.std), (op.name, cfg.std.name)


@pytest.mark.parametrize("lang", ["c", "c++"])
def test_a_row_standard_is_moved_into_the_edits_standards(
    lang: str, fake_probe: None
) -> None:
    options = tiers.stds(lang)
    for op in OPERATORS:
        if not any(op.standard_ok(s) for s in options):
            continue
        for slot in range(len(options)):
            chosen = tiers._repair_std(op, lang, slot, TC)
            assert op.standard_ok(chosen), (op.name, slot, chosen.name)


def test_the_dynamic_exception_edit_is_removed_at_c17_not_generated_there(
    fake_probe: None,
) -> None:
    # The weekly tier used to start every edit at the newest standard, so this
    # edit was emitted under C++23 and then reported as not applicable there.
    dyn = OP_BY_NAME["func_exception_spec_widened"]
    base = tiers._weekly_configs("c++", TC, (), dyn)[0]
    assert base.std.name == "c++14"


def test_every_range_in_the_operators_carries_its_citation() -> None:
    for op in OPERATORS:
        for r in op.standards:
            assert r.cite, op.name
            if r.last is not None:
                assert r.end_cite, op.name

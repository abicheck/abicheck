"""Unit tests for the undeclared-export census (field report H5)."""

from __future__ import annotations

import pytest

from abicheck.model.undeclared_export_census import (
    EXAMPLES_PER_GUARD,
    GUARD_WARNING_MARKER,
    UNDECLARED_EXPORT_WARNING_MARKER,
    census_to_dict,
    census_undeclared_exports,
    census_warnings,
    function_base_name,
)

# (mangled, demangled or None for a C symbol, expected cause or None)
_SYMBOLS = (
    ("_Z3fooRN4sycl5queueE", "foo(sycl::queue&)", "sycl"),
    ("_Z3barRKN4sycl3_V15eventE", "bar(sycl::_V1::event const&)", "sycl"),
    ("_ZN4sycl6detail1fEv", "sycl::detail::f()", "sycl"),
    ("_ZN2ns6helperEi", "ns::helper(int)", None),
    ("_ZN6mysycl1gEv", "mysycl::g()", None),
    ("c_entry", None, None),
)


@pytest.mark.parametrize("size", range(len(_SYMBOLS) + 1))
def test_census_counts_every_subset(size: int) -> None:
    chosen = _SYMBOLS[:size] + _SYMBOLS[size:][::2]
    demangled = {m: d for m, d, _ in chosen if d is not None}
    census = census_undeclared_exports([m for m, _, _ in chosen], demangled)
    expected_sycl = sum(1 for *_, c in chosen if c == "sycl")
    assert census.total == len(chosen)
    assert census.by_cause.get("sycl", 0) == expected_sycl
    assert census.attributed == expected_sycl


def test_duplicates_counted_once() -> None:
    census = census_undeclared_exports(
        ["_Z3fooRN4sycl5queueE"] * 3, {"_Z3fooRN4sycl5queueE": "foo(sycl::queue&)"}
    )
    assert (census.total, census.attributed) == (1, 1)


def test_warning_only_when_a_cause_is_recognized() -> None:
    unattributed = census_undeclared_exports(["c_entry", "_ZN2ns6helperEi"], {})
    assert census_warnings("new", unattributed) == []
    census = census_undeclared_exports(
        ["c_entry", "_Z3fooRN4sycl5queueE"],
        {"_Z3fooRN4sycl5queueE": "foo(sycl::queue&)"},
    )
    assert census_warnings("new", census) == [
        f"new side headers: 2 {UNDECLARED_EXPORT_WARNING_MARKER} "
        "(1 reference sycl:: (likely declared under #ifdef SYCL_LANGUAGE_VERSION, "
        "undefined in this parse; pass -D SYCL_LANGUAGE_VERSION=... to include "
        "them)); their API changes are not header-checked"
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("foo(int)", "foo"),
        ("c_entry", "c_entry"),
        ("ns::foo<int>(sycl::queue&) const", "foo"),
        ("ns::Outer<a::b<c>>::method(std::map<int, int> const&)", "method"),
        ("std::vector<int> ns::make<long>(long)", "make"),
        ("ns::Widget::Widget(int)", "Widget"),
        ("", ""),
    ],
)
def test_function_base_name(text: str, expected: str) -> None:
    assert function_base_name(text) == expected


_HIDDEN = {"FOO": ("foo_one", "foo_two"), "BAR": ("foo_two", "bar")}
_DEMANGLED = {
    "_Z7foo_onei": "foo_one(int)",
    "_Z7foo_twod": "foo_two(double)",
    "_ZN2ns3barERN4sycl5queueE": "ns::bar(sycl::queue&)",
    "_Z3bazRN4sycl5queueE": "baz(sycl::queue&)",
    "_Z6helperv": "helper()",
}


@pytest.mark.parametrize("size", range(len(_DEMANGLED) + 1))
def test_guard_attribution_wins_over_the_name_rule(size: int) -> None:
    chosen = sorted(_DEMANGLED)[:size]
    census = census_undeclared_exports(chosen, _DEMANGLED, _HIDDEN)
    # Oracle: each export's function name against the hand-written guard map.
    by_guard: dict[str, int] = {}
    guarded = sycl = 0
    for mangled in chosen:
        base = _DEMANGLED[mangled].split("(")[0].rsplit("::", 1)[-1]
        macros = [m for m, names in _HIDDEN.items() if base in names]
        for m in macros:
            by_guard[m] = by_guard.get(m, 0) + 1
        if macros:
            guarded += 1
        elif "sycl::" in _DEMANGLED[mangled]:
            sycl += 1
    assert dict(census.by_guard) == by_guard
    assert census.guard_attributed == guarded
    assert census.by_cause.get("sycl", 0) == sycl
    assert census.attributed == guarded + sycl
    assert census.total == len(chosen)


def test_guard_lines_one_per_macro_then_the_name_rule() -> None:
    census = census_undeclared_exports(sorted(_DEMANGLED), _DEMANGLED, _HIDDEN)
    assert census_warnings("old", census) == [
        f"old side headers: 2 {GUARD_WARNING_MARKER} BAR (not defined; pass -D BAR); "
        "their API changes are not header-checked",
        f"old side headers: 2 {GUARD_WARNING_MARKER} FOO (not defined; pass -D FOO); "
        "their API changes are not header-checked",
        f"old side headers: 5 {UNDECLARED_EXPORT_WARNING_MARKER} "
        "(1 reference sycl:: (likely declared under #ifdef SYCL_LANGUAGE_VERSION, "
        "undefined in this parse; pass -D SYCL_LANGUAGE_VERSION=... to include "
        "them)); their API changes are not header-checked",
    ]
    assert census_to_dict(census) == {
        "undeclared_total": 5,
        "attributed": 4,
        "by_guard": [
            {
                "macro": "BAR",
                "count": 2,
                "examples": ["foo_two(double)", "ns::bar(sycl::queue&)"],
            },
            {
                "macro": "FOO",
                "count": 2,
                "examples": ["foo_one(int)", "foo_two(double)"],
            },
        ],
        "by_cause": [{"cause": "sycl", "count": 1}],
    }


def test_examples_are_capped_and_counts_are_not() -> None:
    names = [f"f{i}" for i in range(EXAMPLES_PER_GUARD + 4)]
    census = census_undeclared_exports(names, {}, {"G": tuple(names)})
    assert census.by_guard == {"G": len(names)}
    assert census.guard_examples["G"] == tuple(sorted(names)[:EXAMPLES_PER_GUARD])

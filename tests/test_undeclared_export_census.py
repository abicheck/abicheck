"""Unit tests for the undeclared-export census (field report H5)."""

from __future__ import annotations

import pytest

from abicheck.model.undeclared_export_census import (
    UNDECLARED_EXPORT_WARNING_MARKER,
    census_undeclared_exports,
    census_warning,
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
    assert census_warning("new", unattributed) is None
    census = census_undeclared_exports(
        ["c_entry", "_Z3fooRN4sycl5queueE"],
        {"_Z3fooRN4sycl5queueE": "foo(sycl::queue&)"},
    )
    line = census_warning("new", census)
    assert line == (
        f"new side headers: 2 {UNDECLARED_EXPORT_WARNING_MARKER} "
        "(1 reference sycl:: (likely declared under #ifdef SYCL_LANGUAGE_VERSION, "
        "undefined in this parse; pass -D SYCL_LANGUAGE_VERSION=... to include "
        "them)); their API changes are not header-checked"
    )

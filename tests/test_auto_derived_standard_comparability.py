"""A header-dialect difference is a bounded (non-fatal) comparability gap
only when both sides derived it automatically and it is the only
unexplained profile difference; every other shape keeps the refusal
(case98, ADR-050 amendment)."""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from abicheck.compare.auto_derived_standard import auto_derived_standard_reason


def _snap(explicit: str | None, std: str, implied: str = "") -> SimpleNamespace:
    tc = {} if explicit is None else {"language_standard_explicit": explicit}
    if "-std=" in implied:
        tc["language_standard_implied"] = "1"
    return SimpleNamespace(
        ast_toolchain=tc,
        contract=SimpleNamespace(profile_fields={"language_standard": std}),
    )


STAMPS = ("0", "1", None)  # auto-derived, user pin, legacy (no provenance)
UNEXPLAINED = (
    {"language_standard"},
    {"language_standard", "target_triple"},
    {"language_standard", "compiler_family"},
    {"target_triple"},
    set(),
)


@pytest.mark.parametrize(
    ("old_stamp", "new_stamp", "unexplained", "unknown"),
    list(itertools.product(STAMPS, STAMPS, UNEXPLAINED, (set(), {"future_field"}))),
)
def test_bounded_only_for_auto_derived_standard_alone(
    old_stamp, new_stamp, unexplained, unknown
):
    reason = auto_derived_standard_reason(
        _snap(old_stamp, "probed:__cplusplus=201703L"),
        _snap(new_stamp, "gnu++20", "-x c++ -std=gnu++20 -march=x86-64"),
        set(unknown),
        set(unexplained),
    )
    bounded = (
        old_stamp == "0"
        and new_stamp == "0"
        and unexplained == {"language_standard"}
        and not unknown
    )
    assert (reason is not None) is bounded
    if bounded:
        assert "201703L" in reason and "gnu++20" in reason
        assert "compile.std" in reason and "--build-info" in reason


@pytest.mark.parametrize(
    ("old_implied", "new_implied", "bounded"),
    [
        ("", "-std=gnu++20", True),
        ("-std=gnu++17", "", True),
        ("-std=gnu++17", "-std=gnu++20", True),
        # No binary-implied standard: the split came from the header-content
        # heuristic or the probe alone, so the refusal stands.
        ("", "", False),
        ("-march=x86-64", "-mtune=generic", False),
    ],
)
def test_bounded_requires_a_binary_implied_standard(old_implied, new_implied, bounded):
    reason = auto_derived_standard_reason(
        _snap("0", "probed:__cplusplus=201703L", old_implied),
        _snap("0", "gnu++20", new_implied),
        set(),
        {"language_standard"},
    )
    assert (reason is not None) is bounded

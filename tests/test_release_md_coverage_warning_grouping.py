"""Release Markdown groups and caps the coverage warnings several members share."""

from __future__ import annotations

import copy
import itertools

import pytest

from abicheck.report.render_release_markdown import (
    MAX_COVERAGE_WARNING_CARRIERS,
    MAX_COVERAGE_WARNING_LINES,
    _release_md_coverage_warnings,
)

_WARNINGS = ["sycl disabled", "fingerprint_renames disabled", "same binary"]


def _entries(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.startswith("- ")]


@pytest.mark.parametrize("n_libs", [1, 2, 5, 28])
@pytest.mark.parametrize("mask", list(itertools.product([0, 1], repeat=3)))
def test_each_warning_once_ranked_and_carriers_capped(
    n_libs: int, mask: tuple[int, ...]
) -> None:
    # Library i carries warning j when bit j is set and (i + j) is even, so
    # carriers differ per warning and some warnings have a single carrier.
    libs = [
        {
            "library": f"lib{i}.so",
            "coverage_warnings": [
                w for j, w in enumerate(_WARNINGS) if mask[j] and (i + j) % 2 == 0
            ],
        }
        for i in range(n_libs)
    ]
    expected = {
        w: [lib["library"] for lib in libs if w in lib["coverage_warnings"]]
        for w in _WARNINGS
    }
    expected = {w: c for w, c in expected.items() if c}
    lines = _release_md_coverage_warnings(libs)
    if not expected:
        assert lines == []
        return
    entries = _entries(lines)
    assert len(entries) == len(expected)  # one line per distinct warning
    # Most-carried first; first-seen order breaks ties.
    first_seen = list(
        dict.fromkeys(w for lib in libs for w in lib["coverage_warnings"])
    )
    ranked = sorted(first_seen, key=lambda w: -len(expected[w]))
    assert [w for e in entries for w in _WARNINGS if w in e] == ranked
    for w, carriers in expected.items():
        (line,) = [e for e in entries if w in e]
        shown = carriers[:MAX_COVERAGE_WARNING_CARRIERS]
        for name in shown:
            assert f"`{name}`" in line
        for name in carriers[len(shown) :]:
            assert f"`{name}`" not in line
        positions = [line.index(f"`{name}`") for name in shown]
        assert positions == sorted(positions)  # first-seen library order
        if len(carriers) > 1:
            assert f"({len(carriers)} libraries:" in line
        if len(carriers) > MAX_COVERAGE_WARNING_CARRIERS:
            more = len(carriers) - MAX_COVERAGE_WARNING_CARRIERS
            assert f", and {more} more)" in line


def test_a_library_repeating_a_warning_is_named_once() -> None:
    libs = [
        {"library": "a.so", "coverage_warnings": ["w", "w"]},
        {"library": "b.so", "coverage_warnings": ["w"]},
    ]
    assert _entries(_release_md_coverage_warnings(libs)) == [
        "- w (2 libraries: `a.so`, `b.so`)"
    ]


def _mkl_like_scan(n_libs: int = 28) -> list[dict[str, object]]:
    """Shared warnings every member carries, plus one per-member variant each."""
    shared = [
        "Detector 'pe' disabled: no PE evidence",
        "Detector 'macho' disabled: no Mach-O evidence",
        "--exclude-header rule '*/internal/*' matched no header",
    ]
    return [
        {
            "library": f"libmkl_{i}.so",
            "coverage_warnings": [
                *shared,
                f"extraction note: {i + 3} declaration(s) moved owner in libmkl_{i}",
                f"{i + 1} undeclared export(s) in libmkl_{i}.so",
            ],
        }
        for i in range(n_libs)
    ]


def test_a_28_library_scan_renders_the_shared_warnings_then_counts_the_rest() -> None:
    libs = _mkl_like_scan()
    lines = _release_md_coverage_warnings(libs)
    entries = _entries(lines)
    # 3 shared + 56 per-member variants = 59 distinct; the cap shows 10 and
    # counts the other 49 in one closing line, so the section stays short.
    assert len(entries) == MAX_COVERAGE_WARNING_LINES + 1
    assert len(lines) <= MAX_COVERAGE_WARNING_LINES + 6
    for shared in (
        "Detector 'pe' disabled",
        "Detector 'macho' disabled",
        "--exclude-header rule",
    ):
        (line,) = [e for e in entries if shared in e]
        assert "(28 libraries: " in line
        assert "and 23 more)" in line
    closing = entries[-1]
    # The first seven per-member variants are shown (libs 0-2 in full, lib 3's
    # first), so the 49 hidden ones sit on libs 3-27: 25 libraries.
    assert "49 more distinct coverage warning(s) on 25 libraries" in closing
    assert "-o json=" in closing


def test_no_warning_is_lost_and_the_input_is_not_mutated() -> None:
    libs = _mkl_like_scan()
    before = copy.deepcopy(libs)
    entries = _entries(_release_md_coverage_warnings(libs))
    # The per-library data the JSON document is built from is unchanged.
    assert libs == before
    total_distinct = len({w for lib in libs for w in lib["coverage_warnings"]})  # type: ignore[union-attr]
    shown = len(entries) - 1
    hidden = int(entries[-1].split("...", 1)[1].split(" more", 1)[0])
    assert shown + hidden == total_distinct


def test_no_coverage_warnings_renders_nothing() -> None:
    assert _release_md_coverage_warnings([{"library": "a.so"}]) == []

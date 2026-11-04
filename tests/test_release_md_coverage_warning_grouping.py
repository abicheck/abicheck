"""Release Markdown groups a coverage warning shared by several members."""

from __future__ import annotations

import itertools

import pytest

from abicheck.report.render_release_markdown import _release_md_coverage_warnings

_WARNINGS = ["sycl disabled", "fingerprint_renames disabled", "same binary"]


@pytest.mark.parametrize("n_libs", [1, 2, 5, 28])
@pytest.mark.parametrize("mask", list(itertools.product([0, 1], repeat=3)))
def test_each_warning_once_and_every_carrier_named(
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
    entries = [ln for ln in lines if ln.startswith("- ")]
    assert len(entries) == len(expected)  # one line per distinct warning
    first_seen = list(
        dict.fromkeys(w for lib in libs for w in lib["coverage_warnings"])
    )
    assert [w for e in entries for w in _WARNINGS if w in e] == first_seen
    for w, carriers in expected.items():
        (line,) = [e for e in entries if w in e]
        for name in carriers:
            assert f"`{name}`" in line
        positions = [line.index(f"`{name}`") for name in carriers]
        assert positions == sorted(positions)  # first-seen library order
        assert sum(f"`{lib['library']}`" in line for lib in libs) == len(carriers)
        if len(carriers) > 1:
            assert f"({len(carriers)} libraries:" in line

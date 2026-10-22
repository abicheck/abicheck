"""#1706: release Libraries table Removed/Changed columns and per-library
public API added/removed lists with internal symbols collapsed to a count."""

from __future__ import annotations

import pytest

from abicheck.model.symbol_ownership import symbol_origin
from abicheck.report.release_public_api import (
    MAX_PUBLIC_API_ITEMS,
    render_release_public_api_markdown,
    split_public_internal,
)
from abicheck.report.render_release_markdown import _release_md_libraries_table

PUBLIC = ["foo_init", "_ZN3lib3runEv", "_ZN3svs7consumeERKNS_6detail5TokenE"]
INTERNAL = ["_ZN3lib6detail4implEv", "_ZN3lib8internal3fooEv", "_ZTIN3lib3FooE"]


@pytest.mark.parametrize(
    ("symbols", "public", "internal"),
    [
        ([], [], 0),
        (PUBLIC, sorted(PUBLIC), 0),
        (INTERNAL, [], len(INTERNAL)),
        (PUBLIC + INTERNAL, sorted(PUBLIC), len(INTERNAL)),
        (["b", "a", "a"], ["a", "b"], 0),
    ],
)
def test_split_partitions_every_symbol(symbols, public, internal):
    got_public, got_internal = split_public_internal(symbols)
    assert got_public == public
    assert got_internal == internal
    # Oracle: every input is either listed or counted -- nothing vanishes.
    hidden = {s for s in symbols if symbol_origin(s) != "public"}
    assert set(got_public) | hidden == set(symbols)


@pytest.mark.parametrize(
    ("added", "removed", "omitted", "expect"),
    [
        (["a"], [], {"added": 0, "removed": 0}, ["`a`"]),
        ([], ["r"], {"added": 0, "removed": 3}, ["`r`", "3 internal symbols omitted"]),
        ([], [], {"added": 1, "removed": 0}, ["1 internal symbol omitted"]),
    ],
)
def test_section_renders_lists_and_omitted_counts(added, removed, omitted, expect):
    lib = {
        "library": "libx.so",
        "public_api": {"added": added, "removed": removed, "internal_omitted": omitted},
    }
    text = "\n".join(render_release_public_api_markdown([lib]))
    assert "## Public API changes" in text
    assert "### `libx.so`" in text
    for needle in expect:
        assert needle in text


@pytest.mark.parametrize(
    "libs",
    [
        [],
        [{"library": "a.so"}],
        [
            {
                "library": "a.so",
                "public_api": {"added": [], "removed": [], "internal_omitted": {}},
            }
        ],
    ],
)
def test_section_absent_when_nothing_to_list(libs):
    assert render_release_public_api_markdown(libs) == []


def test_section_caps_with_visible_remainder():
    syms = [f"s{i:03d}" for i in range(MAX_PUBLIC_API_ITEMS + 5)]
    lib = {
        "library": "a.so",
        "public_api": {"added": syms, "removed": [], "internal_omitted": {}},
    }
    text = "\n".join(render_release_public_api_markdown([lib]))
    assert "5 more omitted" in text


@pytest.mark.parametrize(
    ("entry", "cells"),
    [
        ({"surface_removals": 4, "surface_modifications": 2}, "| 4 | 2 |"),
        ({}, "| — | — |"),
    ],
)
def test_libraries_table_has_removed_and_changed(entry, cells):
    lib = {"library": "a.so", "verdict": "COMPATIBLE", **entry}
    lines = _release_md_libraries_table([lib], {})
    assert any("| Removed | Changed |" in ln for ln in lines)
    assert lines[-1].endswith(cells)

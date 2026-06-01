# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Structural coverage for the Markdown and HTML reporters (G3).

JSON/SARIF/JUnit are schema-validated elsewhere; the human-facing
Markdown and HTML renderers were comparatively thin on tests, so a
misplaced section or unescaped value could slip through. These tests
drive real ``compare`` results across verdict tiers and assert the major
report sections (summary, severity groups, impact, release
recommendation, confidence) and HTML escaping are present — guarding
output *structure* without committing brittle full-text golden files.
"""
from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.html_report import generate_html_report
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.reporter import to_markdown


def _snap(ver: str, funcs: list[Function]) -> AbiSnapshot:
    s = AbiSnapshot(library="libfoo.so", version=ver)
    s.functions = funcs
    return s


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _removal_result():
    old = _snap("1.0", [_fn("compute", "_Z7computei"), _fn("helper", "_Z6helperi")])
    new = _snap("2.0", [_fn("compute", "_Z7computei")])
    return compare(old, new)


def _addition_result():
    old = _snap("1.0", [_fn("compute", "_Z7computei")])
    new = _snap("2.0", [_fn("compute", "_Z7computei"), _fn("extra", "_Z5extrai")])
    return compare(old, new)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def test_markdown_breaking_has_all_sections() -> None:
    result = _removal_result()
    assert result.verdict == Verdict.BREAKING
    md = to_markdown(result, show_recommendation=True, show_impact=True)
    # Severity group for the breaking change.
    assert "Breaking Changes" in md
    # Release recommendation reflects a MAJOR bump.
    assert "## Release Recommendation" in md
    assert "MAJOR" in md
    # Confidence/evidence metadata.
    assert "## Analysis Confidence" in md
    # Impact summary requested via show_impact.
    assert "## Impact Summary" in md


def test_markdown_additive_recommends_minor() -> None:
    result = _addition_result()
    assert result.verdict == Verdict.COMPATIBLE
    md = to_markdown(result, show_recommendation=True)
    assert "Additions" in md
    assert "## Release Recommendation" in md
    assert "MINOR" in md


def test_markdown_no_change_is_clean() -> None:
    same = [_fn("compute", "_Z7computei")]
    result = compare(_snap("1.0", same), _snap("2.0", list(same)))
    assert result.verdict == Verdict.NO_CHANGE
    md = to_markdown(result, show_recommendation=True)
    # No-change still produces a recommendation (none/patch), never a MAJOR.
    assert "## Release Recommendation" in md
    assert "MAJOR" not in md


@pytest.mark.parametrize("show_rec", [True, False])
def test_markdown_recommendation_is_opt_in(show_rec: bool) -> None:
    md = to_markdown(_removal_result(), show_recommendation=show_rec)
    assert ("## Release Recommendation" in md) is show_rec


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def test_html_breaking_has_sections() -> None:
    html = generate_html_report(
        _removal_result(), lib_name="libfoo.so",
        old_version="1.0", new_version="2.0",
    )
    assert html.lstrip().startswith("<!DOCTYPE")
    for section in ("Removed", "Changed", "Added", "Binary Compatibility"):
        assert section in html
    assert "BREAKING" in html.upper()


def test_html_escapes_symbol_names() -> None:
    # A symbol carrying HTML metacharacters must be escaped, not injected.
    old = _snap("1.0", [_fn("compute", "_Z7computei"), _fn("he<lp>er", "_Z6helperi")])
    new = _snap("2.0", [_fn("compute", "_Z7computei")])
    html = generate_html_report(
        compare(old, new), lib_name="libfoo.so",
        old_version="1.0", new_version="2.0",
    )
    assert "he&lt;lp&gt;er" in html
    assert "he<lp>er" not in html


def test_html_compatible_is_100_percent() -> None:
    html = generate_html_report(
        _addition_result(), lib_name="libfoo.so",
        old_version="1.0", new_version="2.0",
    )
    # Pure additions keep binary compatibility at 100%.
    assert "100" in html

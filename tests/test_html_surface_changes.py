"""The scalar ``compare`` HTML report's "Surface changes" section.

Oracle: the ``surface_changes`` block of the JSON report written by the same
run. Every addition, removal and modification listed there appears in the
HTML (up to the per-group cap, with the omitted count disclosed), and the
HTML lists no entry the JSON does not.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.report.render_html_review_sections import render_surface_changes_html
from abicheck.report.surface_changes import MAX_COMPACT_SURFACE_ITEMS
from abicheck.serialization import snapshot_to_json


def _fn(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _section_rows(page: str) -> dict[str, list[str]]:
    """Group -> symbols listed under it, read back out of the HTML."""
    m = re.search(r"<div class='section' id='surface-changes'>(.*?)</div>", page, re.S)
    assert m, "surface changes section missing"
    body = m.group(1)
    groups: dict[str, list[str]] = {}
    for label, chunk in re.findall(
        r"<h4>(\w+) \(\d+\)</h4>(.*?)(?=<h4>|$)", body, re.S
    ):
        groups[label] = [
            html.unescape(s)
            for s in re.findall(r"<tr><td><code>(.*?)</code></td>", chunk)
        ]
    return groups


def _run(*args: str) -> int:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code


def test_cli_html_lists_exactly_the_json_surface_changes(tmp_path: Path) -> None:
    old = AbiSnapshot(
        library="libs.so",
        version="1",
        functions=[_fn("keep"), _fn("gone"), _fn("retyped", params=("int",))],
    )
    new = AbiSnapshot(
        library="libs.so",
        version="2",
        functions=[_fn("keep"), _fn("added<T>"), _fn("retyped", params=("long",))],
    )
    (tmp_path / "old.json").write_text(snapshot_to_json(old))
    (tmp_path / "new.json").write_text(snapshot_to_json(new))
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    code = _run(
        "compare",
        str(tmp_path / "old.json"),
        str(tmp_path / "new.json"),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
    )
    code_plain = _run(
        "compare",
        str(tmp_path / "old.json"),
        str(tmp_path / "new.json"),
        "-o",
        f"json={tmp_path / 'p.json'}",
    )
    assert code == code_plain  # rendering never changes a gate
    section = json.loads(out_json.read_text(encoding="utf-8"))["surface_changes"]
    assert section["total"] >= 3
    groups = _section_rows(out_html.read_text(encoding="utf-8"))
    for label, key in (
        ("Additions", "additions"),
        ("Removals", "removals"),
        ("Modifications", "modifications"),
    ):
        assert groups[label] == [e["symbol"] for e in section[key]]
    assert "added&lt;T&gt;" in out_html.read_text(encoding="utf-8")


def test_no_surface_changes_renders_no_section() -> None:
    assert render_surface_changes_html(None) == ""
    assert (
        render_surface_changes_html(
            {"total": 0, "additions": [], "removals": [], "modifications": []}
        )
        == ""
    )


def _entry(sym: str) -> dict[str, object]:
    return {
        "kind": "func_added",
        "symbol": sym,
        "description": "",
        "verdict": "COMPATIBLE",
        "category": "addition",
        "old_declaration": None,
        "new_declaration": f"int {sym}()",
        "source_location": None,
    }


@pytest.mark.parametrize(
    ("n_add", "n_rem", "n_mod", "limit"),
    [(0, 1, 0, 12), (30, 1, 5, 12), (12, 13, 0, 12), (3, 3, 3, 0), (40, 0, 2, 5)],
)
def test_each_group_is_capped_independently_with_disclosed_counts(
    n_add: int, n_rem: int, n_mod: int, limit: int
) -> None:
    section = {
        "total": n_add + n_rem + n_mod,
        "additions": [_entry(f"a{i}") for i in range(n_add)],
        "removals": [_entry(f"r{i}") for i in range(n_rem)],
        "modifications": [_entry(f"m{i}") for i in range(n_mod)],
    }
    page = render_surface_changes_html(section, limit=limit)
    groups = _section_rows(page)
    for label, n, prefix in (
        ("Additions", n_add, "a"),
        ("Removals", n_rem, "r"),
        ("Modifications", n_mod, "m"),
    ):
        shown = min(n, limit)
        assert groups.get(label, []) == [f"{prefix}{i}" for i in range(shown)]
        assert f"<h4>{label} ({n})</h4>" in page
        omitted = n - shown
        if omitted:
            quantifier = f"{omitted} more" if shown else f"all {omitted}"
            assert f"{quantifier} {label.lower()} omitted" in page
        else:
            assert f"{label.lower()} omitted" not in page


def test_default_cap_matches_markdown() -> None:
    section = {
        "total": 20,
        "additions": [_entry(f"a{i}") for i in range(20)],
        "removals": [],
        "modifications": [],
    }
    groups = _section_rows(render_surface_changes_html(section))
    assert len(groups["Additions"]) == MAX_COMPACT_SURFACE_ITEMS

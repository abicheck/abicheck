"""The release (directory/package) ``surface_changes`` block (schema 2.2).

Oracles, stated independently of the implementation:

* the JSON document is the oracle for the HTML and Markdown renderings: every
  entry the JSON lists appears once in each, and nothing else does;
* a one-member package reports the same entries a standalone scalar
  ``compare`` of that pair reports (the scalar report is the oracle);
* a one-sided library is never a removal or addition without inventory proof
  (a live directory listing is unproven, so its absence is ``unproven``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.serialization import snapshot_to_json


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        params=[Param(name="x", type="int")],
        visibility=Visibility.PUBLIC,
    )


def _snap(library: str, funcs: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1",
        functions=[_fn(f) for f in funcs],
        elf=ElfMetadata(soname=library),
    )


def _write_release(
    tmp_path: Path, spec: dict[str, dict[str, list[str] | None]]
) -> tuple[Path, Path]:
    """*spec*: member -> {"old": [funcs] | None, "new": [funcs] | None}."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for member, sides in spec.items():
        for side, d in (("old", old_dir), ("new", new_dir)):
            funcs = sides.get(side)
            if funcs is None:
                continue
            (d / member).write_text(
                snapshot_to_json(_snap(member, funcs)), encoding="utf-8"
            )
    return old_dir, new_dir


def _run(*args: str) -> int:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code


_SPEC: dict[str, dict[str, list[str] | None]] = {
    "libcore.so.1": {"old": ["core_a", "core_gone"], "new": ["core_a", "core_new"]},
    "libmid.so.1": {"old": ["mid"], "new": ["mid", "mid_new"]},
    "libtop.so.1": {"old": ["top"], "new": ["top"]},
    "libold.so.1": {"old": ["old_only"], "new": None},
}


def _entries(block: dict) -> set[tuple[str, str, str]]:
    """(library, group, symbol) for every surface entry the block lists."""
    return {
        (e["library"], group, e["symbol"])
        for group in ("additions", "removals", "modifications")
        for e in block[group]
    }


def _run_release(tmp_path: Path, spec, *, extra: tuple[str, ...] = ()) -> dict:
    old_dir, new_dir = _write_release(tmp_path, spec)
    out_json = tmp_path / "r.json"
    out_html = tmp_path / "r.html"
    out_md = tmp_path / "r.md"
    _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
        "-o",
        f"markdown={out_md}",
        *extra,
    )
    doc = json.loads(out_json.read_text())
    doc["_html"] = out_html.read_text()
    doc["_md"] = out_md.read_text()
    return doc


def test_release_json_attributes_every_member_entry(tmp_path: Path) -> None:
    doc = _run_release(tmp_path, _SPEC)
    block = doc["surface_changes"]

    assert _entries(block) == {
        ("libcore.so.1", "additions", "core_new"),
        ("libcore.so.1", "removals", "core_gone"),
        ("libmid.so.1", "additions", "mid_new"),
    }
    assert block["counts"] == {
        "additions": 2,
        "removals": 1,
        "modifications": 0,
        "members_compared": 3,
    }
    assert block["total"] == 3
    assert block["members"] == [
        {
            "library": "libcore.so.1",
            "total": 2,
            "additions": 1,
            "removals": 1,
            "modifications": 0,
        },
        {
            "library": "libmid.so.1",
            "total": 1,
            "additions": 1,
            "removals": 0,
            "modifications": 0,
        },
        {
            "library": "libtop.so.1",
            "total": 0,
            "additions": 0,
            "removals": 0,
            "modifications": 0,
        },
    ]


def test_html_and_markdown_list_exactly_the_json_entries(tmp_path: Path) -> None:
    doc = _run_release(tmp_path, _SPEC)
    expected = _entries(doc["surface_changes"])

    md_entries = set()
    group = None
    for line in doc["_md"].splitlines():
        if line.startswith("**Additions**"):
            group = "additions"
        elif line.startswith("**Removals**"):
            group = "removals"
        elif line.startswith("**Modifications**"):
            group = "modifications"
        match = re.match(r"- `([^`]+)` · \*\*(.+?)\*\* —", line)
        if match and group:
            md_entries.add((match.group(1), group, match.group(2)))
    assert md_entries == expected

    html_page = doc["_html"]
    surface = html_page[html_page.index("id='surface-changes'") :]
    surface = surface[: surface.index("</div>")]
    html_entries = set(
        re.findall(
            r"<code>([^<]+)</code> &middot; <code>([^<]+)</code>",
            surface,
        )
    )
    assert {(lib, sym) for lib, _, sym in expected} == html_entries
    assert html_page.count("&middot; <code>") == len(expected)


def test_fully_compatible_release_still_lists_its_additions(tmp_path: Path) -> None:
    spec = {
        "libcore.so.1": {"old": ["core_a"], "new": ["core_a", "core_new"]},
        "libmid.so.1": {"old": ["mid"], "new": ["mid", "mid_new"]},
    }
    doc = _run_release(tmp_path, spec)

    assert doc["verdict"] not in ("BREAKING", "API_BREAK")
    assert doc["surface_changes"]["removals"] == []
    assert _entries(doc["surface_changes"]) == {
        ("libcore.so.1", "additions", "core_new"),
        ("libmid.so.1", "additions", "mid_new"),
    }
    assert "## Surface changes" in doc["_md"]
    assert "core_new" in doc["_md"] and "mid_new" in doc["_md"]


def test_one_sided_library_without_inventory_proof_is_never_a_removal(
    tmp_path: Path,
) -> None:
    doc = _run_release(tmp_path, _SPEC)
    states = {
        row["library"]: row["state"] for row in doc["surface_changes"]["library_states"]
    }

    # A live directory listing cannot prove absence: libold is unmatched,
    # not removed (ADR-065 D2), and no removal is invented for it.
    assert states == {"libold.so.1": "unproven"}
    assert all(
        row["library"] != "libold.so.1" for row in doc["surface_changes"]["removals"]
    )


def test_one_member_package_reports_the_scalar_compare_entries(tmp_path: Path) -> None:
    spec = {
        "libcore.so.1": {"old": ["core_a", "core_gone"], "new": ["core_a", "core_new"]}
    }
    old_dir, new_dir = _write_release(tmp_path, spec)
    scalar_json = tmp_path / "scalar.json"
    _run(
        "compare",
        str(old_dir / "libcore.so.1"),
        str(new_dir / "libcore.so.1"),
        "-o",
        f"json={scalar_json}",
    )
    scalar = json.loads(scalar_json.read_text())["surface_changes"]

    (tmp_path / "rel").mkdir()
    release = _run_release(tmp_path / "rel", spec)["surface_changes"]

    for group in ("additions", "removals", "modifications"):
        stripped = [
            {k: v for k, v in e.items() if k != "library"} for e in release[group]
        ]
        assert stripped == scalar[group]
        assert all(e["library"] == "libcore.so.1" for e in release[group])


def test_stored_and_live_folds_agree_over_the_same_member_results() -> None:
    """Both operands fold their members through one owner. Asserted in-process
    (the CLI stored path's name pairing is a separate, pre-existing matter):
    the same ``DiffResult`` projected through a live ``libraries[]`` entry and
    through a stored ``per_library`` result must give the identical block."""
    from abicheck.checker import compare
    from abicheck.report.release_surface_changes import (
        live_surface_changes,
        stored_surface_changes,
    )

    diff = compare(
        _snap("libcore.so", ["core_a", "core_gone"]),
        _snap("libcore.so", ["core_a", "core_new"]),
    )
    live = live_surface_changes([{"library": "libcore.so", "_diff_result": diff}], None)
    stored = stored_surface_changes(
        SimpleNamespace(
            result=SimpleNamespace(per_library=[diff]),
            scope_terms=SimpleNamespace(record=None),
        )
    )

    assert live == stored
    assert _entries(live) == {
        ("libcore.so", "additions", "core_new"),
        ("libcore.so", "removals", "core_gone"),
    }

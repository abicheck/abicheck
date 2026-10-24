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
    stored = stored_surface_changes([diff], None)

    assert live == stored
    assert _entries(live) == {
        ("libcore.so", "additions", "core_new"),
        ("libcore.so", "removals", "core_gone"),
    }


def test_markdown_state_table_escapes_pipes_and_line_breaks() -> None:
    """A library name or reason with a pipe or a newline must not change the
    table's columns or rows (CodeRabbit review on #1781)."""
    from abicheck.report.release_surface_changes import render_release_surface_lines

    block = {
        "total": 0,
        "additions": [],
        "removals": [],
        "modifications": [],
        "members": [],
        "library_states": [
            {"library": "lib`x|y.so", "state": "unproven", "reason": "a | b\nc"}
        ],
    }
    rows = [
        line for line in render_release_surface_lines(block) if line.startswith("| `")
    ]

    assert rows == ["| `lib'x\\|y.so` | unproven | a \\| b c |"]


def _member(name: str, state, old: bool, new: bool, reason: str = ""):
    from abicheck.model.scope_acquisition import MemberAcquisition

    return MemberAcquisition(
        member=name, state=state, old_present=old, new_present=new, reason=reason
    )


def _record(members, removed=(), added=()):
    """The two proven-inventory properties and the expected set are all the
    release block reads from an acquisition record."""
    return SimpleNamespace(
        expected_members=tuple(members),
        proven_removed_members=tuple(m for m in members if m.member in removed),
        proven_added_members=tuple(m for m in members if m.member in added),
    )


def test_library_states_name_each_one_sided_state_and_skip_two_sided_members() -> None:
    from abicheck.model.scope_acquisition import AcquisitionState as S
    from abicheck.report.release_surface_changes import release_surface_changes

    members = [
        _member("libgone.so", S.NOT_SUPPLIED, old=True, new=False),
        _member("libnew.so", S.NOT_SUPPLIED, old=False, new=True),
        _member("libunproven.so", S.NOT_SUPPLIED, old=True, new=False),
        _member("libnever.so", S.EXPECTED_NOT_PRODUCED, old=True, new=False),
        _member("libbroke.so", S.FAILED, old=False, new=True),
        _member("libout.so", S.OUT_OF_SCOPE, old=True, new=False),
        _member("libboth.so", S.AVAILABLE, old=True, new=True),
    ]
    record = _record(members, removed={"libgone.so"}, added={"libnew.so"})

    block = release_surface_changes([], record)

    states = {row["library"]: row["state"] for row in block["library_states"]}
    assert states == {
        "libgone.so": "removed",
        "libnew.so": "added",
        "libunproven.so": "unproven",
        "libnever.so": "expected_not_produced",
        "libbroke.so": "failed",
        "libout.so": "unselected",
    }


def test_support_promise_component_is_a_state_not_a_member() -> None:
    from abicheck.checker import compare
    from abicheck.report.release_surface_changes import (
        compute_surface_changes,
        release_surface_changes,
    )

    diff = compare(_snap("libpromised.so", ["p"]), _snap("libpromised.so", ["p", "p2"]))
    section = compute_surface_changes(diff)

    block = release_surface_changes(
        [("libpromised.so", section)], None, {"libpromised.so": "retired"}
    )

    assert block["library_states"] == [
        {"library": "libpromised.so", "state": "retired", "reason": ""}
    ]
    assert block["members"] == []


def test_live_block_takes_promise_entries_out_of_the_member_fold() -> None:
    from abicheck.checker import compare
    from abicheck.report.release_surface_changes import live_surface_changes

    diff = compare(_snap("libx.so", ["x_old"]), _snap("libx.so", ["x_old", "x_new"]))
    results = [
        {"library": "libcomp.so", "support_promise": "introduced"},
        {"library": "libnodata.so"},
        {"library": "libx.so", "_diff_result": diff},
    ]

    block = live_surface_changes(results, None)

    assert [m["library"] for m in block["members"]] == ["libx.so"]
    assert block["library_states"] == [
        {"library": "libcomp.so", "state": "introduced", "reason": ""}
    ]
    assert _entries(block) == {("libx.so", "additions", "x_new")}


def test_attach_is_idempotent_and_skips_entries_without_a_diff() -> None:
    from abicheck.checker import compare
    from abicheck.report.release_surface_changes import attach_member_surface_changes

    diff = compare(_snap("libx.so", ["x"]), _snap("libx.so", ["x", "x_new"]))
    preset = {"total": 99}
    results = [
        {"library": "libnodiff.so"},
        {"library": "libpreset.so", "_diff_result": diff, "surface_changes": preset},
        {"library": "libx.so", "_diff_result": diff},
    ]

    attach_member_surface_changes(results)
    attach_member_surface_changes(results)

    assert "surface_changes" not in results[0]
    assert results[1]["surface_changes"] is preset
    assert results[2]["surface_changes"]["total"] == 1


def test_member_section_reads_attached_block_then_diff_then_nothing() -> None:
    from abicheck.checker import compare
    from abicheck.report.release_surface_changes import member_surface_section

    diff = compare(_snap("libx.so", ["x"]), _snap("libx.so", ["x", "x_new"]))

    attached = member_surface_section({"surface_changes": {"total": 0}})
    computed = member_surface_section({"_diff_result": diff})
    neither = member_surface_section({"library": "libnone.so"})

    assert attached is not None and attached.total == 0
    assert computed is not None and computed.total == 1
    assert neither is None


def test_markdown_section_omits_the_state_table_when_there_are_none() -> None:
    from abicheck.report.release_surface_changes import render_release_surface_lines

    block = {
        "total": 1,
        "additions": [
            {
                "kind": "func_added",
                "symbol": "x_new",
                "description": "",
                "verdict": "COMPATIBLE",
                "category": "addition",
                "old_declaration": None,
                "new_declaration": "int x_new()",
                "source_location": None,
                "library": "libx.so",
            }
        ],
        "removals": [],
        "modifications": [],
        "members": [{"library": "libx.so"}],
        "library_states": None,
    }

    lines = render_release_surface_lines(block)

    assert "## Surface changes" in lines
    assert not any("Libraries present on one side" in line for line in lines)
    assert render_release_surface_lines({"total": 0, "library_states": []}) == []

"""The directory/package (release) ``compare`` HTML report.

Oracle: the release JSON document the same run writes with ``-o json=...``.
Every member, scope entry and recorded dependency edge in that document must
appear in the HTML, and the HTML must name no member, node or edge the
document does not record. The expected edge set is derived here, directly
from the document's own ``libraries[].dependencies`` facts, by a
deliberately separate procedure (no import of the graph module's matching
code), so a matching bug in the implementation cannot agree with itself.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.report.release_dependency_graph import MAX_GRAPH_NODES
from abicheck.report.render_release_html import render_release_html
from abicheck.serialization import snapshot_to_json

# ── helpers ────────────────────────────────────────────────────────────────


class _Tables(HTMLParser):
    """Collects, per table id/section, the text of every row's cells, plus
    every SVG node/edge ``<title>``."""

    def __init__(self) -> None:
        super().__init__()
        self.section: str | None = None
        self.table_id: str | None = None
        self.rows: dict[str, list[list[str]]] = {}
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.node_names: list[str] = []
        self.edge_titles: list[str] = []
        self._in_edge = False
        self._title: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "div" and a.get("id"):
            self.section = a["id"]
        if tag == "table":
            self.table_id = a.get("id") or self.section
        if tag == "tr":
            self._row = []
        if tag in ("td",) and self._row is not None:
            self._cell = []
        if tag == "g" and a.get("class") == "rel-node":
            self.node_names.append(str(a.get("data-name")))
        if tag == "path" and a.get("class") == "rel-edge":
            self._in_edge = True
        if tag == "title" and self._in_edge:
            self._title = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if tag == "tr" and self._row:
            self.rows.setdefault(str(self.table_id), []).append(self._row)
            self._row = None
        if tag == "title" and self._title is not None:
            self.edge_titles.append("".join(self._title))
            self._title = None
            self._in_edge = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        if self._title is not None:
            self._title.append(data)


def _parse(page: str) -> _Tables:
    parser = _Tables()
    parser.feed(page)
    return parser


def _expected_edges(doc: dict) -> set[tuple[str, str, str]]:
    """Independent oracle: (library, needed-node, sides) straight off the
    document. A needed entry names a member when it equals some member's
    recorded soname or file name; otherwise it is its own external node."""
    owner: dict[str, str] = {}
    for m in doc["libraries"]:
        owner[m["library"]] = m["library"]
    for m in doc["libraries"]:
        for side in ("old", "new"):
            facts = (m.get("dependencies") or {}).get(side)
            if facts and facts.get("soname") and facts["soname"] not in owner:
                owner[facts["soname"]] = m["library"]
    seen: dict[tuple[str, str], set[str]] = {}
    for m in doc["libraries"]:
        for side in ("old", "new"):
            facts = (m.get("dependencies") or {}).get(side)
            for lib in (facts or {}).get("needed", []):
                seen.setdefault((m["library"], owner.get(lib, lib)), set()).add(side)
    return {
        (s, t, "old+new" if len(sides) == 2 else sides.pop())
        for (s, t), sides in seen.items()
    }


def _snap(
    library: str, funcs: list[str], needed: list[str], soname: str
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1",
        functions=[
            Function(name=f, mangled=f, return_type="int", visibility=Visibility.PUBLIC)
            for f in funcs
        ],
        elf=ElfMetadata(soname=soname, needed=list(needed)),
    )


def _write_release(tmp_path: Path, spec: dict[str, dict]) -> tuple[Path, Path]:
    """*spec*: member -> {"old": (funcs, needed) | None, "new": ...}."""
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for member, sides in spec.items():
        for side, d in (("old", old_dir), ("new", new_dir)):
            if sides.get(side) is None:
                continue
            funcs, needed = sides[side]
            (d / f"{member}.json").write_text(
                snapshot_to_json(_snap(member, funcs, needed, member)), encoding="utf-8"
            )
    return old_dir, new_dir


def _run(*args: str) -> int:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code


_SPEC = {
    "libcore.so.1": {
        "old": (["core_a", "core_gone"], ["libc.so.6"]),
        "new": (["core_a"], ["libc.so.6"]),
    },
    "libmid.so.1": {
        "old": (["mid"], ["libcore.so.1"]),
        "new": (["mid", "mid_new"], ["libcore.so.1", "libz.so.1"]),
    },
    "libtop.so.1": {
        "old": (["top"], ["libmid.so.1", "libm.so.6"]),
        "new": (["top"], ["libmid.so.1"]),
    },
    "libold.so.1": {"old": (["old_only"], []), "new": None},
}


# ── the CLI path, JSON as oracle ───────────────────────────────────────────


def test_release_html_states_exactly_what_the_release_json_records(
    tmp_path: Path,
) -> None:
    old_dir, new_dir = _write_release(tmp_path, _SPEC)
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    code = _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
    )
    doc = json.loads(out_json.read_text())
    page = out_html.read_text()
    parsed = _parse(page)

    # headline
    assert f"Release verdict: {doc['verdict']}" in page
    assert f"Exit code <strong>{doc['exit']['code']}</strong>" in page
    assert code == doc["exit"]["code"]

    # members: exactly the document's libraries, with their counts
    member_rows = {r[0]: r for r in parsed.rows["members"]}
    assert set(member_rows) == {m["library"] for m in doc["libraries"]}
    for m in doc["libraries"]:
        row = member_rows[m["library"]]
        assert row[1] == m["verdict"]
        assert row[2:] == [
            str(m[k])
            for k in (
                "breaking",
                "source_breaks",
                "risk_changes",
                "compatible_additions",
                "quality_issues",
            )
        ]

    # scope: every unchecked / out-of-scope / proven entry, with its reason
    scope = doc["comparison_scope"]
    scope_rows = parsed.rows["comparison-scope"]
    listed = {r[0] for r in scope_rows if len(r) == 5}
    expected_scope = (
        set(scope["unchecked"])
        | set(scope["out_of_scope"])
        | set(scope["proven_removed"])
        | set(scope["proven_added"])
    )
    assert listed == expected_scope and "libold.so.1.json" in listed
    reasons = {m["name"]: m["reason"] for m in scope["members"]}
    for r in scope_rows:
        if len(r) == 5:
            assert r[4] == reasons[r[0]]

    # graph: every recorded edge drawn and tabulated, nothing else
    expected = _expected_edges(doc)
    assert expected, "fixture must record dependencies"
    table = {tuple(r[:2]) + (r[3],) for r in parsed.rows["dependency-edges"]}
    assert table == expected
    titles = {
        re.match(r"(.+) needs (.+) \(recorded on (.+)\)", t).groups()
        for t in parsed.edge_titles
    }
    assert titles == expected
    nodes = set(parsed.node_names)
    expected_nodes = (
        {m["library"] for m in doc["libraries"]}
        | {m["name"] for m in scope["members"]}
        | {t for _, t, _ in expected}
    )
    assert nodes == expected_nodes
    # one-side-only relations are real and disclosed as such
    assert ("libtop.so.1.json", "libm.so.6", "old") in expected
    assert ("libmid.so.1.json", "libz.so.1", "new") in expected


def test_rendering_html_never_changes_the_exit_code(tmp_path: Path) -> None:
    old_dir, new_dir = _write_release(tmp_path, _SPEC)
    plain = _run(
        "compare", str(old_dir), str(new_dir), "-o", f"json={tmp_path / 'a.json'}"
    )
    with_html = _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={tmp_path / 'b.json'}",
        "-o",
        f"html={tmp_path / 'b.html'}",
    )
    html_only = _run(
        "compare", str(old_dir), str(new_dir), "-o", f"html={tmp_path / 'c.html'}"
    )
    assert plain == with_html == html_only
    assert plain != 0  # the fixture really gates, so equality is not vacuous
    a, b = (json.loads((tmp_path / n).read_text()) for n in ("a.json", "b.json"))
    assert a["verdict"] == b["verdict"] and a["exit"] == b["exit"]


def test_release_json_records_per_side_dependency_facts(tmp_path: Path) -> None:
    old_dir, new_dir = _write_release(tmp_path, _SPEC)
    out = tmp_path / "r.json"
    _run("compare", str(old_dir), str(new_dir), "-o", f"json={out}")
    doc = json.loads(out.read_text())
    assert doc["release_schema_version"] == "1.9"
    by_name = {m["library"]: m for m in doc["libraries"]}
    checked = 0
    for member, sides in _SPEC.items():
        if sides["new"] is None:
            assert (
                f"{member}.json" not in by_name
            )  # never compared, so nothing recorded
            continue
        for side in ("old", "new"):
            assert by_name[f"{member}.json"]["dependencies"][side] == {
                "soname": member,
                "needed": sides[side][1],
            }
            checked += 1
    assert checked == 6


def test_a_member_without_elf_facts_records_no_dependencies_block(
    tmp_path: Path,
) -> None:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for d in (old_dir, new_dir):
        d.mkdir()
        snap = AbiSnapshot(
            library="libx.so",
            version="1",
            functions=[
                Function(
                    name="f",
                    mangled="f",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        (d / "libx.json").write_text(snapshot_to_json(snap), encoding="utf-8")
    out = tmp_path / "r.json"
    _run(
        "compare",
        str(old_dir),
        str(new_dir),
        "-o",
        f"json={out}",
        "-o",
        f"html={tmp_path / 'r.html'}",
    )
    doc = json.loads(out.read_text())
    assert "dependencies" not in doc["libraries"][0]
    assert (
        "0 of 1 compared member(s) recorded dependency facts"
        in (tmp_path / "r.html").read_text()
    )


# ── projection properties over generated documents ─────────────────────────


def _random_document(rng: random.Random, n_members: int) -> dict:
    verdicts = [
        "BREAKING",
        "API_BREAK",
        "COMPATIBLE",
        "COMPATIBLE_WITH_RISK",
        "NO_CHANGE",
    ]
    names = [f"lib{i}<&\"'>.so" for i in range(n_members)]
    externals = ["libc.so.6", "libm.so.6", "libext<x>.so"]
    libs = []
    for i, name in enumerate(names):
        deps = {}
        for side in ("old", "new"):
            if rng.random() < 0.85:
                pool = names[:i] + externals + names[i + 1 :]
                deps[side] = {
                    "soname": name,
                    "needed": sorted(rng.sample(pool, rng.randint(0, 3))),
                }
        entry = {
            "library": name,
            "verdict": rng.choice(verdicts),
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
            "quality_issues": 0,
        }
        if deps:
            entry["dependencies"] = deps
        libs.append(entry)
    return {
        "release_schema_version": "1.9",
        "verdict": "BREAKING",
        "old_dir": "/o",
        "new_dir": "/n",
        "exit": {"code": 4, "reasons": ["compatibility_gate"]},
        "libraries": libs,
        "comparison_scope": {
            "completeness": "complete",
            "counts": {"available": n_members},
            "members": [
                {
                    "name": "lib<gone>.so",
                    "state": "not_supplied",
                    "old_present": True,
                    "new_present": False,
                    "reason": "why <b>",
                }
            ],
            "unchecked": ["lib<gone>.so"],
            "out_of_scope": [],
            "proven_removed": [],
            "proven_added": [],
        },
    }


@pytest.mark.parametrize("seed", range(12))
def test_generated_documents_edges_and_nodes_match_the_oracle(seed: int) -> None:
    rng = random.Random(seed)
    doc = _random_document(rng, rng.randint(1, 9))
    page = render_release_html(doc)
    parsed = _parse(page)
    expected = _expected_edges(doc)
    table = {tuple(r[:2]) + (r[3],) for r in parsed.rows.get("dependency-edges", [])}
    assert table == expected
    assert set(parsed.node_names) == (
        {m["library"] for m in doc["libraries"]}
        | {"lib<gone>.so"}
        | {t for _, t, _ in expected}
    )
    # every name is escaped: no raw markup survives from the data
    assert "<gone>" not in page and "why <b>" not in page and "<&" not in page
    assert "lib&lt;gone&gt;.so" in page


def test_large_graph_is_capped_with_a_disclosed_count() -> None:
    n = MAX_GRAPH_NODES + 25
    doc = _random_document(random.Random(7), n)
    page = render_release_html(doc)
    parsed = _parse(page)
    expected = _expected_edges(doc)
    all_nodes = (
        {m["library"] for m in doc["libraries"]}
        | {"lib<gone>.so"}
        | {t for _, t, _ in expected}
    )
    assert len(parsed.node_names) == MAX_GRAPH_NODES
    assert set(parsed.node_names) <= all_nodes
    drawn = {tuple(r[:2]) + (r[3],) for r in parsed.rows["dependency-edges"]}
    assert drawn <= expected
    omitted_nodes = len(all_nodes) - MAX_GRAPH_NODES
    omitted_edges = len(expected) - len(drawn)
    assert (
        f"{omitted_nodes} more librar(ies) and {omitted_edges} more dependenc(ies) are not drawn"
        in page
    )
    # every drawn edge joins two drawn nodes
    assert all(s in parsed.node_names and t in parsed.node_names for s, t, _ in drawn)


# ── real binaries ──────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_real_shared_libraries_record_dt_needed_edges(tmp_path: Path) -> None:
    src = {
        "a1.c": "int a_base(int x){return x+1;}\nint a_gone(void){return 2;}\n",
        "a2.c": "int a_base(int x){return x+1;}\n",
        "b.c": "int a_base(int);\nint b_use(int x){return a_base(x);}\n",
    }
    for name, text in src.items():
        (tmp_path / name).write_text(text)
    for side, a_src in (("old", "a1.c"), ("new", "a2.c")):
        d = tmp_path / side
        d.mkdir()
        cc = ["gcc", "-shared", "-fPIC", "-g", "-Wl,--no-as-needed"]
        subprocess.run(
            [
                *cc,
                "-o",
                str(d / "liba.so.1"),
                "-Wl,-soname,liba.so.1",
                str(tmp_path / a_src),
            ],
            check=True,
        )
        subprocess.run(
            [
                *cc,
                "-o",
                str(d / "libb.so.1"),
                "-Wl,-soname,libb.so.1",
                str(tmp_path / "b.c"),
                f"-L{d}",
                "-l:liba.so.1",
            ],
            check=True,
        )
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    code = _run(
        "compare",
        str(tmp_path / "old"),
        str(tmp_path / "new"),
        "-o",
        f"json={out_json}",
        "-o",
        f"html={out_html}",
    )
    doc = json.loads(out_json.read_text())
    assert code == doc["exit"]["code"]
    expected = _expected_edges(doc)
    assert ("libb.so.1", "liba.so.1", "old+new") in expected
    parsed = _parse(out_html.read_text())
    assert {tuple(r[:2]) + (r[3],) for r in parsed.rows["dependency-edges"]} == expected

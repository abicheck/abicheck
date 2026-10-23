"""Operand routing of compile-database entries (plan l4-l2 Phase 3 item 4).

:class:`~abicheck.buildsource.adapters.compile_db.CompileDbAdapter` routes each
entry by its parsed operands:

* **legacy** — one source (or an MSVC entry): one compile unit, exactly as before.
  A single-source link keeps its unit; its link step is not recorded yet.
* **per_operand** — several sources: one compile unit per source operand, each
  with the language the driver applies at its own ``-x`` position.
* **link_only** — a link with no source operand compiles nothing, so it yields
  no compile unit and one link unit. The shared corpus has no such entry, so it
  is pinned by an inline entry below.

``multi_source_unit_routing_golden.json`` records every entry's output before
and after the change. The ``before`` section is the pre-change adapter output
recorded from ``origin/main``; the ``after`` section is what this module asserts.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from test_compile_invocation_differential import _render_unit

from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.source_extractors.compile_action import parse_gnu_operands

_DATA = Path(__file__).parent / "data" / "compile_invocation"
_CORPUS = _DATA / "multi_source_compile_commands.json"
_GOLDEN = _DATA / "multi_source_unit_routing_golden.json"

#: Hand-written oracle, from the gcc driver's documented behaviour rather than
#: from the adapter: entry -> (routing, [(unit source, unit language), ...]).
ROUTING_ORACLE: dict[str, tuple[str, list[tuple[str, str]]]] = {
    # gcc -c a.c b.c: each source is its own compile.
    "a.c": ("per_operand", [("/work/a.c", "C"), ("/work/b.c", "C")]),
    # g++ -x c++ a.h -x none b.c -o prog: -x applies per position, so b.c is C.
    "a.h": ("per_operand", [("/work/a.h", "CXX"), ("/work/b.c", "C")]),
    "c.c": ("legacy", [("/work/c.c", "C")]),
    "d.c": ("legacy", [("/work/d.c", "C")]),
    "e.cpp": ("legacy", [("/work/e.cpp", "C")]),
    "f.c": ("legacy", [("/work/f.c", "C")]),
    "g.c": ("legacy", [("/work/g.c", "C")]),
    # g++ -c h1.cpp h2.cc: both are C++ by the driver.
    "h1.cpp": ("per_operand", [("/work/h1.cpp", "CXX"), ("/work/h2.cc", "CXX")]),
    "k.S": ("legacy", [("/work/k.S", "")]),
    "m.mm": ("legacy", [("/work/m.mm", "OBJCXX")]),
    "main.c": ("legacy", [("/work/main.c", "C")]),
    "n.cpp": ("legacy", [("/work/n.cpp", "CXX")]),
    "o.c": ("legacy", [("/work/o.c", "C")]),
    # g++ -c p.cpp p2.cpp
    "p.cpp": ("per_operand", [("/work/p.cpp", "CXX"), ("/work/p2.cpp", "CXX")]),
    "script_src": ("legacy", [("/work/script_src", "C")]),
    "stdio_like.h": ("legacy", [("/work/stdio_like.h", "C")]),
}

_CORPUS_ENTRIES = json.loads(_CORPUS.read_text(encoding="utf-8"))
_GOLDEN_DATA: dict[str, Any] = json.loads(_GOLDEN.read_text(encoding="utf-8"))


def _collect_entry(entry: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """Run the adapter on one entry alone, so each output is attributable to it."""
    db = tmp_path / "compile_commands.json"
    db.write_text(json.dumps([entry]), encoding="utf-8")
    ev = CompileDbAdapter(db).collect()
    return {
        "compile_units": [
            {
                "source": cu.source,
                "language": cu.language,
                "id": cu.id,
                "rendering": _render_unit(cu),
            }
            for cu in ev.compile_units
        ],
        "link_units": [asdict(lu) for lu in ev.link_units],
    }


def _entry(name: str) -> dict[str, Any]:
    return next(e for e in _CORPUS_ENTRIES if e["file"] == name)


@pytest.mark.parametrize("name", sorted(ROUTING_ORACLE))
def test_routing_and_units_match_oracle(name: str, tmp_path: Path) -> None:
    routing, expected = ROUTING_ORACLE[name]
    out = _collect_entry(_entry(name), tmp_path)
    assert [(u["source"], u["language"]) for u in out["compile_units"]] == expected
    golden = _GOLDEN_DATA[name]
    assert golden["routing"] == routing


def test_every_corpus_entry_is_in_the_oracle() -> None:
    assert {e["file"] for e in _CORPUS_ENTRIES} == set(ROUTING_ORACLE)


@pytest.mark.parametrize("name", sorted(ROUTING_ORACLE))
def test_output_matches_recorded_golden(name: str, tmp_path: Path) -> None:
    golden = _GOLDEN_DATA[name]
    entry = _entry(name)
    ops = parse_gnu_operands(entry["arguments"][1:])
    assert [o.source for o in ops.operands] == [o["source"] for o in golden["operands"]]
    assert _collect_entry(entry, tmp_path) == golden["after"]


@pytest.mark.parametrize(
    "name", sorted(n for n, rec in _GOLDEN_DATA.items() if rec["routing"] == "legacy")
)
def test_single_source_entries_are_byte_identical(name: str, tmp_path: Path) -> None:
    golden = _GOLDEN_DATA[name]
    assert golden["after"]["compile_units"] == golden["before"]["compile_units"]
    assert golden["after"]["link_units"] == []
    assert (
        _collect_entry(_entry(name), tmp_path)["compile_units"]
        == (golden["before"]["compile_units"])
    )


def test_exactly_the_multi_source_entries_change() -> None:
    changed = {
        name
        for name, rec in _GOLDEN_DATA.items()
        if rec["after"]["compile_units"] != rec["before"]["compile_units"]
        or rec["after"]["link_units"]
    }
    assert changed == {
        name for name, rec in _GOLDEN_DATA.items() if rec["routing"] != "legacy"
    }
    assert changed == {"a.c", "a.h", "h1.cpp", "p.cpp"}


def test_link_only_entry_records_link_unit_and_no_compile_unit(tmp_path: Path) -> None:
    # gcc util.o libfoo.a -o app: nothing is compiled, the link is the product.
    entry = {
        "directory": "/work",
        "file": "util.o",
        "arguments": ["gcc", "util.o", "libfoo.a", "-o", "app"],
    }
    out = _collect_entry(entry, tmp_path)
    assert out["compile_units"] == []
    assert [
        (lu["id"], lu["output"], lu["kind"], lu["inputs"]) for lu in out["link_units"]
    ] == [
        (
            "link:///work/app",
            "/work/app",
            "executable",
            ["/work/util.o", "/work/libfoo.a"],
        )
    ]


def test_link_only_entry_without_output_uses_driver_default(tmp_path: Path) -> None:
    entry = {"directory": "/work", "file": "util.o", "arguments": ["gcc", "util.o"]}
    out = _collect_entry(entry, tmp_path)
    assert [lu["output"] for lu in out["link_units"]] == ["/work/a.out"]
    assert [lu["inputs"] for lu in out["link_units"]] == [["/work/util.o"]]


@pytest.mark.parametrize("flag", ["-shared", "-dynamiclib"])
def test_shared_link_unit_is_a_shared_library(flag: str, tmp_path: Path) -> None:
    entry = {
        "directory": "/work",
        "file": "c.o",
        "arguments": ["gcc", "c.o", flag, "-o", "libc.so"],
    }
    out = _collect_entry(entry, tmp_path)
    assert [lu["kind"] for lu in out["link_units"]] == ["shared_library"]


def test_absolute_operand_keeps_its_path_and_relative_one_is_resolved(
    tmp_path: Path,
) -> None:
    # The first operand is spelled absolute, the second relative to the entry directory.
    entry = {
        "directory": "/work",
        "file": "/abs/x.c",
        "arguments": ["gcc", "-c", "/abs/x.c", "y.c"],
    }
    out = _collect_entry(entry, tmp_path)
    assert [u["source"] for u in out["compile_units"]] == ["/abs/x.c", "/work/y.c"]


def test_cl_driver_entry_keeps_one_unit_per_entry(tmp_path: Path) -> None:
    # CL-mode operands are out of scope: the entry stays one unit, as before.
    entry = {
        "directory": "/work",
        "file": "a.c",
        "arguments": ["cl.exe", "/c", "a.c", "b.c"],
    }
    out = _collect_entry(entry, tmp_path)
    assert [u["source"] for u in out["compile_units"]] == ["/work/a.c"]
    assert out["link_units"] == []

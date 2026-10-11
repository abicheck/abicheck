"""A directory ``compare`` parses each member with the headers it owns, not
the union of every member's ``-H`` headers, and a type a member provably
does not reach stays off that member's verdict (known gap "A directory
``compare`` still feeds the union ``-H`` set to every member", steps 2 and
the verdict half of 3; now archived in
``docs/contribute/archive/known-gaps-closed.md``).

Built from real gcc binaries and run through the public CLI. The oracle is
the binaries' own export tables and the fixture's declared header layout,
never the assignment helper under test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ELF fixture"),
]

_WIDGET_OLD = "struct Widget { int x; };\n"
_WIDGET_NEW = "struct Widget { long x; int y; };\n"


@pytest.fixture
def toolchain() -> None:
    for tool in ("gcc", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _build(
    root: Path, headers: dict[str, dict[str, str]], sources: dict[str, str]
) -> None:
    """*headers*: side -> {file: text}; *sources*: member -> C body."""
    for side, files in headers.items():
        inc = root / side / "include"
        lib = root / side / "lib"
        inc.mkdir(parents=True)
        lib.mkdir(parents=True)
        for name, text in files.items():
            (inc / name).write_text(text)
        for member, body in sources.items():
            src = root / side / f"{member}.c"
            src.write_text(body)
            subprocess.run(
                [
                    "gcc",
                    "-shared",
                    "-fPIC",
                    "-g",
                    f"-I{inc}",
                    str(src),
                    "-o",
                    str(lib / f"{member}.so"),
                ],
                check=True,
            )


def _compare(root: Path) -> tuple[int, dict, str]:
    out = root / "report.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(root / "old/lib"),
            str(root / "new/lib"),
            "--header",
            f"old={root / 'old/include'}",
            "--header",
            f"new={root / 'new/include'}",
            "-o",
            f"json={out}",
        ],
        env={**os.environ, "ABICHECK_AST_FRONTEND": "castxml"},
        capture_output=True,
        text=True,
    )
    assert out.exists(), proc.stderr
    return proc.returncode, json.loads(out.read_text()), proc.stderr


def _members(report: dict) -> dict[str, dict]:
    return {lib["library"]: lib for lib in report["libraries"]}


def _symbols(member: dict) -> set[str]:
    return {f.get("symbol", "") for f in member.get("findings", [])}


def _separate_headers(root: Path, *, a_breaks: bool) -> None:
    a_h = "#pragma once\n{widget}int a_fn(struct Widget *w);\n"
    _build(
        root,
        {
            side: {
                "a.h": a_h.format(
                    widget=_WIDGET_NEW if side == "new" and a_breaks else _WIDGET_OLD
                ),
                "b.h": "#pragma once\nint b_fn(int v);\n",
            }
            for side in ("old", "new")
        },
        {
            "liba": '#include "a.h"\nint a_fn(struct Widget *w){return (int)w->x;}\n',
            "libb": '#include "b.h"\nint b_fn(int v){return v+1;}\n',
        },
    )


def test_a_type_change_in_one_members_header_stays_with_that_member(
    tmp_path: Path, toolchain: None
) -> None:
    _separate_headers(tmp_path, a_breaks=True)
    code, report, stderr = _compare(tmp_path)
    members = _members(report)
    # (2) the real break in liba's own header still breaks liba ...
    assert members["liba.so"]["verdict"] == "BREAKING", stderr
    assert "Widget" in _symbols(members["liba.so"])
    # ... and the release exits as a binary break (exit 4).
    assert code == 4, stderr
    # (1)/(3) libb parsed only the header it owns, so the Widget change is
    # not a finding of libb's at all -- neither counted nor filtered.
    libb = members["libb.so"]
    assert libb["verdict"] in {"NO_CHANGE", "COMPATIBLE"}, libb
    assert not any("Widget" in s for s in _symbols(libb))
    assert libb.get("filtered_internal_count", 0) == 0


def test_negative_control_no_change_no_finding(tmp_path: Path, toolchain: None) -> None:
    """Same layout, nothing changed: neither member reports anything, so the
    first test's findings come from the change, not from the narrowing."""
    _separate_headers(tmp_path, a_breaks=False)
    code, report, stderr = _compare(tmp_path)
    assert code == 0, stderr
    for member in _members(report).values():
        assert member["verdict"] == "NO_CHANGE", member


def test_shared_header_unreached_type_leaves_the_member_verdict(
    tmp_path: Path, toolchain: None
) -> None:
    """One header declares both members' functions, so both parse ``Widget``.
    Only liba's exports reach it: libb records the finding as out of its
    contract (visible, with its reason) instead of breaking on it."""
    shared = "#pragma once\n{widget}int a_fn(struct Widget *w);\nint b_fn(int v);\n"
    _build(
        tmp_path,
        {
            side: {
                "api.h": shared.format(
                    widget=_WIDGET_NEW if side == "new" else _WIDGET_OLD
                )
            }
            for side in ("old", "new")
        },
        {
            "liba": '#include "api.h"\nint a_fn(struct Widget *w){return (int)w->x;}\n',
            "libb": '#include "api.h"\nint b_fn(int v){return v+1;}\n',
        },
    )
    code, report, stderr = _compare(tmp_path)
    members = _members(report)
    assert members["liba.so"]["verdict"] == "BREAKING", stderr
    assert code == 4, stderr
    libb = members["libb.so"]
    assert libb["verdict"] in {"NO_CHANGE", "COMPATIBLE"}, libb
    assert libb["breaking"] == 0
    # Recorded before disposed: each finding stays in libb's scope ledger
    # with a reason naming the member that does reach the type, and the
    # audit labels it out_of_contract under a named rule.
    reasons = {
        e["reason"]
        for e in libb["surface_scope"]["out_of_surface_changes"]
        if e["symbol"] == "Widget"
    }
    assert reasons == {"type not reached by this library's exports; reached by liba.so"}
    assert libb["disposition_audit"]["counts"]["out_of_contract"] >= 1
    assert libb["disposition_audit"]["counts"]["gating"] == 0


def test_unreached_by_every_member_still_counts(
    tmp_path: Path, toolchain: None
) -> None:
    """Negative control for the verdict half: a type no member's exports
    reach is not relieved anywhere -- exclusion needs a sibling that gates it."""
    shared = "#pragma once\n{widget}int a_fn(int v);\nint b_fn(int v);\n"
    _build(
        tmp_path,
        {
            side: {
                "api.h": shared.format(
                    widget=_WIDGET_NEW if side == "new" else _WIDGET_OLD
                )
            }
            for side in ("old", "new")
        },
        {
            "liba": '#include "api.h"\nint a_fn(int v){return v;}\n',
            "libb": '#include "api.h"\nint b_fn(int v){return v+1;}\n',
        },
    )
    _code, report, _stderr = _compare(tmp_path)
    for member in _members(report).values():
        ledger = (member.get("surface_scope") or {}).get("out_of_surface_changes", [])
        assert not any("reached by" in str(e.get("reason")) for e in ledger), member

"""Real-binary check (ADR-050 amendment 2026-11-09): a library rebuilt with the
other libstdc++ dual ABI (``_GLIBCXX_USE_CXX11_ABI=0`` vs the default ``=1``)
is reported as an ABI break, not refused as a profile mismatch.

Each surface the selection changes is its own library -- a function taking
``std::string``, one taking ``std::list``, and a record holding both that the
API passes only by pointer (so no mangled name changes) -- built under both
selections and compared in both directions, through the CLI, with each side's
own ``-D`` given to the header parse as a real build would. A library rebuilt
under the same selection is the control.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="ELF + libstdc++ fixture (Linux only)",
    ),
]

_HEADERS = {
    "string": "#include <string>\nstd::string greet(const std::string& who);\nint add(int a, int b);\n",
    "list": "#include <list>\nint count(const std::list<int>& xs);\nint add(int a, int b);\n",
    "record": (
        "#include <list>\n#include <string>\n"
        "struct Rec { int id; std::string name; std::list<int> items; };\n"
        "int rec_id(const Rec* r);\nint add(int a, int b);\n"
    ),
}
_SOURCES = {
    "string": 'std::string greet(const std::string& who) { return "hi " + who; }\n',
    "list": "int count(const std::list<int>& xs) { return (int)xs.size(); }\n",
    "record": "int rec_id(const Rec* r) { return r->id; }\n",
}

#: ``None`` is the compiler default (``=1`` on every GCC this lane runs).
_DEFAULT, _OLD = None, "0"


def _flag(abi: str | None) -> list[str]:
    return [] if abi is None else [f"-D_GLIBCXX_USE_CXX11_ABI={abi}"]


def _build(tmp: Path, role: str, surface: str, abi: str | None) -> tuple[Path, Path]:
    d = tmp / role
    d.mkdir()
    header = d / "api.h"
    header.write_text("#pragma once\n" + _HEADERS[surface], encoding="utf-8")
    src = d / "api.cpp"
    src.write_text(
        '#include "api.h"\n'
        + _SOURCES[surface]
        + "int add(int a, int b) { return a + b; }\n",
        encoding="utf-8",
    )
    so = d / "libapi.so"
    res = subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", *_flag(abi), str(src), "-o", str(so)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        pytest.fail(f"g++ {_flag(abi)} failed:\n{res.stderr}")
    return so, header


def _dump(so: Path, header: Path | None, abi: str | None, out: Path) -> Path:
    args = ["dump", str(so), "-o", str(out)]
    if header is not None:
        args += ["-H", str(header)]
        for flag in _flag(abi):
            args += ["-D", flag.removeprefix("-D")]
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output[-3000:]
    return out


def _compare(old: Path, new: Path, tmp: Path) -> tuple[int, dict]:
    report = tmp / "report.json"
    sarif = tmp / "report.sarif"
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "-o", f"json={report}", "-o", f"sarif={sarif}"],
        catch_exceptions=False,
    )
    doc = json.loads(report.read_text(encoding="utf-8"))
    doc["_sarif"] = json.loads(sarif.read_text(encoding="utf-8"))
    return result.exit_code, doc


@pytest.fixture(autouse=True)
def _need_gxx() -> None:
    if shutil.which("g++") is None:
        pytest.skip("g++ not found in PATH")


def _pair(
    tmp: Path, surface: str, old_abi: str | None, new_abi: str | None, *, headers: bool
):
    old_so, old_h = _build(tmp, "old", surface, old_abi)
    new_so, new_h = _build(tmp, "new", surface, new_abi)
    old = _dump(old_so, old_h if headers else None, old_abi, tmp / "old" / "snap.json")
    new = _dump(new_so, new_h if headers else None, new_abi, tmp / "new" / "snap.json")
    return _compare(old, new, tmp)


def _kinds(doc: dict) -> list[str]:
    return [c["kind"] for c in doc["changes"]]


@pytest.mark.parametrize("surface", sorted(_HEADERS))
@pytest.mark.parametrize(
    ("old_abi", "new_abi"), [(_DEFAULT, _OLD), (_OLD, _DEFAULT)], ids=["1to0", "0to1"]
)
def test_dual_abi_flip_with_headers_is_a_break(tmp_path, surface, old_abi, new_abi):
    exit_code, doc = _pair(tmp_path, surface, old_abi, new_abi, headers=True)

    # Compared (no exit 16 refusal), and gated as an ABI break.
    assert exit_code == 4, doc.get("reason")
    assert doc["verdict"] == "BREAKING"
    (root,) = [c for c in doc["changes"] if c["kind"] == "glibcxx_dual_abi_changed"]
    assert "_GLIBCXX_USE_CXX11_ABI=0" in f"{root['old_value']} {root['new_value']}"
    assert "libstdc++ dual ABI changed" in root["description"]
    # The per-entity consequences stay reported beside the root (ADR-067).
    kinds = set(_kinds(doc))
    if surface == "record":
        assert "type_size_changed" in kinds
        assert "Rec" in root["description"]
    else:
        assert kinds & {"func_removed", "func_params_changed"}
        assert "re-mangled" in root["description"]
    # SARIF names the dual ABI too.
    sarif_text = json.dumps(doc["_sarif"])
    assert "glibcxx_dual_abi_changed" in sarif_text
    assert "dual ABI" in sarif_text


@pytest.mark.parametrize("surface", ["string", "list"])
@pytest.mark.parametrize(
    ("old_abi", "new_abi"), [(_DEFAULT, _OLD), (_OLD, _DEFAULT)], ids=["1to0", "0to1"]
)
def test_dual_abi_flip_binary_only_pairs_the_re_mangled_exports(
    tmp_path, surface, old_abi, new_abi
):
    exit_code, doc = _pair(tmp_path, surface, old_abi, new_abi, headers=False)
    assert exit_code == 4
    (root,) = [c for c in doc["changes"] if c["kind"] == "glibcxx_dual_abi_changed"]
    assert "1 export(s) re-mangled" in root["description"]


@pytest.mark.parametrize(
    ("old_abi", "new_abi"), [(_DEFAULT, _OLD), (_OLD, _DEFAULT)], ids=["1to0", "0to1"]
)
def test_record_only_flip_binary_only_still_breaks_on_layout(
    tmp_path, old_abi, new_abi
):
    # Partial evidence: no export is re-mangled and no side records its
    # selection, so the root cause is not claimed -- the DWARF layout
    # findings still gate the verdict.
    exit_code, doc = _pair(tmp_path, "record", old_abi, new_abi, headers=False)
    assert exit_code == 4
    assert "type_size_changed" in _kinds(doc)


@pytest.mark.parametrize("surface", sorted(_HEADERS))
@pytest.mark.parametrize("abi", [_DEFAULT, _OLD], ids=["default", "abi0"])
def test_same_selection_is_no_dual_abi_change(tmp_path, surface, abi):
    exit_code, doc = _pair(tmp_path, surface, abi, abi, headers=True)
    assert exit_code == 0
    kinds = _kinds(doc)
    assert "glibcxx_dual_abi_changed" not in kinds
    assert "toolchain_flag_drift" not in kinds

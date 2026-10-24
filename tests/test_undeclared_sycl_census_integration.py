"""A ``sycl::queue&`` overload under ``#ifdef SYCL_LANGUAGE_VERSION`` is
counted in the coverage block, never silently dropped (field report H5).

Oracle: the fixture's own list of ``#ifdef``-hidden exported functions,
cross-checked against ``nm -D``. The count must also create no finding.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model.undeclared_export_census import UNDECLARED_EXPORT_WARNING_MARKER

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("g++") and shutil.which("nm")),
        reason="needs castxml, g++ and nm",
    ),
]

#: Exported functions the header declares only under SYCL_LANGUAGE_VERSION.
_HIDDEN = ("foo(sycl::queue&)", "foo(sycl::queue&, int)", "bar(sycl::queue&)")
_VISIBLE = ("foo(int)",)

_HEADER = """\
#pragma once
int foo(int);
#ifdef SYCL_LANGUAGE_VERSION
namespace sycl { class queue; }
int foo(sycl::queue&);
int foo(sycl::queue&, int);
int bar(sycl::queue&);
#endif
"""

_SOURCE = """\
namespace sycl { class queue {}; }
int foo(int x) { return x; }
int foo(sycl::queue&) { return 1; }
int foo(sycl::queue&, int y) { return y; }
int bar(sycl::queue&) { return 2; }
"""


def _build(root: Path) -> Path:
    root.mkdir()
    (root / "api.h").write_text(_HEADER)
    (root / "api.cpp").write_text(_SOURCE)
    lib = root / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(lib), str(root / "api.cpp")],
        check=True,
    )
    return lib


def _dynamic_function_exports(lib: Path) -> set[str]:
    out = subprocess.run(
        ["nm", "-D", "-C", "--defined-only", str(lib)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {line.split(" ", 2)[2] for line in out.splitlines() if " T " in line}


def test_hidden_sycl_overloads_are_counted_not_findings(tmp_path: Path) -> None:
    old = _build(tmp_path / "old")
    new = _build(tmp_path / "new")
    exports = _dynamic_function_exports(new)
    # Oracle cross-check: every fixture function really is exported, and the
    # sycl-referencing exports are exactly the #ifdef-hidden list.
    assert set(_HIDDEN) | set(_VISIBLE) <= exports
    assert {e for e in exports if "sycl::" in e} == set(_HIDDEN)
    report = tmp_path / "r.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "-H",
            str(tmp_path / "new" / "api.h"),
            "-o",
            f"json={report}",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(report.read_text())
    warnings = data["coverage_warnings"]
    lines = [w for w in warnings if UNDECLARED_EXPORT_WARNING_MARKER in w]
    assert [line.split(" ", 1)[0] for line in lines] == ["old", "new"]
    for line in lines:
        assert f"({len(_HIDDEN)} reference sycl::" in line
    # The census creates no finding: the only findings are the pre-existing
    # `exported_not_public` hygiene rows for the same hidden exports.
    assert {c["kind"] for c in data["changes"]} <= {"exported_not_public"}
    assert data["verdict"] in ("NO_CHANGE", "COMPATIBLE")
    assert any(
        "Detector 'sycl' disabled: missing SYCL runtime (PI plugin) metadata" in w
        for w in warnings
    )

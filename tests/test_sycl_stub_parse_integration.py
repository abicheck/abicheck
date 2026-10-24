"""The documented SYCL parse path works (field report H5, step 2).

``docs/use/dump-compare-flags.md`` tells a user to parse the
``#ifdef SYCL_LANGUAGE_VERSION`` branch of their headers with
``-D SYCL_LANGUAGE_VERSION=202001`` plus ``-I`` of a forward-declaration stub.
This runs that exact ``abicheck compare`` command on a library built from the
``tests/field_shapes/ifdef_sycl_overload`` sources (the stub is a test
fixture there, never bundled in the package):

* with the define, removing the ``bar(sycl::queue&, int)`` overload reports
  ``func_removed`` on it, and no export is left undeclared;
* without it, the census line counts the hidden ``sycl::`` exports per side
  (old: ``foo(sycl::queue&)`` and ``bar(...)``; new: ``foo`` only) and the
  removal is invisible to the header-level diff.

Oracle: the fixture's ``#ifdef`` lists, cross-checked against ``nm -D``.
"""

from __future__ import annotations

import json
import re
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

SHAPE = Path(__file__).parent / "field_shapes" / "ifdef_sycl_overload"
STUBS = SHAPE / "stubs"
DEFINE = "SYCL_LANGUAGE_VERSION=202001"

#: ``#ifdef SYCL_LANGUAGE_VERSION`` exports per side (the oracle).
HIDDEN = {
    "v1": {"foo(sycl::_V1::queue&)", "bar(sycl::_V1::queue&, int)"},
    "v2": {"foo(sycl::_V1::queue&)"},
}


def _build(side: str, out: Path) -> Path:
    out.mkdir()
    lib = out / "libapi.so"
    cmd = [
        "g++", "-shared", "-fPIC", f"-D{DEFINE}", f"-I{SHAPE / side}",
        f"-I{STUBS}", str(SHAPE / side / "api.cpp"), "-o", str(lib),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return lib


def _sycl_exports(lib: Path) -> set[str]:
    out = subprocess.run(
        ["nm", "-D", "-C", "--defined-only", str(lib)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    funcs = {line.split(" ", 2)[2] for line in out.splitlines() if " T " in line}
    return {f for f in funcs if "sycl::" in f}


def _compare(tmp: Path, *extra: str) -> dict:
    old, new = _build("v1", tmp / "old"), _build("v2", tmp / "new")
    assert _sycl_exports(old) == HIDDEN["v1"]
    assert _sycl_exports(new) == HIDDEN["v2"]
    report = tmp / "r.json"
    # The documented command shape: -H old=/new= headers, -I stubs, -D macro.
    args = [
        "compare", str(old), str(new),
        "-H", f"old={SHAPE / 'v1' / 'api.h'}", "-H", f"new={SHAPE / 'v2' / 'api.h'}",
        *extra, "-o", f"json={report}",
    ]  # fmt: skip
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code in (0, 4), result.output
    return json.loads(report.read_text())


def _census(report: dict) -> dict[str, int]:
    """``{side: N}`` from the ``(N reference sycl::`` census lines."""
    out = {}
    for w in report["coverage_warnings"]:
        if UNDECLARED_EXPORT_WARNING_MARKER in w:
            m = re.search(r"\((\d+) reference sycl::", w)
            assert m, w
            out[w.split(" ", 1)[0]] = int(m.group(1))
    return out


def test_defined_macro_and_stub_parse_the_sycl_overloads(tmp_path: Path) -> None:
    data = _compare(tmp_path, "-I", str(STUBS), "-D", DEFINE)
    removed = {c["symbol"] for c in data["changes"] if c["kind"] == "func_removed"}
    assert removed == {"_Z3barRN4sycl3_V15queueEi"}, data["changes"]
    assert data["verdict"] == "BREAKING"
    assert _census(data) == {}


def test_documented_spelling_without_space_also_parses(tmp_path: Path) -> None:
    """``docs/use/dump-compare-flags.md`` spells it ``-DSYCL_LANGUAGE_VERSION=...``."""
    data = _compare(tmp_path, "-I", str(STUBS), f"-D{DEFINE}")
    assert {c["kind"] for c in data["changes"]} >= {"func_removed"}
    assert _census(data) == {}


def test_without_the_macro_the_census_counts_the_hidden_overloads(
    tmp_path: Path,
) -> None:
    data = _compare(tmp_path)
    assert _census(data) == {"old": len(HIDDEN["v1"]), "new": len(HIDDEN["v2"])}
    assert "func_removed" not in {c["kind"] for c in data["changes"]}

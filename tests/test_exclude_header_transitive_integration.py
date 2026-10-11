"""``--exclude-header`` scopes out a header reached only through ``#include``.

``public.h`` includes ``internal.h``; only ``public.h`` is passed as ``-H``,
so the parse-root filter has nothing to remove and the exclusion must be
honoured post-parse, by the declaring header of each declaration
(``extract.header_exclusions.scoping_header_predicate`` feeding
``workflows.dump.dependency_scope``). Driven through the public CLI over
real g++-built libraries, once per header backend.

Three sibling cases pin the contract rather than one input:

* a change confined to ``internal.h``-only declarations (a record layout and
  an enum reorder) is reported without the flag and not with it;
* the run still records the exclusion as a coverage warning (ADR-067:
  the narrowing is visible, not silent);
* a real break in ``public.h`` is still reported under the same flag.
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

pytestmark = pytest.mark.integration

_INTERNAL = """\
#pragma once
namespace impl {
struct Cache { int hits; };
enum Mode { Fast, Slow };
int cache_hits(const Cache& c);
}
"""
_INTERNAL_CHANGED = _INTERNAL.replace(
    "int hits; }", "int hits; long misses; }"
).replace("Fast, Slow", "Slow, Fast")
_PUBLIC = """\
#pragma once
#include "internal.h"
namespace lib { int api(int x); }
"""
_PUBLIC_BROKEN = _PUBLIC.replace("int api(int x);", "int api(long x);")
# The definition follows whatever `lib::api` the header declares.
_SRC = """\
#include "public.h"
namespace impl { int cache_hits(const Cache& c) { return c.hits + (int)sizeof(Cache) + Fast; } }
namespace lib { int api(API_ARG x) { impl::Cache c{int(x)}; return impl::cache_hits(c); } }
"""
_INTERNAL_NAMES = ("Cache", "Mode", "Fast", "Slow")


def _frontends() -> list[str]:
    return [fe for fe in ("castxml", "clang") if shutil.which(fe)]


def _build(root: Path, internal: str, public: str, api_arg: str) -> None:
    cxx = shutil.which("g++")
    if cxx is None:
        pytest.skip("g++ not found")
    (root / "include").mkdir(parents=True)
    (root / "include/internal.h").write_text(internal)
    (root / "include/public.h").write_text(public)
    (root / "lib.cpp").write_text(_SRC)
    subprocess.run(
        [
            cxx,
            "-shared",
            "-fPIC",
            "-g",
            f"-DAPI_ARG={api_arg}",
            "-Iinclude",
            "lib.cpp",
            "-o",
            "libx.so",
        ],
        cwd=root,
        check=True,
    )


def _compare(extra: list[str]) -> tuple[int, dict]:
    args = ["compare", "old/libx.so", "new/libx.so"]
    for side in ("old", "new"):
        args += [
            "--header",
            f"{side}={side}/include/public.h",
            "-I",
            f"{side}={side}/include",
        ]
    args += ["-o", "json=report.json", *extra]
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(Path("report.json").read_text())


def _internal_findings(report: dict) -> list[tuple[str, str]]:
    return [
        (c["kind"], c.get("symbol") or "")
        for c in report["changes"]
        if any(n in (c.get("symbol") or "") for n in _INTERNAL_NAMES)
        # The standing note that an exported symbol is declared only in an
        # excluded header is the exclusion's recorded disposition, not a
        # change finding; it is asserted on separately below.
        and c["kind"] != "exported_not_public"
    ]


@pytest.mark.parametrize("frontend", ["castxml", "clang"])
@pytest.mark.parametrize("public_break", [False, True])
def test_exclude_header_scopes_out_transitively_included_header(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontend: str,
    public_break: bool,
) -> None:
    if frontend not in _frontends():
        pytest.skip(f"{frontend} not found in PATH")
    if frontend == "clang" and sys.platform == "win32":
        # A MinGW g++ DLL exports Itanium-mangled names while clang parses
        # the header for the MSVC target, so no header declaration matches an
        # export (as in test_checkout_root_independence_integration).
        pytest.skip(
            "header/export mangling differs between MinGW g++ and MSVC-target clang"
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
    _build(tmp_path / "old", _INTERNAL, _PUBLIC, "int")
    _build(
        tmp_path / "new",
        _INTERNAL_CHANGED,
        _PUBLIC_BROKEN if public_break else _PUBLIC,
        "long" if public_break else "int",
    )

    plain_exit, plain = _compare([])
    scoped_exit, scoped = _compare(["--exclude-header", "internal.h"])

    # Not vacuous: without the flag the internal-only change is observed.
    assert _internal_findings(plain), plain["changes"]
    assert plain_exit == 4
    # With it, nothing the excluded header alone declares produces a finding.
    assert not _internal_findings(scoped), scoped["changes"]
    # The narrowing is recorded, not silent.
    assert any(
        "internal.h" in w and "--exclude-header" in w
        for w in scoped.get("coverage_warnings", [])
    ), scoped.get("coverage_warnings")
    # ...and the exported `impl::cache_hits`, declared only in the excluded
    # header, is kept visible with the excluded header named, not dropped.
    notes = [c for c in scoped["changes"] if c["kind"] == "exported_not_public"]
    assert any(
        "cache_hits" in c["symbol"] and "internal.h" in c["description"] for c in notes
    ), scoped["changes"]

    api_findings = [c for c in scoped["changes"] if "api" in (c.get("symbol") or "")]
    if public_break:
        # Negative control: a real break in the public header still gates.
        assert scoped_exit == 4, scoped["changes"]
        assert api_findings, scoped["changes"]
    else:
        assert scoped_exit == 0, scoped["changes"]
        assert not api_findings, scoped["changes"]

# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Metamorphic relations of ``compare`` over real headers and libraries.

Two source-level edits must not change what a comparison finds:

* **declaration reorder** -- the same declarations written in a different order
  in the header, and the same definitions in a different order in the ``.c``
  file, yield the same findings as the original order;
* **unrelated header** -- adding a header that declares, and a library that
  exports, a function neither side's own API touches adds no finding.

Each relation is checked on the live operands (the libraries and headers as
compared in one run) and on the stored operands (each side dumped to a snapshot
first, then compared), since a relation that held only for one path would hide
a stored/live divergence. The oracle is the one change the two versions were
written to differ by, plus the soname advice a breaking change correctly draws.

Integration-marked: the builds need gcc and castxml, and the libraries are ELF.
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
        not (shutil.which("castxml") and shutil.which("gcc")),
        reason="needs castxml and gcc",
    ),
    pytest.mark.skipif(sys.platform != "linux", reason="ELF-only builds"),
]

#: The findings of the one breaking edit both versions are written to differ by:
#: ``delta`` returns ``int`` in v2 instead of ``long``. A breaking change to a
#: library that keeps its soname draws the soname advice as well.
EXPECTED_FINDINGS = [
    ("func_return_changed", "delta"),
    ("soname_bump_recommended", "DT_SONAME"),
]

_HEADER_V1 = """\
#ifndef META_API_H
#define META_API_H
struct point { int x; int y; };
int alpha(int x);
long delta(long x);
int omega(struct point p);
#endif
"""

_HEADER_V2 = """\
#ifndef META_API_H
#define META_API_H
struct point { int x; int y; };
int alpha(int x);
int delta(long x);
int omega(struct point p);
#endif
"""

#: The same declarations as ``_HEADER_V2``, in reverse order.
_HEADER_V2_REORDERED = """\
#ifndef META_API_H
#define META_API_H
struct point { int x; int y; };
int omega(struct point p);
int delta(long x);
int alpha(int x);
#endif
"""

_SOURCE_V1 = """\
#include "api.h"
int alpha(int x) { return x + 1; }
long delta(long x) { return x * 2; }
int omega(struct point p) { return p.x + p.y; }
"""

_SOURCE_V2 = """\
#include "api.h"
int alpha(int x) { return x + 1; }
int delta(long x) { return (int)(x * 2); }
int omega(struct point p) { return p.x + p.y; }
"""

_SOURCE_V2_REORDERED = """\
#include "api.h"
int omega(struct point p) { return p.x + p.y; }
int delta(long x) { return (int)(x * 2); }
int alpha(int x) { return x + 1; }
"""

#: A header and definition neither version's own API uses; both sides export it.
_UNRELATED_HEADER = "int unrelated_helper(int x);\n"
_UNRELATED_SOURCE = "int unrelated_helper(int x) { return x - 1; }\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build(src_dir: Path, sources: list[str], lib: Path) -> Path:
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-fvisibility=default",
            "-Wl,-soname,libmeta.so.1",
            "-I",
            str(src_dir),
            "-o",
            str(lib),
            *(str(src_dir / name) for name in sources),
        ],
        check=True,
    )
    return lib


def _findings(report: dict) -> list[tuple[str, str]]:
    """Each finding as (kind, symbol), sorted."""
    return sorted((c["kind"], c["symbol"]) for c in report["changes"])


def _run(args: list[str], report: Path) -> dict:
    result = CliRunner().invoke(
        main, [*args, "-o", f"json={report}"], catch_exceptions=False
    )
    assert result.exit_code in (0, 4), result.output
    return json.loads(report.read_text(encoding="utf-8"))


def _findings_both_paths(
    old: Path,
    new: Path,
    old_headers: list[str],
    new_headers: list[str],
    tmp: Path,
) -> dict[str, list[tuple[str, str]]]:
    """Findings of the live and the stored path for one operand pair.

    Live: both libraries and their own header lists, parsed in this run. Stored:
    each side dumped to a snapshot with the same headers, then the two snapshot
    files compared.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    live_args = ["compare", str(old), str(new)]
    for header in old_headers:
        live_args += ["--header", f"old={header}"]
    for header in new_headers:
        live_args += ["--header", f"new={header}"]
    live = _run(live_args, tmp / "live.json")

    stored_snaps = []
    for label, lib, headers in (("old", old, old_headers), ("new", new, new_headers)):
        out = tmp / f"{label}.abi.json"
        args = ["dump", str(lib)]
        for header in headers:
            args += ["-H", header]
        result = CliRunner().invoke(
            main, [*args, "-o", str(out)], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        stored_snaps.append(out)
    stored = _run(
        ["compare", str(stored_snaps[0]), str(stored_snaps[1])], tmp / "stored.json"
    )
    return {"live": _findings(live), "stored": _findings(stored)}


@pytest.fixture()
def tree(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "meta"
    for name, header, source in (
        ("v1", _HEADER_V1, _SOURCE_V1),
        ("v2", _HEADER_V2, _SOURCE_V2),
        ("v2r", _HEADER_V2_REORDERED, _SOURCE_V2_REORDERED),
    ):
        _write(base / name / "api.h", header)
        _write(base / name / "lib.c", source)
        _write(base / name / "unrelated.h", _UNRELATED_HEADER)
        _write(base / name / "unrelated.c", _UNRELATED_SOURCE)
    return {name: base / name for name in ("v1", "v2", "v2r")} | {"base": base}


def _library(tree: dict[str, Path], name: str, *, unrelated: bool) -> Path:
    sources = ["lib.c", *(["unrelated.c"] if unrelated else [])]
    lib = tree["base"] / f"libmeta_{name}_{int(unrelated)}.so"
    return _build(tree[name], sources, lib)


def test_declaration_reorder_yields_the_same_findings(
    tree: dict[str, Path], tmp_path: Path
) -> None:
    old = _library(tree, "v1", unrelated=False)
    new = _library(tree, "v2", unrelated=False)
    reordered = _library(tree, "v2r", unrelated=False)

    original = _findings_both_paths(
        old,
        new,
        [str(tree["v1"] / "api.h")],
        [str(tree["v2"] / "api.h")],
        tmp_path / "original",
    )
    reorder = _findings_both_paths(
        old,
        reordered,
        [str(tree["v1"] / "api.h")],
        [str(tree["v2r"] / "api.h")],
        tmp_path / "reorder",
    )
    assert original == {"live": EXPECTED_FINDINGS, "stored": EXPECTED_FINDINGS}
    assert reorder == original


def test_unrelated_header_adds_no_finding(
    tree: dict[str, Path], tmp_path: Path
) -> None:
    old = _library(tree, "v1", unrelated=False)
    new = _library(tree, "v2", unrelated=False)
    old_u = _library(tree, "v1", unrelated=True)
    new_u = _library(tree, "v2", unrelated=True)

    base = _findings_both_paths(
        old,
        new,
        [str(tree["v1"] / "api.h")],
        [str(tree["v2"] / "api.h")],
        tmp_path / "base",
    )
    with_unrelated = _findings_both_paths(
        old_u,
        new_u,
        [str(tree["v1"] / "api.h"), str(tree["v1"] / "unrelated.h")],
        [str(tree["v2"] / "api.h"), str(tree["v2"] / "unrelated.h")],
        tmp_path / "unrelated",
    )
    assert base == {"live": EXPECTED_FINDINGS, "stored": EXPECTED_FINDINGS}
    assert with_unrelated == base

# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A header input that yields no declarations is reported, never a silent surface.

Field report D6: an empty ``--header`` file was parsed to a zero-declaration
surface, so every declaration the library had read as a removal (exit 4) with
nothing naming the cause. A header with NUL bytes was not an input error at
all: it was read as an empty development-package archive and its side lost
its header evidence without a word.

The contract, checked through the public ``compare`` command with real compiled
libraries and the rendered JSON report:

* a header set that was parsed and produced zero public declarations is
  *named* in ``coverage_warnings`` for that side -- and the findings it causes
  are kept, because a header that was really emptied is a real removal;
* a header that is not valid text (a NUL byte) is an input error (exit 64),
  on either side and whether it is a file or a directory member;
* the sibling inputs -- empty, whitespace, comment-only, system-include-only,
  NUL-only, NUL-inside-text, an empty directory, a directory of comment-only
  headers -- each get their own explicit outcome.
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
from abicheck.model.empty_header_surface import EMPTY_HEADER_SURFACE_MARKER

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("g++") is None or shutil.which("castxml") is None,
        reason="needs g++ to build the fixture libraries and castxml to parse headers",
    ),
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
]

#: The full public header of the old library. The new library drops
#: ``removed_fn``, so a *complete* new header is a valid baseline for a
#: real-removal expectation.
_FULL_HEADER = (
    "struct pt { int x; int y; };\n"
    "int keep_me(int a);\n"
    "int removed_fn(int a);\n"
    "int use_pt(struct pt p);\n"
)
_OLD_SOURCE = (
    "struct pt { int x; int y; };\n"
    "int keep_me(int a) { return a; }\n"
    "int removed_fn(int a) { return a; }\n"
    "int use_pt(struct pt p) { return p.x; }\n"
)
_NEW_SOURCE = (
    "struct pt { int x; int y; };\n"
    "int keep_me(int a) { return a; }\n"
    "int use_pt(struct pt p) { return p.x; }\n"
)

#: Headers that parse cleanly but declare nothing of the library's own.
_EMPTY_SURFACE_HEADERS = {
    "empty_file": "",
    "whitespace_only": "   \n\t\n\n",
    "comments_only": "/* nothing declared */\n// still nothing\n",
    "system_includes_only": "#include <stddef.h>\n#include <stdint.h>\n",
}

#: Headers that are not text. Both must be an input error.
_NON_TEXT_HEADERS = {
    "nul_only": b"\0" * 512,
    "nul_inside_text": b"int keep_me(int a);\n\0\nint use_pt(int);\n",
}

_SIDE_LABELS = ("old", "new")


def _build(root: Path, name: str, source: str) -> Path:
    """Compile a real shared library from *source*."""
    root.mkdir(parents=True, exist_ok=True)
    src = root / f"{name}.c"
    src.write_text(source, encoding="utf-8")
    so = root / f"lib{name}.so"
    subprocess.run(
        ["g++", "-x", "c++", "-fPIC", "-shared", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    return so


@pytest.fixture(scope="module")
def libs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("header_text_libs")
    return _build(root / "old", "v1", _OLD_SOURCE), _build(
        root / "new", "v2", _NEW_SOURCE
    )


@pytest.fixture(autouse=True)
def _isolated_snapshot_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh snapshot cache per test: a cache hit must never mask a header change."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))


def _write_header(root: Path, name: str, content: str | bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _compare(
    libs: tuple[Path, Path],
    tmp_path: Path,
    header_args: list[str],
) -> tuple[int, dict[str, object] | None, str]:
    """Run ``compare`` through the CLI; return (exit, JSON report or None, output)."""
    old_so, new_so = libs
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        ["compare", str(old_so), str(new_so), *header_args, "-o", f"json={out}"],
    )
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return result.exit_code, report, result.output


def _side_header_args(side: str, header: Path, full_header: Path) -> list[str]:
    """``--header`` for the side under test; the other side gets the full header."""
    other = "new" if side == "old" else "old"
    return [f"--header={side}={header}", f"--header={other}={full_header}"]


def _zero_surface_warnings(report: dict[str, object]) -> list[str]:
    warnings = report.get("coverage_warnings", [])
    assert isinstance(warnings, list)
    return [str(w) for w in warnings if EMPTY_HEADER_SURFACE_MARKER in str(w)]


def _kinds(report: dict[str, object]) -> list[tuple[str, str]]:
    changes = report.get("changes", [])
    assert isinstance(changes, list)
    return [(str(c.get("kind")), str(c.get("symbol"))) for c in changes]


def _has_change(report: dict[str, object], kind: str, name: str) -> bool:
    """A change of *kind* whose symbol spells *name*. Symbols are mangled for
    the C++ fixture (``_Z7keep_mei``), so the match is on the name's substring."""
    return any(k == kind and name in sym for k, sym in _kinds(report))


# ---------------------------------------------------------------------------
# A header set that parses and declares nothing: named, not suppressed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", sorted(_EMPTY_SURFACE_HEADERS))
def test_new_header_with_zero_declarations_is_named_and_removals_kept(
    libs: tuple[Path, Path], tmp_path: Path, variant: str
) -> None:
    """Pins the new side. The emptied header is a real removal, so exit 4 and
    every removed declaration stay reported; the warning names the NEW side."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    empty = _write_header(tmp_path / "empty", "api.h", _EMPTY_SURFACE_HEADERS[variant])

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("new", empty, full)
    )

    assert exit_code == 4, output
    assert report is not None
    warnings = _zero_surface_warnings(report)
    assert len(warnings) == 1, report.get("coverage_warnings")
    assert warnings[0].startswith("new: "), warnings[0]
    assert _has_change(report, "func_removed", "keep_me"), _kinds(report)
    assert _has_change(report, "func_removed", "use_pt"), _kinds(report)
    assert _has_change(report, "type_removed", "pt"), _kinds(report)


@pytest.mark.parametrize("variant", sorted(_EMPTY_SURFACE_HEADERS))
def test_old_header_with_zero_declarations_is_named_and_additions_kept(
    libs: tuple[Path, Path], tmp_path: Path, variant: str
) -> None:
    """Pins the OLD side: the same contract with the sides exchanged. The new
    library's declarations read as additions, and the warning names OLD."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    empty = _write_header(tmp_path / "empty", "api.h", _EMPTY_SURFACE_HEADERS[variant])

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("old", empty, full)
    )

    assert report is not None, output
    warnings = _zero_surface_warnings(report)
    assert len(warnings) == 1, report.get("coverage_warnings")
    assert warnings[0].startswith("old: "), warnings[0]
    assert _has_change(report, "func_added", "keep_me"), _kinds(report)
    assert _has_change(report, "func_added", "use_pt"), _kinds(report)
    assert _has_change(report, "type_added", "pt"), _kinds(report)


def test_header_on_both_sides_names_each_empty_side(
    libs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A bare ``--header`` applies to both sides. When it is empty, both sides
    must be named -- one warning per side, never one shared line."""
    empty = _write_header(tmp_path / "empty", "api.h", "")

    exit_code, report, output = _compare(libs, tmp_path, ["--header", str(empty)])

    assert report is not None, output
    warnings = _zero_surface_warnings(report)
    assert sorted(w.split(":", 1)[0] for w in warnings) == ["new", "old"], warnings


def test_directory_of_comment_only_headers_is_named(
    libs: tuple[Path, Path], tmp_path: Path
) -> None:
    """The directory form of the same condition: every header in the directory
    parses, none declares anything."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    directory = tmp_path / "hdr_dir"
    _write_header(directory, "a.h", _EMPTY_SURFACE_HEADERS["comments_only"])
    _write_header(directory, "b.h", _EMPTY_SURFACE_HEADERS["whitespace_only"])

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("new", directory, full)
    )

    assert exit_code == 4, output
    assert report is not None
    warnings = _zero_surface_warnings(report)
    assert len(warnings) == 1 and warnings[0].startswith("new: "), warnings


def test_full_header_names_no_side(libs: tuple[Path, Path], tmp_path: Path) -> None:
    """The negative control: a header that declares the surface produces no
    zero-declaration warning, so the warning is not a blanket note on every run."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    full_new = _write_header(tmp_path / "full_new", "api.h", _FULL_HEADER)

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("new", full_new, full)
    )

    assert report is not None, output
    assert _zero_surface_warnings(report) == []


# ---------------------------------------------------------------------------
# A header that is not valid text: an input error, on either side.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("side", _SIDE_LABELS)
@pytest.mark.parametrize("variant", sorted(_NON_TEXT_HEADERS))
def test_header_with_nul_bytes_is_an_input_error(
    libs: tuple[Path, Path], tmp_path: Path, side: str, variant: str
) -> None:
    """Exit 64 (usage / unrecognised input), naming the byte offset, and no
    report is written. Before the fix this exited 4 with the side's header
    evidence silently dropped."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    bad = _write_header(tmp_path / "bad", "api.h", _NON_TEXT_HEADERS[variant])

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args(side, bad, full)
    )

    assert exit_code == 64, output
    assert report is None
    assert "not valid text" in output
    assert "NUL byte at offset" in output


def test_directory_member_with_nul_bytes_is_an_input_error(
    libs: tuple[Path, Path], tmp_path: Path
) -> None:
    """The NUL refusal applies to every header a directory expands to, not only
    to a file operand: one bad member makes the whole operand an input error."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    directory = tmp_path / "hdr_dir"
    _write_header(directory, "good.h", "int keep_me(int a);\n")
    _write_header(directory, "bad.h", _NON_TEXT_HEADERS["nul_only"])

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("new", directory, full)
    )

    assert exit_code == 64, output
    assert report is None
    assert "bad.h" in output


# ---------------------------------------------------------------------------
# Sibling inputs with an explicit outcome that is not a warning.
# ---------------------------------------------------------------------------


def test_empty_header_directory_stays_an_input_error(
    libs: tuple[Path, Path], tmp_path: Path
) -> None:
    """A directory with no header files at all is already refused. Pinned here
    beside its siblings so the three empty-input forms read as one contract."""
    full = _write_header(tmp_path / "full", "api.h", _FULL_HEADER)
    empty_dir = tmp_path / "no_headers"
    _write_header(empty_dir, "README.txt", "docs only\n")

    exit_code, report, output = _compare(
        libs, tmp_path, _side_header_args("new", empty_dir, full)
    )

    assert exit_code == 64, output
    assert report is None
    assert "contains no supported header files" in output

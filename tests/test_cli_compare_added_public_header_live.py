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

"""The reported `abicheck compare` invocation, run for real.

`tests/test_comparability_gate_header_insertion.py` (PR #1274) owns the
invariant that a declared-header insertion bounds a comparison rather than
refusing it, and owns it well: every insertion position, every permutation,
an independent oracle, the `checker.compare` disposition, and the assurance
rollup. Every one of those builds its `ExtractionContract` by calling
`compute_extraction_contract` with a hand-assembled `declared_headers` list.

That leaves one link untested, and it is the link the reported failure
actually ran through. The bug was only ever reachable because
`header_utils.iter_directory_headers` returns its expansion **sorted**: that
is what places an added `pvxs/json.h` between `pvxs/data.h` and `pvxs/log.h`
instead of at the end. A hand-built list states the resulting order as a
premise; nothing in that suite would notice if directory expansion, the
`--header old=<dir>`/`new=<dir>` side-assignment, live L2 extraction, or the
report rendering regressed, and the user's command would be broken again
with the whole suite green (Codex review on PR #1276, P1: AGENTS.md
"Validate the user-facing result" -- prove a change through the public
workflow and the rendered report, not only through an internal detector).

So this module deliberately asserts nothing about the predicate. It runs the
reported command: two really-compiled `.so` files, two real header
directories, `--header old=<dir> --header new=<dir>`, live extraction, and
the rendered JSON report as the thing checked.
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
from abicheck.header_utils import iter_directory_headers

# The reported inventory. `json.h` sorts between `data.h` and `log.h`, so a
# sorted directory expansion places it INTERIOR -- the shape a trailing-only
# waiver rejected.
_OLD_HEADERS = ("client.h", "data.h", "log.h", "source.h")
_ADDED_HEADER = "json.h"
_NEW_HEADERS = tuple(sorted([*_OLD_HEADERS, _ADDED_HEADER]))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None, reason="g++ required to build the fixtures"
    ),
]


def _build(root: Path, names: tuple[str, ...]) -> Path:
    """Compile a real `libpvxs.so.1.5` exporting one function per header."""
    include = root / "pvxs"
    include.mkdir(parents=True)
    for name in names:
        (include / name).write_text(
            f"int pvxs_{Path(name).stem}(int);\n", encoding="utf-8"
        )
    stems = [Path(n).stem for n in names]
    cpp = root / "lib.cpp"
    cpp.write_text(
        "\n".join(
            [
                *(f'#include "pvxs/{n}"' for n in names),
                *(f"int pvxs_{s}(int x) {{ return x; }}" for s in stems),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    so = root / "libpvxs.so.1.5"
    subprocess.run(
        [
            "g++",
            "-std=gnu++11",
            "-fPIC",
            "-shared",
            "-I",
            str(root),
            "-o",
            str(so),
            str(cpp),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return so


def test_directory_expansion_really_places_the_added_header_interior(
    tmp_path: Path,
) -> None:
    """The premise every hand-built `declared_headers` list states rather than
    proves: the real expander returns the added header in the MIDDLE.

    Without this, the end-to-end test below could pass for the wrong reason
    (a trailing append, which a trailing-only waiver accepts anyway) and stop
    covering the reported shape the moment expansion order changed.
    """
    _build(tmp_path / "new", _NEW_HEADERS)
    expanded = [p.name for p in iter_directory_headers(tmp_path / "new" / "pvxs")]
    assert expanded == list(_NEW_HEADERS)
    position = expanded.index(_ADDED_HEADER)
    assert 0 < position < len(expanded) - 1, expanded


def test_reported_invocation_reports_the_added_api(tmp_path: Path) -> None:
    """The reported command, verbatim.

    Pre-fix this exited 16 with "profile_fingerprint mismatch; differing
    fields: header_sequence" and produced no verdict at all.
    """
    old_so = _build(tmp_path / "old", _OLD_HEADERS)
    new_so = _build(tmp_path / "new", _NEW_HEADERS)
    out = tmp_path / "report.json"

    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={tmp_path / 'old' / 'pvxs'}",
            "--header",
            f"new={tmp_path / 'new' / 'pvxs'}",
            "-o",
            f"json={out}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "not comparable" not in result.output
    assert "header_sequence" not in result.output

    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "COMPATIBLE", report["verdict"]
    # The rendered report, not just the exit code: the added API is REPORTED.
    added = [c for c in report.get("changes", []) if c.get("kind") == "func_added"]
    assert any("pvxs_json" in str(c.get("symbol", "")) for c in added), report.get(
        "changes"
    )
    # Every pre-existing export survives the run unremarked -- an insertion
    # must not manufacture findings against the headers it was inserted among.
    removed = [c for c in report.get("changes", []) if c.get("kind") == "func_removed"]
    assert not removed, removed


def test_a_reordered_header_surface_still_refuses_through_the_cli(
    tmp_path: Path,
) -> None:
    """The negative control at the same public surface: a genuine reorder of
    the existing headers must still reach the user as exit 16.

    Explicit `--header old=<file> ...` operands, since a directory operand
    cannot express a reorder at all -- expansion always sorts.
    """
    old_so = _build(tmp_path / "old", _OLD_HEADERS)
    new_so = _build(tmp_path / "new", _OLD_HEADERS)
    old_dir = tmp_path / "old" / "pvxs"
    new_dir = tmp_path / "new" / "pvxs"

    argv = ["compare", str(old_so), str(new_so)]
    for name in _OLD_HEADERS:
        argv += ["--header", f"old={old_dir / name}"]
    for name in reversed(_OLD_HEADERS):
        argv += ["--header", f"new={new_dir / name}"]

    result = CliRunner().invoke(main, argv)
    assert result.exit_code == 16, result.output
    assert "not comparable" in result.output

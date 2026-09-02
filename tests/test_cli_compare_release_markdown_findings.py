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

"""R2 (CLI-audit): a directory/package (release) `compare`'s Markdown report
(the default ``format`` for that operand shape) carried zero symbol names --
the ``## Libraries`` table is counts only (``Breaking: 3``), so identifying
*which* symbol broke needed a separate single-pair `compare`, an
``--output-dir`` re-run, or JSON. The complete per-library finding data was
already computed (``entry["findings"]``, the same capped kind/symbol/
description projection JSON renders) -- it just never reached the Markdown
renderer.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library=lib,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        from_headers=True,
    )
    new = AbiSnapshot(library=lib, version="2.0", functions=[], from_headers=True)
    return old, new


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


class TestReleaseMarkdownCarriesSymbolNames:
    def test_broken_symbol_name_appears_in_markdown(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair()
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        assert code == 4, out
        assert "## Per-Library Findings" in out
        assert "`libfoo.json` Findings" in out
        assert "_Z3foov" in out
        assert "func_removed" in out

    def test_no_findings_section_when_nothing_gates(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name="foo",
                    mangled="_Z3foov",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
        )
        _write_snap(old_dir / "libfoo.json", snap)
        _write_snap(new_dir / "libfoo.json", snap)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        assert code == 0, out
        assert "## Per-Library Findings" not in out

    def test_truncation_note_appears_when_findings_are_capped(
        self, tmp_path: Path
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        # 15 removed functions -> more than _MAX_RELEASE_FINDINGS_PER_LIBRARY (10).
        old_funcs = [
            Function(
                name=f"foo{i}",
                mangled=f"_Z4foo{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(15)
        ]
        old_snap = AbiSnapshot(
            library="libfoo.so", version="1.0", functions=old_funcs, from_headers=True
        )
        new_snap = AbiSnapshot(
            library="libfoo.so", version="2.0", functions=[], from_headers=True
        )
        _write_snap(old_dir / "libfoo.json", old_snap)
        _write_snap(new_dir / "libfoo.json", new_snap)

        code, out = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        assert code == 4, out
        assert "additional findings omitted" in out
        # Codex review, PR #1016: the truncation note must not point to
        # `--format json` as a source of the complete list -- the release
        # JSON's own `findings` field is the identical
        # `_MAX_RELEASE_FINDINGS_PER_LIBRARY`-capped projection, so a reader
        # following that advice would see the same truncated list again.
        assert "--format json" not in out
        assert "--output-dir" in out

    def test_truncation_note_matches_the_actual_json_output(
        self, tmp_path: Path
    ) -> None:
        """The JSON render's own `findings` array for a truncated library
        must actually be capped -- proving the markdown note's claim (that
        `--format json` does *not* carry the complete list) is true, not
        just that the note avoids saying otherwise."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_funcs = [
            Function(
                name=f"foo{i}",
                mangled=f"_Z4foo{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(15)
        ]
        old_snap = AbiSnapshot(
            library="libfoo.so", version="1.0", functions=old_funcs, from_headers=True
        )
        new_snap = AbiSnapshot(
            library="libfoo.so", version="2.0", functions=[], from_headers=True
        )
        _write_snap(old_dir / "libfoo.json", old_snap)
        _write_snap(new_dir / "libfoo.json", new_snap)

        code, out = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1", "--format", "json"
        )
        assert code == 4, out
        data = json.loads(out)
        entry = next(
            lib for lib in data["libraries"] if lib["library"] == "libfoo.json"
        )
        assert entry["findings_truncated"] is True
        assert len(entry["findings"]) < 15

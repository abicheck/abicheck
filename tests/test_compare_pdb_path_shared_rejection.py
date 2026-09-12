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

"""Codex review, PR #1180, fresh evidence ("Preserve per-side PDB
selection"): once ``compare --pdb-path`` lost its per-side ``old=``/``new=``
spelling (Phase 7, ``debug.pdb_path`` is its only source now), the
``old_pdb_path or pdb_path`` / ``new_pdb_path or pdb_path`` fallback every
consumer already used resolves to the *identical* file for both sides of a
two-operand compare, since ``old_pdb_path``/``new_pdb_path`` are now
permanently ``None``. ``locate_pdb`` honors an explicit override
unconditionally (no GUID/age check against the binary it's paired with), so
two different PE binaries could silently read the same PDB and report a
false clean result. ``run_compare`` rejects ``debug.pdb_path`` for a
two-operand compare -- but only when **both** sides are genuinely live PE
inputs (a PDB is only ever consulted while extracting a PE binary,
``service_dump_native_pe.py``'s own ``_extract_pdb_debug``): a second,
later Codex round ("Allow PDB config when only one operand is live") found
the first version rejected unconditionally, breaking the common
stored-baseline-vs-live-candidate shape (``compare old.json new.dll
--config ...``) where only the live side could possibly read it.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _write_snapshot(path: Path, *funcs: str) -> None:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(
                name=f, mangled=f, return_type="void",
                visibility=Visibility.PUBLIC,
            )
            for f in funcs
        ],
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


def _write_fake_pe(path: Path) -> None:
    """A file just real enough to classify as ``pe`` by magic bytes alone.

    ``detect_binary_format`` only reads the first 4 bytes (``binary_utils.
    classify_magic``); this is not a loadable PE and never needs to be --
    the rejection this module tests fires before any real extraction.
    """
    path.write_bytes(b"MZ" + b"\x00" * 62)


class TestDebugPdbPathRejectedOnlyForTwoLivePeOperands:
    def test_rejected_with_two_pe_binaries(self, tmp_path: Path) -> None:
        old_path = tmp_path / "old.dll"
        new_path = tmp_path / "new.dll"
        _write_fake_pe(old_path)
        _write_fake_pe(new_path)
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  pdb_path: C:/symbols/foo.pdb\n")

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--config",
            str(config_path),
            "-o",
            "json=-",
        )

        assert code == 64, out
        assert "debug.pdb_path" in out
        assert "--debug-info" in out

    def test_accepted_with_one_stored_snapshot_and_one_live_pe(
        self, tmp_path: Path
    ) -> None:
        """The scenario the second Codex round named directly: a stored
        baseline (``old.json``) against a live PE candidate -- only the
        live side could ever read ``debug.pdb_path``, so there is no
        sharing risk and this must not be rejected.
        """
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.dll"
        _write_snapshot(old_path, "foo", "bar")
        _write_fake_pe(new_path)
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  pdb_path: C:/symbols/foo.pdb\n")

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--config",
            str(config_path),
            "-o",
            "json=-",
        )

        # Never the pdb_path usage error; the run may still fail for
        # unrelated reasons (no real PE parse of the 64-byte stub above),
        # but not with exit 64 naming debug.pdb_path/--debug-info.
        assert not (code == 64 and "debug.pdb_path" in out), out

    def test_accepted_without_pdb_path_configured(self, tmp_path: Path) -> None:
        """Companion: the rejection must not regress the ordinary case."""
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_snapshot(old_path, "foo", "bar")
        _write_snapshot(new_path, "foo")

        code, _out = _invoke(
            "compare", str(old_path), str(new_path), "-o", "json=-",
        )

        assert code == 4

    def test_accepted_with_two_json_snapshots(self, tmp_path: Path) -> None:
        """Neither side is PE, so debug.pdb_path is configured but unused --
        must not be rejected (it was, unconditionally, before the second
        Codex round narrowed this to a genuinely two-live-PE-sides check).
        """
        old_path = tmp_path / "old.json"
        new_path = tmp_path / "new.json"
        _write_snapshot(old_path, "foo")
        _write_snapshot(new_path, "foo", "bar")
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  pdb_path: C:/symbols/foo.pdb\n")

        code, out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--config",
            str(config_path),
            "-o",
            "json=-",
        )

        assert not (code == 64 and "debug.pdb_path" in out), out


class TestDebugPdbPathRejectedForReleaseFanOut:
    """Codex review, PR #1180, third round ("Reject PDB config for release
    fan-outs"): the two-live-PE-sides check above sees the raw directory/
    package *operand*, never its per-member DLLs, so it never fires for a
    directory/package compare -- ``detect_binary_format(directory)`` is
    ``None``. The release dispatch has no PDB parameter of its own at all,
    so a configured ``debug.pdb_path`` used to be silently dropped: every
    member fell back to auto-discovery with no PDB, which can hide a real
    layout change behind a false clean release verdict. Now rejected
    outright for a directory/package operand, the same way every other
    single-pair-only flag already is.
    """

    def test_rejected_for_directory_operands(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_fake_pe(old_dir / "widget.dll")
        _write_fake_pe(new_dir / "widget.dll")
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text("debug:\n  pdb_path: C:/symbols/foo.pdb\n")

        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(config_path),
            "-o",
            "json=-",
        )

        assert code == 64, out
        assert "debug.pdb_path" in out
        assert "directory/package" in out

    def test_accepted_for_directory_operands_without_pdb_path_configured(
        self, tmp_path: Path
    ) -> None:
        """Companion: the rejection must not regress an ordinary directory
        compare with no debug.pdb_path configured."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_fake_pe(old_dir / "widget.dll")
        _write_fake_pe(new_dir / "widget.dll")

        code, out = _invoke(
            "compare", str(old_dir), str(new_dir), "-o", "json=-",
        )

        assert not (code == 64 and "debug.pdb_path" in out), out

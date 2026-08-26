# Copyright 2026 Nikolay Petrov
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

"""abicheck code-review report item 4, follow-up (Codex review on PR #872):
``confidence.note_if_same_binary_compared`` was wired into `compare`'s own
result-finalization path, but `scan --against`'s baseline-compare path
(`cli_scan_baseline._run_baseline_compare`) calls `compare_snapshots`
directly and builds its own summary, bypassing both the warning call and
the field that would carry it -- so a `scan --against` run with byte-
identical old/new native binaries silently reported a clean comparison
with no signal that nothing could have been detected either way, exactly
the gap the warning exists to close for `compare`.

Kept in its own file (not ``tests/test_cli_scan.py``, an ADR-061
no-growth-baselined legacy module already at its line-budget cap).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from abicheck import dumper as dumper_mod
from abicheck.cli import main
from abicheck.model import AbiSnapshot


def test_scan_against_byte_identical_binary_warns() -> None:
    """Public-surface test: exercised through the real `scan --against`
    CLI entry point (same dumper-mocking pattern as
    TestNoteIfSameBinaryCompared.test_end_to_end_through_the_real_cli_
    compare_command in test_confidence_evidence.py), not only the
    internal helper directly."""
    with CliRunner().isolated_filesystem() as tmp_dir:
        so_path = Path(tmp_dir) / "lib.so"
        so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        out_path = Path(tmp_dir) / "scan.json"

        with mock.patch.object(dumper_mod, "dump", mock.MagicMock(return_value=snap)):
            result = CliRunner().invoke(
                main,
                [
                    "scan",
                    str(so_path),
                    "--against",
                    str(so_path),
                    "--format",
                    "json",
                    "-o",
                    str(out_path),
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(out_path.read_text())
        warnings = payload["diff"].get("coverage_warnings", [])
        assert any("byte-identical" in w for w in warnings), payload


def test_scan_against_byte_identical_binary_warns_in_default_text_output() -> None:
    """Follow-up Codex review: the JSON summary carried this warning, but
    `scan`'s own text renderer (`cli_scan_helpers.render_baseline_lines`) --
    the *default* format, printed with no `--format`/`-o` at all -- never
    surfaced it, so the warning was invisible in an ordinary console run."""
    with CliRunner().isolated_filesystem() as tmp_dir:
        so_path = Path(tmp_dir) / "lib.so"
        so_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])

        with mock.patch.object(dumper_mod, "dump", mock.MagicMock(return_value=snap)):
            result = CliRunner().invoke(
                main, ["scan", str(so_path), "--against", str(so_path)]
            )
        assert result.exit_code == 0, result.output
        assert "byte-identical" in result.output, result.output


def test_scan_against_linker_script_and_its_target_warns() -> None:
    """Follow-up (Codex review): `resolve_input()` follows a GNU ld linker
    script to its resolved target DSO, but the same-binary warning must
    hash *that* resolved target too -- hashing the original script text
    instead would always differ from the target's own hash, silently
    missing a linker-script-vs-DSO comparison of the same underlying
    binary."""
    with CliRunner().isolated_filesystem() as tmp_dir:
        real_so = Path(tmp_dir) / "libfoo.so.1"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        script_so = Path(tmp_dir) / "libfoo.so"
        script_so.write_text("INPUT(libfoo.so.1)\n")
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        out_path = Path(tmp_dir) / "scan.json"

        with mock.patch.object(dumper_mod, "dump", mock.MagicMock(return_value=snap)):
            result = CliRunner().invoke(
                main,
                [
                    "scan",
                    str(real_so),
                    "--against",
                    str(script_so),
                    "--format",
                    "json",
                    "-o",
                    str(out_path),
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(out_path.read_text())
        warnings = payload["diff"].get("coverage_warnings", [])
        assert any("byte-identical" in w for w in warnings), payload


def test_scan_against_a_multi_hop_linker_script_chain_warns() -> None:
    """Follow-up (Codex review): a linker script can itself point at
    another linker script -- `resolve_input()` follows the whole chain
    recursively, so hashing must too, not just one hop."""
    with CliRunner().isolated_filesystem() as tmp_dir:
        real_so = Path(tmp_dir) / "libfoo.so.1.2.3"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)
        middle_script = Path(tmp_dir) / "libfoo.so.1"
        middle_script.write_text("INPUT(libfoo.so.1.2.3)\n")
        outer_script = Path(tmp_dir) / "libfoo.so"
        outer_script.write_text("INPUT(libfoo.so.1)\n")
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        out_path = Path(tmp_dir) / "scan.json"

        with mock.patch.object(dumper_mod, "dump", mock.MagicMock(return_value=snap)):
            result = CliRunner().invoke(
                main,
                [
                    "scan",
                    str(real_so),
                    "--against",
                    str(outer_script),
                    "--format",
                    "json",
                    "-o",
                    str(out_path),
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(out_path.read_text())
        warnings = payload["diff"].get("coverage_warnings", [])
        assert any("byte-identical" in w for w in warnings), payload


def test_scan_against_distinct_binaries_does_not_warn() -> None:
    """Negative control: two genuinely different binaries must not trip
    the warning, and the summary must not even carry the key."""
    with CliRunner().isolated_filesystem() as tmp_dir:
        old_path = Path(tmp_dir) / "old.so"
        new_path = Path(tmp_dir) / "new.so"
        old_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_path.write_bytes(b"\x7fELF" + b"\x00" * 201)
        snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        out_path = Path(tmp_dir) / "scan.json"

        with mock.patch.object(dumper_mod, "dump", mock.MagicMock(return_value=snap)):
            result = CliRunner().invoke(
                main,
                [
                    "scan",
                    str(new_path),
                    "--against",
                    str(old_path),
                    "--format",
                    "json",
                    "-o",
                    str(out_path),
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(out_path.read_text())
        warnings = payload["diff"].get("coverage_warnings", [])
        assert not any("byte-identical" in w for w in warnings), payload


def test_scan_against_snapshot_text_matching_linker_script_regex_does_not_warn() -> None:
    """Codex review, fresh evidence: a JSON snapshot whose serialized text
    happens to contain something matching the INPUT()/GROUP() linker-script
    probe (e.g. a library name embedded verbatim) was resolved as if it
    were a linker script pointing at the real DSO of that name -- hashing
    that DSO's real bytes instead of correctly reading `None` for a
    text-based snapshot, producing a false "byte-identical" claim even
    though the old side was serialized snapshot data, not a binary."""
    from abicheck.serialization import snapshot_to_json

    with CliRunner().isolated_filesystem() as tmp_dir:
        real_so = Path(tmp_dir) / "libfoo.so"
        real_so.write_bytes(b"\x7fELF" + b"\x00" * 200)

        # A perfectly valid AbiSnapshot whose own `library` field happens to
        # read as `INPUT(libfoo.so)` once serialized -- exactly the shape
        # `binary_utils.resolve_linker_script`'s regex probe matches.
        baseline_snap = AbiSnapshot(
            library="INPUT(libfoo.so)", version="1.0", functions=[]
        )
        baseline_path = Path(tmp_dir) / "baseline.abicheck.json"
        baseline_path.write_text(snapshot_to_json(baseline_snap), encoding="utf-8")
        assert "INPUT(libfoo.so)" in baseline_path.read_text()

        candidate_snap = AbiSnapshot(library="libfoo.so", version="1.0", functions=[])
        out_path = Path(tmp_dir) / "scan.json"

        with mock.patch.object(
            dumper_mod, "dump", mock.MagicMock(return_value=candidate_snap)
        ):
            result = CliRunner().invoke(
                main,
                [
                    "scan",
                    str(real_so),
                    "--against",
                    str(baseline_path),
                    "--format",
                    "json",
                    "-o",
                    str(out_path),
                ],
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(out_path.read_text())
        warnings = payload["diff"].get("coverage_warnings", [])
        assert not any("byte-identical" in w for w in warnings), payload

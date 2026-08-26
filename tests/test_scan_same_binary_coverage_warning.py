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

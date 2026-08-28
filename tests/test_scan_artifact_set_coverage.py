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

"""Coverage-gap tests for ``scan --artifact-set`` (CLI cleanup phase two,
PR 5), split out of ``tests/test_scan_artifact_set.py`` rather than added
there: that module is a ``no_growth``-debt-tracked file
(``architecture/debt.yaml``), so new coverage for the repeatable-option
refactor's own branches -- the directory-discovery return, the
member-not-found rejection, and the ``--dry-run``-with-``--artifact-set``
rejection -- lives here instead of raising that file's line-count baseline.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


def _write_elf_shared_object_stub(path: Path) -> None:
    """Minimal, structurally-valid ELF64 shared object (ET_DYN, no program
    headers) -- see ``tests/test_scan_artifact_set.py``'s identical helper
    for why this beats a bare 4-byte magic sniff."""
    data = bytearray(64)
    data[0:4] = b"\x7fELF"
    data[4] = 2  # ELFCLASS64
    data[5] = 1  # little-endian
    struct.pack_into("<H", data, 16, 3)  # e_type = ET_DYN
    struct.pack_into("<Q", data, 32, 0)  # e_phoff = 0
    struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
    path.write_bytes(bytes(data))


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestArtifactSetRepeatableOptionBranches:
    def test_rejects_dry_run_with_artifact_set(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        result = runner.invoke(
            main,
            [
                "scan", "--artifact-set", str(p1), "--artifact-set", str(p2),
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "--dry-run is not yet supported with --artifact-set" in result.output

    def test_rejects_explicit_member_that_does_not_exist(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1 = tmp_path / "liba.so"
        _write_elf_shared_object_stub(p1)
        missing = tmp_path / "missing.so"
        result = runner.invoke(
            main, ["scan", "--artifact-set", str(p1), "--artifact-set", str(missing)]
        )
        assert result.exit_code != 0
        assert f"--artifact-set member not found: {missing}" in result.output

    def test_directory_form_resolves_via_discover_shared_libraries(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single value that is a directory (len(spec) == 1) takes the
        # directory-discovery branch, not the explicit-path-list branch --
        # mocked here (rather than requiring real ELF fixtures under
        # @pytest.mark.integration) to exercise it in the fast unit lane too.
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult
        from abicheck.workflows import extraction as extraction_mod

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)

        monkeypatch.setattr(
            extraction_mod, "discover_shared_libraries", lambda d: [p1, p2]
        )
        captured: dict[str, object] = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0)

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)

        result = runner.invoke(main, ["scan", "--artifact-set", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert sorted(captured["req"].binaries) == sorted([p1, p2])

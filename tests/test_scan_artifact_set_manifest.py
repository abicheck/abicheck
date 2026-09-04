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

"""``scan --artifact-set --manifest`` CLI tests (PR H, CLI cleanup phase
two, ADR-056 D2), split out of ``tests/test_scan_artifact_set.py`` rather
than added there: that module is a ``no_growth``-debt-tracked file
(``architecture/debt.yaml``), so new coverage for the opt-in
expected-provider ownership manifest lives here instead of raising that
file's line-count baseline -- the same split ``test_scan_artifact_set_
coverage.py`` already established for a different --artifact-set slice.

The detector logic itself (``_detect_manifest_ownership``,
``_manifest_ownership_findings``) is unit-tested directly in
``tests/test_bundle_provider_ownership.py``; this file covers only the
CLI/service wiring on top of it.
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


class TestArtifactSetManifest:
    """CLI validation + wiring for the opt-in expected-provider ownership
    manifest."""

    def test_rejects_manifest_without_artifact_set(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        artifact = tmp_path / "libonly.so"
        _write_elf_shared_object_stub(artifact)
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text("provides: []\n")
        result = runner.invoke(
            main, ["scan", str(artifact), "--manifest", str(manifest_path)]
        )
        assert result.exit_code != 0
        assert "--manifest requires --artifact-set" in result.output

    def test_bad_manifest_file_is_a_click_exception(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text("not: {a: valid, manifest: shape}\n")
        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set",
                str(p1),
                "--artifact-set",
                str(p2),
                "--manifest",
                str(manifest_path),
            ],
        )
        assert result.exit_code != 0
        assert "Failed to load manifest" in result.output

    def test_manifest_loaded_and_forwarded_to_scan_request(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.service_scan as service_scan_mod
        from abicheck.service_scan import ScanSetResult

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(
            "provides:\n"
            "  - symbol: shared_util\n"
            "    library: libutil.so\n"
            "    optional_provider: false\n"
        )

        captured: dict = {}

        def _fake_run_scan_set(req):
            captured["req"] = req
            return ScanSetResult(verdict="COMPATIBLE", exit_code=0, per_artifact=[])

        monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)
        result = runner.invoke(
            main,
            [
                "scan",
                "--artifact-set",
                str(p1),
                "--artifact-set",
                str(p2),
                "--manifest",
                str(manifest_path),
            ],
        )
        assert result.exit_code == 0, result.output
        req = captured["req"]
        assert req.bundle_manifest is not None
        assert req.bundle_manifest.entries[0].symbol == "shared_util"
        assert req.bundle_manifest.entries[0].library == "libutil.so"
        assert req.bundle_manifest.entries[0].optional_provider is False

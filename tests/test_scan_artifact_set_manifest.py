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
from abicheck.elf_metadata import ElfSymbol
from abicheck.model import AbiSnapshot, AccessLevel, Function, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _func(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


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


class TestRunScanSetRejectsTypedApiBypass:
    """P2 (Codex review, fresh evidence, PR H follow-up): a directly-
    constructed ``ScanRequest(bundle_manifest=...)`` reaches
    ``audit_bundle()`` without ever going through ``load_manifest()``'s own
    ``optional_provider: false`` / no-``library`` validation -- the CLI
    path (``load_artifact_set_manifest``) always routes through
    ``load_manifest``, but a Python API caller building the manifest
    in-process (``InstantiationManifest``/``ManifestEntry`` constructed
    directly, never touching a file) can skip it entirely. ``run_scan_set``
    must re-check the same invariant itself.
    """

    def test_rejects_required_provider_with_no_library(self, tmp_path: Path) -> None:
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.service import ScanRequest, run_scan_set

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="foo", optional_provider=False),)
        )
        with pytest.raises(ValueError, match="requires a 'library'"):
            run_scan_set(
                ScanRequest(binaries=[p1, p2], mode="audit", bundle_manifest=manifest)
            )

    def test_rejects_entry_with_no_selector(self, tmp_path: Path) -> None:
        # Codex review, fresh evidence, follow-up round: a bare
        # ManifestEntry() has none of symbol/pattern/template set --
        # _entry_targets silently expands it to zero match targets, so the
        # scan would report no unsatisfied entry at all instead of raising.
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.service import ScanRequest, run_scan_set

        p1, p2 = tmp_path / "liba.so", tmp_path / "libb.so"
        _write_elf_shared_object_stub(p1)
        _write_elf_shared_object_stub(p2)
        manifest = InstantiationManifest(entries=(ManifestEntry(),))
        with pytest.raises(ValueError, match="exactly one of"):
            run_scan_set(
                ScanRequest(binaries=[p1, p2], mode="audit", bundle_manifest=manifest)
            )

    def test_required_provider_with_library_is_not_rejected_here(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A well-formed manifest must not be rejected by this guard --
        # regression-guards against an overly-broad check that flags every
        # bundle_manifest instead of only the malformed shape. Mirrors
        # tests/test_scan_artifact_set.py::TestRunScanSetAmbiguousSoname's
        # own real-per-member-scan-then-mocked-audit_bundle pattern.
        import abicheck.bundle as bundle_mod
        from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
        from abicheck.checker_types import Verdict
        from abicheck.elf_metadata import ElfMetadata as _ElfMeta
        from abicheck.service import ScanRequest
        from abicheck.service_scan import ScanSetResult, run_scan_set

        snap_a = _write_snapshot(
            tmp_path / "a.abi.json",
            AbiSnapshot(
                library="liba.so",
                version="1.0",
                from_headers=True,
                functions=[_func("a_run", "_Z5a_runv")],
                elf=_ElfMeta(symbols=[ElfSymbol(name="_Z5a_runv")]),
            ),
        )
        snap_b = _write_snapshot(
            tmp_path / "b.abi.json",
            AbiSnapshot(
                library="libb.so",
                version="1.0",
                from_headers=True,
                functions=[_func("b_run", "_Z5b_runv")],
                elf=_ElfMeta(symbols=[ElfSymbol(name="_Z5b_runv")]),
            ),
        )
        manifest = InstantiationManifest(
            entries=(
                ManifestEntry(
                    symbol="foo", library="liba.so", optional_provider=False
                ),
            )
        )

        from abicheck.bundle import BundleSnapshot

        def _fake_discover(paths, *, explicit):
            return {"liba.so": snap_a, "libb.so": snap_b}

        class _FakeAudit:
            findings: list = []
            verdict = Verdict.COMPATIBLE
            snapshot = BundleSnapshot(
                root=snap_a.parent,
                libraries={"liba.so": snap_a, "libb.so": snap_b},
                metadata={},
                resolution=None,
            )

        def _fake_audit_bundle(libraries, *, bundle_system_providers=(), manifest=None):
            assert manifest is not None
            return _FakeAudit()

        monkeypatch.setattr(bundle_mod, "discover_artifact_set", _fake_discover)
        monkeypatch.setattr(bundle_mod, "audit_bundle", _fake_audit_bundle)
        result = run_scan_set(
            ScanRequest(
                binaries=[snap_a, snap_b], mode="audit", bundle_manifest=manifest
            )
        )
        assert isinstance(result, ScanSetResult)

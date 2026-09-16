# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Resolved member evidence prevents final bundle metadata rereads."""

from pathlib import Path

from abicheck import bundle
from abicheck.bundle_models import BundleSignatureEvidence
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata


def _snapshot(filename: str) -> AbiSnapshot:
    return AbiSnapshot(
        library=filename,
        version="1",
        elf=ElfMetadata(soname=filename, needed=[], symbols=[], imports=[]),
    )


def test_live_member_uses_already_resolved_elf_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    library = tmp_path / "libready.so.1"
    library.write_bytes(b"not reread as ELF")
    resolved = _snapshot(library.name)

    def fail_parse(*args, **kwargs):
        raise AssertionError("resolved live member was parsed again")

    monkeypatch.setattr(bundle, "build_bundle_snapshot", fail_parse)
    result = bundle.build_bundle_snapshot_mixed(
        {"libready.so": library}, resolved_evidence={"libready.so": resolved}
    )
    assert result.metadata["libready.so"] is resolved.elf


def test_stored_member_uses_compact_evidence_without_snapshot_decode(
    tmp_path: Path, monkeypatch
) -> None:
    stored = tmp_path / "libready.so.abicheck.json"
    stored.write_text("{}", encoding="utf-8")
    evidence = BundleSignatureEvidence.from_snapshot(_snapshot("libready.so.1"))

    def fail_decode(*args, **kwargs):
        raise AssertionError("resolved stored member was decoded again")

    monkeypatch.setattr(bundle, "_stored_file_snapshot_evidence", fail_decode)
    result = bundle.build_bundle_snapshot_mixed(
        {"libready.so": stored}, resolved_evidence={"libready.so": evidence}
    )
    assert result.metadata["libready.so"] is evidence.elf
    assert result.libraries["libready.so"] == Path("libready.so.1")


def test_stored_directory_falls_back_to_resolved_native_filename(
    tmp_path: Path, monkeypatch
) -> None:
    stored = tmp_path / "stored-member"
    stored.mkdir()
    evidence = BundleSignatureEvidence.from_snapshot(_snapshot("libready.so.1"))
    monkeypatch.setattr(bundle, "_is_stored_member", lambda path: path == stored)
    monkeypatch.setattr(
        bundle,
        "_stored_library_identity",
        lambda path, count: (None, ("libready-alias.so",), count + 1),
    )
    result = bundle.build_bundle_snapshot_mixed(
        {"libready.so": stored}, resolved_evidence={"libready.so": evidence}
    )
    assert result.libraries["libready.so"] == Path("libready.so.1")
    assert result.resolution.soname_to_name["libready-alias.so"] == "libready.so"

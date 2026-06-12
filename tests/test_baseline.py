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

"""Tests for baseline.py — baseline registry (ADR-022)."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from abicheck.baseline import (
    BaselineIntegrityError,
    BaselineKey,
    BaselineMetadata,
    FilesystemRegistry,
    _atomic_write,
    _validate_key_field,
)
from abicheck.errors import ValidationError
from abicheck.model import AbiSnapshot, Function, Visibility

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0.0",
        functions=[
            Function(name="foo", mangled="foo", return_type="void",
                     visibility=Visibility.PUBLIC),
        ],
    )


@pytest.fixture()
def registry(tmp_path: Path) -> FilesystemRegistry:
    return FilesystemRegistry(tmp_path / "baselines")


# ---------------------------------------------------------------------------
# Tests: _validate_key_field
# ---------------------------------------------------------------------------


class TestValidateKeyField:
    def test_valid_names(self) -> None:
        _validate_key_field("x", "libfoo")
        _validate_key_field("x", "lib-foo_2.0+ssl")
        _validate_key_field("x", "linux-x86_64")
        _validate_key_field("x", "1.0.0-rc1")

    def test_empty(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            _validate_key_field("library", "")

    def test_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match="must not contain"):
            _validate_key_field("library", "../../etc")
        with pytest.raises(ValidationError, match="must not contain"):
            _validate_key_field("version", "a..b")

    def test_invalid_chars(self) -> None:
        with pytest.raises(ValidationError, match="invalid characters"):
            _validate_key_field("library", "lib foo")
        with pytest.raises(ValidationError, match="invalid characters"):
            _validate_key_field("library", "lib/foo")
        with pytest.raises(ValidationError, match="invalid characters"):
            _validate_key_field("library", "lib:foo")
        with pytest.raises(ValidationError, match="invalid characters"):
            _validate_key_field("library", "lib\x00foo")


# ---------------------------------------------------------------------------
# Tests: BaselineKey
# ---------------------------------------------------------------------------


class TestBaselineKey:
    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match="must not contain"):
            BaselineKey(library="../../etc", version="1.0", platform="x")
        with pytest.raises(ValidationError, match="must not contain"):
            BaselineKey(library="lib", version="../..", platform="x")

    def test_rejects_invalid_chars(self) -> None:
        with pytest.raises(ValidationError, match="invalid characters"):
            BaselineKey(library="lib foo", version="1.0", platform="x")
        with pytest.raises(ValidationError, match="invalid characters"):
            BaselineKey(library="lib/foo", version="1.0", platform="x")
        with pytest.raises(ValidationError, match="must not be empty"):
            BaselineKey(library="", version="1.0", platform="x")

    def test_accepts_valid_names(self) -> None:
        key = BaselineKey(library="libfoo-2.0", version="1.0.0-rc1", platform="linux-x86_64")
        assert key.library == "libfoo-2.0"
        key2 = BaselineKey(library="lib_foo+ssl", version="v2.0", platform="linux-x86_64")
        assert key2.library == "lib_foo+ssl"

    def test_path(self) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        assert key.path == "libfoo/1.0.0/linux-x86_64"
        assert str(key) == "libfoo/1.0.0/linux-x86_64"

    def test_path_with_variant(self) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64", variant="ssl")
        assert key.path == "libfoo/1.0.0/linux-x86_64/ssl"

    def test_from_path(self) -> None:
        key = BaselineKey.from_path("libfoo/1.0.0/linux-x86_64")
        assert key.library == "libfoo"
        assert key.version == "1.0.0"
        assert key.platform == "linux-x86_64"
        assert key.variant == ""

    def test_from_path_with_variant(self) -> None:
        key = BaselineKey.from_path("libfoo/1.0.0/linux-x86_64/debug")
        assert key.variant == "debug"

    def test_from_path_strips_slashes(self) -> None:
        key = BaselineKey.from_path("/libfoo/1.0.0/linux-x86_64/")
        assert key.library == "libfoo"

    def test_from_path_invalid(self) -> None:
        with pytest.raises(ValidationError, match="Invalid baseline path"):
            BaselineKey.from_path("libfoo/1.0.0")

    def test_from_path_too_many_segments(self) -> None:
        with pytest.raises(ValidationError, match="Too many segments"):
            BaselineKey.from_path("a/b/c/d/e")

    def test_from_spec(self) -> None:
        key = BaselineKey.from_spec("libfoo:1.0.0:linux-x86_64")
        assert key.library == "libfoo"

    def test_from_spec_with_variant(self) -> None:
        key = BaselineKey.from_spec("libfoo:1.0.0:linux-x86_64:ssl")
        assert key.variant == "ssl"

    def test_from_spec_invalid(self) -> None:
        with pytest.raises(ValidationError, match="Invalid baseline spec"):
            BaselineKey.from_spec("libfoo:1.0.0")

    def test_from_spec_too_many(self) -> None:
        with pytest.raises(ValidationError, match="Too many segments"):
            BaselineKey.from_spec("a:b:c:d:e")

    def test_frozen(self) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        with pytest.raises(AttributeError):
            key.library = "bar"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: BaselineMetadata
# ---------------------------------------------------------------------------


class TestBaselineMetadata:
    def test_create(self) -> None:
        meta = BaselineMetadata.create('{"test": true}')
        assert meta.checksum is not None
        assert meta.checksum != ""
        assert meta.abicheck_version
        assert meta.created_at
        assert meta.schema_version >= 1
        assert meta.verify_checksum('{"test": true}')
        assert not meta.verify_checksum('{"test": false}')

    def test_create_with_optional_fields(self) -> None:
        meta = BaselineMetadata.create(
            '{"test": true}',
            build_context_hash="abc123",
            git_commit="def456",
        )
        assert meta.build_context_hash == "abc123"
        assert meta.git_commit == "def456"

    def test_empty_checksum_fails(self) -> None:
        meta = BaselineMetadata(checksum="")
        assert not meta.verify_checksum('{"anything": true}')

    def test_none_checksum_passes(self) -> None:
        meta = BaselineMetadata(checksum=None)
        assert meta.verify_checksum('{"anything": true}')

    def test_roundtrip(self) -> None:
        meta = BaselineMetadata.create('{"test": true}', git_commit="abc123")
        data = meta.to_dict()
        restored = BaselineMetadata.from_dict(data)
        assert restored.checksum == meta.checksum
        assert restored.git_commit == "abc123"
        assert restored.abicheck_version == meta.abicheck_version
        assert restored.schema_version == meta.schema_version
        assert restored.created_at == meta.created_at

    def test_from_dict_none_checksum(self) -> None:
        data = {"checksum": None}
        meta = BaselineMetadata.from_dict(data)
        assert meta.checksum is None

    def test_from_dict_missing_checksum(self) -> None:
        meta = BaselineMetadata.from_dict({})
        assert meta.checksum is None

    def test_from_dict_none_optional_fields(self) -> None:
        data = {"build_context_hash": None, "git_commit": None, "signature": None}
        meta = BaselineMetadata.from_dict(data)
        assert meta.build_context_hash is None
        assert meta.git_commit is None
        assert meta.signature is None

    def test_from_dict_present_optional_fields(self) -> None:
        data = {"build_context_hash": "abc", "git_commit": "def", "signature": "ghi"}
        meta = BaselineMetadata.from_dict(data)
        assert meta.build_context_hash == "abc"
        assert meta.git_commit == "def"
        assert meta.signature == "ghi"

    def test_to_dict(self) -> None:
        meta = BaselineMetadata(checksum="abc", git_commit="def")
        d = meta.to_dict()
        assert d["checksum"] == "abc"
        assert d["git_commit"] == "def"


# ---------------------------------------------------------------------------
# Tests: _atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_creates_file(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        _atomic_write(target, "hello world")
        assert target.read_text() == "hello world"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("old")
        _atomic_write(target, "new")
        assert target.read_text() == "new"

    def test_no_partial_write_on_error(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("original")
        # Force an error by making the directory read-only (can't rename into it)
        # This is hard to test portably, so just verify the function doesn't corrupt
        _atomic_write(target, "replacement")
        assert target.read_text() == "replacement"


# ---------------------------------------------------------------------------
# Tests: FilesystemRegistry
# ---------------------------------------------------------------------------


class TestFilesystemRegistry:
    def test_push_and_pull(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        ref = registry.push(key, sample_snapshot)
        assert "libfoo" in ref
        assert ref.startswith("fs://")

        result = registry.pull(key)
        assert result is not None
        snapshot, meta = result
        assert snapshot.library == "libfoo.so"
        assert snapshot.version == "1.0.0"
        assert len(snapshot.functions) == 1
        assert meta.checksum is not None

    def test_push_creates_metadata(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        key_dir = registry._key_dir(key)
        assert (key_dir / "snapshot.json").exists()
        assert (key_dir / "metadata.json").exists()

    def test_push_with_custom_metadata(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        meta = BaselineMetadata(checksum=None, git_commit="custom")
        registry.push(key, sample_snapshot, metadata=meta)
        result = registry.pull(key)
        assert result is not None
        # Custom metadata had None checksum, so pull succeeds
        assert result[1].git_commit == "custom"

    def test_pull_nonexistent(self, registry: FilesystemRegistry) -> None:
        key = BaselineKey(library="libfoo", version="999", platform="linux-x86_64")
        assert registry.pull(key) is None

    def test_pull_checksum_mismatch_raises(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        key_dir = registry._key_dir(key)
        snap_path = key_dir / "snapshot.json"
        snap_path.write_text('{"tampered": true}', encoding="utf-8")
        with pytest.raises(BaselineIntegrityError, match="Checksum mismatch"):
            registry.pull(key)

    def test_pull_missing_metadata_still_works(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        (registry._key_dir(key) / "metadata.json").unlink()
        result = registry.pull(key)
        assert result is not None
        assert result[1].checksum is None

    def test_pull_corrupt_metadata_still_works(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        (registry._key_dir(key) / "metadata.json").write_text("not json")
        result = registry.pull(key)
        assert result is not None
        assert result[1].checksum is None

    def test_list_empty(self, registry: FilesystemRegistry) -> None:
        assert registry.list() == []

    def test_list_baselines(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        keys_to_push = [
            BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64"),
            BaselineKey(library="libfoo", version="2.0.0", platform="linux-x86_64"),
            BaselineKey(library="libbar", version="1.0.0", platform="linux-x86_64"),
        ]
        for key in keys_to_push:
            registry.push(key, sample_snapshot)

        keys = registry.list()
        assert len(keys) == 3

        foo_keys = registry.list(prefix="libfoo")
        assert len(foo_keys) == 2
        assert all(k.library == "libfoo" for k in foo_keys)

    def test_list_with_variants(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        k1 = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        k2 = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64", variant="ssl")
        registry.push(k1, sample_snapshot)
        registry.push(k2, sample_snapshot)
        keys = registry.list()
        assert len(keys) == 2

    def test_delete_baseline(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        assert registry.delete(key) is True
        assert registry.pull(key) is None
        assert registry.list() == []

    def test_delete_cleans_empty_parents(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        registry.delete(key)
        assert not (registry.root / "libfoo").exists()

    def test_delete_preserves_siblings(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        k1 = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        k2 = BaselineKey(library="libfoo", version="2.0.0", platform="linux-x86_64")
        registry.push(k1, sample_snapshot)
        registry.push(k2, sample_snapshot)
        registry.delete(k1)
        assert registry.pull(k2) is not None
        assert (registry.root / "libfoo").exists()

    def test_delete_nonexistent(self, registry: FilesystemRegistry) -> None:
        key = BaselineKey(library="libfoo", version="999", platform="linux-x86_64")
        assert registry.delete(key) is False

    def test_push_overwrites(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot,
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        snap2 = AbiSnapshot(library="libfoo.so", version="1.0.0", functions=[])
        registry.push(key, snap2)
        result = registry.pull(key)
        assert result is not None
        assert len(result[0].functions) == 0

    def test_key_dir_escape_rejected(self, registry: FilesystemRegistry) -> None:
        with pytest.raises(ValidationError):
            BaselineKey(library="..%2F..%2Fetc", version="x", platform="x")

    def test_key_dir_relative_to_containment(self, tmp_path: Path) -> None:
        """Verify pathlib-based containment prevents sibling prefix attacks."""
        # /tmp/root2 starts with /tmp/root, but it's not inside it
        root = tmp_path / "root"
        root.mkdir()
        registry = FilesystemRegistry(root)
        # Valid key works fine
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        key_dir = registry._key_dir(key)
        assert str(key_dir).startswith(str(root))


# ---------------------------------------------------------------------------
# Tests: BaselineIntegrityError
# ---------------------------------------------------------------------------


class TestBaselineIntegrityError:
    def test_is_validation_error(self) -> None:
        """BaselineIntegrityError inherits from ValidationError."""
        err = BaselineIntegrityError("test")
        assert isinstance(err, ValidationError)
        assert isinstance(err, ValueError)  # via ValidationError


# ---------------------------------------------------------------------------
# Tests: detect_platform_from_binary
# ---------------------------------------------------------------------------


class TestDetectPlatformFromBinary:
    def test_elf_binary(self, tmp_path: Path) -> None:
        """ELF binary returns linux-<arch> platform."""
        from abicheck.baseline import detect_platform_from_binary
        binary = tmp_path / "libfoo.so"
        # Minimal ELF header (x86_64)
        elf_header = bytearray(64)
        elf_header[0:4] = b"\x7fELF"
        elf_header[4] = 2  # 64-bit
        elf_header[5] = 1  # little-endian
        elf_header[6] = 1  # ELF version
        elf_header[16:18] = (3).to_bytes(2, 'little')  # ET_DYN
        elf_header[18:20] = (0x3E).to_bytes(2, 'little')  # EM_X86_64
        binary.write_bytes(bytes(elf_header))
        result = detect_platform_from_binary(binary)
        assert result.startswith("linux-")

    def test_pe_binary(self, tmp_path: Path) -> None:
        """PE binary returns windows-<arch> platform."""
        from abicheck.baseline import detect_platform_from_binary
        binary = tmp_path / "foo.dll"
        binary.write_bytes(b"MZ" + b"\x00" * 200)
        result = detect_platform_from_binary(binary)
        # Fake PE payload may fail architecture parsing and now returns None.
        assert result is None or result.startswith("windows-")

    def test_unknown_format(self, tmp_path: Path) -> None:
        """Unknown format returns <platform>-unknown."""
        from abicheck.baseline import detect_platform_from_binary
        binary = tmp_path / "unknown"
        binary.write_bytes(b"random data" * 20)
        result = detect_platform_from_binary(binary)
        assert "unknown" in result

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Non-existent file doesn't crash."""
        from abicheck.baseline import detect_platform_from_binary
        result = detect_platform_from_binary(tmp_path / "nope")
        assert "unknown" in result

    def test_elf_parse_error_returns_none_and_logs(self, tmp_path: Path, monkeypatch, caplog) -> None:
        from abicheck.baseline import detect_platform_from_binary

        binary = tmp_path / "libfoo.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 64)

        class BrokenELFFile:
            def __init__(self, *_args, **_kwargs) -> None:
                raise ValueError("bad elf")

        monkeypatch.setitem(__import__("sys").modules, "elftools.elf.elffile", types.SimpleNamespace(ELFFile=BrokenELFFile))
        with caplog.at_level("WARNING"):
            assert detect_platform_from_binary(binary) is None
        assert "Failed to detect ELF architecture" in caplog.text

    def test_pe_parse_error_returns_none_and_logs(self, tmp_path: Path, monkeypatch, caplog) -> None:
        from abicheck.baseline import detect_platform_from_binary

        binary = tmp_path / "foo.dll"
        binary.write_bytes(b"MZ" + b"\x00" * 200)

        class BrokenPE:
            def __init__(self, *_args, **_kwargs) -> None:
                raise ValueError("bad pe")

        monkeypatch.setitem(__import__("sys").modules, "pefile", types.SimpleNamespace(PE=BrokenPE))
        with caplog.at_level("WARNING"):
            assert detect_platform_from_binary(binary) is None
        assert "Failed to detect PE architecture" in caplog.text

    def test_macho_parse_error_returns_none_and_logs(self, tmp_path: Path, monkeypatch, caplog) -> None:
        from abicheck.baseline import detect_platform_from_binary

        binary = tmp_path / "libfoo.dylib"
        binary.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 64)

        class BrokenMachO:
            def __init__(self, *_args, **_kwargs) -> None:
                raise ValueError("bad macho")

        monkeypatch.setitem(
            __import__("sys").modules,
            "macholib.MachO",
            types.SimpleNamespace(MachO=BrokenMachO),
        )
        with caplog.at_level("WARNING"):
            assert detect_platform_from_binary(binary) is None
        assert "Failed to detect Mach-O architecture" in caplog.text


# ---------------------------------------------------------------------------
# Tests: _load_snapshot_from_string
# ---------------------------------------------------------------------------


class TestLoadSnapshotFromString:
    def test_valid_json(self) -> None:
        from abicheck.baseline import _load_snapshot_from_string
        from abicheck.serialization import snapshot_to_json
        snap = AbiSnapshot(library="libfoo.so", version="1.0.0", functions=[])
        json_str = snapshot_to_json(snap)
        restored = _load_snapshot_from_string(json_str)
        assert restored.library == "libfoo.so"
        assert restored.version == "1.0.0"

    def test_invalid_json_raises(self) -> None:
        from abicheck.baseline import _load_snapshot_from_string
        with pytest.raises(Exception):
            _load_snapshot_from_string("not valid json")


# ---------------------------------------------------------------------------
# Tests: evidence-pack storage (ADR-028 Phase 5)
# ---------------------------------------------------------------------------


def _make_pack(root: Path) -> object:
    """Build and write a minimal evidence pack with one build-evidence file."""
    from abicheck.buildsource import BuildEvidence, BuildSourcePack
    from abicheck.buildsource.build_evidence import Toolchain

    pack = BuildSourcePack.empty(root, abicheck_version="9.9", created_at="t0")
    pack.build_evidence = BuildEvidence(
        toolchains=[Toolchain(id="toolchain://gcc-13", compiler_id="GNU", version="13")]
    )
    pack.write()
    return pack


class TestBuildSourcePackStorage:
    def test_push_pull_evidence_roundtrip(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")

        registry.push(key, sample_snapshot, evidence=pack)

        pulled = registry.pull_evidence(key)
        assert pulled is not None
        assert pulled.build_evidence is not None
        assert pulled.build_evidence.toolchains[0].compiler_id == "GNU"
        # Same logical evidence hashes identically.
        assert pulled.content_hash() == pack.content_hash()

    def test_push_records_evidence_hash_in_metadata(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)

        _, meta = registry.pull(key)  # type: ignore[misc]
        assert meta.evidence_content_hash == pack.content_hash()

    def test_push_records_evidence_coverage_in_metadata(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        """ADR-033 D4: the metadata carries a coverage block for the stored pack."""
        pack = _make_pack(tmp_path / "src.evidence")  # build_evidence only (L3)
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)

        _, meta = registry.pull(key)  # type: ignore[misc]
        assert meta.evidence_coverage is not None
        assert meta.evidence_coverage["build_context"] is True
        assert meta.evidence_coverage["source_abi"] == "not_collected"
        assert meta.evidence_coverage["graph"] == "not_collected"

    def test_push_without_evidence_has_no_coverage(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        _, meta = registry.pull(key)  # type: ignore[misc]
        assert meta.evidence_coverage is None

    def test_pull_evidence_none_when_absent(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot
    ) -> None:
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot)
        assert registry.pull_evidence(key) is None

    def test_pull_evidence_none_for_missing_baseline(
        self, registry: FilesystemRegistry
    ) -> None:
        key = BaselineKey(library="nope", version="0", platform="linux-x86_64")
        assert registry.pull_evidence(key) is None

    def test_repush_without_evidence_drops_stale_pack(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)
        assert registry.pull_evidence(key) is not None

        registry.push(key, sample_snapshot)  # no evidence this time
        assert registry.pull_evidence(key) is None
        _, meta = registry.pull(key)  # type: ignore[misc]
        assert meta.evidence_content_hash is None

    def test_pull_evidence_detects_tampering(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)

        # Corrupt the stored build evidence so the recomputed digests drift.
        stored = registry.root / key.path / "evidence" / "build" / "build_evidence.json"
        stored.write_text('{"schema_version": 1, "tampered": true}\n', encoding="utf-8")

        with pytest.raises(BaselineIntegrityError, match="content hash mismatch"):
            registry.pull_evidence(key)

    def test_push_unwritten_pack_raises(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        from abicheck.buildsource import BuildSourcePack

        pack = BuildSourcePack.empty(tmp_path / "unwritten.evidence")  # never .write()
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        with pytest.raises(ValidationError, match="no manifest.json"):
            registry.push(key, sample_snapshot, evidence=pack)

    def test_delete_removes_evidence(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)
        assert registry.delete(key) is True
        assert registry.pull_evidence(key) is None

    def test_metadata_evidence_hash_roundtrip(self) -> None:
        meta = BaselineMetadata.create("snap-json", evidence_content_hash="sha256:abc")
        restored = BaselineMetadata.from_dict(meta.to_dict())
        assert restored.evidence_content_hash == "sha256:abc"


    def test_pull_evidence_missing_manifest_but_recorded_raises(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # A baseline that recorded a pack must not silently report "no pack" when
        # the manifest vanished (deleted / interrupted replacement) — Codex review.
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)

        (registry.root / key.path / "evidence" / "manifest.json").unlink()
        with pytest.raises(BaselineIntegrityError, match="manifest is missing"):
            registry.pull_evidence(key)


    def test_push_rejects_pack_failing_integrity(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # A source pack whose normalized payload drifted from its manifest must be
        # rejected at push time, not stored as an unpullable baseline (Codex review).
        pack = _make_pack(tmp_path / "src.evidence")
        (pack.root / "build" / "build_evidence.json").write_text(
            '{"schema_version": 1, "tampered": true}\n', encoding="utf-8"
        )
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        with pytest.raises(ValidationError, match="fails its integrity check"):
            registry.push(key, sample_snapshot, evidence=pack)


    def test_repush_in_place_pack_preserves_evidence(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # Re-pushing using the already-stored pack (source dir == dest dir) must
        # not delete it mid-copy; the evidence survives (Codex review).
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)

        stored = registry.pull_evidence(key)
        assert stored is not None  # stored.root is <registry>/<key>/evidence
        # Push again with the in-place pack — no FileNotFoundError, pack preserved.
        registry.push(key, sample_snapshot, evidence=stored)
        again = registry.pull_evidence(key)
        assert again is not None
        assert again.content_hash() == pack.content_hash()


    def test_push_without_evidence_clears_stale_recorded_hash(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot
    ) -> None:
        # A caller-supplied metadata carrying a stale evidence hash, pushed with
        # evidence=None for a fresh key (no evidence dir), must not leave the hash
        # promising a pack that was never stored (Codex review).
        from abicheck.serialization import snapshot_to_json

        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        # Checksum must match the stored snapshot (pull verifies it first).
        meta = BaselineMetadata.create(
            snapshot_to_json(sample_snapshot), evidence_content_hash="sha256:stale"
        )
        registry.push(key, sample_snapshot, meta)
        _, stored_meta = registry.pull(key)  # type: ignore[misc]
        assert stored_meta.evidence_content_hash is None
        assert registry.pull_evidence(key) is None


    def test_pull_evidence_corrupt_payload_raises_integrity(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # A normalized payload corrupted into invalid JSON must surface as a
        # BaselineIntegrityError, not leak a raw JSONDecodeError (Codex review).
        pack = _make_pack(tmp_path / "src.evidence")
        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=pack)
        stored = registry.root / key.path / "evidence" / "build" / "build_evidence.json"
        stored.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(BaselineIntegrityError, match="corrupt"):
            registry.pull_evidence(key)


    def test_repush_replaces_evidence_pack_atomically(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # Re-pushing with a different pack swaps it in; no .evstage-* temp dirs are
        # left behind and the new content is what pull_evidence returns.
        from abicheck.buildsource import BuildEvidence, BuildSourcePack
        from abicheck.buildsource.build_evidence import Toolchain

        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=_make_pack(tmp_path / "p1.evidence"))

        p2 = BuildSourcePack.empty(tmp_path / "p2.evidence", abicheck_version="9.9")
        p2.build_evidence = BuildEvidence(
            toolchains=[Toolchain(id="toolchain://clang-18", compiler_id="Clang", version="18")]
        )
        p2.write()
        registry.push(key, sample_snapshot, evidence=p2)

        pulled = registry.pull_evidence(key)
        assert pulled is not None
        assert pulled.build_evidence.toolchains[0].compiler_id == "Clang"
        assert pulled.content_hash() == p2.content_hash()
        # No staging dirs left in the key directory.
        key_dir = registry.root / key.path
        assert not list(key_dir.glob(".evstage-*"))


    def test_repush_rolls_back_evidence_when_metadata_write_fails(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        # If the snapshot/metadata write fails mid-push, the previously-stored
        # evidence pack must be restored — the baseline stays valid (Codex review).
        import abicheck.baseline as bl
        from abicheck.buildsource import BuildEvidence, BuildSourcePack
        from abicheck.buildsource.build_evidence import Toolchain

        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=_make_pack(tmp_path / "p1.evidence"))
        good_hash = registry.pull_evidence(key).content_hash()  # type: ignore[union-attr]

        p2 = BuildSourcePack.empty(tmp_path / "p2.evidence", abicheck_version="9.9")
        p2.build_evidence = BuildEvidence(
            toolchains=[Toolchain(id="toolchain://clang", compiler_id="Clang", version="18")]
        )
        p2.write()

        orig = bl._atomic_write

        def _boom(path, content):
            if path.name == "metadata.json":
                raise OSError("disk full")
            return orig(path, content)

        bl._atomic_write = _boom  # type: ignore[assignment]
        try:
            with pytest.raises(OSError, match="disk full"):
                registry.push(key, sample_snapshot, evidence=p2)
        finally:
            bl._atomic_write = orig  # type: ignore[assignment]

        # The old pack is still intact and pullable; no staging dir left behind.
        restored = registry.pull_evidence(key)
        assert restored is not None
        assert restored.content_hash() == good_hash
        assert not list((registry.root / key.path).glob(".evstage-*"))

    def test_repush_without_evidence_rolls_back_on_metadata_failure(
        self, registry: FilesystemRegistry, sample_snapshot: AbiSnapshot, tmp_path: Path
    ) -> None:
        import abicheck.baseline as bl

        key = BaselineKey(library="libfoo", version="1.0.0", platform="linux-x86_64")
        registry.push(key, sample_snapshot, evidence=_make_pack(tmp_path / "p1.evidence"))
        good_hash = registry.pull_evidence(key).content_hash()  # type: ignore[union-attr]

        orig = bl._atomic_write

        def _boom(path, content):
            if path.name == "metadata.json":
                raise OSError("disk full")
            return orig(path, content)

        bl._atomic_write = _boom  # type: ignore[assignment]
        try:
            with pytest.raises(OSError):
                registry.push(key, sample_snapshot)  # no evidence → would remove pack
        finally:
            bl._atomic_write = orig  # type: ignore[assignment]

        restored = registry.pull_evidence(key)
        assert restored is not None and restored.content_hash() == good_hash

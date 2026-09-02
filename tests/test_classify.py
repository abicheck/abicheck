"""Unit tests for abicheck.classify — the file-classification pipeline.

These tests exercise classifiers in isolation and as an integrated pipeline,
covering all real-world false-positive patterns found in wave-2 wheel scans:
  - CycloneDX SBOMs  (pillow)
  - test data JSON   (scipy)
  - Jinja templates  (pandas)
  - Parquet files    (pyarrow — 'some' substring in filename)
"""

from __future__ import annotations

import re
from pathlib import Path

from abicheck.classify import (
    AbiJsonClassifier,
    BinaryExtensionClassifier,
    FallbackSniffClassifier,
    MagicByteClassifier,
    PerlDumpClassifier,
    is_supported_compare_input,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_abi_snapshot(path: Path) -> Path:
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
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


ELF_MAGIC = b"\x7fELF" + b"\x00" * 12  # minimal ELF header prefix


# ── BinaryExtensionClassifier ─────────────────────────────────────────────────


class TestBinaryExtensionClassifier:
    clf = BinaryExtensionClassifier()

    def test_so_plain(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "libfoo.so") is True

    def test_so_versioned(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "libfoo.so.1.2") is True

    def test_dll(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "foo.dll") is True

    def test_dylib(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "libfoo.dylib") is True

    def test_pyd(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "module.pyd") is True

    def test_so_substring_false_positive_parquet(self, tmp_path: Path) -> None:
        """'some' in filename must NOT trigger the .so check."""
        assert self.clf.accepts(tmp_path / "v0.7.1.some-named-index.parquet") is None

    def test_solution_json_not_so(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "solution.json") is None

    def test_json_passthrough(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "libfoo.json") is None

    def test_tpl_passthrough(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "html.tpl") is None

    def test_dll_txt_not_matched(self, tmp_path: Path) -> None:
        assert self.clf.accepts(tmp_path / "something.dll.txt") is None


# ── MagicByteClassifier ───────────────────────────────────────────────────────


class TestMagicByteClassifier:
    clf = MagicByteClassifier()

    def test_elf_magic(self, tmp_path: Path, monkeypatch) -> None:
        f = tmp_path / "noext"
        f.write_bytes(ELF_MAGIC)
        monkeypatch.setattr("abicheck.classify._detect_binary_format", lambda _p: "elf")
        assert self.clf.accepts(f) is True

    def test_plain_text_passthrough(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.txt"
        f.write_text("hello world")
        assert self.clf.accepts(f) is None

    def test_parquet_magic_passthrough(self, tmp_path: Path) -> None:
        """PAR1 is not a known binary ABI format → should pass through."""
        f = tmp_path / "data.parquet"
        f.write_bytes(b"PAR1" + b"\x00" * 100)
        assert self.clf.accepts(f) is None


# ── AbiJsonClassifier ─────────────────────────────────────────────────────────


class TestAbiJsonClassifier:
    clf = AbiJsonClassifier()

    def test_valid_snapshot_accepted(self, tmp_path: Path) -> None:
        p = _write_abi_snapshot(tmp_path / "libfoo.json")
        assert self.clf.accepts(p) is True

    def test_cyclonedx_sbom_rejected(self, tmp_path: Path) -> None:
        """CycloneDX SBOM has "type": "library" (value) but not "library": (key)."""
        p = tmp_path / "auditwheel.cdx.json"
        p.write_text(
            '{"bomFormat":"CycloneDX","specVersion":"1.4",'
            '"metadata":{"component":{"type":"library"}},"components":[]}'
        )
        assert self.clf.accepts(p) is False

    def test_data_json_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "studentized_range_mpmath_ref.json"
        p.write_text('{"data":[[1,2,3],[4,5,6]]}')
        assert self.clf.accepts(p) is False

    def test_solution_json_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "solution.json"
        p.write_text('{"answer":42}')
        assert self.clf.accepts(p) is False

    def test_non_json_ext_passthrough(self, tmp_path: Path) -> None:
        p = tmp_path / "libfoo.xml"
        p.write_text("<root/>")
        assert self.clf.accepts(p) is None

    def test_incidental_section_kind_property_rejected(self, tmp_path: Path) -> None:
        """Codex/CodeRabbit review, fresh evidence: an ordinary JSON file
        that happens to carry a "section_kind" property for an unrelated
        reason (not a real abicheck sectioned document -- no top-level
        "sections" object) must not be misclassified as an ABI snapshot.
        An earlier version of this fingerprint matched the too-generic
        nested "section_kind" key alone."""
        p = tmp_path / "release-notes.json"
        p.write_text('{"section_kind": "changelog", "entries": []}')
        assert self.clf.accepts(p) is False

    def test_incidental_top_level_sections_key_rejected(self, tmp_path: Path) -> None:
        """Codex/CodeRabbit review, fresh evidence (second round): a
        top-level "sections" key alone is still too generic -- an unrelated
        document (an OpenAPI/docs-config-shaped file) can carry its own
        top-level "sections" object with no "schema_version" immediately
        before it. Must not be misclassified as an ABI snapshot."""
        p = tmp_path / "openapi-like.json"
        p.write_text('{"title": "API", "sections": {"intro": {"text": "hi"}}}')
        assert self.clf.accepts(p) is False

    def test_real_sectioned_snapshot_accepted(self, tmp_path: Path) -> None:
        """ADR-063 Phase 8: a real sectioned document (the "sections" key
        genuinely at the top level, `to_sectioned_document`'s own shape)
        must still be recognized as an ABI snapshot."""
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
        from abicheck.storage.sectioned_document import to_sectioned_document

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        sectioned = to_sectioned_document(
            snapshot_to_dict(snap), max_known_schema_version=SCHEMA_VERSION
        )
        import json

        p = tmp_path / "libfoo.json"
        p.write_text(json.dumps(sectioned, indent=2))
        assert self.clf.accepts(p) is True

    def test_fingerprint_registry_extensible(self) -> None:
        """Ensure FINGERPRINTS is extensible and restored after mutation."""
        original_len = len(AbiJsonClassifier.FINGERPRINTS)
        new_fp = ("test-format", re.compile(r'"abi-corpus"\s*:'))
        AbiJsonClassifier.FINGERPRINTS.append(new_fp)
        try:
            assert len(AbiJsonClassifier.FINGERPRINTS) == original_len + 1
        finally:
            AbiJsonClassifier.FINGERPRINTS.pop()


# ── PerlDumpClassifier ────────────────────────────────────────────────────────


class TestPerlDumpClassifier:
    clf = PerlDumpClassifier()

    def test_non_perl_ext_passthrough(self, tmp_path: Path) -> None:
        p = tmp_path / "libfoo.json"
        p.write_text('{"x":1}')
        assert self.clf.accepts(p) is None

    def test_valid_perl_dump_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "libfoo.pl"
        p.write_text("$VAR1 = { 'library' => 'libfoo.so' };\n")
        assert self.clf.accepts(p) is True

    def test_pl_not_perl_dump_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "script.pl"
        p.write_text("#!/usr/bin/perl\nprint 'hello';\n")
        # Not a $VAR1 dump → rejected
        assert self.clf.accepts(p) is False


# ── FallbackSniffClassifier ───────────────────────────────────────────────────


class TestFallbackSniffClassifier:
    clf = FallbackSniffClassifier()

    def test_abi_json_with_custom_extension_accepted(self, tmp_path: Path) -> None:
        p = _write_abi_snapshot(tmp_path / "libfoo.abi")
        assert self.clf.accepts(p) is True

    def test_jinja_tpl_rejected(self, tmp_path: Path) -> None:
        """Jinja template starting with {# / {% has no ABI marker → rejected."""
        p = tmp_path / "html.tpl"
        p.write_text("{# Update docs too #}\n{% block content %}\n...\n{% endblock %}")
        assert self.clf.accepts(p) is False

    def test_latex_tpl_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "latex.tpl"
        p.write_text("{# latex template #}\n\\begin{document}\n\\end{document}")
        assert self.clf.accepts(p) is False

    def test_unrelated_text_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "notes.txt"
        p.write_text("some notes about the library")
        assert self.clf.accepts(p) is False


# ── Integrated pipeline (is_supported_compare_input) ─────────────────────────


class TestPipeline:
    def test_abi_snapshot_json_accepted(self, tmp_path: Path) -> None:
        p = _write_abi_snapshot(tmp_path / "libfoo.json")
        assert is_supported_compare_input(p) is True

    def test_cyclonedx_sbom_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "auditwheel.cdx.json"
        p.write_text(
            '{"bomFormat":"CycloneDX","metadata":{"component":{"type":"library"}}}'
        )
        assert is_supported_compare_input(p) is False

    def test_parquet_with_so_substring_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "v0.7.1.some-named-index.parquet"
        p.write_bytes(b"PAR1" + b"fake" * 100)
        assert is_supported_compare_input(p) is False

    def test_so_versioned_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "libfoo.so.1.2"
        p.write_bytes(b"binary content")
        assert is_supported_compare_input(p) is True

    def test_node_extension_elf_accepted_via_magic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        p = tmp_path / "addon.node"
        p.write_bytes(b"binary content")
        monkeypatch.setattr("abicheck.classify._detect_binary_format", lambda _p: "elf")
        assert is_supported_compare_input(p) is True

    def test_dll_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "foo.dll"
        p.write_bytes(b"MZ binary")
        assert is_supported_compare_input(p) is True

    def test_pyd_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "module.pyd"
        p.write_bytes(b"not-a-real-pe")
        assert is_supported_compare_input(p) is True

    def test_jinja_tpl_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "html.tpl"
        p.write_text("{# Update docs too #}\n{% block content %}..{% endblock %}")
        assert is_supported_compare_input(p) is False

    def test_data_json_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "test_data.json"
        p.write_text('{"rows":[[1,2,3]]}')
        assert is_supported_compare_input(p) is False

    def test_directory_rejected(self, tmp_path: Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        assert is_supported_compare_input(d) is False

    def test_nonexistent_rejected(self, tmp_path: Path) -> None:
        assert is_supported_compare_input(tmp_path / "ghost.so") is False


class TestCompressedAbiJsonClassifier:
    """ADR-059: gzip/zstd-compressed ABI snapshots in a compare-release directory."""

    def test_gzip_snapshot_accepted(self, tmp_path: Path) -> None:
        from abicheck.serialization import write_snapshot

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
        )
        p = tmp_path / "libfoo.abicheck.json.gz"
        write_snapshot(snap, p)
        assert is_supported_compare_input(p) is True

    def test_zstd_snapshot_accepted(self, tmp_path: Path) -> None:
        from abicheck.serialization import write_snapshot

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
        )
        p = tmp_path / "libfoo.abicheck.json.zst"
        write_snapshot(snap, p)
        assert is_supported_compare_input(p) is True

    def test_tar_zst_archive_not_misclassified_as_snapshot(
        self, tmp_path: Path
    ) -> None:
        import io
        import tarfile

        import zstandard

        inner = tmp_path / "inner.txt"
        inner.write_text("hi")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            tf.add(str(inner), arcname="inner.txt")
        cctx = zstandard.ZstdCompressor()
        p = tmp_path / "bundle.tar.zst"
        p.write_bytes(cctx.compress(buf.getvalue()))
        assert is_supported_compare_input(p) is False

    def test_gzip_non_snapshot_rejected(self, tmp_path: Path) -> None:
        import gzip

        p = tmp_path / "notes.txt.gz"
        p.write_bytes(gzip.compress(b'{"rows": [[1, 2, 3]]}'))
        assert is_supported_compare_input(p) is False

    def test_compressed_snapshot_under_neutral_dot_json_name_accepted(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #699: `dump --compression gzip|zstd -o out.json`
        explicitly permits writing compressed bytes under a plain `.json`
        name (resolve_write_compression() only rejects a *canonical*
        suffix that contradicts an explicit --compression, not a neutral
        one) -- but AbiJsonClassifier used to run first for any `.json`
        path, read the raw (still-compressed) bytes as text, never match
        the fingerprint regex, and return False rather than abstaining --
        ending the "first non-None wins" pipeline before
        CompressedAbiJsonClassifier ever got a chance to decode it. A
        compare-release directory scan would then silently skip this
        supported snapshot."""
        from abicheck.serialization import write_snapshot

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
        )
        for suffix, compression in ((".json", "gzip"), (".json", "zstd")):
            p = tmp_path / f"libfoo{suffix}"
            write_snapshot(snap, p, compression=compression)
            assert is_supported_compare_input(p) is True, (compression, p)

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

"""Unit tests for :mod:`abicheck.storage.bundle_archive` (G40) -- the
low-level, content-addressed zip-archive primitive.

These tests exercise the module on its own terms (raw bytes/dicts) since it
deliberately knows nothing about ``BundleFacts``/``AbiSnapshot`` -- see the
module's own docstring for why. The ``BundleFacts``-aware round-trip lives
in ``tests/test_bundle_facts.py``'s own archive-format tests, exercised
through ``serialization.save_bundle_facts``/``load_bundle_facts``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.bundle_archive import (
    DEFAULT_MAX_BLOB_BYTES,
    DEFAULT_MAX_MANIFEST_BYTES,
    MANIFEST_MEMBER,
    BundleArchiveReader,
    BundleArchiveWriter,
    content_hash,
)


class TestContentHash:
    def test_deterministic(self) -> None:
        assert content_hash(b"hello") == content_hash(b"hello")

    def test_distinguishes_different_content(self) -> None:
        assert content_hash(b"hello") != content_hash(b"world")


class TestBundleArchiveWriterReader:
    def test_round_trip_single_blob(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b'{"library": "libfoo.so"}'
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"libfoo.so": h}})

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            assert manifest["library_blobs"]["libfoo.so"] == h
            assert reader.read_blob(h) == payload

    def test_dedup_identical_payloads_share_one_blob_member(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b'{"shared": true}'
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(payload)
            h2 = writer.put_blob(payload)  # byte-identical -> same hash
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        assert h1 == h2
        with zipfile.ZipFile(path) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == 1

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            assert (
                manifest["library_blobs"]["a.so"] == manifest["library_blobs"]["b.so"]
            )

    def test_distinct_payloads_get_distinct_blob_members(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        assert h1 != h2
        with zipfile.ZipFile(path) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == 2

    def test_partial_load_reads_exactly_one_blob_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production-scale-shaped partial-load proof (AGENTS.md's
        "Third-party-boundary tests" convention): a real, multi-blob
        archive where reading one blob touches exactly that member's data,
        not the whole archive -- proving lazy access is real, not merely
        API-shaped."""
        path = tmp_path / "bundle.archive.zip"
        payloads = {
            f"lib{i}.so": f'{{"library": "lib{i}.so", "padding": "{"x" * 5000}"}}'.encode()
            for i in range(25)
        }
        hashes: dict[str, str] = {}
        with BundleArchiveWriter(path) as writer:
            for name, payload in payloads.items():
                hashes[name] = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": hashes})

        opened_members: list[str] = []
        real_open = zipfile.ZipFile.open

        def _tracking_open(self: zipfile.ZipFile, name: str, *a: object, **kw: object):  # type: ignore[no-untyped-def]
            opened_members.append(name if isinstance(name, str) else name.filename)
            return real_open(self, name, *a, **kw)

        monkeypatch.setattr(zipfile.ZipFile, "open", _tracking_open)

        with BundleArchiveReader.open(path) as reader:
            target_name = "lib7.so"
            data = reader.read_blob(hashes[target_name])
            assert data == payloads[target_name]

        blob_opens = [m for m in opened_members if m.startswith("blobs/")]
        assert blob_opens == [f"blobs/{hashes[target_name]}.json.zst"]

    def test_read_blob_for_unreferenced_hash_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            writer.write_manifest({"library_blobs": {}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="no corresponding archive member"):
                reader.read_blob("0" * 64)

    def test_write_manifest_twice_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        writer.write_manifest({})
        with pytest.raises(SnapshotError, match="write_manifest"):
            writer.write_manifest({})
        writer.close()

    def test_close_without_manifest_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        writer.put_blob(b"x")
        with pytest.raises(SnapshotError, match="no manifest.json"):
            writer.close()

    def test_context_manager_propagates_exception_without_requiring_manifest(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with pytest.raises(ValueError, match="boom"):
            with BundleArchiveWriter(path) as writer:
                writer.put_blob(b"x")
                raise ValueError("boom")

    def test_read_blob_enforces_max_decoded_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b"y" * (1024 * 64)
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=1024)
            # Unbounded (default cap) read still succeeds for the same blob.
            assert reader.read_blob(h) == payload

    def test_default_max_blob_bytes_is_one_gib(self) -> None:
        assert DEFAULT_MAX_BLOB_BYTES == 1024 * 1024 * 1024

    def test_default_max_manifest_bytes_is_64_mib(self) -> None:
        assert DEFAULT_MAX_MANIFEST_BYTES == 64 * 1024 * 1024

    def test_read_blob_rejects_content_that_does_not_match_its_hash(
        self, tmp_path: Path
    ) -> None:
        """A blob member's name is not itself verified content -- a
        corrupted or hand-assembled archive storing the wrong bytes under a
        given hash's member name must be rejected, not handed back silently
        (Codex review)."""
        import zstandard

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"real": true}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        # Replace the blob member's content in place -- same member name
        # (still keyed by the *original* content's hash), different bytes.
        member = f"blobs/{h}.json.zst"
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            other = {n: zf.read(n) for n in names if n != member}
        wrong_payload = zstandard.ZstdCompressor().compress(b'{"corrupted": true}')
        with zipfile.ZipFile(path, mode="w") as zf:
            for n, data in other.items():
                zf.writestr(n, data)
            zf.writestr(member, wrong_payload)

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="not the .* its own member name"):
                reader.read_blob(h)


class TestBundleArchiveWriterAtomicity:
    """Codex review: the original revision opened *path* directly with
    ``mode="w"``, which truncates any pre-existing archive immediately --
    a later error would then leave a partial file where a valid prior
    archive used to be. Writes now go to a temp file, promoted only on a
    fully successful close()."""

    def test_close_without_manifest_leaves_existing_destination_untouched(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        original_bytes = path.read_bytes()

        writer = BundleArchiveWriter(path)
        writer.put_blob(b"x")
        with pytest.raises(SnapshotError, match="no manifest.json"):
            writer.close()

        assert path.read_bytes() == original_bytes
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_exception_mid_write_leaves_existing_destination_untouched(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        original_bytes = path.read_bytes()

        with pytest.raises(ValueError, match="boom"):
            with BundleArchiveWriter(path) as writer:
                writer.put_blob(b"y")
                raise ValueError("boom")

        assert path.read_bytes() == original_bytes
        assert list(tmp_path.glob("*.tmp-*")) == []

    def test_successful_write_leaves_no_temp_file_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        assert list(tmp_path.glob("*.tmp-*")) == []
        assert path.exists()

    def test_writing_through_a_symlink_updates_the_target_not_the_link(
        self, tmp_path: Path
    ) -> None:
        """A bare os.replace(tmp, path) on a symlink destination would
        swap the symlink's own directory entry for a regular file,
        destroying the link -- every other reader still following it
        would then see nothing written here (Codex review)."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        target = real_dir / "bundle.archive.zip"
        link = tmp_path / "bundle.archive.link.zip"
        link.symlink_to(target)

        with BundleArchiveWriter(link) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert link.is_symlink(), "the symlink itself must survive the write"
        assert link.resolve() == target.resolve()
        assert target.exists()
        with BundleArchiveReader.open(link) as reader:
            manifest = reader.read_manifest()
            assert manifest["library_blobs"]["a.so"] == h
        # No stray temp file under either the link's or the target's dir.
        assert list(tmp_path.glob("*.tmp-*")) == []
        assert list(real_dir.glob("*.tmp-*")) == []

    def test_creates_missing_destination_parent_directory(self, tmp_path: Path) -> None:
        """save_bundle_facts(..., format="archive") must behave like the
        format="json" path already does (parent dirs auto-created via
        snapshot_io.write_snapshot_text), not raise FileNotFoundError on a
        first write below a not-yet-existing directory (Codex review)."""
        path = tmp_path / "does" / "not" / "exist" / "bundle.archive.zip"
        assert not path.parent.exists()
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

    @pytest.mark.skipif(sys.platform == "win32", reason="hard links behave differently on Windows")
    def test_rejects_a_hard_linked_destination_before_any_write(self, tmp_path: Path) -> None:
        """Replacing just this one directory entry would silently
        desynchronize every other hard link from it, leaving them pointing
        at stale content while this call reports success (Codex review)."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        other_link = tmp_path / "other_link.zip"
        os.link(path, other_link)
        original_bytes = path.read_bytes()

        with pytest.raises(SnapshotError, match="hard link"):
            BundleArchiveWriter(path)

        # Neither alias was touched, and no temp file was left behind.
        assert path.read_bytes() == original_bytes
        assert other_link.read_bytes() == original_bytes
        assert list(tmp_path.glob("*.tmp-*")) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode semantics")
    def test_preserves_the_existing_destinations_file_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o600)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.exists()


class TestBundleArchiveReaderRejectsNonStoredMembers:
    """Codex review: every member BundleArchiveWriter produces is
    ZIP_STORED deliberately -- a crafted archive using ZIP_DEFLATED for a
    member could otherwise expand to an arbitrary in-memory allocation via
    plain ZipExtFile.read(), before read_blob's own zstd decoded-size
    guard ever runs. Both read_manifest and read_blob must reject it."""

    def test_read_manifest_rejects_a_deflated_manifest_member(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {}}))

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="ZIP_STORED"):
                reader.read_manifest()

    def test_read_blob_rejects_a_deflated_blob_member(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b'{"a": 1}'
        h = content_hash(payload)
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(
                f"blobs/{h}.json.zst", payload, compress_type=zipfile.ZIP_DEFLATED
            )

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="ZIP_STORED"):
                reader.read_blob(h)

    def test_read_stored_member_rejects_a_member_exceeding_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """Rejecting deflate alone is not a size bound -- a still-ZIP_STORED
        member can simply claim (and actually contain) an oversized payload.
        Exercised directly on the shared primitive (rather than through
        read_manifest()'s own much larger, production-sized
        DEFAULT_MAX_MANIFEST_BYTES default) with a small max_bytes, so the
        test doesn't need to actually write 64 MiB (Codex review)."""
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, b"x" * 2048)

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader._read_stored_member(MANIFEST_MEMBER, max_bytes=1024)
            # Unbounded (large enough) read still succeeds for the same member.
            assert reader._read_stored_member(MANIFEST_MEMBER, max_bytes=4096) == b"x" * 2048

    def test_read_blob_rejects_a_stored_member_exceeding_max_decoded_bytes(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        # A payload whose zstd-*compressed* form is still large relative to
        # a small cap -- incompressible random-looking bytes, unlike the
        # existing test's highly-repetitive payload, so this exercises the
        # outer ZIP_STORED size gate specifically, not the inner zstd
        # decoded-size gate test_read_blob_enforces_max_decoded_bytes
        # already covers.
        payload = os.urandom(4096)
        h = content_hash(payload)
        with BundleArchiveWriter(path) as writer:
            written_hash = writer.put_blob(payload)
            assert written_hash == h
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=16)

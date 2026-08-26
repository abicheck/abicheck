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

Exercises the module on its own terms (raw bytes/dicts), since it
deliberately knows nothing about ``BundleFacts``/``AbiSnapshot``. The
``BundleFacts``-aware round-trip lives in
``tests/test_bundle_facts_archive.py``."""

from __future__ import annotations

import errno
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
        "Third-party-boundary tests" convention): reading one blob from a
        multi-blob archive touches exactly that member."""
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

    def test_write_manifest_rejects_a_manifest_over_the_reader_own_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The primitive `write_manifest()` itself must reject an oversized
        manifest, not only `bundle_facts.write_bundle_facts_archive`'s own
        higher-level preflight -- a caller using this public primitive
        directly bypasses that check entirely, and `read_manifest()`
        rejects anything over this same limit unconditionally (Codex)."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 100)
        path = tmp_path / "bundle.archive.zip"
        with pytest.raises(SnapshotError, match="exceeding the 100 byte"):
            with BundleArchiveWriter(path) as writer:
                writer.write_manifest({"library_blobs": {}, "padding": "x" * 200})
        assert not path.exists()

    def test_read_blob_rejects_content_that_does_not_match_its_hash(
        self, tmp_path: Path
    ) -> None:
        """A blob member's name is not itself verified content -- storing
        the wrong bytes under a hash's member name must be rejected, not
        handed back silently (Codex review)."""
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
    """The original revision opened *path* with ``mode="w"``, truncating
    any pre-existing archive immediately -- writes go to a temp file now,
    promoted only on a fully successful close() (Codex review)."""

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
        destroy the link instead of updating its target (Codex review)."""
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

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_cyclic_symlink_destination_raises_instead_of_being_overwritten(
        self, tmp_path: Path
    ) -> None:
        """A bare `except OSError:` around the target stat() swallowed
        ELOOP the same as genuine absence -- only real absence may be
        treated that way (Codex review, fresh evidence)."""
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.symlink_to(b)
        b.symlink_to(a)  # a <-> b cycle

        with pytest.raises(OSError):
            BundleArchiveWriter(a)

        # Neither link in the cycle was touched.
        assert a.is_symlink()
        assert b.is_symlink()

    def test_creates_missing_destination_parent_directory(self, tmp_path: Path) -> None:
        """Must behave like format="json" (parent dirs auto-created), not
        raise FileNotFoundError on a first write below a missing
        directory (Codex review)."""
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

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_rejects_a_non_regular_destination_before_any_write(
        self, tmp_path: Path
    ) -> None:
        """A pre-existing FIFO/socket/device destination is rejected
        outright, not silently replaced by os.replace() (Codex review)."""
        path = tmp_path / "bundle.archive.zip"
        os.mkfifo(path)

        with pytest.raises(SnapshotError, match="not a regular file"):
            BundleArchiveWriter(path)

        assert stat.S_ISFIFO(path.stat().st_mode)
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

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode semantics")
    def test_restores_mode_after_ownership_not_before(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """chown() can silently clear a setuid/setgid bit -- restoring
        mode before chown let those bits be stripped (Codex review)."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o6755)

        real_fchmod = os.fchmod
        real_fchown = os.fchown

        def _clearing_fchown(fd: int, uid: int, gid: int) -> None:
            real_fchown(fd, uid, gid)
            current = stat.S_IMODE(os.fstat(fd).st_mode)
            real_fchmod(fd, current & 0o777)  # simulate the kernel clearing setuid/setgid

        monkeypatch.setattr(os, "fchown", _clearing_fchown)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert stat.S_IMODE(path.stat().st_mode) == 0o6755

    def test_close_failure_removes_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure anywhere in close()'s post-zf.close() block (fsync,
        chown, chmod, replace) must not leave the temp file behind --
        repeated failures would otherwise accumulate temp files (Codex)."""
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')
        writer.write_manifest({"library_blobs": {"a.so": h}})

        def _failing_replace(*_args: object, **_kw: object) -> None:
            raise OSError(errno.ENOSPC, "simulated disk full")

        monkeypatch.setattr(os, "replace", _failing_replace)
        with pytest.raises(OSError, match="simulated disk full"):
            writer.close()
        assert list(tmp_path.glob("*.tmp-*")) == []
        assert not path.exists()

    def test_close_failure_removes_temp_file_even_when_wrapper_close_also_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If _tmp_file.close() itself raises while handling an earlier
        failure, the temp file must still be unlinked -- a plain sibling
        statement after a raising close() would never reach it (Codex)."""
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')
        writer.write_manifest({"library_blobs": {"a.so": h}})

        def _failing_fsync() -> None:
            raise OSError(errno.EIO, "simulated fsync failure")

        # A real close() failure still releases the underlying OS fd
        # (confirmed empirically: CPython's buffered writer marks itself
        # closed even when close() itself raises, e.g. a failing flush).
        # A double that skips the real close() entirely leaves a
        # genuinely open handle, harmless on POSIX but making the
        # cleanup `unlink()` below fail with a real "file in use" error
        # on Windows -- masking the simulated failure (Codex, Windows CI).
        real_close = writer._tmp_file.close

        def _failing_close() -> None:
            real_close()
            raise OSError(errno.ENOSPC, "simulated close failure")

        monkeypatch.setattr(writer, "_fsync_tmp_file", _failing_fsync)
        monkeypatch.setattr(writer._tmp_file, "close", _failing_close)

        with pytest.raises(OSError, match="simulated close failure"):
            writer.close()
        assert list(tmp_path.glob("*.tmp-*")) == []
        assert not path.exists()


class TestReadManifestTranslatesRecursionError:
    """A small manifest.json can still nest deeply enough to blow
    Python's json decoder's own recursion budget (a few thousand levels
    of ``[[[...]]]``) -- `RecursionError` is a distinct exception class
    caught separately, else it surfaces raw. On Python 3.14 this specific
    payload no longer raises RecursionError at all, so `read_manifest()`'s
    own container-node/nesting-depth pre-scan enforces it instead."""

    def test_a_deeply_nested_manifest_raises_snapshot_error_not_recursion_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        deeply_nested = ("[" * 10_000) + ("]" * 10_000)
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(
                zipfile.ZipInfo(MANIFEST_MEMBER),
                deeply_nested,
                compress_type=zipfile.ZIP_STORED,
            )

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="too deeply nested"):
                reader.read_manifest()


class TestReadManifestTranslatesIntegerDigitLimit:
    """Python 3.11+'s own integer-string-conversion digit limit
    (`sys.get_int_max_str_digits()`, 4300 by default) makes `json.loads()`
    raise a bare `ValueError` for an integer literal with more digits than
    that -- a different exception than `json.JSONDecodeError` (a
    `ValueError` subclass, but not raised through it here), so it
    bypassed both handlers and escaped raw instead of `SnapshotError`."""

    def test_an_oversized_integer_literal_raises_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        huge_int_manifest = '{"library_blobs": {}, "n": ' + ("9" * 5000) + "}"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(
                zipfile.ZipInfo(MANIFEST_MEMBER),
                huge_int_manifest,
                compress_type=zipfile.ZIP_STORED,
            )

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="not valid JSON"):
                reader.read_manifest()


class TestBundleArchiveReaderRejectsNonStoredMembers:
    """Every member BundleArchiveWriter produces is ZIP_STORED
    deliberately -- ZIP_DEFLATED could expand to an arbitrary in-memory
    allocation before read_blob's zstd guard ever runs (Codex)."""

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
        """Rejecting deflate alone is not a size bound -- a ZIP_STORED
        member can still claim an oversized payload (Codex)."""
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


class TestBundleArchiveDeterminism:
    """writestr(name, data) with a bare name stamps time.localtime() at
    write time -- content-identical facts on different days must still
    produce byte-identical archives (Codex review)."""

    def _write(self, path: Path) -> None:
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

    def test_identical_content_written_twice_produces_identical_bytes(
        self, tmp_path: Path
    ) -> None:
        first = tmp_path / "first.zip"
        second = tmp_path / "second.zip"
        self._write(first)
        self._write(second)
        assert first.read_bytes() == second.read_bytes()

    def test_member_timestamps_are_pinned_to_the_zip_epoch(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        self._write(path)
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0)


class TestBundleArchiveWriterDurability:
    """Codex review: ZipFile.close() only flushes to the OS buffer cache,
    not to storage -- close() must fsync the completed temp file before
    os.replace() and the parent directory afterward."""

    def test_close_fsyncs_the_temp_file_before_replacing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        fsynced_fds: list[int] = []
        real_fsync = os.fsync

        def _tracking_fsync(fd: int) -> None:
            fsynced_fds.append(fd)
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", _tracking_fsync)
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        # At least one fsync for the temp file's own data, and (POSIX) one
        # for the parent directory entry -- both best-effort in the narrow
        # "unsupported" sense only, both real here since tmp_path is a real
        # filesystem.
        assert len(fsynced_fds) >= 1
        assert path.exists()

    def test_close_propagates_a_real_fsync_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A genuine storage failure (not an unsupported-fs errno) must
        abort the write rather than silently proceeding to os.replace()
        with unconfirmed data."""
        path = tmp_path / "bundle.archive.zip"

        def _failing_fsync(fd: int) -> None:
            raise OSError(errno.EIO, "simulated storage failure")

        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')
        writer.write_manifest({"library_blobs": {"a.so": h}})
        monkeypatch.setattr(os, "fsync", _failing_fsync)

        with pytest.raises(OSError, match="simulated storage failure"):
            writer.close()
        assert not path.exists()


class TestBundleArchiveReaderWrapsThirdPartyExceptions:
    """Every deliberate failure raises SnapshotError -- a truncated or
    hand-assembled archive must not surface a raw traceback (Codex)."""

    def test_opening_a_corrupt_zip_raises_snapshot_error(self, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.zip"
        # Passes sniff_bundle_archive_format()'s 4-byte magic check but is
        # not a valid zip beyond that.
        path.write_bytes(b"PK\x03\x04" + b"not a real zip" * 4)

        with pytest.raises(SnapshotError, match="not a valid bundle archive"):
            BundleArchiveReader.open(path)

    def test_a_non_zstd_blob_payload_raises_snapshot_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        # Overwrite the blob member's bytes with something that is not a
        # valid zstd frame at all (still ZIP_STORED, still under the
        # original hash's member name).
        member = f"blobs/{h}.json.zst"
        with zipfile.ZipFile(path) as zf:
            other = {n: zf.read(n) for n in zf.namelist() if n != member}
        with zipfile.ZipFile(path, mode="w") as zf:
            for n, data in other.items():
                zf.writestr(n, data)
            zf.writestr(member, b"not a zstd frame at all")

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="failed to decompress"):
                reader.read_blob(h)

    def test_an_encrypted_member_raises_snapshot_error(self, tmp_path: Path) -> None:
        """A member with the zip 'encrypted' bit set makes `ZipFile.open()`
        raise a bare RuntimeError, not the `BadZipFile` the CRC-mismatch
        handling translates -- must be rejected explicitly (Codex)."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        member = f"blobs/{h}.json.zst"
        with BundleArchiveReader.open(path) as reader:
            # Flip the "encrypted" bit on the already-parsed ZipInfo --
            # avoids hand-crafting real zip encryption (which stdlib
            # zipfile can't write anyway).
            info = reader._zf.getinfo(member)
            info.flag_bits |= 0x1
            with pytest.raises(SnapshotError, match="is encrypted"):
                reader.read_blob(h)

    @pytest.mark.parametrize("flag_bit", [0x20, 0x40])
    def test_an_unsupported_flag_bit_raises_snapshot_error(
        self, tmp_path: Path, flag_bit: int
    ) -> None:
        """Flag bit 5 (compressed-patched data) or 6 (strong encryption)
        makes `ZipFile.open()` raise a bare `NotImplementedError` -- same
        class of gap as the encrypted-bit check above, and the same
        proactive-check fix (Codex review, fresh evidence)."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        member = f"blobs/{h}.json.zst"
        with BundleArchiveReader.open(path) as reader:
            info = reader._zf.getinfo(member)
            info.flag_bits |= flag_bit
            with pytest.raises(SnapshotError, match="unsupported general-purpose flag"):
                reader.read_blob(h)

    def test_a_crc_mismatched_member_raises_snapshot_error(self, tmp_path: Path) -> None:
        """ZipExtFile's CRC-32 mismatch raises raw BadZipFile -- must be
        wrapped as SnapshotError like every other failure (Codex)."""
        import struct

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        member = f"blobs/{h}.json.zst"
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo(member)
            offset = info.header_offset

        raw = bytearray(path.read_bytes())
        # Local file header is a fixed 30 bytes, then the filename, then
        # any extra field -- corrupt one byte of the *stored data* that
        # follows without touching the header's own CRC-32 field, so the
        # header's recorded CRC no longer matches the (now-different)
        # bytes it is checked against on read.
        (
            _sig,
            _ver,
            _flags,
            _comp,
            _mtime,
            _mdate,
            _crc,
            _csize,
            _usize,
            fname_len,
            extra_len,
        ) = struct.unpack_from("<4sHHHHHLLLHH", raw, offset)
        data_start = offset + 30 + fname_len + extra_len
        raw[data_start] ^= 0xFF
        path.write_bytes(bytes(raw))

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="CRC-32"):
                reader.read_blob(h)

    def test_a_transient_io_failure_while_streaming_a_member_raises_snapshot_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read failure partway through streaming a member (e.g. EIO on
        a network filesystem) raises a raw `OSError` from `ZipExtFile.
        read()` -- only `BadZipFile` was translated, so this escaped this
        module's `SnapshotError` contract (Codex review, fresh evidence)."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            real_open = reader._zf.open

            class _FailingRead:
                def __init__(self, fp: object) -> None:
                    self._fp = fp

                def read(self, n: int) -> bytes:
                    raise OSError("simulated transient I/O failure")

                def __enter__(self) -> _FailingRead:
                    return self

                def __exit__(self, *exc_info: object) -> None:
                    self._fp.__exit__(*exc_info)  # type: ignore[attr-defined]

            def _tracking_open(name: str) -> object:
                return _FailingRead(real_open(name))

            monkeypatch.setattr(reader._zf, "open", _tracking_open)
            with pytest.raises(SnapshotError, match="could not be read"):
                reader.read_blob(h)


class TestBundleArchiveReadBlobCompressedSlack:
    """Codex review: zstd frame/block overhead can make an incompressible
    payload's compressed form slightly larger than its decoded size --
    the outer, still-compressed read must not reject a payload that
    legitimately satisfies the documented decoded-size contract."""

    def test_incompressible_payload_at_the_decoded_cap_still_succeeds(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = os.urandom(4096)  # incompressible -- compressed form is larger
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            assert reader.read_blob(h, max_decoded_bytes=4096) == payload

    def test_a_genuinely_oversized_blob_is_still_rejected(self, tmp_path: Path) -> None:
        """The slack margin only accommodates real zstd frame overhead --
        it must not open a loophole for an actually oversized payload."""
        path = tmp_path / "bundle.archive.zip"
        payload = os.urandom(4096)
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=16)


class TestSniffBundleArchiveFormatNonRegularSource:
    """A real bundle archive can never be delivered via a FIFO/pipe
    (ZipFile needs to seek to its end) -- sniffing must never consume
    bytes from a non-regular source (Codex review)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_sniff_never_reads_a_fifo_source(self, tmp_path: Path) -> None:
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        fifo = tmp_path / "input.fifo"
        os.mkfifo(fifo)

        # A regular-file stat check alone must be enough to answer "json"
        # -- opening/reading the FIFO here would block forever with no
        # writer, so a hang (rather than a wrong answer) is exactly what
        # a regression in this guard would look like.
        assert sniff_bundle_archive_format(fifo) == "json"

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_end_to_end_load_through_a_fifo_with_format_auto(
        self, tmp_path: Path
    ) -> None:
        """The real regression this guards against: format="auto" losing
        the sniff's consumed bytes on the plain-JSON path's own, later,
        separate open of the same (non-rewindable) source."""
        import threading

        from abicheck.serialization import load_bundle_facts

        fifo = tmp_path / "input.fifo"
        os.mkfifo(fifo)
        payload = b'{"schema_version": 1, "per_library_snapshots": {}}'

        def _writer() -> None:
            with open(fifo, "wb") as f:
                f.write(payload)

        t = threading.Thread(target=_writer)
        t.start()
        try:
            facts = load_bundle_facts(fifo)  # format="auto" default
        finally:
            t.join()
        assert facts.per_library_snapshots == {}

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_open_regular_file_for_format_sniff_does_not_block_on_a_fifo(
        self, tmp_path: Path
    ) -> None:
        """The TOCTOU fix (Codex review, fresh evidence): a plain path
        `stat()` followed by a separate `open()` left a window where a
        concurrent replacement could swap a regular file for a FIFO,
        whose `open()` then blocks until a writer connects. Now opened
        `O_NONBLOCK` and classified via `fstat()` on that same fd, so
        even a bare FIFO with *no* writer at all must never hang -- run
        in a thread with a bounded join() since a hang here can't
        otherwise be distinguished from "still running"."""
        import threading

        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        fifo = tmp_path / "no_writer.fifo"
        os.mkfifo(fifo)

        result: list[tuple[object, str]] = []

        def _call() -> None:
            result.append(open_regular_file_for_format_sniff(fifo))

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "open_regular_file_for_format_sniff blocked on a FIFO"
        assert result == [(None, "json")]


class TestSniffBundleArchiveFormatUsesTheSameSingleOpenClassification:
    """`sniff_bundle_archive_format()` previously did its own, separate
    `Path.stat()` then a separate `open()` -- a two-inode TOCTOU window
    where a concurrent replacement (a regular file swapped for a FIFO)
    between the two could make the second `open()` block indefinitely,
    even though `open_regular_file_for_format_sniff()`'s own identical
    class of race had already been fixed. Now delegates to that helper's
    single O_NONBLOCK-open-then-fstat() sequence instead (Codex)."""

    def test_sniff_never_calls_a_separate_stat_before_opening(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structural proof, not a timing-dependent race reproduction: a
        bare `Path.stat()` call is exactly the first half of the
        vulnerable two-step sequence, so asserting it's never reached at
        all closes the TOCTOU window by construction rather than merely
        narrowing it. Confirmed to fail against the pre-fix
        `p.stat()`-then-`open()` implementation."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        def _fail_if_called(*a: object, **kw: object) -> object:
            raise AssertionError(
                "sniff_bundle_archive_format() must not call a separate "
                "Path.stat() -- it should classify via the same single "
                "opened descriptor open_regular_file_for_format_sniff() "
                "uses, with no preceding stat() at all"
            )

        monkeypatch.setattr(bundle_archive_module.Path, "stat", _fail_if_called)
        path = tmp_path / "bundle.archive.zip"
        path.write_bytes(b"PK\x03\x04junk")
        assert bundle_archive_module.sniff_bundle_archive_format(path) == "archive"

    def test_classification_is_unaffected_for_json_and_archive_sources(
        self, tmp_path: Path
    ) -> None:
        """Positive control: the delegation must not change the answer
        for either real classification, only the mechanism."""
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        archive_path = tmp_path / "bundle.archive.zip"
        archive_path.write_bytes(b"PK\x03\x04junk")
        assert sniff_bundle_archive_format(archive_path) == "archive"

        json_path = tmp_path / "bundle.json"
        json_path.write_bytes(b'{"schema_version": 1}')
        assert sniff_bundle_archive_format(json_path) == "json"

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_still_does_not_hang_on_a_bare_fifo(self, tmp_path: Path) -> None:
        """Sibling to `open_regular_file_for_format_sniff`'s own identical
        test -- run in a thread with a bounded join() since a hang here
        can't otherwise be distinguished from "still running"."""
        import threading

        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        fifo = tmp_path / "no_writer.fifo"
        os.mkfifo(fifo)

        result: list[str] = []

        def _call() -> None:
            result.append(sniff_bundle_archive_format(fifo))

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "sniff_bundle_archive_format blocked on a FIFO"
        assert result == ["json"]


class TestSniffDetectsAPrefixedArchive:
    """`BundleArchiveReader.open()`/`reject_absurd_central_directory()`
    already handle a concatenated/self-extracting archive correctly (an
    arbitrary prefix before the zip data) -- but the byte-0-only prefix
    check `sniff_bundle_archive_format()`/`open_regular_file_for_format_
    sniff()` used misclassified one as "json", so `load_bundle_facts()`'s
    documented default (`format="auto"`) failed on a path the identical
    call with `format="archive"` opens fine (Codex)."""

    def _prefixed_archive(self, tmp_path: Path) -> Path:
        real = tmp_path / "real.bundlefacts.archive.zip"
        with BundleArchiveWriter(real) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        prefixed = tmp_path / "prefixed.zip"
        prefixed.write_bytes(b"#!/bin/sh\nexit 0\n" + real.read_bytes())
        return prefixed

    def _prefixed_bundle_facts_archive(self, tmp_path: Path) -> Path:
        """A genuinely valid (if empty) `BundleFacts` archive -- unlike
        `_prefixed_archive()`'s hand-rolled low-level manifest, this
        round-trips through the real `save_bundle_facts()` glue so
        `load_bundle_facts()` can actually parse what it reads back."""
        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.serialization import save_bundle_facts

        real = tmp_path / "real-facts.bundlefacts.archive.zip"
        save_bundle_facts(capture_bundle_facts({}), real, format="archive")
        prefixed = tmp_path / "prefixed-facts.zip"
        prefixed.write_bytes(b"#!/bin/sh\nexit 0\n" + real.read_bytes())
        return prefixed

    def test_sniff_bundle_archive_format_classifies_it_as_archive(
        self, tmp_path: Path
    ) -> None:
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        path = self._prefixed_archive(tmp_path)
        assert not path.read_bytes().startswith(b"PK")
        assert sniff_bundle_archive_format(path) == "archive"

    def test_open_regular_file_for_format_sniff_classifies_it_as_archive(
        self, tmp_path: Path
    ) -> None:
        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        path = self._prefixed_archive(tmp_path)
        fp, fmt = open_regular_file_for_format_sniff(path)
        try:
            assert fmt == "archive"
        finally:
            if fp is not None:
                fp.close()

    def test_load_bundle_facts_default_auto_format_opens_it(self, tmp_path: Path) -> None:
        """The real regression this guards against: `load_bundle_facts()`'s
        documented default (`format="auto"`) must succeed on a path the
        identical call with `format="archive"` already opens fine."""
        from abicheck.serialization import load_bundle_facts

        path = self._prefixed_bundle_facts_archive(tmp_path)
        facts = load_bundle_facts(path)  # format="auto" default
        assert facts.per_library_snapshots == {}

    def test_a_genuine_json_file_is_still_classified_as_json(self, tmp_path: Path) -> None:
        """Positive control: an ordinary JSON file, with no EOCD signature
        anywhere in its tail, must still classify as "json" -- the
        tail-scan fallback only widens what's *also* recognized as an
        archive, it must not misclassify ordinary content."""
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        path = tmp_path / "plain.json"
        path.write_text('{"schema_version": 1, "per_library_snapshots": {}}')
        assert sniff_bundle_archive_format(path) == "json"


class TestSniffSkipsTailScanForRecognizedCompressionEnvelopes:
    """A gzip/zstd `BundleFacts` JSON file's own magic bytes already
    unambiguously identify its format (the plain-JSON path transparently
    decompresses from that same magic), so `looks_like_zip_from_tail()`'s
    tail-scan fallback must never run against one -- a crafted gzip
    `FEXTRA` sub-field (unlike `FCOMMENT`, already closed) can embed a
    `PK\\x05\\x06` whose own comment-length field still lands exactly at
    the file's true end, satisfying the earlier structural-EOCD check
    without being a real EOCD (Codex)."""

    def _gzip_with_eocd_in_extra_field(self, payload: bytes) -> bytes:
        """A real, independently-decodable gzip stream whose FEXTRA field
        embeds a structurally-plausible empty-ZIP EOCD (comment length
        crafted to reach exactly to the file's own end)."""
        import struct
        import zlib

        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        compressed = co.compress(payload) + co.flush()
        trailer = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + struct.pack(
            "<I", len(payload) & 0xFFFFFFFF
        )
        tail_after_header = compressed + trailer
        header_prefix = (
            b"\x1f\x8b\x08"
            + bytes([0x04])  # FLG = FEXTRA
            + struct.pack("<I", 0)  # MTIME
            + b"\x02\xff"  # XFL, OS
        )
        # Subfield: SI1 SI2 LEN(2) DATA -- DATA is a 22-byte empty-ZIP EOCD
        # whose comment_len is set so the "comment" runs to the file's end.
        si = b"AB"
        eocd_offset_in_file = len(header_prefix) + 2 + len(si) + 2
        comment_len = len(tail_after_header)
        eocd = struct.pack(
            "<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, 0, comment_len
        )
        subfield = si + struct.pack("<H", len(eocd)) + eocd
        data = header_prefix + struct.pack("<H", len(subfield)) + subfield + tail_after_header
        assert b"PK\x05\x06" in data  # premise: the coincidental match exists
        assert eocd_offset_in_file + 22 + comment_len == len(data)  # premise: structurally "valid"
        return data

    def test_looks_like_zip_from_tail_would_be_fooled_by_this_construction(
        self, tmp_path: Path
    ) -> None:
        """Premise check: confirms the crafted bytes really do satisfy the
        structural EOCD check on their own -- so the *sniff*-level fix
        below is what's actually being tested, not a bad fixture."""
        from abicheck.storage.bundle_archive_cd_guard import looks_like_zip_from_tail

        path = tmp_path / "premise.gz"
        path.write_bytes(self._gzip_with_eocd_in_extra_field(b'{"a": 1}'))
        with open(path, "rb") as f:
            assert looks_like_zip_from_tail(f) is True

    def test_sniff_bundle_archive_format_still_classifies_it_as_json(
        self, tmp_path: Path
    ) -> None:
        import gzip

        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        payload = b'{"schema_version": 1, "per_library_snapshots": {}}'
        data = self._gzip_with_eocd_in_extra_field(payload)
        path = tmp_path / "envelope.json.gz"
        path.write_bytes(data)

        # Premise: still a perfectly valid, decodable gzip stream.
        with gzip.GzipFile(path, mode="rb") as gz:
            assert gz.read() == payload

        assert sniff_bundle_archive_format(path) == "json"

    def test_sniff_and_load_bundle_facts_treat_it_as_json(self, tmp_path: Path) -> None:
        """End-to-end pin through the public loader: the real regression
        this guards against."""
        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.serialization import bundle_facts_to_dict, load_bundle_facts
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        facts = capture_bundle_facts({})
        payload = json.dumps(bundle_facts_to_dict(facts), indent=2).encode("utf-8")
        path = tmp_path / "envelope-facts.json.gz"
        path.write_bytes(self._gzip_with_eocd_in_extra_field(payload))

        assert sniff_bundle_archive_format(path) == "json"
        loaded = load_bundle_facts(path)  # format="auto" default
        assert loaded.per_library_snapshots == facts.per_library_snapshots


class TestOpenRegularFileForFormatSniffClosesOnReadFailure:
    """A failure reading the 4-byte peek itself (e.g. EIO on a network
    filesystem) after open() succeeds must not leak the fd or propagate a
    raw OSError -- this module's whole error contract is SnapshotError
    (Codex review, fresh evidence)."""

    def test_prefix_read_failure_closes_fp_and_raises_snapshot_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real call site is `os.fdopen(fd, "rb")` -- not the builtin
        `open()` -- since the fix for the FIFO TOCTOU below opens via
        `os.open()`/`fstat()` on the same fd (Codex review, fresh
        evidence)."""
        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        path = tmp_path / "bundle.archive.zip"
        path.write_bytes(b"PK\x03\x04junk")

        closed: list[bool] = []
        real_fdopen = os.fdopen

        class _FailingRead:
            def __init__(self, fp: object) -> None:
                self._fp = fp

            def read(self, n: int) -> bytes:
                raise OSError(errno.EIO, "simulated read failure")

            def close(self) -> None:
                closed.append(True)
                self._fp.close()

            def fileno(self) -> int:
                return self._fp.fileno()  # type: ignore[no-any-return, attr-defined]

        def _tracking_fdopen(fd: int, *a: object, **kw: object) -> object:
            return _FailingRead(real_fdopen(fd, *a, **kw))

        monkeypatch.setattr(os, "fdopen", _tracking_fdopen)
        with pytest.raises(SnapshotError, match="simulated read failure"):
            open_regular_file_for_format_sniff(path)
        assert closed == [True]

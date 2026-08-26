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
deliberately knows nothing about ``BundleFacts``/``AbiSnapshot`` -- see the
module's own docstring for why. The ``BundleFacts``-aware round-trip lives
in ``tests/test_bundle_facts_archive.py``, through
``serialization.save_bundle_facts``/``load_bundle_facts``.
"""

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
    """Codex review: the original revision opened *path* with ``mode="w"``,
    truncating any pre-existing archive immediately -- a later error would
    leave a partial file where a valid prior archive used to be. Writes go
    to a temp file now, promoted only on a fully successful close()."""

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

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_cyclic_symlink_destination_raises_instead_of_being_overwritten(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a bare `except OSError:` around
        the target stat() swallowed ELOOP (a cyclic symlink) the same as
        genuine absence, letting os.replace() silently destroy the cyclic
        symlink and install a regular zip in its place. Only real absence
        (FileNotFoundError/NotADirectoryError) may be treated that way --
        anything else must propagate."""
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

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_rejects_a_non_regular_destination_before_any_write(
        self, tmp_path: Path
    ) -> None:
        """A pre-existing FIFO/socket/device destination is rejected
        outright rather than being silently replaced by os.replace() with
        a regular zip file (Codex review) -- unlike the hard-link case,
        there's no way to "write through" such a destination with this
        writer's atomic-rename design."""
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
        """Codex review, fresh evidence: chown() can silently clear a
        setuid/setgid bit on POSIX -- restoring mode before chown (the
        original order) let a destination's setuid/setgid bits be set by
        chmod only to be stripped by the chown that followed it.
        Simulated via a fake chown that mirrors that kernel behavior,
        rather than depending on a real setuid/setgid-capable sandbox."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o6755)

        real_chmod = os.chmod
        real_chown = os.chown

        def _clearing_chown(path_arg: object, uid: int, gid: int) -> None:
            real_chown(path_arg, uid, gid)
            current = stat.S_IMODE(os.stat(path_arg).st_mode)
            real_chmod(path_arg, current & 0o777)  # simulate the kernel clearing setuid/setgid

        monkeypatch.setattr(os, "chown", _clearing_chown)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert stat.S_IMODE(path.stat().st_mode) == 0o6755

    def test_close_failure_removes_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review: a failure anywhere in close()'s post-zf.close()
        block (fsync, chown, chmod, or the replace itself) must not leave
        the temp file behind -- repeated failures would otherwise
        accumulate temp files and starve later retries of the space
        they're trying to free up."""
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


class TestBundleArchiveDeterminism:
    """Codex review: writestr(name, data) with a bare string name stamps
    each member with time.localtime() at write time -- content-identical
    facts saved on different days must still produce byte-identical
    archives."""

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
    """Codex review: every deliberate failure in this module raises
    SnapshotError, which the CLI boundary translates into a clean usage
    error -- a truncated or hand-assembled archive must not surface as a
    raw zipfile/zstandard traceback instead."""

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
        """Codex review, fresh evidence: a member with the zip 'encrypted'
        general-purpose bit set makes `ZipFile.open()` raise a bare
        `RuntimeError("... is encrypted, password required ...")`, not
        `BadZipFile` -- the only exception the CRC-mismatch handling below
        translates -- so it must be rejected explicitly, not left to leak
        a raw RuntimeError past this module's SnapshotError contract."""
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

    def test_a_crc_mismatched_member_raises_snapshot_error(self, tmp_path: Path) -> None:
        """Codex review: ZipExtFile validates a ZIP_STORED member's CRC-32
        as it is consumed, raising a raw zipfile.BadZipFile on mismatch --
        that must be wrapped as SnapshotError like every other deliberate
        failure in this module, not leak as a third-party exception."""
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


class TestBundleArchiveCentralDirectoryGuard:
    """Codex review: zipfile.ZipFile(...) eagerly parses the whole central
    directory and builds one ZipInfo per entry before any of this
    module's own per-member size guards ever run -- a crafted archive
    with an absurd entry count must be rejected before that parse."""

    def test_rejects_an_absurd_central_directory_entry_count(
        self, tmp_path: Path
    ) -> None:
        import struct

        path = tmp_path / "fake.zip"
        # A hand-crafted EOCD record claiming far more entries than this
        # format ever legitimately needs -- no real central directory
        # backs it, since the guard must fire before zipfile.ZipFile ever
        # tries to parse one.
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 65000, 0, 0, 0)
        path.write_bytes(b"PK\x03\x04" + b"junk" + eocd)

        with pytest.raises(SnapshotError, match="central directory claims"):
            BundleArchiveReader.open(path)

    def test_a_real_archive_with_ordinary_member_count_opens_fine(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            assert reader.read_manifest()["library_blobs"] == {"a.so": h}

    def test_rejects_a_central_directory_claiming_an_absurd_byte_size(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: the entry-count cap alone isn't a
        byte-size bound -- a crafted archive can pair a low total_entries
        with an enormous cd_size, which zipfile.ZipFile reads and parses
        until fully consumed regardless of the entry count."""
        import struct

        path = tmp_path / "fake.zip"
        # Low entry count (10, well under the cap), huge cd_size (200 MiB).
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 10, 200 * 1024 * 1024, 0, 0)
        path.write_bytes(b"PK\x03\x04" + b"junk" + eocd)

        with pytest.raises(SnapshotError, match="central directory claims"):
            BundleArchiveReader.open(path)

    def test_rejects_a_zip64_archive_claiming_an_absurd_entry_count(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a crafted archive using the ZIP64
        entry-count sentinel (0xFFFF) in the standard EOCD must not skip
        the cap outright -- the real count must be recovered from the
        ZIP64 EOCD locator/record and checked too."""
        import struct

        path = tmp_path / "fake_zip64.zip"
        zip64_eocd_offset = 4  # arbitrary; only this test's own bytes matter
        # ZIP64 EOCD record: sig(4) size_of_record(8) ver_made_by(2)
        # ver_needed(2) disk(4) disk_start_cd(4) entries_this_disk(8)
        # total_entries(8) cd_size(8) cd_offset(8) = 56 bytes fixed portion.
        zip64_record = struct.pack(
            "<IQHHIIQQQQ", 0x06064B50, 44, 0, 0, 0, 0, 0, 70_000, 1024, 0
        )
        # ZIP64 EOCD locator: sig(4) disk_with_zip64_eocd(4) offset(8) total_disks(4)
        zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_eocd_offset, 1)
        # Standard EOCD with the 0xFFFF sentinel for total_entries.
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0xFFFF, 0, 0, 0)

        data = bytearray(b"\x00" * zip64_eocd_offset)
        data += zip64_record
        data += zip64_locator
        data += eocd
        path.write_bytes(bytes(data))

        with pytest.raises(SnapshotError, match="central directory claims 70000"):
            BundleArchiveReader.open(path)

    @pytest.mark.parametrize(
        ("build_prefix", "match"),
        [
            # No room before the EOCD for a 20-byte locator at all.
            (lambda offset: b"", "too short to hold a ZIP64 EOCD locator"),
            # Bytes precede the EOCD, but aren't a real ZIP64 locator.
            (lambda offset: b"\x00" * 20, "no valid ZIP64 EOCD locator"),
        ],
    )
    def test_rejects_a_zip64_sentinel_with_no_usable_locator(
        self, tmp_path: Path, build_prefix, match
    ) -> None:
        """Codex review, fresh evidence: a ZIP64 sentinel whose locator
        can't be found/validated must be rejected outright, not silently
        passed through to zipfile.ZipFile's own fallback onto the
        (sentinel, unverified) standard EOCD fields."""
        import struct

        path = tmp_path / "fake_zip64.zip"
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0xFFFF, 0, 0, 0)
        path.write_bytes(build_prefix(None) + bytes(eocd))

        with pytest.raises(SnapshotError, match=match):
            BundleArchiveReader.open(path)

    def test_rejects_a_zip64_locator_pointing_at_a_malformed_record(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: the locator itself is well-formed
        and points somewhere, but what's there isn't a real ZIP64 EOCD
        record (wrong signature/too short) -- also rejected outright."""
        import struct

        path = tmp_path / "fake_zip64_bad_record.zip"
        zip64_eocd_offset = 4
        junk_record = b"\x00" * 56  # 56 bytes, but not the ZIP64 record signature
        zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_eocd_offset, 1)
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0xFFFF, 0, 0, 0)

        data = bytearray(b"\x00" * zip64_eocd_offset)
        data += junk_record
        data += zip64_locator
        data += eocd
        path.write_bytes(bytes(data))

        with pytest.raises(SnapshotError, match="no valid ZIP64 EOCD record"):
            BundleArchiveReader.open(path)


class TestBundleArchivePreflightUsesTheSameFdAsZipFile:
    """Codex review, fresh evidence: the earlier preflight reopened *path*
    a second time for `zipfile.ZipFile` -- a concurrent atomic replacement
    between the two opens could swap in a different generation, bypassing
    the preflight entirely. Both now read through one shared fd."""

    def test_preflight_and_zipfile_share_one_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        opened_paths: list[object] = []
        real_open = open

        def _tracking_open(file, *a, **kw):  # type: ignore[no-untyped-def]
            if file == path or file == str(path):
                opened_paths.append(file)
            return real_open(file, *a, **kw)

        monkeypatch.setattr("builtins.open", _tracking_open)
        with BundleArchiveReader.open(path) as reader:
            assert reader.read_blob(h) == b'{"a": 1}'
        # Exactly one open() of *path* -- zipfile.ZipFile receives the
        # already-open fd, not the path again.
        assert len(opened_paths) == 1


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
    """Codex review: a real bundle archive can never be delivered via a
    FIFO/pipe regardless (zipfile.ZipFile needs to seek to its end to
    locate the central directory), so sniffing must never consume bytes
    from a non-regular source -- doing so would silently lose them for
    the plain-JSON path's own separate, later open."""

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


class TestBundleArchiveWriterTempFileCreation:
    """Codex review, fresh evidence: a predictable temp filename in a
    directory writable by another account can be pre-created as a
    symlink, and ZipFile(path, mode="w")'s own open(path, "w+b") follows
    it and truncates whatever it points at. The writer must create its
    temp file itself, exclusively, so it can never be tricked into
    writing through an attacker-planted entry."""

    def test_round_trip_still_works_through_the_new_temp_file_creation(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        with BundleArchiveReader.open(path) as reader:
            assert reader.read_manifest()["library_blobs"] == {"a.so": h}
        assert list(tmp_path.glob("*.tmp")) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
    def test_a_planted_symlink_at_a_would_be_temp_path_is_never_followed(
        self, tmp_path: Path
    ) -> None:
        """Simulates the attack directly: pre-create a symlink at every
        temp-name shape the old "<name>.tmp-<pid>-<id>" scheme could have
        produced, then confirm a real write still lands correctly and
        none of the planted symlinks were touched -- proving temp
        creation no longer uses (or falls back to) a guessable path."""
        path = tmp_path / "bundle.archive.zip"
        evil_target = tmp_path / "evil_target"
        evil_target.write_bytes(b"do not touch me")
        planted = []
        for guess_id in range(-2, 3):
            p = tmp_path / f"bundle.archive.zip.tmp-{os.getpid()}-{guess_id:x}"
            p.symlink_to(evil_target)
            planted.append(p)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert path.exists()
        for p in planted:
            assert p.is_symlink()
        assert evil_target.read_bytes() == b"do not touch me"


class TestBundleArchiveWriterCloseFailureCleanup:
    """Codex review, fresh evidence: self._zf.close() itself (the central-
    directory write) must be inside the same cleanup-on-failure block as
    the fsync/chown/chmod/replace steps -- a failure there previously
    left the temp file behind uncleaned."""

    def test_a_failure_while_closing_the_zip_still_removes_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')
        writer.write_manifest({"library_blobs": {"a.so": h}})

        calls = 0

        def _failing_close(self: zipfile.ZipFile) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.ENOSPC, "simulated disk full while writing central directory")
            # A later __del__-triggered close() (the underlying fp is
            # already closed by this class's own cleanup path by then)
            # must not raise a second, unrelated exception during GC.

        monkeypatch.setattr(zipfile.ZipFile, "close", _failing_close)
        with pytest.raises(OSError, match="simulated disk full"):
            writer.close()
        assert list(tmp_path.glob("*.tmp")) == []
        assert not path.exists()

    def test_a_failure_while_aborting_still_removes_the_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CodeRabbit review: _abort() (reached via __exit__ on an
        exception, or close() with no manifest written) must still unlink
        the temp file when self._zf.close() itself raises -- not just
        close()'s own guarded path."""
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')

        calls = 0

        def _failing_close(self: zipfile.ZipFile) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.ENOSPC, "simulated disk full during abort")

        monkeypatch.setattr(zipfile.ZipFile, "close", _failing_close)
        # No write_manifest() -- close() takes the _abort() branch, and
        # the abort's own zip-close failure propagates ahead of the
        # "no manifest written" SnapshotError close() would otherwise
        # raise -- the fix under test is that the temp file is still
        # removed either way, not which exception wins.
        with pytest.raises(OSError, match="simulated disk full during abort"):
            writer.close()
        assert list(tmp_path.glob("*.tmp")) == []
        assert not path.exists()
        del h  # unused beyond establishing a non-empty archive


class TestBundleArchiveWriterMetadataDurability:
    """Codex review, fresh evidence: chown/chmod mutate the temp file's
    inode metadata *after* the first fsync -- without a second fsync
    after those mutations and before os.replace(), a crash could lose
    the restored owner/mode even though the file content itself is
    durable."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode semantics")
    def test_close_fsyncs_again_after_restoring_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o600)  # gives close() an existing_mode to restore

        events: list[str] = []
        real_fsync = os.fsync
        real_chmod = os.chmod

        def _tracking_fsync(fd: int) -> None:
            events.append("fsync")
            real_fsync(fd)

        def _tracking_chmod(p: object, mode: int) -> None:
            events.append("chmod")
            real_chmod(p, mode)

        monkeypatch.setattr(os, "fsync", _tracking_fsync)
        monkeypatch.setattr(os, "chmod", _tracking_chmod)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        # At least two fsyncs on the temp file's own fd (before and after
        # the chmod), with the chmod strictly between two of them.
        fsync_indices = [i for i, e in enumerate(events) if e == "fsync"]
        chmod_indices = [i for i, e in enumerate(events) if e == "chmod"]
        assert len(fsync_indices) >= 2
        assert chmod_indices
        assert any(
            fsync_indices[k] < chmod_indices[0] < fsync_indices[k + 1]
            for k in range(len(fsync_indices) - 1)
        ), f"expected a fsync both before and after chmod, got order: {events}"


class TestBundleArchiveDeterministicCreateSystem:
    """Codex review, fresh evidence: ZipInfo.__init__ defaults
    create_system to the host platform (0 Windows, 3 Unix), serialized
    into the central directory -- identical facts archived on different
    platforms would otherwise still produce different bytes despite every
    other reproducibility-affecting field already being pinned."""

    def test_every_member_pins_create_system_to_unix(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                assert info.create_system == 3


class TestBundleArchiveWriterNewArchivePermissions:
    """Codex review, fresh evidence: tempfile.mkstemp() always creates its
    file at mode 0600 regardless of the process umask -- a brand-new
    archive must not silently publish more restrictively than a normal
    `open(..., "wb")` would under the same umask. Implemented via
    `_open_unique_temp` (`os.O_CREAT` filtered through the umask by the
    kernel at creation), not a process-wide `os.umask()` read-zero-restore
    dance -- see `TestBundleArchiveWriterDoesNotToggleTheProcessUmask`
    below for that specific hazard's own regression test."""

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows"
    )
    def test_a_new_archive_gets_umask_appropriate_permissions(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        old_umask = os.umask(0o022)
        try:
            with BundleArchiveWriter(path) as writer:
                h = writer.put_blob(b'{"a": 1}')
                writer.write_manifest({"library_blobs": {"a.so": h}})
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(path.stat().st_mode) == 0o644

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows"
    )
    def test_a_new_archive_honors_a_stricter_umask(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        old_umask = os.umask(0o077)
        try:
            with BundleArchiveWriter(path) as writer:
                h = writer.put_blob(b'{"a": 1}')
                writer.write_manifest({"library_blobs": {"a.so": h}})
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission bits are not meaningful on Windows"
    )
    def test_overwriting_an_existing_archive_still_preserves_its_mode(
        self, tmp_path: Path
    ) -> None:
        """The umask-based default only applies when there is no
        pre-existing destination -- overwriting one must still preserve
        its own mode exactly, unaffected by whatever the umask is."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o640)

        old_umask = os.umask(0o022)
        try:
            with BundleArchiveWriter(path) as writer:
                h = writer.put_blob(b'{"a": 2}')
                writer.write_manifest({"library_blobs": {"b.so": h}})
        finally:
            os.umask(old_umask)

        assert stat.S_IMODE(path.stat().st_mode) == 0o640


class TestBundleArchiveWriterDoesNotToggleTheProcessUmask:
    """Codex review, fresh evidence: the earlier `os.umask(0)`/
    `os.umask(current_umask)` read-zero-restore sequence is process-wide
    and not thread-safe -- a concurrent thread creating any file during
    that window could observe the temporarily-zeroed umask. Confirms the
    fix directly: the process umask is bit-for-bit unchanged by opening a
    writer for a brand-new archive."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX umask semantics")
    def test_umask_is_never_read_or_modified(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        sentinel = 0o037
        old_umask = os.umask(sentinel)
        try:
            with BundleArchiveWriter(path) as writer:
                # The umask must already be back at *sentinel* here, mid-write
                # -- not merely restored afterward -- since the old
                # implementation's zeroed window spanned exactly this scope.
                during = os.umask(sentinel)
                os.umask(during)
                assert during == sentinel
                h = writer.put_blob(b'{"a": 1}')
                writer.write_manifest({"library_blobs": {"a.so": h}})
            after = os.umask(sentinel)
            os.umask(after)
            assert after == sentinel
        finally:
            os.umask(old_umask)

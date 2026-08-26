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

"""``BundleArchiveWriter`` temp-file/security/metadata hardening tests
(G40) -- split out of ``tests/test_bundle_archive.py`` (an ADR-061
test-size-capped module) to keep both under the 1200-line test cap.

Covers the writer's own publication-safety properties: exclusive,
non-guessable temp-file creation, cleanup on every failure path, fd-based
(not path-based) chown/chmod/hash to avoid a substitution race, metadata
durability (fsync ordering), deterministic zip metadata, and permission/
umask handling. The core read/write/dedup primitive is still tested
directly in ``tests/test_bundle_archive.py``.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

import abicheck.storage.bundle_archive as bundle_archive_module
from abicheck.errors import SnapshotError
from abicheck.storage.bundle_archive import BundleArchiveReader, BundleArchiveWriter


class TestBundleArchiveWriterTempFileCreation:
    """A predictable temp filename in a writable-by-another-account
    directory can be pre-created as a symlink ZipFile(mode="w") would
    follow and truncate -- the writer must create its temp file itself,
    exclusively (Codex review, fresh evidence)."""

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
        """Pre-creates a symlink at every temp-name shape the old
        guessable scheme could have produced, then confirms a real write
        still lands correctly and no planted symlink was touched."""
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

    def test_a_failure_closing_the_tmp_file_itself_still_removes_it(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: `_abort()`'s original single
        `finally` around only `self._zf.close()` skipped the unlink when
        the *second* close -- `self._tmp_file.close()` -- itself raised.
        Both closes must be nested in their own `finally` so the unlink
        always runs regardless of which one fails."""
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        writer.put_blob(b'{"a": 1}')

        real_close = writer._tmp_file.close

        def _failing_tmp_close() -> None:
            real_close()
            raise OSError(errno.EIO, "simulated I/O error closing tmp file")

        writer._tmp_file.close = _failing_tmp_close  # type: ignore[method-assign]
        with pytest.raises(OSError, match="simulated I/O error"):
            writer._abort()
        assert list(tmp_path.glob("*.tmp")) == []
        assert not path.exists()


class TestBundleArchiveWriterAvoidsPathBasedReopen:
    """A hostile actor sharing a non-sticky, writable directory could
    substitute a file/symlink at the temp path between exclusive creation
    and a later path-based reopen -- chown/chmod/hash would then follow
    the substitution instead of the file actually created here (Codex
    review, fresh evidence). Publication must operate on the fd held open
    since creation, not by reopening `self._tmp_path`."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX chown/chmod semantics")
    def test_close_uses_fd_based_chown_and_chmod_not_path_based(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        os.chmod(path, 0o640)  # gives close() existing_mode/uid/gid to restore

        def _forbidden(*_a: object, **_kw: object) -> None:
            raise AssertionError("path-based chown/chmod must not be called")

        monkeypatch.setattr(os, "chown", _forbidden)
        monkeypatch.setattr(os, "chmod", _forbidden)
        fd_calls: list[str] = []
        real_fchown, real_fchmod = os.fchown, os.fchmod

        def _tracking_fchown(fd: int, u: int, g: int) -> None:
            fd_calls.append("fchown")
            real_fchown(fd, u, g)

        def _tracking_fchmod(fd: int, m: int) -> None:
            fd_calls.append("fchmod")
            real_fchmod(fd, m)

        monkeypatch.setattr(os, "fchown", _tracking_fchown)
        monkeypatch.setattr(os, "fchmod", _tracking_fchmod)

        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        assert "fchown" in fd_calls
        assert "fchmod" in fd_calls

    def test_close_never_reopens_the_temp_path_for_reading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        h = writer.put_blob(b'{"a": 1}')
        writer.write_manifest({"library_blobs": {"a.so": h}})
        tmp_path_seen = writer._tmp_path
        real_open = os.open

        def _tracking_open(file: object, flags: int, *a: object, **kw: object) -> int:
            if file == tmp_path_seen or file == str(tmp_path_seen):
                raise AssertionError("close() must read via dup(), not reopen by path")
            return real_open(file, flags, *a, **kw)

        monkeypatch.setattr(os, "open", _tracking_open)
        writer.close()

        with BundleArchiveReader.open(path) as reader:
            assert reader.read_blob(h) == b'{"a": 1}'


class TestBundleArchiveWriterMetadataDurability:
    """chown/chmod mutate the temp file's inode metadata after the first
    fsync -- without a second fsync before os.replace(), a crash could
    lose the restored owner/mode (Codex review, fresh evidence)."""

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
        real_fchmod = os.fchmod

        def _tracking_fsync(fd: int) -> None:
            events.append("fsync")
            real_fsync(fd)

        def _tracking_fchmod(fd: int, mode: int) -> None:
            events.append("chmod")
            real_fchmod(fd, mode)

        monkeypatch.setattr(os, "fsync", _tracking_fsync)
        monkeypatch.setattr(os, "fchmod", _tracking_fchmod)

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
    """ZipInfo.__init__ defaults create_system to the host platform (0
    Windows, 3 Unix) -- identical facts on different platforms would
    otherwise produce different bytes (Codex review, fresh evidence)."""

    def test_every_member_pins_create_system_to_unix(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                assert info.create_system == 3


class TestBundleArchiveWriterNewArchivePermissions:
    """tempfile.mkstemp() always creates its file at mode 0600 regardless
    of the process umask -- a brand-new archive must not silently publish
    more restrictively than `open(..., "wb")` would (Codex review).
    Implemented via `_open_unique_temp`, not an `os.umask()` dance -- see
    `TestBundleArchiveWriterDoesNotToggleTheProcessUmask` below."""

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
    """The earlier `os.umask(0)`/restore sequence is process-wide and not
    thread-safe -- a concurrent thread could observe the zeroed umask
    (Codex review, fresh evidence). Confirms the process umask is
    bit-for-bit unchanged by opening a writer."""

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


class TestPutBlobReservesTheManifestMemberSlot:
    """`put_blob()` must itself refuse to exceed MAX_ARCHIVE_MEMBERS, not
    only `bundle_facts.write_bundle_facts_archive()`'s own higher-level
    preflight -- a direct caller of this public primitive bypasses that
    check entirely, and a mandatory manifest.json member always follows
    (Codex review, fresh evidence: a writer that let `put_blob` fill every
    slot would publish an archive one member over the reader's own
    central-directory cap, unreadable the moment it was written)."""

    def test_put_blob_raises_before_exceeding_the_reader_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "MAX_ARCHIVE_MEMBERS", 3)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            # 2 distinct blobs + 1 reserved manifest slot == the cap; a
            # 3rd distinct blob must be rejected before it is ever written.
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            with pytest.raises(SnapshotError, match="more than 3 zip members"):
                writer.put_blob(b'{"a": 3}')
            # A caller recovering from the rejection can still finish the
            # archive with what was already accepted.
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

    def test_a_duplicate_payload_never_counts_against_the_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "MAX_ARCHIVE_MEMBERS", 3)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            # Re-writing the identical payload is a dedup no-op, not a new
            # member -- must not itself trip the cap.
            h2 = writer.put_blob(b'{"a": 1}')
            assert h1 == h2
            writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h1}})
        with BundleArchiveReader(path) as reader:
            assert reader.read_manifest()["library_blobs"]["a.so"] == h1

    def test_exactly_at_the_cap_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "MAX_ARCHIVE_MEMBERS", 3)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})
        # The reader's own cap must accept exactly what the writer allowed.
        with BundleArchiveReader(path) as reader:
            manifest = reader.read_manifest()
            assert manifest["library_blobs"]["a.so"] == h1
            assert manifest["library_blobs"]["b.so"] == h2


class TestPutBlobRejectsPayloadsTheReaderCannotDecode:
    """`put_blob()` must itself refuse a payload bigger than
    `DEFAULT_MAX_BLOB_BYTES`, not just accept and publish it -- `read_
    blob()`'s own default cap is exactly this value, and the high-level
    bundle loader never grants a larger allowance, so a direct caller of
    this primitive could otherwise create an archive its own paired
    reader could never reopen (Codex review, fresh evidence)."""

    def test_put_blob_raises_before_writing_an_oversized_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_BLOB_BYTES", 100)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            with pytest.raises(SnapshotError, match="exceeding the 100 byte"):
                writer.put_blob(b"x" * 101)
            # A caller recovering from the rejection can still finish the
            # archive with whatever else was already accepted.
            writer.write_manifest({"library_blobs": {}})

    def test_a_payload_at_the_cap_still_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_BLOB_BYTES", 100)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b"x" * 100)
            writer.write_manifest({"library_blobs": {"a.so": h}})
        with BundleArchiveReader(path) as reader:
            assert reader.read_blob(h) == b"x" * 100


class TestReaderTranslatesUnsupportedZipVersions:
    """`zipfile.ZipFile` raises a bare `NotImplementedError` (not
    `BadZipFile`) for a central-directory member whose extract_version
    exceeds what this zipfile module supports -- previously escaped this
    module's SnapshotError contract entirely (Codex review, fresh
    evidence)."""

    def test_unsupported_extract_version_raises_snapshot_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        real_init = zipfile.ZipFile.__init__

        def _failing_init(self, *a, **kw):  # type: ignore[no-untyped-def]
            real_init(self, *a, **kw)
            raise NotImplementedError("zip file version 9.9")

        monkeypatch.setattr(zipfile.ZipFile, "__init__", _failing_init)
        with pytest.raises(SnapshotError, match="not a valid bundle archive"):
            BundleArchiveReader.open(path)


class TestReadManifestTranslatesInvalidEncoding:
    """`json.loads(raw)` on invalid UTF-8/JSON bytes raises a raw
    `UnicodeDecodeError`/`json.JSONDecodeError`, bypassing this module's
    `SnapshotError` vocabulary the same way corrupt ZIP members and zstd
    payloads are already translated (Codex review, fresh evidence)."""

    def test_invalid_utf8_manifest_raises_snapshot_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            # A lone 0x80 continuation byte with no leading byte is not
            # valid UTF-8 on its own.
            zf.writestr(bundle_archive_module.MANIFEST_MEMBER, b"\x80")

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="not valid JSON"):
                reader.read_manifest()

    def test_malformed_json_syntax_raises_snapshot_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(bundle_archive_module.MANIFEST_MEMBER, b"{not json")

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="not valid JSON"):
                reader.read_manifest()


class TestWriteManifestBoundsEncodingBeforeMaterializing:
    """`write_manifest()` must reject an oversized manifest without first
    materializing the whole encoded string in memory via `json.dumps()` --
    it now encodes via `iterencode()` and checks the running byte count
    chunk by chunk instead (Codex review, fresh evidence)."""

    def test_an_oversized_manifest_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 100)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            with pytest.raises(SnapshotError, match="exceeding the 100 byte"):
                writer.write_manifest(
                    {"library_blobs": {f"lib{i}.so": "x" * 20 for i in range(20)}}
                )
            # A caller recovering from the rejection can still finish the
            # archive with a manifest that does fit.
            writer.write_manifest({"library_blobs": {}})

    def test_a_manifest_under_the_cap_still_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        manifest = {"library_blobs": {"a.so": "deadbeef"}, "note": "hello"}
        with BundleArchiveWriter(path) as writer:
            writer.write_manifest(manifest)
        with BundleArchiveReader(path) as reader:
            assert reader.read_manifest() == manifest

    def test_iterencode_never_materializes_the_full_oversized_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A regression-proof pin, not just a byte-count check: patches
        `json.dumps` itself to fail the test if `write_manifest()` ever
        calls it -- the old, reverted implementation's own mechanism."""
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 100)
        import json as json_module

        def _fail_if_called(*a: object, **kw: object) -> str:
            raise AssertionError(
                "write_manifest() must not call json.dumps() -- it should "
                "encode incrementally via json.JSONEncoder.iterencode()"
            )

        monkeypatch.setattr(json_module, "dumps", _fail_if_called)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            with pytest.raises(SnapshotError, match="exceeding the 100 byte"):
                writer.write_manifest(
                    {"library_blobs": {f"lib{i}.so": "x" * 20 for i in range(20)}}
                )
            writer.write_manifest({"library_blobs": {}})


class TestWriteManifestRejectsASingleOversizedString:
    """`iterencode()` yields one whole escaped string as a single chunk,
    so the chunk-by-chunk length check in `TestWriteManifestBoundsEncoding
    BeforeMaterializing` above can't reject a manifest containing one
    string value alone larger than the cap before that one allocation
    already happened. `write_manifest()` now pre-checks every string leaf
    directly, over the raw (unescaped) manifest, before ever calling
    `iterencode()` (Codex review, fresh evidence)."""

    def test_a_single_oversized_string_is_rejected_before_iterencode_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 100)
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            with pytest.raises(SnapshotError, match="alone exceeding the 100 byte"):
                writer.write_manifest({"library_blobs": {}, "padding": "x" * 200})
            writer.write_manifest({"library_blobs": {}})

    def test_iterencode_never_runs_on_a_manifest_with_an_oversized_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression-proof pin: patches `iterencode` itself to fail the
        test if `write_manifest()` ever reaches it for this input -- the
        oversized string must be caught before that call, not merely by
        one of its early chunks."""
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 100)

        def _fail_if_called(self: object, *a: object, **kw: object) -> object:
            raise AssertionError(
                "write_manifest() must not reach iterencode() for a "
                "manifest containing a single oversized string -- it "
                "should be rejected by the raw-string pre-check first"
            )

        monkeypatch.setattr(
            bundle_archive_module.json.JSONEncoder, "iterencode", _fail_if_called
        )
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            with pytest.raises(SnapshotError, match="alone exceeding the 100 byte"):
                writer.write_manifest({"library_blobs": {}, "padding": "x" * 200})
            # A fitting manifest must still work once iterencode() is
            # un-patched -- restored so the writer can finish cleanly.
            monkeypatch.undo()
            writer.write_manifest({"library_blobs": {}})

    def test_a_string_just_under_the_cap_still_reaches_iterencode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-check must not be overzealous -- a string comfortably
        under the cap should round-trip normally, not be rejected."""
        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 1000)
        path = tmp_path / "bundle.archive.zip"
        manifest = {"library_blobs": {}, "padding": "x" * 50}
        with BundleArchiveWriter(path) as writer:
            writer.write_manifest(manifest)
        with BundleArchiveReader(path) as reader:
            assert reader.read_manifest() == manifest


class TestOversizedRawStringNeverEscapesTheWholeString:
    """A prior revision of this pre-check called ``obj.encode("utf-8")``
    on the whole string before comparing its length -- for a guaranteed-
    oversized value (a multi-gigabyte string) that allocates a second
    object as large as the input, exactly the allocation the pre-check
    exists to prevent (Codex review, fresh evidence). Verified directly
    against `_escaped_length_exceeds` by tracking calls to
    `encode_basestring_ascii` (the function it now uses in place of a raw
    `.encode()`), since a functional round-trip test alone can't
    distinguish "rejected cheaply" from "rejected after a hidden
    full-size encode/escape"."""

    def test_a_guaranteed_oversized_string_is_never_escaped_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.storage.bundle_archive_json_guard as guard_module

        calls: list[str] = []
        real_fn = guard_module.encode_basestring_ascii

        def _tracking(s: str) -> str:
            calls.append(s)
            return real_fn(s)  # type: ignore[no-any-return]

        monkeypatch.setattr(guard_module, "encode_basestring_ascii", _tracking)

        # Character count alone (10) already exceeds the limit (5), so no
        # escaping call is needed -- or permitted -- to answer this.
        assert guard_module._escaped_length_exceeds("x" * 10, 5) == 10
        assert calls == []

    def test_a_string_near_the_limit_is_escaped_only_in_bounded_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.storage.bundle_archive_json_guard as guard_module

        calls: list[str] = []
        real_fn = guard_module.encode_basestring_ascii

        def _tracking(s: str) -> str:
            calls.append(s)
            return real_fn(s)  # type: ignore[no-any-return]

        monkeypatch.setattr(guard_module, "encode_basestring_ascii", _tracking)

        # Char count (under the limit) can't answer the question on its
        # own -- escaping expansion means the real escaped length still
        # needs checking. Every individual call must still be bounded by
        # _CHUNK_CHARS characters, never the whole string.
        char_count = guard_module._CHUNK_CHARS * 3
        s = "a" * char_count
        result = guard_module._escaped_length_exceeds(s, char_count + 1000)
        assert result is None
        assert calls
        assert all(len(c) <= guard_module._CHUNK_CHARS for c in calls)

    def test_a_multibyte_string_under_the_char_limit_but_over_the_byte_limit_is_rejected(
        self,
    ) -> None:
        """Functional correctness check alongside the structural ones
        above: a non-ASCII string whose character count alone would pass
        a naive `len(s) > limit` check must still be caught once its real
        escaped length is counted. `é` (2 raw UTF-8 bytes) escapes to
        `\\u00e9` (6 ASCII bytes) under `ensure_ascii=True`."""
        from abicheck.storage.bundle_archive_json_guard import _escaped_length_exceeds

        s = "é" * 10
        assert len(s) == 10
        assert _escaped_length_exceeds(s, 15) == 62  # 10 * 6 + 2 wrapping quotes
        assert _escaped_length_exceeds(s, 62) is None

    def test_json_escaping_inflation_is_caught_even_when_the_raw_length_fits(
        self,
    ) -> None:
        """Codex review, fresh evidence: JSON escaping can inflate a
        string's size well past its own raw byte count -- a control
        character (1 raw byte) becomes a 6-character `\\uXXXX` escape.
        A check against the *raw* UTF-8 length alone would pass a string
        whose *escaped* form still exceeds the limit, letting
        `iterencode()` materialize that oversized escaped chunk anyway
        -- reproducing the exact vulnerability this whole module exists
        to prevent."""
        from abicheck.storage.bundle_archive_json_guard import _escaped_length_exceeds

        # 10 control characters: 10 raw UTF-8 bytes, but each escapes to
        # "" (6 ASCII chars) -- 60 bytes, +2 for the wrapping quotes.
        s = chr(1) * 10
        assert len(s.encode("utf-8")) == 10
        assert _escaped_length_exceeds(s, 15) == 62
        assert _escaped_length_exceeds(s, 62) is None

    def test_a_lone_surrogate_is_counted_instead_of_raising(self) -> None:
        """Codex review, fresh evidence: a lone surrogate (e.g. from a
        POSIX filename captured through `os.fsdecode`'s
        `surrogateescape` handling of non-UTF-8 bytes) makes a strict
        `.encode("utf-8")` raise `UnicodeEncodeError`. This module no
        longer calls that at all -- `encode_basestring_ascii()` (the
        same function `json.dumps()`'s `ensure_ascii=True` uses
        internally) round-trips the identical value fine as a plain
        `\\uXXXX` sequence -- so this pre-check must never crash on one
        either."""
        from abicheck.storage.bundle_archive_json_guard import _escaped_length_exceeds

        s = "bad\udcffname"
        # Confirms the premise: strict encode() really does raise here.
        with pytest.raises(UnicodeEncodeError):
            s.encode("utf-8")

        assert _escaped_length_exceeds(s, 100) is None
        assert _escaped_length_exceeds(s, 1) is not None


class TestReaderClosesTheOwnedFdWhenRewindFails:
    """`from_open_file()` hands the reader ownership of the caller's fd --
    a `seek()` failure while rewinding it must be caught by the same
    guarded block that closes it on every other failure, not escape as a
    raw, unhandled exception with the fd leaked (Codex review, fresh
    evidence)."""

    def test_a_seek_failure_on_the_shared_fd_raises_snapshot_error_and_closes_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        fp = open(path, "rb")
        closed = {"value": False}
        real_close = fp.close

        def _tracking_close() -> None:
            closed["value"] = True
            real_close()

        monkeypatch.setattr(fp, "close", _tracking_close)

        def _failing_seek(*a: object, **kw: object) -> None:
            raise OSError("simulated EIO on rewind")

        monkeypatch.setattr(fp, "seek", _failing_seek)

        with pytest.raises(SnapshotError, match="not a valid bundle archive"):
            BundleArchiveReader.from_open_file(fp, path)
        assert closed["value"], "the caller-owned fd must be closed on a seek() failure"

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

"""Central-directory bomb guard and preflight tests for
``bundle_archive_cd_guard.py``/``bundle_archive.py`` (G40) -- split out of
``tests/test_bundle_archive.py`` (an ADR-061 test-size-capped module) to
keep both under the 1200-line test cap.

Covers ``reject_absurd_central_directory()`` itself (declared vs. actual
entry counts, byte-size caps, ZIP64 locator/record handling, prefixed/
self-extracting archives) and the preflight's own publication-safety
properties (sharing one fd with ``zipfile.ZipFile``, rejecting non-regular
sources, rejecting in-place growth between the preflight and construction).
"""

from __future__ import annotations

import errno
import io
import json
import os
import struct
import sys
import zipfile
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.bundle_archive import (
    MANIFEST_MEMBER,
    BundleArchiveReader,
    BundleArchiveWriter,
    content_hash,
)


class TestBundleArchiveCentralDirectoryGuard:
    """ZipFile(...) eagerly parses the whole central directory before any
    per-member size guard runs -- an absurd entry count must be rejected
    before that parse (Codex review)."""

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

    def test_rejects_a_central_directory_with_more_real_records_than_it_claims(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: `total_entries` can be understated
        while `cd_size` (bounded, but still generously sized) holds far
        more real central-directory-file-header records -- CPython's
        `zipfile.ZipFile` parses every record it finds within `cd_size`
        regardless of `total_entries`, so trusting the declared count
        alone lets this straight through. Calls the guard directly with a
        small `max_entries` (not through `BundleArchiveReader.open`'s real
        20,000-entry cap, which would need an unwieldy number of records
        to reproduce): 5 real minimal records must be rejected against a
        cap of 3, even though the EOCD's own `total_entries` claims 1."""
        import struct

        from abicheck.storage.bundle_archive_cd_guard import (
            reject_absurd_central_directory,
        )

        # A minimal central-directory-file-header record: signature(4) +
        # 42 remaining fixed bytes, all zero -- which already makes the
        # filename/extra/comment length fields inside those 42 bytes zero.
        one_record = b"PK\x01\x02" + b"\x00" * 42
        assert len(one_record) == 46
        central_directory = one_record * 5
        cd_offset = 4  # arbitrary; only this test's own bytes matter
        eocd = struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            0,
            1,  # total_entries: understated -- the real count is 5
            len(central_directory),
            cd_offset,
            0,
        )

        data = bytearray(b"\x00" * cd_offset)
        data += central_directory
        data += eocd
        path = tmp_path / "understated_entry_count.zip"
        path.write_bytes(bytes(data))

        with path.open("rb") as f:
            with pytest.raises(SnapshotError, match="actually contains more than"):
                reject_absurd_central_directory(f, path, max_entries=3)

    def test_rejects_understated_entries_even_with_a_prepended_prefix(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: `zipfile.ZipFile` rebases the
        central directory's real position via `eocd_location - size_cd`
        (the claimed `cd_offset` field cancels out of that formula
        entirely), so a self-extracting archive with N prefix bytes still
        opens correctly even though its EOCD's `cd_offset` is relative to
        the zip data alone, not the whole file. A guard seeking to the
        raw, prefix-unaware `cd_offset` instead finds nothing there and
        silently counts zero. Reproduced: 5 real records behind a
        100-byte prefix, `cd_offset` still claiming its pre-prefix
        (now-wrong) value, `total_entries` understated to 1."""
        import struct

        from abicheck.storage.bundle_archive_cd_guard import (
            reject_absurd_central_directory,
        )

        prefix = b"\x00" * 100  # simulates a self-extracting stub
        one_record = b"PK\x01\x02" + b"\x00" * 42
        central_directory = one_record * 5
        cd_offset_claimed = 4  # correct with no prefix; wrong with one
        eocd = struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            0,
            1,  # total_entries: understated -- the real count is 5
            len(central_directory),
            cd_offset_claimed,
            0,
        )

        data = bytearray(prefix)
        data += b"\x00" * cd_offset_claimed
        data += central_directory
        data += eocd
        path = tmp_path / "prefixed_understated_entry_count.zip"
        path.write_bytes(bytes(data))

        with path.open("rb") as f:
            with pytest.raises(SnapshotError, match="actually contains more than"):
                reject_absurd_central_directory(f, path, max_entries=3)

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
        """A ZIP64 entry-count sentinel (0xFFFF) must not skip the cap --
        the real count is recovered from the locator/record (Codex)."""
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

    def test_zip64_locator_falls_back_to_the_verified_position_when_prefixed(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a valid ZIP64 archive with self-
        extracting bytes prepended has a locator whose own claimed record
        offset is relative to the zip payload alone, not the whole file --
        wrong once a prefix exists. `zipfile.ZipFile` (via CPython's own
        `_EndRecData64`) falls back to the fixed position immediately
        before the locator (assuming no "zip64 extensible data sector")
        when the raw claimed offset doesn't find a valid record. This
        guard must do the same, not reject a prefixed archive `ZipFile`
        itself accepts."""
        import struct

        from abicheck.storage.bundle_archive_cd_guard import (
            reject_absurd_central_directory,
        )

        prefix = b"\x00" * 100  # simulates a self-extracting stub
        one_record = b"PK\x01\x02" + b"\x00" * 42
        central_directory = one_record * 2
        zip64_record = struct.pack(
            "<IQHHIIQQQQ", 0x06064B50, 44, 0, 0, 0, 0, 0, 2, len(central_directory), 0
        )
        # Correct before prepending the prefix; wrong (too small by
        # len(prefix)) after -- the real record now sits 100 bytes later.
        claimed_reloff = len(central_directory)
        zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, claimed_reloff, 1)
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0xFFFF, 0, 0, 0)

        data = bytearray(prefix)
        data += central_directory
        data += zip64_record
        data += zip64_locator
        data += eocd
        path = tmp_path / "prefixed_zip64.zip"
        path.write_bytes(bytes(data))

        with path.open("rb") as f:
            # Must not raise -- the fallback recovers the real position.
            validated_size = reject_absurd_central_directory(f, path, max_entries=10)
        assert validated_size == len(data)

    def test_rejects_a_zip64_locator_even_without_a_standard_eocd_sentinel(
        self, tmp_path: Path
    ) -> None:
        """CPython's ZipFile inspects a preceding ZIP64 locator
        unconditionally, not only on a sentinel overflow -- a hostile
        archive can pair small standard-EOCD values with a real oversized
        ZIP64 record (Codex review, fresh evidence)."""
        import struct

        path = tmp_path / "fake_zip64_no_sentinel.zip"
        zip64_eocd_offset = 4
        zip64_record = struct.pack(
            "<IQHHIIQQQQ", 0x06064B50, 44, 0, 0, 0, 0, 0, 70_000, 1024, 0
        )
        zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, zip64_eocd_offset, 1)
        # Standard EOCD with small, non-sentinel total_entries/cd_size.
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 1, 100, 0, 0)

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

    def test_rejects_a_zip64_locator_offset_beyond_the_file_size(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: a crafted locator can name an
        offset the platform's f.seek() would raise ValueError for (e.g.
        2**64-1), which the surrounding `except OSError:` does not catch --
        this must surface as SnapshotError, not a raw exception, matching
        this module's own documented error vocabulary. Bounding against
        the file's own size rejects both the huge-offset case and any
        offset merely past EOF, without depending on seek() to fail."""
        import struct

        path = tmp_path / "fake_zip64_huge_offset.zip"
        absurd_offset = 2**64 - 1
        zip64_locator = struct.pack("<IIQI", 0x07064B50, 0, absurd_offset, 1)
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0xFFFF, 0, 0, 0)

        data = bytearray(zip64_locator)
        data += eocd
        path.write_bytes(bytes(data))

        with pytest.raises(SnapshotError, match="beyond the file's own size"):
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

        # BundleArchiveReader.__init__ opens via os.open() (not the
        # builtin open()) as of the FIFO-TOCTOU fix -- tracked there.
        opened_paths: list[object] = []
        real_os_open = os.open

        def _tracking_open(file, *a, **kw):  # type: ignore[no-untyped-def]
            if file == path or file == str(path):
                opened_paths.append(file)
            return real_os_open(file, *a, **kw)

        monkeypatch.setattr(os, "open", _tracking_open)
        with BundleArchiveReader.open(path) as reader:
            assert reader.read_blob(h) == b'{"a": 1}'
        # Exactly one open() of *path* -- zipfile.ZipFile receives the
        # already-open fd, not the path again.
        assert len(opened_paths) == 1


class TestBundleArchiveReaderRejectsNonRegularSourcesDirectly:
    """Codex review, fresh evidence: an explicit `format="archive"`
    caller reaches `BundleArchiveReader.open()`/`__init__` directly,
    bypassing `open_regular_file_for_format_sniff`'s own non-regular-
    source guard entirely -- a FIFO with no writer must still be
    rejected cleanly rather than hanging on a blocking `open()`."""

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_open_does_not_block_on_a_fifo_with_no_writer(self, tmp_path: Path) -> None:
        import threading

        fifo = tmp_path / "no_writer.fifo"
        os.mkfifo(fifo)

        outcomes: list[object] = []

        def _call() -> None:
            try:
                BundleArchiveReader.open(fifo)
            except SnapshotError as exc:
                outcomes.append(exc)

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "BundleArchiveReader.open() blocked on a FIFO"
        assert len(outcomes) == 1
        assert "not a regular file" in str(outcomes[0])

    def test_fstat_failure_closes_the_fd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: `os.open()` succeeding but
        `os.fstat()` then raising (e.g. EIO) must not leak the fd --
        previously only the not-S_ISREG branch closed it."""
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        real_open = os.open
        real_fstat = os.fstat
        real_close = os.close
        opened_fds: list[int] = []
        closed_fds: list[int] = []

        def _tracking_open(*a, **kw):  # type: ignore[no-untyped-def]
            fd = real_open(*a, **kw)
            opened_fds.append(fd)
            return fd

        def _failing_fstat(fd, *a, **kw):  # type: ignore[no-untyped-def]
            if opened_fds and fd == opened_fds[-1]:
                raise OSError(errno.EIO, "simulated fstat failure")
            return real_fstat(fd, *a, **kw)

        def _tracking_close(fd):  # type: ignore[no-untyped-def]
            closed_fds.append(fd)
            real_close(fd)

        monkeypatch.setattr(os, "open", _tracking_open)
        monkeypatch.setattr(os, "fstat", _failing_fstat)
        monkeypatch.setattr(os, "close", _tracking_close)
        with pytest.raises(SnapshotError, match="simulated fstat failure"):
            BundleArchiveReader.open(path)
        assert opened_fds[-1] in closed_fds


class TestBundleArchivePreflightRejectsInPlaceGrowth:
    """Codex review, fresh evidence, reproduced: sharing one fd between
    the preflight and `zipfile.ZipFile` closes a *path-substitution*
    race but not an *in-place content* one -- another writer with this
    inode's access could still grow the file between the preflight
    returning and `ZipFile`'s own scan. Re-checked immediately before
    construction."""

    def test_growth_between_preflight_and_zipfile_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.storage.bundle_archive_cd_guard as cd_guard_module

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        real_guard = cd_guard_module.reject_absurd_central_directory

        def _guard_then_grow(f, archive_path, *, max_entries):  # type: ignore[no-untyped-def]
            validated_size = real_guard(f, archive_path, max_entries=max_entries)
            # Simulates another writer appending to the same inode right
            # after this check returns but before ZipFile is constructed.
            with open(path, "ab") as grower:
                grower.write(b"\x00" * 64)
            return validated_size

        monkeypatch.setattr(
            cd_guard_module, "reject_absurd_central_directory", _guard_then_grow
        )
        with pytest.raises(SnapshotError, match="changed size while being opened"):
            BundleArchiveReader.open(path)


class TestBundleArchivePreflightFailsClosedOnIoErrors:
    """A transient I/O failure partway through this preflight (fstat(),
    or any seek()/read() while locating the EOCD/ZIP64 record/central
    directory) must not be treated as "skip the check, trust ZipFile" --
    an attacker able to trigger such a failure at the right moment could
    otherwise bypass every entry-count/byte-size bound this preflight
    exists to enforce entirely (Codex review, fresh evidence)."""

    def test_a_failing_fstat_raises_instead_of_skipping_the_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.storage.bundle_archive_cd_guard as cd_guard_module

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        # Simulate os.fstat() raising -- reproduces directly against
        # `reject_absurd_central_directory()`, the load-bearing unit for
        # this fix.
        def _failing_fstat(fd: int) -> os.stat_result:
            raise OSError(errno.EIO, "simulated transient I/O error")

        with open(path, "rb") as f:
            monkeypatch.setattr(cd_guard_module.os, "fstat", _failing_fstat)
            with pytest.raises(SnapshotError, match="could not stat the archive"):
                cd_guard_module.reject_absurd_central_directory(f, path, max_entries=1)

    def test_a_failing_fstat_on_the_reader_itself_raises_snapshot_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end pin: opening a real archive through
        `BundleArchiveReader` while `os.fstat()` fails must surface
        `SnapshotError`, not silently construct `zipfile.ZipFile` with no
        bound applied at all (reproduced against the pre-fix code: a
        two-entry archive opened past a one-entry cap when this `fstat()`
        alone raised)."""
        import abicheck.storage.bundle_archive_cd_guard as cd_guard_module

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        # os.fstat() is a single, process-wide function -- the *first*
        # real call inside BundleArchiveReader.__init__ is the explicit-
        # open path's own regular-file classification check, not the
        # guard's; the guard's own initial fstat() is the *second* call.
        real_fstat = os.fstat
        calls = 0

        def _failing_on_second_call(fd: int) -> os.stat_result:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EIO, "simulated transient I/O error")
            return real_fstat(fd)

        monkeypatch.setattr(cd_guard_module.os, "fstat", _failing_on_second_call)
        with pytest.raises(SnapshotError, match="could not stat the archive"):
            BundleArchiveReader.open(path)

    def test_a_failing_read_while_scanning_actual_records_raises_snapshot_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second, independent OSError fallback -- reading the
        actual central-directory bytes to count real records -- must
        also fail closed, not just the initial fstat()."""
        import abicheck.storage.bundle_archive_cd_guard as cd_guard_module

        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(b'{"a": 1}')
            writer.write_manifest({"library_blobs": {"a.so": h}})

        def _failing_count(*a: object, **kw: object) -> int:
            raise OSError(errno.EIO, "simulated transient I/O error")

        monkeypatch.setattr(
            cd_guard_module, "_actual_central_directory_entry_count", _failing_count
        )
        with pytest.raises(SnapshotError, match="could not read the actual central"):
            BundleArchiveReader.open(path)


class TestLooksLikeZipFromTail:
    """`looks_like_zip_from_tail()` -- the `format="auto"` sniff's tail-scan
    fallback -- must require the EOCD's own declared comment length to land
    exactly on the file's true end, not merely find the 4-byte signature
    anywhere in the tail. A bare substring search misclassified a valid
    ``format="json"`` (gzip-compressed) ``BundleFacts`` file as an archive
    whenever its compressed tail happened to contain the signature by
    coincidence, failing the documented ``format="auto"`` default outright
    even though ``format="json"`` on the identical path succeeds (Codex
    review, fresh evidence, reproduced)."""

    def _eocd(self, *, comment_len: int, comment: bytes = b"") -> bytes:
        import struct

        return (
            struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, 0, comment_len)
            + comment
        )

    def test_a_real_eocd_at_the_true_end_of_file_is_accepted(
        self, tmp_path: Path
    ) -> None:
        from abicheck.storage.bundle_archive_cd_guard import looks_like_zip_from_tail

        path = tmp_path / "real.zip"
        path.write_bytes(b"leading prefix bytes" + self._eocd(comment_len=0))
        with open(path, "rb") as f:
            assert looks_like_zip_from_tail(f) is True

    def test_a_real_eocd_with_a_matching_nonempty_comment_is_accepted(
        self, tmp_path: Path
    ) -> None:
        from abicheck.storage.bundle_archive_cd_guard import looks_like_zip_from_tail

        path = tmp_path / "real-commented.zip"
        comment = b"a real trailing archive comment"
        path.write_bytes(self._eocd(comment_len=len(comment), comment=comment))
        with open(path, "rb") as f:
            assert looks_like_zip_from_tail(f) is True

    def test_a_spurious_signature_with_a_mismatched_comment_length_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The load-bearing case: the 4-byte signature appears, but the
        two bytes immediately after it (read as the EOCD's own comment
        length) do not account for the bytes actually remaining to the
        end of the file -- exactly what a coincidental match inside
        compressed/arbitrary data looks like, never what a real,
        unmodified EOCD looks like."""
        from abicheck.storage.bundle_archive_cd_guard import looks_like_zip_from_tail

        path = tmp_path / "spurious.bin"
        # A spurious signature followed by two comment-length bytes that
        # don't match the 20 bytes of trailing junk that actually follow.
        path.write_bytes(b"PK\x05\x06" + b"\x00" * 18 + b"trailing junk bytes!")
        with open(path, "rb") as f:
            assert looks_like_zip_from_tail(f) is False

    def test_a_spurious_signature_embedded_in_gzip_header_comment_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """The real-world repro: a valid gzip stream whose FCOMMENT header
        field happens to contain the raw EOCD signature bytes (gzip's own
        comment field is attacker/coincidence-controlled free-form text) --
        the file decodes as ordinary gzip-compressed JSON, but a bare
        substring scan over its tail still finds the signature."""
        import gzip
        import struct
        import zlib

        from abicheck.storage.bundle_archive_cd_guard import looks_like_zip_from_tail

        payload = b'{"schema_version": 1, "per_library_snapshots": {}}'
        comment = b"junk PK\x05\x06 more junk"
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        compressed = co.compress(payload) + co.flush()
        header = (
            b"\x1f\x8b\x08"
            + bytes([0x10])  # FLG = FCOMMENT
            + struct.pack("<I", 0)  # MTIME
            + b"\x02\xff"  # XFL, OS
            + comment
            + b"\x00"
        )
        trailer = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + struct.pack(
            "<I", len(payload) & 0xFFFFFFFF
        )
        data = header + compressed + trailer
        assert b"PK\x05\x06" in data  # premise: the coincidental match exists
        path = tmp_path / "coincidental.json.gz"
        path.write_bytes(data)

        # Premise: it's still a perfectly valid, decodable gzip stream.
        with gzip.GzipFile(path, mode="rb") as gz:
            assert gz.read() == payload

        with open(path, "rb") as f:
            assert looks_like_zip_from_tail(f) is False

    def test_sniff_and_load_bundle_facts_treat_the_coincidental_gzip_as_json(
        self, tmp_path: Path
    ) -> None:
        """End-to-end pin through the public loader (Codex review asked
        for gzip coverage specifically): the documented `format="auto"`
        default must still succeed on a real gzip `BundleFacts` file whose
        tail coincidentally contains the EOCD signature."""
        import struct
        import zlib

        from abicheck.bundle_facts import capture_bundle_facts
        from abicheck.serialization import bundle_facts_to_dict, load_bundle_facts
        from abicheck.storage.bundle_archive import sniff_bundle_archive_format

        facts = capture_bundle_facts({})
        payload = json.dumps(bundle_facts_to_dict(facts), indent=2).encode("utf-8")
        comment = b"junk PK\x05\x06 more junk"
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        compressed = co.compress(payload) + co.flush()
        header = (
            b"\x1f\x8b\x08"
            + bytes([0x10])
            + struct.pack("<I", 0)
            + b"\x02\xff"
            + comment
            + b"\x00"
        )
        trailer = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + struct.pack(
            "<I", len(payload) & 0xFFFFFFFF
        )
        data = header + compressed + trailer
        assert b"PK\x05\x06" in data
        path = tmp_path / "coincidental-facts.json.gz"
        path.write_bytes(data)

        assert sniff_bundle_archive_format(path) == "json"
        loaded = load_bundle_facts(path)  # format="auto" default
        assert loaded.per_library_snapshots == facts.per_library_snapshots


class TestBundleArchiveReaderRejectsInvalidUtf8Filenames:
    """A central-directory entry marked UTF-8 (general-purpose flag bit
    11) but storing bytes that aren't valid UTF-8 makes `zipfile.ZipFile`
    raise a bare `UnicodeDecodeError` while building its own file list --
    a different exception class than the ones this constructor already
    translates (Codex review, fresh evidence)."""

    def _zip_with_invalid_utf8_filename(self) -> bytes:
        name = b"\xff\xfe invalid utf8"
        data = b"hello"
        flags = 0x0800  # general-purpose flag bit 11: filename is UTF-8
        crc = zipfile.crc32(data) & 0xFFFFFFFF
        lfh = (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                flags,
                0,
                0,
                0,
                crc,
                len(data),
                len(data),
                len(name),
                0,
            )
            + name
            + data
        )
        cd = (
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,
                20,
                flags,
                0,
                0,
                0,
                crc,
                len(data),
                len(data),
                len(name),
                0,
                0,
                0,
                0,
                0,
                0,
            )
            + name
        )
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(lfh), 0)
        return lfh + cd + eocd

    def test_raises_snapshot_error_not_unicode_decode_error(self, tmp_path: Path) -> None:
        # Premise: real zipfile really does raise UnicodeDecodeError for
        # this construction, confirming the fixture reproduces the bug.
        data = self._zip_with_invalid_utf8_filename()
        with pytest.raises(UnicodeDecodeError):
            zipfile.ZipFile(io.BytesIO(data), mode="r")

        path = tmp_path / "bad_filename.zip"
        path.write_bytes(data)
        with pytest.raises(SnapshotError, match="not a valid bundle archive"):
            BundleArchiveReader.open(path)


class TestBundleArchiveReaderRejectsInvalidUtf8LocalHeaderFilenames:
    """The central-directory filename can be perfectly valid (so
    `ZipFile()` construction succeeds) while the *local* file header --
    a separate, independently-flagged copy `open()` re-reads and
    re-decodes -- sets its own UTF-8 bit over invalid bytes, raising a
    bare `UnicodeDecodeError` neither `read_manifest()` nor `read_blob()`
    caught (Codex review, fresh evidence)."""

    def _zip_with_invalid_utf8_local_filename(self) -> bytes:
        cd_name = b"manifest.json"
        local_name = b"\xff\xfe\xff\xfe\xff\xfe\xff\xfe\xff\xfe\xff\xfe\xff"
        data = b"{}"
        flags_local = 0x0800  # local header only: filename is UTF-8
        crc = zipfile.crc32(data) & 0xFFFFFFFF
        lfh = (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                flags_local,
                0,
                0,
                0,
                crc,
                len(data),
                len(data),
                len(local_name),
                0,
            )
            + local_name
            + data
        )
        cd = (
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                20,
                20,
                0,  # central directory: no UTF-8 flag, plain ASCII name
                0,
                0,
                0,
                crc,
                len(data),
                len(data),
                len(cd_name),
                0,
                0,
                0,
                0,
                0,
                0,
            )
            + cd_name
        )
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(lfh), 0)
        return lfh + cd + eocd

    def test_raises_snapshot_error_not_unicode_decode_error(self, tmp_path: Path) -> None:
        # Premise: real zipfile really does raise UnicodeDecodeError from
        # open() -- construction itself succeeds, since the CD name is clean.
        data = self._zip_with_invalid_utf8_local_filename()
        zf = zipfile.ZipFile(io.BytesIO(data), mode="r")
        with pytest.raises(UnicodeDecodeError):
            zf.open("manifest.json")

        path = tmp_path / "bad_local_filename.zip"
        path.write_bytes(data)
        with pytest.raises(SnapshotError, match="invalid local file header filename"):
            with BundleArchiveReader.open(path) as reader:
                reader.read_manifest()


class TestReadBlobRejectsTruncatedZstdFrames:
    """A zstd frame truncated at just the right point can decompress with
    no error at all, silently yielding fewer bytes than intended instead
    of raising -- confirmed against a real truncated frame (Codex
    review). A hostile archive can then name the member after the
    truncated payload's own (still-correct) content hash, so the
    post-decode hash check alone would not catch it either."""

    def _truncated_blob_archive(self, tmp_path: Path) -> tuple[Path, bytes]:
        import zstandard

        json_bytes = b'{"k": "v"}'
        full_payload = json_bytes + b" " * 300_000  # spans multiple zstd blocks
        compressor = zstandard.ZstdCompressor(level=19)
        compressed = compressor.compress(full_payload)
        truncated = compressed[:-3]

        # Premise: confirm real zstandard really does silently short-decode
        # this, not raise -- otherwise this fixture proves nothing.
        dctx = zstandard.ZstdDecompressor()
        out = io.BytesIO()
        with dctx.stream_reader(io.BytesIO(truncated)) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        partial = out.getvalue()
        assert partial and partial != full_payload and partial.startswith(json_bytes)
        partial_hash = content_hash(partial)

        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": partial_hash}}))
            zf.writestr(f"blobs/{partial_hash}.json.zst", truncated, compress_type=zipfile.ZIP_STORED)
        return path, partial

    def test_read_blob_raises_instead_of_silently_returning_truncated_content(
        self, tmp_path: Path
    ) -> None:
        path, partial = self._truncated_blob_archive(tmp_path)
        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            h = manifest["library_blobs"]["a.so"]
            with pytest.raises(SnapshotError, match="corrupt or truncated zstd stream"):
                reader.read_blob(h)

    def test_read_blob_raises_for_a_member_truncated_inside_the_frame_header(
        self, tmp_path: Path
    ) -> None:
        """A member truncated to just the 4-byte zstd magic (no full frame
        header at all) decodes to `b""` with no error via `stream_reader()`
        -- so a member named after the empty-payload hash would otherwise
        pass the post-decode hash check too, accepting a malformed archive
        as an empty blob (Codex review, fresh evidence)."""
        magic = b"\x28\xb5\x2f\xfd"

        # Premise: confirm real zstandard's stream_reader() really does
        # silently decode this to nothing, not raise.
        import zstandard

        dctx = zstandard.ZstdDecompressor()
        out = io.BytesIO()
        with dctx.stream_reader(io.BytesIO(magic)) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        assert out.getvalue() == b""

        h = content_hash(b"")
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", magic, compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            with pytest.raises(SnapshotError, match="corrupt or truncated zstd stream"):
                reader.read_blob(manifest["library_blobs"]["a.so"])

    def test_read_blob_raises_for_a_completely_empty_member(self, tmp_path: Path) -> None:
        """A zero-byte stored member decodes to `b""` via `stream_reader()`
        with no error, and the frame-completeness `while` loop never even
        runs (nothing to walk) -- so a member named after the empty-
        payload hash would otherwise pass every check, accepting an
        archive containing no zstd frame at all as a valid empty blob.
        `BundleArchiveWriter.put_blob()` always calls `ZstdCompressor.
        compress()` unconditionally, even for an empty payload, so a
        zero-byte member can never be this codebase's own legitimate
        output (Codex review, fresh evidence)."""
        # Premise: confirm real zstandard's stream_reader() really does
        # silently decode a genuinely empty input to nothing, not raise.
        import zstandard

        dctx = zstandard.ZstdDecompressor()
        out = io.BytesIO()
        with dctx.stream_reader(io.BytesIO(b"")) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        assert out.getvalue() == b""

        h = content_hash(b"")
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", b"", compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            with pytest.raises(SnapshotError, match="corrupt or truncated zstd stream"):
                reader.read_blob(manifest["library_blobs"]["a.so"])


class TestReadBlobHandlesSkippableFrames:
    """A standard zstd "skippable frame" (magic 0x184D2A50-0x184D2A5F) can
    legally appear between real data frames in an externally-produced
    stream. `get_frame_parameters()`/`decompressobj()` don't recognize
    these at all -- they misread the frame's own 4-byte Frame_Size field
    as a bogus content-size declaration, producing a false "truncated"
    rejection for a stream `stream_reader()` decodes correctly. But
    skipping them blindly reopens a different hole: *only* skippable
    frames (including a lone one) must still be rejected, since a real
    data frame is never actually validated in that case (Codex review,
    two rounds)."""

    @staticmethod
    def _skippable(payload: bytes = b"hello") -> bytes:
        return struct.pack("<I", 0x184D2A50) + struct.pack("<I", len(payload)) + payload

    def test_read_blob_accepts_a_stream_with_an_interspersed_skippable_frame(
        self, tmp_path: Path
    ) -> None:
        import zstandard

        c = zstandard.ZstdCompressor(level=19)
        frame1 = c.compress(b'{"a": 1}')
        frame2 = c.compress(b"more")
        stream = frame1 + self._skippable() + frame2
        decoded = b'{"a": 1}more'

        # Premise: confirm real zstandard's stream_reader() really does
        # decode this correctly despite the interspersed skippable frame.
        dctx = zstandard.ZstdDecompressor()
        out = io.BytesIO()
        with dctx.stream_reader(io.BytesIO(stream)) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        assert out.getvalue() == decoded

        h = content_hash(decoded)
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", stream, compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            assert reader.read_blob(manifest["library_blobs"]["a.so"]) == decoded

    def test_read_blob_rejects_a_stream_made_only_of_skippable_frames(
        self, tmp_path: Path
    ) -> None:
        stream = self._skippable()
        h = content_hash(b"")
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", stream, compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            with pytest.raises(SnapshotError, match="corrupt or truncated zstd stream"):
                reader.read_blob(manifest["library_blobs"]["a.so"])

    def test_read_blob_walks_many_skippable_frames_in_near_linear_time(
        self, tmp_path: Path
    ) -> None:
        """A naive ``remaining = remaining[total:]`` on ``bytes`` copies
        the entire unread suffix on every skippable-frame iteration,
        making the walk quadratic in stored size -- confirmed empirically
        at ~11s for 200,000 tiny skippable frames (~1.6 MiB) before the
        fix to a zero-copy ``memoryview`` cursor, a real DoS vector since
        the archive reader permits stored blobs near 1 GiB (Codex
        review). A generous 5s bound comfortably separates the ~0.1s
        post-fix time from the ~11s pre-fix time without flaking on a
        loaded CI runner."""
        import time

        import zstandard

        n_frames = 200_000
        stream = self._skippable(payload=b"") * n_frames
        stream += zstandard.ZstdCompressor().compress(b"hello world")
        h = content_hash(b"hello world")
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", stream, compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            t0 = time.monotonic()
            decoded = reader.read_blob(manifest["library_blobs"]["a.so"])
            elapsed = time.monotonic() - t0
        assert decoded == b"hello world"
        assert elapsed < 5.0, f"expected near-linear walk, took {elapsed:.2f}s for {n_frames} frames"

    def test_read_blob_walks_many_small_real_frames_in_near_linear_time(
        self, tmp_path: Path
    ) -> None:
        """A separate, independent quadratic shape from the skippable-frame
        one above: feeding a whole (potentially huge) remaining buffer to
        ``decompressobj().decompress()`` in one call per real data frame
        makes ``.unused_data`` materialize a fresh copy of the *entire*
        unread tail each time -- confirmed empirically at ~8s for 160,000
        empty real data frames (~1.4 MiB) before the fix to incremental,
        geometrically-growing chunk feeding. A generous 5s bound
        comfortably separates the ~0.5s post-fix time from the ~8s
        pre-fix time without flaking on a loaded CI runner."""
        import time

        import zstandard

        n_frames = 160_000
        one_empty_frame = zstandard.ZstdCompressor().compress(b"")
        stream = one_empty_frame * (n_frames - 1)
        stream += zstandard.ZstdCompressor().compress(b"hello world")
        h = content_hash(b"hello world")
        path = tmp_path / "bundle.archive.zip"
        with zipfile.ZipFile(path, mode="w") as zf:
            zf.writestr(MANIFEST_MEMBER, json.dumps({"library_blobs": {"a.so": h}}))
            zf.writestr(f"blobs/{h}.json.zst", stream, compress_type=zipfile.ZIP_STORED)

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            t0 = time.monotonic()
            decoded = reader.read_blob(manifest["library_blobs"]["a.so"])
            elapsed = time.monotonic() - t0
        assert decoded == b"hello world"
        assert elapsed < 5.0, f"expected near-linear walk, took {elapsed:.2f}s for {n_frames} frames"


class TestSniffDoesNotConsumeAOneShotFifoProducer:
    """A *thread*-based writer doesn't reliably reproduce this: Python
    thread scheduling can let the writer's open()+write()+close() finish
    entirely before the sniff's own open() ever runs, masking the bug.
    Uses a real *subprocess* with an explicit delay instead, so the
    writer is deterministically still blocked in its own open()-for-write
    (a FIFO write-end open() blocks until >=1 reader connects) when the
    sniff runs -- exactly the scenario this fix targets: opening (even
    briefly, even nonblocking) a FIFO with no *intended* reader can
    complete that blocking open(), letting the writer proceed and close
    before the caller's real, separate read ever gets a chance to
    connect (Codex review, fresh evidence)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="no os.mkfifo on Windows")
    def test_sniff_leaves_the_one_shot_writer_for_the_real_read(
        self, tmp_path: Path
    ) -> None:
        import subprocess
        import threading
        import time

        from abicheck.storage.bundle_archive import open_regular_file_for_format_sniff

        fifo = tmp_path / "producer.fifo"
        os.mkfifo(fifo)
        payload = b'{"schema_version": 1, "per_library_snapshots": {}}'
        proc = subprocess.Popen(
            [sys.executable, "-c", f"open({str(fifo)!r}, 'wb').write({payload!r})"]
        )
        try:
            time.sleep(0.3)  # let the child block in its own open()-for-write
            fp, fmt = open_regular_file_for_format_sniff(fifo)
            assert fp is None and fmt == "json"
            # Give the child a chance to run its write()+exit *now*, if the
            # sniff's own open()+close() already connected-then-dropped a
            # reader from under it (the pre-fix bug) -- without this, the
            # real read below can race ahead and connect before the child
            # is scheduled, masking the bug non-deterministically.
            time.sleep(0.3)
            # The real, single read: run with a bounded join(), since a
            # regression here is a hang, indistinguishable from "still
            # running" without one.
            result: list[bytes] = []

            def _read() -> None:
                with open(fifo, "rb") as f:
                    result.append(f.read())

            t = threading.Thread(target=_read, daemon=True)
            t.start()
            t.join(timeout=5)
            assert not t.is_alive(), "the real read blocked on the FIFO"
            assert result == [payload]
            assert proc.wait(timeout=5) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

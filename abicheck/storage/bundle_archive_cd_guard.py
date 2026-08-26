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

"""Central-directory bomb guard for ``bundle_archive.py`` (G40), split out
of that module purely to stay under its ADR-061 800-line production cap --
this is a cohesive, self-contained unit (read the EOCD/ZIP64 fields
ourselves, before ``zipfile.ZipFile`` ever parses them) with no other
coupling to the rest of that module beyond the two imports below.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..errors import SnapshotError

#: Bytes to search from the end of the file for the End-Of-Central-
#: Directory record's signature -- the record itself is 22 bytes plus up
#: to a 64 KiB archive comment (the zip format's own comment-length field
#: is 2 bytes), so this comfortably covers the worst case.
_EOCD_SEARCH_WINDOW_BYTES = 65536 + 22

#: Cap on the central directory's own declared byte size: the entry-
#: *count* cap above isn't itself a byte-size bound -- a low
#: `total_entries` can still pair with an enormous `cd_size`, parsed in
#: full regardless of entry count. A real archive's directory is small
#: (~120 bytes/record); generous but bounded.
_MAX_CENTRAL_DIRECTORY_BYTES = 8 * 1024 * 1024

#: ZIP64 EOCD Locator (20 bytes, always immediately preceding the
#: standard EOCD when ZIP64 is in play) and Record signatures -- recover
#: the real count/size when the standard EOCD's fields overflow.
_ZIP64_EOCD_LOCATOR_SIG = b"PK\x06\x07"
_ZIP64_EOCD_RECORD_SIG = b"PK\x06\x06"
_ZIP64_EOCD_LOCATOR_SIZE = 20


def _actual_central_directory_entry_count(
    f: Any, *, cd_offset: int, cd_size: int, max_entries: int
) -> int:
    """Walk the central directory's own file-header records directly,
    counting them ourselves rather than trusting the EOCD/ZIP64 record's
    declared total_entries -- CPython's `zipfile._RealGetContents()`
    parses every record it can find within `cd_size` bytes regardless of
    what total_entries claims, so an archive understating total_entries
    while `cd_size` (independently capped, but still generously sized)
    holds far more minimal-sized real records bypasses a total_entries-
    only cap entirely (Codex review, fresh evidence). Bails as soon as
    the count exceeds max_entries, so this never walks more than
    max_entries + 1 records even for a maximally record-dense directory.
    A record it can't fully parse (truncated/malformed) stops the walk
    early rather than raising -- `zipfile.ZipFile`'s own parse is
    authoritative for that failure mode, same as this module's other
    best-effort fallbacks."""
    f.seek(cd_offset)
    buf = f.read(cd_size)
    count = 0
    pos = 0
    while pos + 46 <= len(buf) and buf[pos : pos + 4] == b"PK\x01\x02":
        filename_len = int.from_bytes(buf[pos + 28 : pos + 30], "little")
        extra_len = int.from_bytes(buf[pos + 30 : pos + 32], "little")
        comment_len = int.from_bytes(buf[pos + 32 : pos + 34], "little")
        record_len = 46 + filename_len + extra_len + comment_len
        if pos + record_len > len(buf):
            break
        count += 1
        if count > max_entries:
            return count
        pos += record_len
    return count


def reject_absurd_central_directory(f: Any, path: Path, *, max_entries: int) -> int | None:
    """Reject *path* if its central directory claims more than *max_entries*
    entries or `_MAX_CENTRAL_DIRECTORY_BYTES` -- read directly from the EOCD
    (and, when present, the ZIP64 EOCD locator/record), without invoking
    `zipfile.ZipFile`'s own central-directory parse (the unbounded work
    this preflights against).

    *f* is an already-open, readable, seekable binary file object -- the
    *same* one the caller hands to `zipfile.ZipFile`, not a path this
    function reopens itself (Codex review, fresh evidence): reopening the
    path let a concurrent replacement between this check and `ZipFile`'s
    own open swap in a different generation, bypassing every guard here.
    Left at its own read position on return.

    Returns the file's own size as observed at the *start* of this check
    (or `None` if even that couldn't be determined) -- the caller re-
    fstat()s immediately before constructing `zipfile.ZipFile` and rejects
    a mismatch, since sharing one fd closes a *path-substitution* race but
    not an *in-place content* one: another writer with access to this
    same inode could still grow the file between this check returning and
    `ZipFile`'s own independent, unbounded scan from the (by-then-larger)
    current end of file (Codex review, fresh evidence, reproduced: a
    one-entry file grown to four entries after this check returned still
    had all four parsed by `ZipFile` despite a three-entry limit). This
    narrows that window to the two adjacent statements at the call site
    rather than closing it outright -- true immutability would need a
    stable, separately-materialized copy of the archive bytes, a much
    larger change than this preflight's own scope.

    Best-effort only for "the EOCD itself can't be found/read" (a
    genuinely truncated/non-zip file) -- `zipfile.ZipFile`'s own error is
    authoritative there. The ZIP64 EOCD locator is inspected
    *unconditionally*, not only when the standard EOCD's own
    `total_entries`/`cd_size` fields overflow to their sentinel values
    (`0xFFFF`/`0xFFFFFFFF`): CPython's own `zipfile._EndRecData` always
    looks for a locator immediately preceding the EOCD and, when a valid
    one is found, always prefers its record's values -- regardless of
    whether the standard EOCD's fields happen to signal an overflow. A
    hostile archive can therefore leave small, sentinel-free values in the
    standard EOCD while a real ZIP64 locator/record right behind it names
    an oversized directory; gating the locator lookup on the sentinel
    would let that straight through this preflight (Codex review, fresh
    evidence). A sentinel set with no locator to back it up, or a locator
    pointing at no valid record, is still rejected outright either way.
    """
    try:
        size = os.fstat(f.fileno()).st_size
    except OSError:
        return None
    tail_len = min(size, _EOCD_SEARCH_WINDOW_BYTES)
    try:
        f.seek(size - tail_len)
        tail = f.read(tail_len)
        idx = tail.rfind(b"PK\x05\x06")
        if idx == -1 or idx + 22 > len(tail):
            return size
        # EOCD layout: signature(4) this_disk(2) cd_start_disk(2)
        # entries_this_disk(2) total_entries(2) cd_size(4) cd_offset(4)
        # comment_len(2) [comment...]. cd_offset itself is deliberately
        # never read -- see cd_start's own comment below for why.
        total_entries = int.from_bytes(tail[idx + 10 : idx + 12], "little")
        cd_size = int.from_bytes(tail[idx + 12 : idx + 16], "little")
        is_zip64_sentinel = total_entries == 0xFFFF or cd_size == 0xFFFFFFFF

        # The locator is always the fixed 20 bytes immediately preceding
        # the standard EOCD's signature -- looked up regardless of the
        # sentinel, per this function's own docstring.
        eocd_abs = (size - tail_len) + idx
        locator_start = eocd_abs - _ZIP64_EOCD_LOCATOR_SIZE
        locator = b""
        if locator_start >= 0:
            f.seek(locator_start)
            locator = f.read(_ZIP64_EOCD_LOCATOR_SIZE)
        has_locator = len(
            locator
        ) == _ZIP64_EOCD_LOCATOR_SIZE and locator.startswith(_ZIP64_EOCD_LOCATOR_SIG)

        if not has_locator:
            if is_zip64_sentinel:
                reason = (
                    "the file is too short to hold a ZIP64 EOCD locator"
                    if locator_start < 0
                    else "no valid ZIP64 EOCD locator precedes it"
                )
                raise SnapshotError(
                    f"{path}: EOCD signals ZIP64 (entry-count/central-"
                    f"directory-size sentinel set) but {reason} -- "
                    "refusing to open (malformed or hostile archive)."
                )
        else:
            zip64_eocd_offset = int.from_bytes(locator[8:16], "little")
            # A crafted locator can name an offset past the platform's
            # representable file-offset range (e.g. 2**64-1); f.seek()
            # raises ValueError for that, not OSError, so it would
            # otherwise escape the except clause below as a raw
            # exception instead of this module's SnapshotError contract
            # (Codex review, fresh evidence). Bounding against the
            # file's own size also rejects it as the malformed archive
            # it is, without relying on the seek call to fail at all.
            if zip64_eocd_offset >= size:
                raise SnapshotError(
                    f"{path}: a ZIP64 EOCD locator names an offset "
                    f"({zip64_eocd_offset}) at or beyond the file's own "
                    f"size ({size}) -- refusing to open (malformed or "
                    "hostile archive)."
                )
            f.seek(zip64_eocd_offset)
            # Fixed portion only (56 bytes) -- signature/total_entries/
            # cd_size all live within it; no need for the record's own
            # variable "extensible data sector" tail.
            record = f.read(56)
            if len(record) != 56 or not record.startswith(_ZIP64_EOCD_RECORD_SIG):
                raise SnapshotError(
                    f"{path}: a ZIP64 EOCD locator precedes the central "
                    "directory but points at no valid ZIP64 EOCD record -- "
                    "refusing to open (malformed or hostile archive)."
                )
            total_entries = int.from_bytes(record[32:40], "little")
            cd_size = int.from_bytes(record[40:48], "little")
    except OSError:
        return size
    # Where CPython's own zipfile._RealGetContents() actually seeks for
    # the central directory: `start_dir = offset_cd + concat`, where
    # `concat = eocd_location - size_cd - offset_cd` -- the claimed
    # cd_offset/diroffset field cancels out of that sum entirely, leaving
    # `start_dir = eocd_location - size_cd` (non-ZIP64) or, for ZIP64,
    # the identical shape anchored on the ZIP64 record's own verified
    # position instead of the standard EOCD's. This is deliberately not
    # the field this preflight itself decoded from either record: a
    # concatenated/self-extracting archive's *claimed* offset is written
    # relative to the start of the zip data, not the whole file, so
    # trusting it directly here (as an earlier revision of this function
    # did) let a crafted archive's directory sit somewhere this guard
    # never actually walked while `ZipFile` correctly rebased and parsed
    # the real one (Codex review, fresh evidence, reproduced: a prefixed
    # 20,001-entry archive whose EOCD count was patched to 1 counted zero
    # records here while `ZipFile` materialized all 20,001).
    record_position = zip64_eocd_offset if has_locator else eocd_abs
    cd_start = record_position - cd_size
    if total_entries > max_entries:
        raise SnapshotError(
            f"{path}: central directory claims {total_entries} entries, "
            f"exceeding the {max_entries} safety limit -- refusing "
            "to open (possible memory-exhaustion attack, or a genuinely "
            "malformed archive)."
        )
    if cd_size > _MAX_CENTRAL_DIRECTORY_BYTES:
        raise SnapshotError(
            f"{path}: central directory claims {cd_size} bytes, exceeding "
            f"the {_MAX_CENTRAL_DIRECTORY_BYTES} byte safety limit -- "
            "refusing to open (possible memory-exhaustion attack, or a "
            "genuinely malformed archive)."
        )
    # The checks above only bound what the EOCD/ZIP64 record *claims* --
    # `zipfile.ZipFile` parses every record it can actually find within
    # `cd_size` bytes, regardless of `total_entries`, so an archive
    # understating that field while `cd_size` still holds far more real
    # (possibly minimal-sized) records would sail through the check above
    # unnoticed. Counted for real here, bounded to at most `cd_size`
    # bytes (already capped) and stopped as soon as the count itself
    # exceeds the limit.
    if 0 <= cd_start < size:
        try:
            actual_entries = _actual_central_directory_entry_count(
                f, cd_offset=cd_start, cd_size=cd_size, max_entries=max_entries
            )
        except OSError:
            return size
        if actual_entries > max_entries:
            raise SnapshotError(
                f"{path}: central directory actually contains more than "
                f"{max_entries} records (its own declared total_entries "
                f"understated this), exceeding the safety limit -- "
                "refusing to open (possible memory-exhaustion attack, or "
                "a genuinely malformed archive)."
            )
    return size

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


def reject_absurd_central_directory(f: Any, path: Path, *, max_entries: int) -> None:
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
        return
    tail_len = min(size, _EOCD_SEARCH_WINDOW_BYTES)
    try:
        f.seek(size - tail_len)
        tail = f.read(tail_len)
        idx = tail.rfind(b"PK\x05\x06")
        if idx == -1 or idx + 22 > len(tail):
            return
        # EOCD layout: signature(4) this_disk(2) cd_start_disk(2)
        # entries_this_disk(2) total_entries(2) cd_size(4) cd_offset(4)
        # comment_len(2) [comment...]
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
        return
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

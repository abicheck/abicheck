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

"""Whether a ``--header`` file is header text at all.

A raw NUL byte is never valid C/C++ header text (a NUL inside a literal is
spelled ``\\0``). A file holding one is a binary, or an archive misnamed as a
header. Two consumers need the answer, and they must agree:

* the header expanders refuse such a file as an input error rather than
  parsing it to a zero-declaration surface (which reads as removals);
* the ``--header`` transport classifier must not route an all-NUL file to the
  development-package path: a file of only NUL bytes is a valid *empty* tar
  archive to the package sniffer, which unpacks to no header at all and so
  drops the side's header evidence without a word.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["first_nul_offset", "is_all_nul", "non_text_header_message"]

#: Chunk size for the NUL scan. A header is read once, in bounded pieces, so a
#: large header directory costs one buffer per file at a time.
_NUL_SCAN_CHUNK = 64 * 1024


def first_nul_offset(path: Path) -> int | None:
    """Byte offset of the first NUL byte in *path*, or ``None`` when there is none.

    Stops reading at the first hit.
    """
    offset = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_NUL_SCAN_CHUNK):
            index = chunk.find(b"\x00")
            if index >= 0:
                return offset + index
            offset += len(chunk)
    return None


def is_all_nul(path: Path) -> bool:
    """Whether *path* is non-empty and holds nothing but NUL bytes.

    Stops reading at the first byte that is not NUL, so a real archive (whose
    magic is among its first bytes) is rejected after one small read.
    """
    saw_byte = False
    with path.open("rb") as fh:
        while chunk := fh.read(_NUL_SCAN_CHUNK):
            saw_byte = True
            # strip removes NULs only from the ends; any other byte survives.
            if chunk.strip(b"\x00"):
                return False
    return saw_byte


def non_text_header_message(path: Path) -> str | None:
    """The input-error message for a header that is not valid text, else ``None``."""
    offset = first_nul_offset(path)
    if offset is None:
        return None
    return (
        f"Header is not valid text (NUL byte at offset {offset}): {path}. "
        "A header must be source text; a binary, or a file of only NUL bytes, "
        "is not one."
    )

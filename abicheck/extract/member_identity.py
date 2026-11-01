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

"""What a release member *is*, read from its content rather than its name.

Two facts about one discovered member file, both answered from its bytes:

* :func:`recorded_library_name` -- the library a stored snapshot document
  records it was dumped from (``AbiSnapshot.library``, which every platform
  dumper sets to the artifact's own ``Path.name``). A directory compare pairs
  members by a canonical library key; deriving that key from a stored file's
  *own* name made ``libfoo.json`` (a dump of ``libfoo.so.1``) and the live
  ``libfoo.so.1`` two different members, so a stored directory against a
  live one paired nothing. Keying a stored member by the library it records
  makes the two one member, on every route that pairs through
  :func:`abicheck.binary_utils.build_match_map`, and agrees with a stored
  ``ProjectSnapshot`` package, whose members are already keyed by their
  recorded filename (``workflows.release_package._release_match_key``).
* :func:`is_content_readable_member` -- whether any reader recognizes the
  file by content (a native ELF/PE/Mach-O image, or a stored snapshot
  document). Discovery accepts ``libfoo.so``-style names on the name alone,
  to keep text linker-script stubs discoverable, so an unreadable member can
  reach the scope record; it must be recorded ``failed``, not as a plain
  one-sided member.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

#: How many leading bytes decide whether a non-native file is worth
#: decoding as a JSON document at all.
_HEAD_BYTES = 64


def _looks_like_json_document(path: Path) -> bool:
    """A plain JSON object on disk, or a recognizably compressed file (the
    snapshot reader decodes gzip/zstd by magic bytes, ADR-059)."""
    from ..errors import SnapshotError
    from ..snapshot_io import SnapshotCompression, detect_snapshot_compression

    try:
        if detect_snapshot_compression(path) is not SnapshotCompression.NONE:
            return True
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except (OSError, ValueError, SnapshotError):
        # `detect_snapshot_compression` reports an unopenable path as
        # `SnapshotError`; such a path is simply not a readable document.
        return False
    return head.lstrip().startswith(b"{")


#: Every ``"library": <string | null>`` member in a JSON text. A string
#: inside another string has its quotes escaped, so it cannot match.
#: Stands for a ``library`` value the scan cannot read (not a string/null).
_UNREADABLE_VALUE = "\0unreadable"
_LIBRARY_MEMBER = re.compile(r'"library"\s*:\s*("(?:[^"\\]|\\.)*"|null)')


def _single_library_value(text: str) -> str | None:
    """The document's ``library`` string when a scan of *text* finds exactly
    one ``"library"`` member, else ``None`` (the caller then parses the whole
    document). Reading one field this way avoids building the full decoded
    document: a sectioned snapshot stores ``library`` in its ``provenance``
    section, after the declarations, so no bounded head read reaches it. A
    nested ``library`` (a SYCL backend record) makes the scan ambiguous,
    which falls back to the parse rather than guess."""
    values: set[str] = set()
    key = '"library"'
    at = text.find(key)
    while at != -1:
        # `str.find` scans at memchr speed; the anchored match only runs
        # where the key actually occurs.
        if at == 0 or text[at - 1] != "\\":
            m = _LIBRARY_MEMBER.match(text, at)
            # A `library` member whose value is not a string or null (a
            # number, an object) is a value the scan cannot read: count it
            # as its own value, so the scan is ambiguous and the caller
            # parses the document instead of trusting a nested string.
            values.add(m.group(1) if m is not None else _UNREADABLE_VALUE)
        at = text.find(key, at + len(key))
    if len(values) != 1:
        return None
    (raw,) = values
    if raw in ("null", _UNREADABLE_VALUE):
        return None
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, str) else None


def recorded_library_name(path: Path) -> str | None:
    """The library file name a stored snapshot document at *path* records.

    ``None`` for a native binary (its own name is its identity), for a file
    that is not a snapshot document, and for a snapshot that records no
    library -- the caller then keys the member by its file name, as before.
    Never raises: an unreadable file is the scope record's concern
    (:func:`is_content_readable_member`), not the pairing key's.
    """
    from ..binary_utils import detect_binary_format

    # No `is_file()`: a path that cannot be stat'ed or opened is simply not
    # a readable document, and both probes below already say so.
    if detect_binary_format(path) is not None or not _looks_like_json_document(path):
        return None
    from ..snapshot_io import read_snapshot_text
    from ..storage.sectioned_document import (
        from_sectioned_document,
        is_sectioned_document,
    )

    try:
        text = read_snapshot_text(path)
        library = _single_library_value(text)
        if library is None:
            document = json.loads(text)
            if not isinstance(document, dict):
                return None
            if is_sectioned_document(document):
                document = from_sectioned_document(document)
            library = document.get("library")
    except Exception as exc:  # noqa: BLE001 -- any undecodable document keys by name
        log.debug("member identity: %s is not a readable snapshot: %s", path, exc)
        return None
    if not isinstance(library, str) or not library.strip():
        return None
    # The dumper records `Path.name`; a hand-written document may carry a
    # path, and only its last component is a library file name.
    name = Path(library.strip().replace("\\", "/")).name
    return name or None


def is_content_readable_member(path: Path) -> bool:
    """Whether a reader recognizes *path* by content: a native ELF, PE or
    Mach-O image, a stored ABI snapshot document, or a GNU ld linker script
    whose ``INPUT()``/``GROUP()`` chain ends at one (extraction follows it,
    ``binary_utils.follow_linker_script``). Applies the content classifiers
    of discovery's own pipeline (``classify``), never the name-only one, so
    ``notes.so`` holding text is unreadable while a snapshot named
    ``libfoo.so.1`` is readable.

    A recognized format is not a parse: a truncated ELF still reads as
    readable here and fails at extraction, which only a paired member
    reaches."""
    from ..binary_utils import resolve_linker_script_chain
    from ..classify import is_content_recognized_compare_input

    if is_content_recognized_compare_input(path):
        return True
    target = resolve_linker_script_chain(path)
    return target != path and is_content_recognized_compare_input(target)

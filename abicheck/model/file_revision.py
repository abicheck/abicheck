# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Content witness for memoized scans of a file's text.

A memo over a file's text is only sound while the file keeps its bytes; the witness
is the digest of those bytes, taken on every lookup.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Hashable
from pathlib import Path


def content_witness(path: str | os.PathLike[str]) -> Hashable:
    """The SHA-256 of a file's bytes, for a *witness* on a text-file scan.

    A stat stamp (mtime, size) can repeat across a rewrite that keeps the size and
    lands within one timestamp tick, so a scan of a header's text would return the
    old answer. The digest changes with any byte change. ``None`` when the file
    cannot be read: then the scan's own unreadable answer is what the memo holds.
    """
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None

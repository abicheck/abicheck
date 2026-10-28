#!/usr/bin/env python3
"""Where a unified diff's hunk bodies end, for the mutation gate's diff readers.

Split out of ``mutation_scope.py`` to keep it under its file-size cap.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_HUNK_COUNTS = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def diff_lines_outside_hunk_bodies(diff_text: str) -> Iterator[str]:
    """Every line of *diff_text* except the body lines of its hunks.

    A hunk header states how many old and new lines its body holds, and only
    that count says where the body ends: a removed source line ``-- note``
    is the body line ``--- note`` and an added ``++ x`` is ``+++ x``, both
    spelled like a file header. Readers that match header prefixes line by
    line read them as one. Counting is what ``git apply`` does. A line that
    cannot be body (no ``-``/``+``/space/``\\`` prefix) ends the count and is
    yielded, so a malformed hunk still reaches the caller's own checks.
    """
    old_left = new_left = 0
    for line in diff_text.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("-"):
                old_left -= 1
                continue
            if line.startswith("+"):
                new_left -= 1
                continue
            if line.startswith(" "):
                old_left, new_left = old_left - 1, new_left - 1
                continue
            if line.startswith("\\"):
                continue
            old_left = new_left = 0
        match = _HUNK_COUNTS.match(line)
        if match:
            old_left = int(match.group(1) or 1)
            new_left = int(match.group(2) or 1)
        yield line

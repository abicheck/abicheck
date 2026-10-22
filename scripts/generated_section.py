"""Splice a generated region between `<!-- BEGIN/END GENERATED: <marker> -->`
sentinels, leaving the hand-written narrative around it untouched."""

from __future__ import annotations

import re


def splice_generated(text: str, marker: str, inner: str, label: object) -> str:
    """*text* with its *marker* region replaced by *inner*; *label* names the
    file in the error raised for missing or misordered sentinels."""
    begin = re.search(rf"<!-- BEGIN GENERATED: {marker} -->\n", text)
    end = re.search(rf"\n<!-- END GENERATED: {marker} -->", text)
    if not begin or not end or begin.end() > end.start():
        raise ValueError(f"{label}: missing/misordered sentinels for {marker!r}")
    return text[: begin.end()] + inner + text[end.start() :]

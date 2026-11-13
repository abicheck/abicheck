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

"""Record export-table and debug-info read failures the parsers swallow.

``parse_{elf,pe,macho}_metadata`` keep going past a malformed section and
return whatever they read, so a library whose symbol table failed to parse
looks exactly like one exporting nothing. A caller that must tell the two
apart wraps the parse in :func:`recording_export_read_failures`; each
swallowing site that loses export-table facts calls
:func:`note_export_read_failure`. Outside a recording block a note is a
no-op, so the parsers' other callers see no change.

Debug info has the same shape: the DWARF readers skip a compilation unit
they cannot parse and return what the others held, so a corrupt
``.debug_abbrev`` read as "no types" and a compare against it reported the
lost types as added (exit 0). :func:`note_debug_info_read_failure` and
:func:`debug_info_read_required` are the debug-info counterparts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from ..errors import SnapshotError

_SINK: ContextVar[list[str] | None] = ContextVar(
    "abicheck_export_read_failures", default=None
)


def note_export_read_failure(reason: str) -> None:
    """Record that an export-table read failed for *reason*."""
    sink = _SINK.get()
    if sink is not None:
        sink.append(reason)


@contextmanager
def recording_export_read_failures() -> Iterator[list[str]]:
    """Collect every :func:`note_export_read_failure` made inside the block."""
    sink: list[str] = []
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


@contextmanager
def export_table_read_required(binary: Any) -> Iterator[None]:
    """Refuse to produce a snapshot from a binary whose export table was not read.

    A parser that swallowed a failure returns whatever it managed to read, and
    an unreadable export table looks like one that exports nothing; a dump
    built on that becomes a clean baseline or a clean comparison. Wrap the
    dump's reads of *binary* in this block: any failure noted inside it
    raises :class:`~abicheck.errors.SnapshotError` (the same exit-1 error a
    truncated binary gets) instead of producing an empty surface. An
    exception raised by the body propagates unchanged.
    """
    with recording_export_read_failures() as failures:
        yield
    if failures:
        reasons = "; ".join(dict.fromkeys(failures))
        raise SnapshotError(f"export table of {binary} was not read: {reasons}")


_DEBUG_SINK: ContextVar[list[str] | None] = ContextVar(
    "abicheck_debug_info_read_failures", default=None
)


def note_debug_info_read_failure(reason: str) -> None:
    """Record that a debug-info read lost facts for *reason* (no-op outside
    :func:`debug_info_read_required`)."""
    sink = _DEBUG_SINK.get()
    if sink is not None:
        sink.append(reason)


@contextmanager
def recording_debug_info_read_failures() -> Iterator[list[str]]:
    """Collect every :func:`note_debug_info_read_failure` made inside the block."""
    sink: list[str] = []
    token = _DEBUG_SINK.set(sink)
    try:
        yield sink
    finally:
        _DEBUG_SINK.reset(token)


@contextmanager
def debug_info_read_required(binary: Any) -> Iterator[None]:
    """Refuse a dump whose debug info was present but could not be read.

    The DWARF readers return what they managed to read; a compilation unit
    they skipped looks exactly like one declaring nothing, so the dump's type
    surface silently shrinks and a compare reads the lost types as added or
    removed. Any :func:`note_debug_info_read_failure` made inside the block
    raises :class:`~abicheck.errors.SnapshotError` (exit 1). A binary with no
    debug info at all notes nothing: absence is not a failure.
    """
    with recording_debug_info_read_failures() as sink:
        yield
    if sink:
        reasons = "; ".join(dict.fromkeys(sink))
        raise SnapshotError(
            f"debug info of {binary} is present but could not be read: {reasons}"
            " (to compare its exported symbols without it, strip the debug"
            " sections, e.g. `objcopy --strip-debug`)"
        )

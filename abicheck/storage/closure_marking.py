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

"""How a snapshot *load* finds out, as cheaply as possible, whether its
closure/anonymous-marker steps have anything to do.

Both load-time steps -- `snapshot_load_normalization`'s spelling strip and
`closure_identity.renumber_anonymous_closure_identities` -- begin by walking
every identity string of the snapshot just to learn whether any marker is
present, and both are no-ops when none is. This module answers that once:

* `json_text_may_hold_marker` answers it from the document *text*, before
  the snapshot exists, in a C-speed substring scan;
  `serialization.load_snapshot` records a negative answer with
  `markers_absent_from_source`, and the load skips both steps.
* `collect_closure_identity_marking` answers it from the snapshot with one
  marking walk whose result `renumber_with_marking` then reuses, so a load
  whose text did hold a marker walks once for the question and the
  renumbering together rather than once each.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TypeVar

from .closure_identity import (
    _LAMBDA_IDENTITY_FIELDS,
    _container_of,
    _defer_renumber,
    _has_any_marker,
    _renumber_collected,
)
from .closure_marker_walk import _collect_marking

_SnapshotT = TypeVar("_SnapshotT")

#: Every substring a closure/anonymous marker can begin with -- a superset
#: of `closure_identity._has_any_marker`'s own prefixes (no trailing
#: space), so a text lacking all of them lacks every marker that test finds.
_SOURCE_MARKER_PREFIXES = ("(lambda", "(unnamed", "(anonymous")

#: A JSON ``\\u00XX`` escape of an ASCII character: the one way a JSON
#: document can spell a marker prefix without its literal characters.
_JSON_ASCII_ESCAPE_RE = re.compile(r"\\u00[0-7]")

_MARKERS_ABSENT_FROM_SOURCE: ContextVar[bool] = ContextVar(
    "abicheck_closure_markers_absent_from_source", default=False
)


def json_text_may_hold_marker(text: str) -> bool:
    """Whether the snapshot decoded from JSON document *text* could carry
    any closure/anonymous marker at all.

    ``False`` only when *text* contains none of the marker prefixes
    literally and no ``\\u00XX``-escaped ASCII character (an escape could
    spell a prefix without its literal characters). Every string the
    renumbering walks is decoded verbatim from the document -- no decode
    step synthesizes marker text -- so a document with no marker cannot
    produce a snapshot with one. ~0.5 s on a 238 MB document, where the
    walk it lets the load skip took several seconds.
    """
    return (
        any(prefix in text for prefix in _SOURCE_MARKER_PREFIXES)
        or _JSON_ASCII_ESCAPE_RE.search(text) is not None
    )


@contextlib.contextmanager
def markers_absent_from_source() -> Iterator[None]:
    """Declare that the document being decoded inside this block was proven
    marker-free by :func:`json_text_may_hold_marker`, so the load-time
    normalization and renumbering -- both no-ops on such a snapshot -- may
    be skipped without walking it. Opened only by
    ``serialization.load_snapshot``, the one reader holding the text."""
    token = _MARKERS_ABSENT_FROM_SOURCE.set(True)
    try:
        yield
    finally:
        _MARKERS_ABSENT_FROM_SOURCE.reset(token)


def source_proven_marker_free() -> bool:
    """Whether the caller is inside :func:`markers_absent_from_source`."""
    return _MARKERS_ABSENT_FROM_SOURCE.get()


def defer_active() -> bool:
    """Whether the calling thread is inside
    `closure_identity.defer_closure_identity_renumbering`."""
    return bool(getattr(_defer_renumber, "active", False))


class ClosureIdentityMarking:
    """One marking pass over a snapshot's `_LAMBDA_IDENTITY_FIELDS`: the
    containers walked, every string collected, and the ids of the subtrees
    that may hold a marker. Valid only until the snapshot is next mutated.
    """

    __slots__ = ("containers", "flagged", "strings")

    def __init__(
        self, containers: list[object], strings: list[str], flagged: set[int]
    ) -> None:
        self.containers = containers
        self.strings = strings
        self.flagged = flagged

    @property
    def has_marker(self) -> bool:
        """Whether any collected string mentions a closure/anonymous marker
        -- the question `_lambda_identity_containers_and_strings` answers."""
        return _has_any_marker(self.strings)


def collect_closure_identity_marking(snapshot: object) -> ClosureIdentityMarking:
    """The single walk `renumber_anonymous_closure_identities` begins with."""
    containers = [_container_of(snapshot, name) for name in _LAMBDA_IDENTITY_FIELDS]
    strings: list[str] = []
    flagged: set[int] = set()
    for container in containers:
        _collect_marking(container, strings, flagged)
    return ClosureIdentityMarking(containers, strings, flagged)


def renumber_with_marking(
    snapshot: _SnapshotT, marking: ClosureIdentityMarking
) -> _SnapshotT:
    """`renumber_anonymous_closure_identities`, reusing a *marking* taken of
    *snapshot* with no mutation since. Same deferral rule."""
    if defer_active():
        return snapshot
    return _renumber_collected(
        snapshot, marking.containers, marking.strings, marking.flagged
    )

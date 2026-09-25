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

"""Declaring-header attribution for the snapshot's flat ``name -> value`` maps.

``AbiSnapshot.constants``/``typedefs``/``typedefs_qualified`` are plain
``dict[str, str]`` fields: unlike a ``Function`` or ``RecordType`` their
entries carry no ``source_header``. Dump-time dependency scoping
(``dumper_scoping.scope_snapshot_excluding_dependencies``) filters every
other declaration kind by its declaring header, so without one these three
maps passed through verbatim -- a constant declared only in a header the
user explicitly ``--exclude-header``-ed was still extracted, and then
*gated* a comparison (reported: ``DEP_CONSTANT: modified`` rejected a run
whose own output said that header's declarations "were not observed").

:class:`HeaderAttributedMap` is the carrier. It **is** the ``dict`` the
field always held -- equality, iteration, serialization and every existing
reader are unchanged -- and additionally remembers, per key, the header the
producer saw that key declared in. The attribution is **runtime-only**: the
snapshot codec writes the mapping as a plain object and decodes a plain
``dict``, so a *loaded* snapshot carries no attribution and scoping leaves
its maps alone, exactly as it always did. That is the same "a loaded
snapshot cannot license itself" rule ``AbiSnapshot.live_source_evidence``
follows; scoping runs on a freshly extracted snapshot, which is where the
evidence exists.

A value object rather than a new ``AbiSnapshot`` field on purpose: the
attribution is a property of the map's own entries, it must survive
``dataclasses.replace`` and every construction site that forwards the map
untouched (there are over a dozen), and a snapshot field would also mean a
schema bump for something that is deliberately never persisted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "HeaderAttributedMap",
    "attributed",
    "declaring_header",
    "declaring_header_set",
    "has_attribution",
]


class HeaderAttributedMap(dict[str, str]):
    """A ``name -> value`` map that also records each key's declaring header.

    ``declaring_headers`` holds ``key -> header path`` for the keys whose
    header the producer knew; a key absent from it (or mapped to ``""``) has
    *unknown* origin, which every consumer must treat as "keep", never as
    "dependency". Only :func:`attributed` should construct one, so the two
    maps always share one key set.
    """

    __slots__ = ("declaring_headers",)

    def __init__(
        self,
        values: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        headers: Mapping[str, str | tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(values)
        # key -> every header a producer saw it declared in. A key that any
        # producer saw with *unknown* origin stays unknown (absent): one
        # unattributed sighting is enough to make "dependency-only" unproven.
        self.declaring_headers: dict[str, tuple[str, ...]] = {}
        for key, header in (headers or {}).items():
            found = (header,) if isinstance(header, str) else tuple(header)
            if key in self and found and all(found):
                self.declaring_headers[key] = tuple(dict.fromkeys(found))

    def __reduce__(self) -> tuple[Any, ...]:
        # ``__slots__`` plus a dict base: the default protocol would drop the
        # attribution on copy/pickle (release fan-out workers pickle results).
        return (type(self), (dict(self), dict(self.declaring_headers)))


def attributed(
    entries: Iterable[tuple[str, str, str]],
) -> HeaderAttributedMap:
    """Build a map from ``(key, value, declaring_header)`` triples.

    Later triples win for a repeated key -- value *and* header together, so
    the recorded header is always the one that produced the kept value.
    """
    values: dict[str, str] = {}
    headers: dict[str, str] = {}
    for key, value, header in entries:
        values[key] = value
        headers[key] = header
    return HeaderAttributedMap(values, headers)


def has_attribution(mapping: Mapping[str, str]) -> bool:
    """Whether *mapping* carries producer header attribution at all."""
    return isinstance(mapping, HeaderAttributedMap)


def declaring_header(mapping: Mapping[str, str], key: str) -> str:
    """*key*'s first recorded declaring header, or ``""`` when unknown."""
    found = declaring_header_set(mapping, key)
    return found[0] if found else ""


def declaring_header_set(mapping: Mapping[str, str], key: str) -> tuple[str, ...]:
    """Every header *key* was seen declared in; ``()`` when unknown.

    A multi-TU merge can see one name declared in several headers; a
    consumer asking "is this dependency-only?" must require *all* of them to
    be dependency headers, never just the first TU's.
    """
    if isinstance(mapping, HeaderAttributedMap):
        return mapping.declaring_headers.get(key, ())
    return ()

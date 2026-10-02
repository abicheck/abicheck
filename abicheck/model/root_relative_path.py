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

"""A path that is relative to an extraction root -- the only path shape an
identity component may carry (design-hardening plan Phase 3, F3).

An absolute path spells the checkout, the build directory, the user name
and the host separator; when one reaches an identity key, the same entity
gets a different id in every environment (H3's relocated-checkout,
path-with-a-space, symlinked-root and Windows-separator cells). Identity
functions therefore take :class:`RootRelativePath`, never ``str``: mypy
rejects a raw string, and the constructors reject an absolute spelling, so
an absolute path cannot be passed where an identity component is expected.

Constructors:

* :meth:`RootRelativePath.relative_to` -- the extraction-time constructor:
  an observed path plus the root it was observed under (each in any
  spelling: ``./``, ``..``, ``\\`` separators, a drive letter).
* :meth:`RootRelativePath.parse` -- a string a producer already recorded
  as root-relative (raises :class:`ValueError` on an absolute one).
* :meth:`RootRelativePath.from_recorded` -- the boundary for a stored
  ``str`` field of unknown provenance: an absolute spelling answers
  ``None`` (no identity evidence) instead of leaking into a key.
* :meth:`RootRelativePath.from_project_layout` -- for a recorded path whose
  root was never recorded (an L5 graph node's ``def_file``): anchored at
  its last conventional project-layout directory (``include``/``inc``/
  ``src``/``source``/``sources``); an absolute path with no such anchor
  answers ``None``.

Normalization is lexical only (no filesystem access, so it is
deterministic and host-independent): separators become ``/``, ``.``
segments drop, ``a/../b`` folds to ``b``. A path that climbs out of its
root (``../x``) is not root-relative and is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PROJECT_LAYOUT_MARKERS",
    "RootRelativePath",
    "is_absolute_spelling",
    "project_layout_spelling",
]

_DRIVE_RE = re.compile(r"[A-Za-z]:")

#: Conventional project-root directory names -- a superset of
#: ``header_utils._INCLUDE_ROOT_NAMES`` (also covering ``src``/``source``/
#: ``sources`` layouts). The anchor :meth:`RootRelativePath.from_project_layout`
#: strips a checkout prefix at when no root was recorded with the path.
PROJECT_LAYOUT_MARKERS: frozenset[str] = frozenset(
    {"include", "inc", "src", "source", "sources"}
)


def is_absolute_spelling(text: str) -> bool:
    """Whether *text* is absolute in POSIX or Windows spelling (``/x``,
    ``\\x``, ``\\\\host\\share``, ``C:``-prefixed), on any host."""
    return text.startswith(("/", "\\")) or bool(_DRIVE_RE.match(text))


def _segments(text: str) -> list[str] | None:
    """Lexically normalized segments of a relative *text*, or ``None``
    when it climbs above its own start."""
    out: list[str] = []
    for seg in text.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not out:
                return None
            out.pop()
            continue
        out.append(seg)
    return out


def _absolute_segments(text: str) -> tuple[str, list[str]]:
    """``(anchor, segments)`` of an absolute *text* (the anchor is the
    lower-cased drive, or ``""`` for a POSIX/UNC root)."""
    anchor = ""
    rest = text
    if _DRIVE_RE.match(text):
        anchor, rest = text[:2].lower(), text[2:]
    out: list[str] = []
    for seg in rest.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return anchor, out


@dataclass(frozen=True, slots=True)
class RootRelativePath:
    """A normalized, ``/``-separated path relative to an extraction root.
    The empty path (``""``) is the root itself."""

    posix: str

    def __post_init__(self) -> None:
        if is_absolute_spelling(self.posix):
            raise ValueError(f"absolute path is not root-relative: {self.posix!r}")
        segs = _segments(self.posix)
        if segs is None or "/".join(segs) != self.posix:
            raise ValueError(f"not a normalized root-relative path: {self.posix!r}")

    def __str__(self) -> str:
        return self.posix

    @classmethod
    def parse(cls, text: str) -> RootRelativePath:
        """Normalize a recorded relative *text*; :class:`ValueError` when it
        is absolute or climbs out of its root."""
        if is_absolute_spelling(text):
            raise ValueError(f"absolute path is not root-relative: {text!r}")
        segs = _segments(text)
        if segs is None:
            raise ValueError(f"path climbs out of its root: {text!r}")
        return cls("/".join(segs))

    @classmethod
    def from_recorded(cls, text: str | None) -> RootRelativePath | None:
        """*text* as identity evidence, or ``None`` when it is empty,
        absolute, or climbs out of its root -- never an exception and never
        an absolute spelling."""
        if not text:
            return None
        try:
            return cls.parse(text)
        except ValueError:
            return None

    @classmethod
    def relative_to(cls, path: str, root: str) -> RootRelativePath | None:
        """*path* relative to *root*, or ``None`` when *path* is absolute
        and lies outside *root*. A relative *path* is taken as already
        relative to *root*. Both are compared lexically after
        normalization, so ``/w/./c``, ``/w/x/../c`` and ``C:\\w\\c`` spell
        the same root as ``/w/c`` and ``c:/w/c`` respectively."""
        if not is_absolute_spelling(path):
            return cls.from_recorded(path) or (cls("") if path in (".", "./") else None)
        if not is_absolute_spelling(root):
            return None
        p, r = _absolute_segments(path), _absolute_segments(root)
        if p[0] != r[0]:
            return None
        p_segs, r_segs = p[1], r[1]
        if p_segs[: len(r_segs)] != r_segs:
            return None
        return cls("/".join(p_segs[len(r_segs) :]))

    @classmethod
    def from_project_layout(cls, path: str | None) -> RootRelativePath | None:
        """*path* anchored at its **last** project-layout marker segment
        (:data:`PROJECT_LAYOUT_MARKERS`), whatever its spelling: two
        independently rooted checkouts of one tree agree, and a real
        cross-directory move (``.../src/foo.h`` -> ``.../include/foo.h``)
        still differs. Without a marker a relative path is kept as
        recorded, and an absolute one answers ``None`` -- its checkout
        prefix cannot be told apart from the project path, so it is no
        identity evidence (an earlier consumer-side copy compared the full
        absolute path instead, reading every relocation as a move)."""
        if not path:
            return None
        absolute = is_absolute_spelling(path)
        if absolute:
            segs = _absolute_segments(path)[1]
        else:
            relative = _segments(path)
            if relative is None:
                return None
            segs = relative
        for i in range(len(segs) - 1, -1, -1):
            if segs[i].lower() in PROJECT_LAYOUT_MARKERS:
                return cls("/".join(segs[i:]))
        return None if absolute else cls("/".join(segs))


def project_layout_spelling(path: str | None) -> str:
    """:meth:`RootRelativePath.from_project_layout` as a plain string
    (``""`` when it answers ``None``), for a comparison key."""
    rel = RootRelativePath.from_project_layout(path)
    return rel.posix if rel is not None else ""

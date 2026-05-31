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

"""Declaration provenance — classify where a declaration's header sits
relative to the user-provided public-header set (ADR-015, schema v6).

Source locations recorded by the DWARF/castxml parsers are frequently
absolute *build* paths (e.g. ``/build/src/foo/include/api.h``) that bear
no resemblance to the paths the user passes on the command line.  Matching
is therefore done on path *segments* (suffix / basename / directory
containment) rather than by resolving real paths, which would be brittle
when a snapshot is produced on a different machine than the public-header
set is described on.

Classification is opt-in.  When the caller supplies no public-header set,
every declaration keeps :class:`~abicheck.model.ScopeOrigin.UNKNOWN` and
no existing behaviour changes (decision D4 of the provenance design).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .model import AbiSnapshot, ScopeOrigin, Visibility

# Directory prefixes that mark a header as belonging to the toolchain or the
# operating system rather than the project under test.  Matched as path-segment
# subsequences so build prefixes (``/sysroot/usr/include/...``) still classify.
_SYSTEM_HEADER_DIRS: tuple[tuple[str, ...], ...] = (
    ("usr", "include"),
    ("usr", "local", "include"),
    ("usr", "lib"),
    ("usr", "lib64"),
    ("Library", "Developer"),  # macOS SDK / Xfwk headers
    ("Applications", "Xcode.app"),  # macOS Xcode toolchain
    ("Program Files",),  # Windows SDK / MSVC
    ("VC", "Tools"),  # MSVC toolchain layout
    ("Windows Kits",),  # Windows SDK
)

# Path segments that mark a header as living in a machine-generated tree.
_GENERATED_DIR_SEGMENTS: frozenset[str] = frozenset(
    {"generated", "_generated", ".generated", "gen", "autogen"}
)

# Basename patterns produced by common code generators (Qt moc/uic/rcc,
# protobuf, flatbuffers, gRPC). Matched case-sensitively on the file name.
_GENERATED_BASENAME = re.compile(
    r"""(?x)
    ^moc_.*\.(?:h|hpp|cpp|cc)$       # Qt meta-object compiler
    | ^ui_.*\.h$                     # Qt uic
    | ^qrc_.*\.(?:cpp|cc)$           # Qt rcc
    | .*\.pb\.(?:h|cc)$              # protobuf
    | .*\.pb\.h$                     # protobuf (header)
    | .*_generated\.h$               # flatbuffers
    | .*\.grpc\.pb\.(?:h|cc)$        # gRPC
    """
)

# A trailing ``:line`` or ``:line:col`` appended to a header path by the
# parsers (e.g. ``include/api.h:42`` or ``api.h:42:9``).
_LINE_COL_SUFFIX = re.compile(r":\d+(?::\d+)?$")


def header_from_location(source_location: str | None) -> str | None:
    """Strip a trailing ``:line`` / ``:line:col`` from a source location,
    yielding just the header path.  Returns ``None`` for a falsy input."""
    if not source_location:
        return None
    return _LINE_COL_SUFFIX.sub("", source_location) or None


def _segments(path: str) -> tuple[str, ...]:
    """Path components in posix order, dropping anchors and ``.`` parts.

    Backslashes are normalised to forward slashes so Windows-style build
    paths segment the same way as posix ones.
    """
    posix = path.replace("\\", "/")
    parts = [p for p in PurePosixPath(posix).parts if p not in ("/", ".", "")]
    return tuple(parts)


def _contiguous_subsequence(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *needle* appears as a contiguous run inside *hay*."""
    n = len(needle)
    if n == 0:
        return False
    return any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def _suffix_match(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *hay* ends with the segments of *needle* (a path-suffix match)."""
    n = len(needle)
    return n > 0 and len(hay) >= n and hay[-n:] == needle


def _matches_public(
    header_segs: tuple[str, ...],
    public_header_segs: list[tuple[str, ...]],
    public_dir_segs: list[tuple[str, ...]],
) -> bool:
    """Suffix/basename match against public headers, plus directory
    containment against public-header directories."""
    basename = header_segs[-1] if header_segs else ""
    for p in public_header_segs:
        # Path-suffix match (build-prefix tolerant) or basename fallback.
        # The basename fallback carries a small false-positive risk on
        # duplicate basenames across trees — an accepted trade-off (D3).
        if _suffix_match(p, header_segs):
            return True
        if basename and p and p[-1] == basename:
            return True
    # Directory containment: a public dir appears among the header's parent dirs.
    parent_segs = header_segs[:-1]
    return any(_contiguous_subsequence(d, parent_segs) for d in public_dir_segs)


def _is_system_header(header_segs: tuple[str, ...]) -> bool:
    return any(_contiguous_subsequence(d, header_segs) for d in _SYSTEM_HEADER_DIRS)


def _is_generated_header(header_segs: tuple[str, ...]) -> bool:
    if not header_segs:
        return False
    if any(seg in _GENERATED_DIR_SEGMENTS for seg in header_segs[:-1]):
        return True
    return bool(_GENERATED_BASENAME.match(header_segs[-1]))


def classify_origin(
    source_header: str | None,
    public_header_segs: list[tuple[str, ...]],
    public_dir_segs: list[tuple[str, ...]],
    *,
    have_public_set: bool,
    export_only: bool = False,
) -> ScopeOrigin:
    """Classify a single declaration into a :class:`ScopeOrigin`.

    The ``*_segs`` arguments are pre-segmented public-header inputs (see
    :func:`build_public_set`).  When ``have_public_set`` is False the result
    is always ``UNKNOWN`` — provenance is opt-in.

    ``export_only`` marks a declaration that the binary exports but that has
    no header provenance (``Visibility.ELF_ONLY``); with a public set in play
    it classifies as ``EXPORT_ONLY`` rather than ``UNKNOWN``.
    """
    if not have_public_set:
        return ScopeOrigin.UNKNOWN
    header_segs = _segments(source_header) if source_header else ()
    if not header_segs:
        return ScopeOrigin.EXPORT_ONLY if export_only else ScopeOrigin.UNKNOWN
    if _matches_public(header_segs, public_header_segs, public_dir_segs):
        return ScopeOrigin.PUBLIC_HEADER
    if _is_generated_header(header_segs):
        return ScopeOrigin.GENERATED
    if _is_system_header(header_segs):
        return ScopeOrigin.SYSTEM_HEADER
    return ScopeOrigin.PRIVATE_HEADER


def build_public_set(
    public_headers: list[Path] | list[str] | None,
    public_header_dirs: list[Path] | list[str] | None,
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]], bool]:
    """Pre-segment the public-header inputs once for reuse across decls.

    Returns ``(public_header_segs, public_dir_segs, have_public_set)``.
    """
    headers = [_segments(str(h)) for h in (public_headers or [])]
    dirs = [_segments(str(d)) for d in (public_header_dirs or [])]
    headers = [h for h in headers if h]
    dirs = [d for d in dirs if d]
    return headers, dirs, bool(headers or dirs)


def apply_provenance(
    snapshot: AbiSnapshot,
    public_headers: list[Path] | list[str] | None = None,
    public_header_dirs: list[Path] | list[str] | None = None,
) -> AbiSnapshot:
    """Populate ``source_header`` and ``origin`` on every declaration in
    *snapshot*, in place, and return it.

    ``source_header`` is always derived from the existing ``source_location``
    (cheap, additive metadata).  ``origin`` is only classified when a
    public-header set is supplied; otherwise it stays ``UNKNOWN`` so default
    invocations are unaffected (decision D4).
    """
    header_segs, dir_segs, have_set = build_public_set(
        public_headers, public_header_dirs
    )

    def _tag(decl: object) -> None:
        loc = getattr(decl, "source_location", None)
        sh = header_from_location(loc)
        export_only = getattr(decl, "visibility", None) == Visibility.ELF_ONLY
        decl.source_header = sh  # type: ignore[attr-defined]
        decl.origin = classify_origin(  # type: ignore[attr-defined]
            sh,
            header_segs,
            dir_segs,
            have_public_set=have_set,
            export_only=export_only,
        )

    for fn in snapshot.functions:
        _tag(fn)
    for var in snapshot.variables:
        _tag(var)
    for rec in snapshot.types:
        _tag(rec)
    for en in snapshot.enums:
        _tag(en)
    return snapshot

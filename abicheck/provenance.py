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
from collections.abc import Sequence
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
    paths segment the same way as posix ones. A ``..`` segment is lexically
    collapsed against the segment before it (no filesystem access -- this
    module matches by path *segments*, never by resolving real paths, so
    normalization has to stay purely textual too): a build-recorded compiler
    path resolved via something like ``$(dirname "$CC")/..`` routinely
    carries a literal ``bin/..`` segment (confirmed against a real
    conda-forge/pixi toolchain path,
    ``.../envs/scanner/bin/../lib/gcc/x86_64-conda-linux-gnu/14.3.0/include/c++/...``),
    and every containment/prefix match in this module must recognize that as
    the same directory its already-collapsed form names -- otherwise a
    system/toolchain-header exclusion silently fails to match. A leading
    ``..`` with nothing left to collapse against is kept as-is.
    """
    posix = path.replace("\\", "/")
    parts = [p for p in PurePosixPath(posix).parts if p not in ("/", ".", "")]
    normalized: list[str] = []
    for p in parts:
        if p == ".." and normalized and normalized[-1] != "..":
            normalized.pop()
        else:
            normalized.append(p)
    return tuple(normalized)


def _contiguous_subsequence(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *needle* appears as a contiguous run inside *hay*."""
    n = len(needle)
    if n == 0:
        return False
    return any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def _suffix_match(needle: tuple[str, ...], hay: tuple[str, ...]) -> bool:
    """True if *hay* ends with the segments of *needle* (a path-suffix match)."""
    n = len(needle)
    return 0 < n <= len(hay) and hay[-n:] == needle


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


def _is_toolchain_compiler_include_dir(header_segs: tuple[str, ...]) -> bool:
    """True when *header_segs* sits inside a compiler's own private include
    tree, recognized *structurally* (with the toolchain's own version /
    target-triple path component treated as a wildcard) rather than by a
    fixed literal prefix.

    ``_SYSTEM_HEADER_DIRS`` only matches a handful of fixed, OS-rooted
    prefixes (``/usr/include``, an Xcode/MSVC SDK root, ...). A relocatable,
    non-OS-managed toolchain -- notably a conda-forge/pixi GCC, which is
    what this project's own GitHub Action installs -- puts its private
    headers under an arbitrary environment prefix instead, e.g.
    ``<prefix>/lib/gcc/x86_64-conda-linux-gnu/14.3.0/include/c++/...``, so no
    fixed prefix can ever match it. Confirmed against a real conda-forge/
    pixi install layout (a `func_removed` false positive was reported for
    libstdc++'s own ``_Iter_pred`` predicate helper via exactly this path).
    Recognizes, each with the triple/version component wildcarded:

    - ``lib/gcc/<triple>/<version>/include`` or ``.../include-fixed`` --
      GCC's own private (non-libstdc++) headers. conda-forge's own layout
      nests libstdc++'s ``c++`` tree directly under this same root (as in
      the confirmed real-world path above), so this one pattern already
      covers it -- no separate check is needed for that case.
    - ``lib/clang/<version>/include`` -- Clang's private builtin headers
      (``stddef.h``, the vector-intrinsic headers, ...).

    Deliberately does NOT match a bare ``include/c++/<version>`` anywhere in
    the path outside a recognized ``lib/gcc/...``/``lib/clang/...`` root
    (Codex review): an earlier revision did, unconditionally, which matched
    an ordinary project path like ``/project/include/c++/api.h`` that has no
    toolchain prefix at all -- a real false-positive risk this function
    exists to avoid, not create. The traditional (non-conda-forge)
    Debian/Ubuntu-style split layout, where libstdc++ lives separately at
    ``/usr/include/c++/<version>/`` rather than nested under ``lib/gcc/``,
    is unaffected: that path is already caught by ``_SYSTEM_HEADER_DIRS``'s
    own ``usr/include`` prefix in :func:`_is_system_header`, so this
    function does not need to duplicate it.

    A caller matching against a raw compiler-reported path should prefer
    normalizing it first (segments here are matched as given, with no
    further ``..``-collapsing beyond what :func:`_segments` already does).
    """
    n = len(header_segs)
    for i, seg in enumerate(header_segs):
        if seg != "lib":
            continue
        if (
            i + 4 < n
            and header_segs[i + 1] == "gcc"
            and header_segs[i + 4] in ("include", "include-fixed")
        ):
            return True
        if (
            i + 3 < n
            and header_segs[i + 1] == "clang"
            and header_segs[i + 3] == "include"
        ):
            return True
    return False


def _is_system_header(header_segs: tuple[str, ...]) -> bool:
    if _is_toolchain_compiler_include_dir(header_segs):
        return True
    return any(_contiguous_subsequence(d, header_segs) for d in _SYSTEM_HEADER_DIRS)


def _is_bare_system_dir(dir_segs: tuple[str, ...]) -> bool:
    """True when *dir_segs* is nothing more than a known system-header
    prefix (``/usr/include``, an alternate-sysroot-prefixed one, ...) with
    no project-specific subdirectory appended after it.

    Unlike :func:`_is_system_header` (a *contiguous-subsequence* match,
    true for a system prefix appearing anywhere, including as a strict
    prefix of something longer like ``/usr/include/mylib``), this is a
    *suffix* match: only true when the system prefix is the last thing in
    the path, i.e. the directory itself IS the bare system dir. Used to
    tell apart a file root installed flat in a system prefix (``-H
    /usr/include/zlib.h``, parent ``/usr/include`` -- must not become a
    project directory) from one installed under its own subdirectory
    there (``-H /usr/include/mylib/api.h``, parent ``/usr/include/mylib``
    -- a legitimate project directory that happens to sit under a system
    prefix).

    Also true for a bare toolchain compiler-include root recognized by
    :func:`_is_toolchain_compiler_include_dir` (``lib/gcc/<triple>/
    <version>/include[-fixed]``, ``include/c++/<version>``, ``lib/clang/
    <version>/include``) when the recognized boundary is the *last* thing in
    *dir_segs* -- the structural analogue of the fixed-prefix suffix check
    above, for the same "directory itself IS the toolchain root, nothing
    project-specific appended after" reason.
    """
    if any(_suffix_match(d, dir_segs) for d in _SYSTEM_HEADER_DIRS):
        return True
    if not _is_toolchain_compiler_include_dir(dir_segs):
        return False
    last = dir_segs[-1] if dir_segs else ""
    return last in ("include", "include-fixed", "c++")


def _is_generated_header(header_segs: tuple[str, ...]) -> bool:
    if not header_segs:
        return False
    if any(seg in _GENERATED_DIR_SEGMENTS for seg in header_segs[:-1]):
        return True
    return bool(_GENERATED_BASENAME.match(header_segs[-1]))


def is_generated_header(source_header: str | None) -> bool:
    """Whether a header path looks machine-generated (``moc_*``, ``*.pb.h``,
    a ``generated/`` directory, …).

    Public wrapper around the segment-based heuristic. ``classify_origin``
    checks the public-header set *before* this heuristic, so a header that is
    both public and generated classifies as ``PUBLIC_HEADER``; callers that need
    to preserve the generated marker (ADR-030 ``generated_header_changed``) can
    consult this directly.
    """
    if not source_header:
        return False
    return _is_generated_header(_segments(source_header))


def is_system_header(source_header: str | None) -> bool:
    """Whether a header path looks like a toolchain/system header
    (``/usr/include``, MSVC ``VC/Tools``, the Xcode/macOS SDK, ...).

    Public wrapper around the segment-based heuristic, usable independent of
    a public-header set (unlike :func:`classify_origin`, which gates
    *all* classification, including this check, behind ``have_public_set`` —
    D4's "opt-in" only applies to the PUBLIC_HEADER/PRIVATE_HEADER split;
    "is this a system header at all" is a pure function of the path and
    needs no public-header input). Used by ``dumper_scoping.py`` to exclude
    dependency-header declarations from a dump by default, without
    requiring the caller to declare a public-header set at all.
    """
    if not source_header:
        return False
    return _is_system_header(_segments(source_header))


_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:[/\\]")


def _absolutize_header_root(h: Path | str) -> Path:
    """Absolutize a ``-H``/``--header`` root, but only when it is genuinely
    *relative* (e.g. ``-H include/api.h``).

    An already-rooted path -- POSIX-style (a leading ``/``), a Windows
    drive root (``C:\\...``), or a UNC path (``\\\\server\\share\\...``) --
    is returned unchanged. Unconditionally calling :meth:`Path.resolve` here
    (an earlier version of this fix did) is wrong on Windows: resolving a
    POSIX-style already-rooted string like ``/usr/include/mylib/api.h``
    drive-anchors it to the current working directory's drive (e.g.
    ``D:\\usr\\include\\mylib\\api.h``), producing a segment sequence that no
    longer matches the very same string's own -- never resolved -- form
    when it later appears as a declaration's ``source_header`` (confirmed by
    a real Windows CI failure). This module's own docstring already commits
    to matching by path *segments* rather than resolving real paths for
    exactly this cross-machine-safety reason; resolving an already-rooted
    root broke that contract for itself.
    """
    s = str(h)
    normalized = s.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_ROOT_RE.match(s):
        return Path(s)
    return Path(h).resolve()


def is_dependency_header(
    source_header: str | None,
    header_roots: Sequence[Path | str] | None,
) -> bool:
    """Whether *source_header* is confidently a toolchain/dependency header,
    given the actual ``-H``/``--header`` root set a dump was invoked with.

    Unlike a bare :func:`is_system_header` path check, this treats any header
    that *is* one of the given roots, or lives under a root's own directory
    (even recursively, e.g. a private header the root ``#include``s), as
    never a dependency -- regardless of whether that directory happens to
    sit under a system prefix. This matters for an installed library
    analyzed via its real install path (``-H /usr/include/mylib/api.h`` or
    ``/usr/local/include/mylib/api.h``): without this check,
    ``is_system_header`` alone would misclassify the library's *own* headers
    as toolchain headers and silently drop the whole snapshot (Codex
    review). Reuses :func:`classify_origin`'s existing public-header-set
    precedence (an explicit match is checked before the system-header
    heuristic ever runs) by treating *header_roots* as that set -- the roots
    themselves as the "public headers" and their parent directories as the
    "public dirs", so both an exact-root match and anything living in the
    same directory tree win over the system-header classification.

    Falls back to a bare :func:`is_system_header` check when no
    *header_roots* were given at all (e.g. a dump built from an already
    in-memory snapshot with no recorded root set).
    """
    if not source_header:
        return False
    if not header_roots:
        return is_system_header(source_header)
    # Resolve relative roots (e.g. `-H include/api.h`) to absolute paths
    # before segmenting. Without this, a short relative parent directory
    # like `include` becomes a single-segment public dir, and
    # `_matches_public`'s contiguous-subsequence containment check then
    # matches that same generic segment inside *any* path containing an
    # "include" component -- including real system paths like
    # `/usr/include/...` -- defeating the exclusion entirely (Codex
    # review). Resolving first makes the root's own segments as specific
    # as the real filesystem location, so only paths actually under it
    # can match.
    #
    # `-H`/`--header` accepts a directory as well as a file (Click help:
    # "Public header file or directory"). Widening *every* root to its
    # parent unconditionally over-widens a directory root -- `-H
    # /usr/include/mylib` would turn into the public dir `/usr/include`,
    # making every unrelated header under that prefix (including real
    # dependency headers) match as project-owned (Codex review). Only a
    # *file* root widens to its parent; a directory root is used as-is.
    #
    # A file root installed flat in a system prefix (e.g. `-H
    # /usr/include/zlib.h`) is a further special case: its parent
    # (`/usr/include`) is not a *project* directory at all -- it's the bare
    # system prefix itself, with nothing appended -- so widening to it
    # would make every unrelated system header underneath match as
    # project-owned too, same failure shape as the directory-root case
    # above (Codex review). A root under a project *subdirectory* of a
    # system prefix (`-H /usr/include/mylib/api.h`, parent
    # `/usr/include/mylib`) is unaffected: that parent is not itself one of
    # the bare system-dir suffixes, only *within* one.
    resolved = [_absolutize_header_root(h) for h in header_roots]
    roots = [str(r) for r in resolved if not r.is_dir()]
    root_dirs = [
        str(r if r.is_dir() else r.parent)
        for r in resolved
        if r.is_dir() or not _is_bare_system_dir(_segments(str(r.parent)))
    ]
    header_segs, dir_segs, have_set = build_public_set(roots, root_dirs)
    origin = classify_origin(
        source_header, header_segs, dir_segs, have_public_set=have_set
    )
    return origin is ScopeOrigin.SYSTEM_HEADER


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


def _public_dirs_from_include_roots(
    include_search_dirs: list[Path] | list[str] | None,
) -> list[tuple[str, ...]]:
    """Segment *include_search_dirs* (a header-AST dump's own ``-I`` roots)
    into public-directory candidates, dropping a bare system-header prefix
    (``/usr/include``, an MSVC toolchain root, ...) the same way an
    :func:`is_system_header_path` root already is (Codex review: a stray
    ``-I /usr/include`` must not make every system header underneath
    classify as project-owned).
    """
    # Resolve first (mirroring the identical reasoning in
    # _absolutize_header_root / is_system_header_path above): an unresolved
    # relative root like ``.`` or ``include`` either segments to nothing at
    # all or becomes a short, generic segment that could spuriously match
    # unrelated paths sharing that same component elsewhere.
    segs = [_segments(str(_absolutize_header_root(d))) for d in (include_search_dirs or [])]
    return [s for s in segs if s and not _is_bare_system_dir(s)]


def apply_provenance(
    snapshot: AbiSnapshot,
    public_headers: list[Path] | list[str] | None = None,
    public_header_dirs: list[Path] | list[str] | None = None,
    *,
    include_search_dirs: list[Path] | list[str] | None = None,
) -> AbiSnapshot:
    """Populate ``source_header`` and ``origin`` on every declaration in
    *snapshot*, in place, and return it.

    ``source_header`` is always derived from the existing ``source_location``
    (cheap, additive metadata).  ``origin`` is only classified when a
    public-header set is supplied; otherwise it stays ``UNKNOWN`` so default
    invocations are unaffected (decision D4).

    ``include_search_dirs`` -- the ``-I`` roots a header-AST dump was given --
    are folded into the public-directory set, but *only* once a real
    ``-H``/``--public-header-dir`` set already opted classification in (they
    can never turn opt-in on by themselves). This closes the "every
    transitively-`#include`d header is private" gap: a header-AST dump only
    ever parses declarations reachable by `#include` from its own `-H`
    root(s) in the first place -- there is no other way for a declaration to
    end up in the snapshot at all -- so a header elsewhere under the same
    include root that the umbrella header pulled in is exactly as much a
    dependency of the public surface as the umbrella header itself, not a
    private implementation detail merely because it isn't the literal `-H`
    file. Treating it as `PRIVATE_HEADER` let a real, breaking layout change
    reached only transitively (e.g. a struct defined in a header the public
    umbrella `#include`s) silently drop out of the compared surface with no
    disclosure. System/generated headers are unaffected: a bare system-dir
    root is filtered out before folding, and ``classify_origin`` still checks
    the system/generated patterns for anything this doesn't match.
    """
    header_segs, dir_segs, have_set = build_public_set(
        public_headers, public_header_dirs
    )
    if have_set and include_search_dirs:
        dir_segs = [*dir_segs, *_public_dirs_from_include_roots(include_search_dirs)]
    # A large surface has far fewer distinct declaring headers than
    # declarations (e.g. thousands of oneDAL functions share a handful of
    # umbrella headers) — reuse each header's classification across every
    # declaration it produced instead of re-running classify_origin per decl.
    origin_cache: dict[tuple[str | None, bool], ScopeOrigin] = {}
    for fn in snapshot.functions:
        tag_provenance(fn, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    for var in snapshot.variables:
        tag_provenance(var, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    for rec in snapshot.types:
        tag_provenance(rec, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    for en in snapshot.enums:
        tag_provenance(en, header_segs, dir_segs, have_set, origin_cache=origin_cache)
    return snapshot


def tag_provenance(
    decl: object,
    header_segs: list[tuple[str, ...]],
    dir_segs: list[tuple[str, ...]],
    have_set: bool,
    *,
    origin_cache: dict[tuple[str | None, bool], ScopeOrigin] | None = None,
) -> None:
    """Populate ``source_header`` and ``origin`` on a single declaration in place.

    Factored out of :func:`apply_provenance` so other producers of model objects
    that bypass the snapshot path — notably the ADR-030 source ABI extractor,
    which parses headers directly — can classify origin against the same public
    set instead of leaving every declaration ``UNKNOWN``. ``header_segs`` /
    ``dir_segs`` come from :func:`build_public_set`.

    ``origin_cache``, when supplied by the caller, memoizes ``classify_origin``
    results by ``(source_header, export_only)`` across the whole batch of
    declarations sharing one ``header_segs``/``dir_segs``/``have_set`` — safe
    because those three inputs are fixed per caller and the classification is a
    pure function of them plus the declaration's own header/export-only pair.
    Omitted (the default) reproduces the previous uncached, per-call behaviour.
    """
    loc = getattr(decl, "source_location", None)
    sh = header_from_location(loc)
    export_only = getattr(decl, "visibility", None) == Visibility.ELF_ONLY
    decl.source_header = sh  # type: ignore[attr-defined]
    if origin_cache is None:
        origin = classify_origin(
            sh, header_segs, dir_segs, have_public_set=have_set, export_only=export_only
        )
    else:
        cache_key = (sh, export_only)
        cached = origin_cache.get(cache_key)
        if cached is None:
            cached = classify_origin(
                sh,
                header_segs,
                dir_segs,
                have_public_set=have_set,
                export_only=export_only,
            )
            origin_cache[cache_key] = cached
        origin = cached
    decl.origin = origin  # type: ignore[attr-defined]

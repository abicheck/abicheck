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

"""When two filesystem path *spellings* name the same header tree.

``provenance.py`` decides what a declaration's header means; this module
owns the narrower question underneath it -- given a path string, which
segment sequences may it legitimately be matched as. Two answers, and the
distinction between them is the whole design:

**Lexical spelling is evidence.** It is what a run was invoked with, what a
stored snapshot carries, and what a configuration digest is computed over.
It is always present, always first, and never replaced. Every match this
module enables is *additive*: a classification that held before aliasing
existed still holds.

**A canonical spelling is an optional local matching alias.** It exists so
that a symlinked include root and the real path a header parser reports can
meet -- the reported defect being ``-H /localdisk/.../inc`` against a
castxml source location under ``/mnt/cached_oses/.../inc``, which left every
declaration non-public and produced false ``exported_not_public`` findings.
Nothing persists it, and it must never make a saved baseline's identity
depend on the current machine's mount layout.

Because canonicalization touches the filesystem and depends on the running
platform, it is deliberately conservative -- see
:func:`canonical_spelling` for the four cases that decline it. It is also
cached per distinct path string, since the question is asked once per
declaring header and per root rather than once per declaration.

Lives under ``extract/`` rather than inside ``provenance.py`` because
reading the filesystem to decide where a header came from is extraction's
job (``AGENTS.md``'s task-routing table), and because ``provenance.py``
carries an ``architecture/debt.yaml`` ``no_growth`` baseline -- the way to
shrink one of those is to move responsibility to a properly-owned module,
the same reasoning ``extract/dependency_header_roots.py`` records. Pure of
any ``abicheck`` import, so ``provenance`` can depend on it freely.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

__all__ = [
    "absolutize_header_root",
    "canonical_spelling",
    "clear_path_alias_caches",
    "dedup_segments",
    "include_root_alias_segments",
    "path_alias_spellings",
    "public_root_alias_segments",
    "segments",
    "source_header_alias_segments",
]


def segments(path: str) -> tuple[str, ...]:
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


_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:[/\\]")
#: A UNC-shaped spelling (``\\\\server\\share\\...`` or its forward-slash
#: form). Checked before the POSIX test below, since ``//server/share`` also
#: starts with a separator.
_UNC_PREFIX_RE = re.compile(r"^[/\\]{2}[^/\\]")


def _is_windows_absolute_spelling(s: str) -> bool:
    """True for a drive-rooted (``C:\\...``) or UNC-shaped spelling."""
    return bool(_DRIVE_ROOT_RE.match(s)) or bool(_UNC_PREFIX_RE.match(s))


def _is_posix_absolute_spelling(s: str) -> bool:
    """True for a genuinely POSIX-rooted spelling (a single leading ``/``)."""
    return s.startswith("/") and not _UNC_PREFIX_RE.match(s)


def _is_absolute_spelling(s: str) -> bool:
    """True when *s* is rooted under *either* platform's syntax."""
    return _is_posix_absolute_spelling(s) or _is_windows_absolute_spelling(s)


def _is_native_absolute_spelling(s: str) -> bool:
    """True when *s* is rooted using the *running* platform's own syntax.

    This is the gate on canonicalization of an already-rooted path. On
    Windows a POSIX-rooted string (``/usr/include/mylib/api.h``) is
    deliberately *not* native: ``Path.resolve`` would drive-anchor it to the
    current drive (``D:\\usr\\include\\...``), inventing a location the
    string never named -- the exact failure :func:`absolutize_header_root`
    already refuses to cause. On POSIX a drive-rooted or UNC spelling is
    likewise not native, and is preserved lexically instead of being
    reinterpreted as a relative path.
    """
    if os.name == "nt":
        return _is_windows_absolute_spelling(s)
    return _is_posix_absolute_spelling(s)


def _cwd_key(raw: str) -> str | None:
    """The working directory a relative *raw* resolves against, else ``None``.

    Part of both caches' keys below: a relative spelling's answer depends on
    the directory it is resolved from, so caching it on the string alone
    served one working directory's answer to another -- two comparisons run
    from two directories in one process (a test session, a long-lived
    service) that both spelled ``old/include`` shared the first one's alias.
    """
    return None if _is_absolute_spelling(raw) else os.getcwd()


def canonical_spelling(raw: str) -> str | None:
    """See :func:`_canonical_spelling` (cached per spelling *and*, for a
    relative spelling, per working directory -- see :func:`_cwd_key`)."""
    return _canonical_spelling(raw, _cwd_key(raw))


@lru_cache(maxsize=8192)
def _canonical_spelling(raw: str, _cwd: str | None) -> str | None:
    """A *conservative* canonical (symlink-resolved) spelling of *raw*, or
    ``None`` when obtaining one would be unsafe or meaningless.

    **Lexical spelling is evidence; a canonical spelling is only a local
    matching alias.** The lexical form is what a run recorded, what a stored
    snapshot carries, and what a configuration digest is computed over -- it
    is never replaced by, and never derived from, the answer here. This
    function exists purely so that two spellings of one local include tree
    (a symlink and its real path) can be recognized as the same tree at
    *matching* time; nothing persists its result.

    Returns ``None`` -- meaning "match lexically only" -- when:

    * *raw* is rooted in the syntax of the *other* platform
      (:func:`_is_native_absolute_spelling`), so a POSIX path on Windows is
      never drive-anchored and a Windows drive/UNC path on POSIX is never
      reinterpreted;
    * the path does not exist on this machine -- a stored snapshot routinely
      carries paths from the machine that produced it, and those must stay
      classifiable without any filesystem access;
    * the filesystem refuses the query (permissions, a symlink loop, an
      invalid name). Resolution failure is never an extraction failure.

    A *relative* path is allowed to resolve when it exists, matching what
    :func:`absolutize_header_root` has always done for a relative root.

    Cached, because the alias question is asked once per declaring header
    and per root rather than once per declaration -- see
    :func:`clear_path_alias_caches` for the one narrowing that implies.
    """
    if not raw:
        return None
    if _is_absolute_spelling(raw) and not _is_native_absolute_spelling(raw):
        return None
    try:
        path = Path(raw)
        if not path.exists():
            return None
        canonical = str(path.resolve())
    except (OSError, ValueError, RuntimeError):
        # A symlink loop, a name the platform rejects, a permission error --
        # all mean "no canonical alias available", never a hard failure.
        return None
    return canonical or None


def path_alias_spellings(raw: str) -> tuple[str, ...]:
    """*raw* plus, when one is safely available, its canonical spelling.

    The lexical spelling always comes first and is always present; see
    :func:`canonical_spelling` for when a second entry appears.
    """
    canonical = canonical_spelling(raw)
    if canonical is None or canonical == raw:
        return (raw,)
    return (raw, canonical)


def dedup_segments(
    seg_lists: list[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Non-empty segment tuples, de-duplicated, in first-seen order."""
    out: list[tuple[str, ...]] = []
    for seg in seg_lists:
        if seg and seg not in out:
            out.append(seg)
    return out


def absolutize_include_roots(roots: list[Path] | None) -> list[Path] | None:
    """*roots* (``-I`` directories) with each relative entry resolved.

    A relative ``-I`` root makes the header parser spell every header it
    reaches through that root relative to the working directory, while a
    header named on the generated umbrella is spelled absolute
    (``Path.resolve()``), so one snapshot mixed both forms for one checkout.
    Relative entries are resolved the way the umbrella spells its headers;
    an already-rooted entry is left exactly as given (see
    :func:`absolutize_header_root` for why resolving one is wrong).
    """
    if not roots:
        return roots
    return [r if r.is_absolute() else r.resolve() for r in roots]


def absolutize_header_root(h: Path | str) -> Path:
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

    An already-rooted root that happens to traverse a *symlink* is handled
    by the alias layer above (:func:`public_root_alias_segments`), which
    *adds* a canonical spelling alongside this lexical one rather than
    replacing it -- so neither spelling's matching power is lost and neither
    platform's syntax is reinterpreted.
    """
    s = str(h)
    normalized = s.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_ROOT_RE.match(s):
        return Path(s)
    return Path(h).resolve()


def public_root_alias_segments(root: Path | str) -> list[tuple[str, ...]]:
    """Every segment spelling a declared public root (``-H``/``--header``)
    may legitimately be matched under.

    Always includes the root's own lexical segments -- exactly what this
    module matched before aliasing existed, so no previously-matching input
    stops matching. Adds the absolutized form (a relative root's
    long-standing treatment, see :func:`absolutize_header_root`) and, when
    one is safely obtainable, the canonical spelling of either.

    This is the fix for the reported defect: castxml records a declaration's
    *real* path, while the user passes a symlinked spelling of the same
    include tree (``-H /localdisk/.../inc`` against a source location under
    ``/mnt/cached_oses/.../inc``). Retaining both spellings lets the two
    meet without resolving anything unconditionally.
    """
    raw = str(root)
    spellings = [raw]
    absolutized = str(absolutize_header_root(raw))
    if absolutized not in spellings:
        spellings.append(absolutized)
    for spelling in tuple(spellings):
        for alias in path_alias_spellings(spelling):
            if alias not in spellings:
                spellings.append(alias)
    return dedup_segments([segments(s) for s in spellings])


def include_root_alias_segments(root: Path | str) -> list[tuple[str, ...]]:
    """:func:`public_root_alias_segments` for an ``-I`` search root.

    Deliberately excludes the *raw relative* spelling the public variant
    keeps: a bare ``-I include`` segmented as ``("include",)`` would match
    by containment inside any path carrying an ``include`` component,
    including ``/usr/include/...`` -- the over-match
    ``provenance._segmented_include_roots`` has always absolutized to
    avoid. Only the absolutized form and its canonical alias are used.
    """
    absolutized = str(absolutize_header_root(root))
    return dedup_segments([segments(s) for s in path_alias_spellings(absolutized)])


def source_header_alias_segments(source_header: str) -> tuple[tuple[str, ...], ...]:
    """See :func:`_source_header_alias_segments`; keyed like
    :func:`canonical_spelling` (see :func:`_cwd_key`)."""
    return _source_header_alias_segments(source_header, _cwd_key(source_header))


@lru_cache(maxsize=8192)
def _source_header_alias_segments(
    source_header: str, _cwd: str | None
) -> tuple[tuple[str, ...], ...]:
    """Every segment spelling a declaration's own header may be matched as.

    The lexical segments always come first, so a stored, cross-machine, or
    otherwise non-resolvable path behaves exactly as before. A locally
    resolvable, natively-rooted path additionally contributes its canonical
    spelling, which is what lets a parser-reported real path meet a
    symlinked ``-H`` root (and the reverse).

    Cached per distinct header path: a large surface has orders of magnitude
    fewer declaring headers than declarations, so the filesystem is
    consulted once per header rather than once per declaration.
    """
    return tuple(
        dedup_segments([segments(s) for s in path_alias_spellings(source_header)])
    )


def clear_path_alias_caches() -> None:
    """Drop the memoized canonical-spelling/alias answers.

    The caches sample the filesystem once per distinct path string, so a
    symlink created, retargeted, or removed afterwards is not re-observed.
    That is the same single-pass narrowing
    ``extract.dependency_header_roots`` already documents for its prepared
    root sets; this exists so a test (or a long-lived process reconfiguring
    a tree between runs) can reset it explicitly.
    """
    _canonical_spelling.cache_clear()
    _source_header_alias_segments.cache_clear()

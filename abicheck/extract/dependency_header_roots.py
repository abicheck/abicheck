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

"""A ``-H``/``--header`` root set prepared once, and the memoized predicate
over it that every batch classifier wants.

:func:`abicheck.provenance.is_dependency_header` answers "is this header a
toolchain/dependency header, given the roots this dump was invoked with" --
a pure path classification *given* a resolved root set, but deriving that
set is not free. It calls :func:`.path_aliases.absolutize_header_root`
(a ``Path.resolve()`` for a relative root) and ``Path.is_dir()`` -- a real ``stat`` -- twice per
root, then re-segments each path and rebuilds the public set. Done once per
call that is fine; done at the call volume its real callers have, it is the
same filesystem work repeated tens of thousands of times for an answer that
cannot change in between:

* ``dumper_scoping.scope_snapshot_excluding_dependencies`` asks once per
  *declaration*, twice over -- it re-scans the same lists to collect the
  dependency types it must retain by reference.
* ``dumper_clang_streaming``'s streaming pruner asks once per candidate
  function/variable node while a header dump's JSON is still being parsed.
* ``extract.occurrence_dependency_scope`` asks per occurrence.

Each had grown, or wanted, its own memo. This module owns that pattern once:
:func:`prepare_dependency_header_roots` hoists the filesystem work to once
per root set, and :func:`dependency_header_predicate` wraps it in the
per-distinct-path memo those callers were writing by hand. On a real
5,769-declaration header snapshot resolving to 15 distinct declaring headers
one scoping pass goes from 0.375 s to 0.0006 s.

**This module changes no classification.**
:func:`abicheck.provenance.is_dependency_header` is now a thin wrapper that
prepares and immediately asks, so every caller that keeps passing raw roots
observes byte-identical behaviour -- including the no-roots fallback to
``is_system_header`` and the relative-root working-directory,
file-vs-directory and bare-system-prefix rules that function's own comments
spell out. ``tests/test_dependency_header_roots.py`` pins that equivalence
as a property over a generated cross-product of root shapes and header
paths; ``tests/test_provenance.py`` remains the oracle for *what* the
answers are.

**The one semantic narrowing, stated rather than assumed:** a prepared
context samples the filesystem once, so a root created, deleted, or flipped
between file and directory *while a batch is running* is not re-observed
within that batch. That is already the established behaviour of the batch
predicates this consolidates (they memoize the *answers*), and a prepared
context's lifetime is a single pass over one already-parsed snapshot. Hold
one no longer than that.

Lives under ``extract/`` rather than beside ``provenance.py`` because
reading the filesystem to decide where a header came from is extraction's
job (``AGENTS.md``'s task-routing table), and because ``provenance.py`` and
``dumper_scoping.py`` both carry an ``architecture/debt.yaml`` ``no_growth``
baseline -- the way to shrink one of those is to move responsibility to a
properly-owned module, which is what this is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model import ScopeOrigin
from ..provenance import (
    _is_bare_system_dir,
    _matches_any_dir,
    _suffix_match,
    build_public_set,
    classify_origin,
    is_system_header,
)
from .path_aliases import (
    absolutize_header_root,
    public_root_alias_segments,
    source_header_alias_segments,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "DependencyHeaderRoots",
    "dependency_header_predicate",
    "is_dependency_header",
    "prepare_dependency_header_roots",
]


@dataclass(frozen=True)
class DependencyHeaderRoots:
    """A ``-H``/``--header`` root set with its filesystem work already done.

    See this module's own docstring for the cost this hoists, the
    equivalence it preserves, and the one narrowing it accepts. Build one
    with :func:`prepare_dependency_header_roots`; ask it with
    :meth:`is_dependency`, which is a pure function of the header path.
    """

    header_segs: tuple[tuple[str, ...], ...]
    dir_segs: tuple[tuple[str, ...], ...]
    have_set: bool
    #: True when no roots were given at all, so :meth:`is_dependency` takes
    #: ``is_dependency_header``'s own bare-:func:`is_system_header` fallback
    #: rather than consulting the (empty) public set -- which would answer
    #: ``False`` for every header, including a real toolchain one.
    system_header_fallback: bool = False

    def is_dependency(self, source_header: str | None) -> bool:
        """:func:`abicheck.provenance.is_dependency_header`'s answer for
        *source_header*, against this already-resolved root set."""
        if not source_header:
            return False
        if self.system_header_fallback:
            return is_system_header(source_header)
        origin = classify_origin(
            source_header,
            list(self.header_segs),
            list(self.dir_segs),
            have_public_set=self.have_set,
        )
        if origin is ScopeOrigin.PUBLIC_HEADER and is_system_header(source_header):
            # `classify_origin`'s D3 basename-only fallback exists for a root
            # spelled under a different prefix than the parser reports. These
            # roots are absolutized on the host that ran the parse, so a
            # basename-only match can only be a coincidence -- libstdc++'s
            # `bits/allocator.h` against a library's own `core/allocator.h`
            # (a real SVS scan kept 3,164 toolchain functions that way).
            # Require a real path-suffix or directory match instead.
            return not self._owned(source_header)
        return origin is ScopeOrigin.SYSTEM_HEADER

    def _owned(self, source_header: str) -> bool:
        """Whether a spelling of *source_header* is a root, or lies under a
        root's directory -- the ownership test minus the basename fallback."""
        return any(
            any(_suffix_match(root, segs) for root in self.header_segs)
            or _matches_any_dir(segs, list(self.dir_segs))
            for segs in source_header_alias_segments(source_header)
        )


def prepare_dependency_header_roots(
    header_roots: Sequence[Path | str] | None,
) -> DependencyHeaderRoots:
    """Resolve *header_roots* once into a reusable
    :class:`DependencyHeaderRoots`.

    Callers classifying many headers against one root set should prepare
    once and reuse, or use :func:`dependency_header_predicate`, which also
    memoizes per distinct header.
    """
    if not header_roots:
        return DependencyHeaderRoots((), (), False, system_header_fallback=True)
    # Identical to the derivation `is_dependency_header` used to run inline on
    # every call -- see that function's own comments for why a relative root is
    # absolutized, why only a *file* root widens to its parent, and why a file
    # root sitting flat in a bare system prefix does not widen at all. The one
    # difference is arithmetic, not semantic: `is_dir()` is read once per root
    # here rather than twice per call.
    resolved = [absolutize_header_root(h) for h in header_roots]
    is_dir = [r.is_dir() for r in resolved]
    roots = [str(r) for r, d in zip(resolved, is_dir) if not d]
    # The bare-system-prefix protection is asked of *every* spelling the
    # parent directory has (``path_aliases.public_root_alias_segments``), not
    # just its lexical one -- otherwise a symlink pointing at ``/usr/include``
    # would widen a flat file root into the bare system prefix after all,
    # which is the exact failure this check exists to prevent.
    root_dirs = [
        str(r if d else r.parent)
        for r, d in zip(resolved, is_dir)
        if d
        or not any(
            _is_bare_system_dir(seg)
            for seg in public_root_alias_segments(str(r.parent))
        )
    ]
    header_segs, dir_segs, have_set = build_public_set(roots, root_dirs)
    return DependencyHeaderRoots(tuple(header_segs), tuple(dir_segs), have_set)


def dependency_header_predicate(
    header_roots: Sequence[Path | str] | None,
) -> Callable[[str | None], bool]:
    """An ``is_dep(source_header) -> bool`` closure over *header_roots*,
    preparing once and memoizing per distinct header path.

    The shape every batch caller here wants: a large surface has orders of
    magnitude fewer *declaring headers* than declarations (a real
    5,769-declaration snapshot resolves to 15), so the classification runs
    per header while the caller iterates per declaration. Mirrors
    ``provenance.apply_provenance``'s own ``origin_cache`` idiom, and
    replaces the hand-rolled copies that had accumulated at each call site.

    The returned closure owns its memo, so it is single-batch scoped like
    the prepared context it wraps -- do not cache one across dumps.
    """
    prepared = prepare_dependency_header_roots(header_roots)
    cache: dict[str | None, bool] = {}

    def is_dep(source_header: str | None) -> bool:
        cached = cache.get(source_header)
        if cached is None:
            cached = prepared.is_dependency(source_header)
            cache[source_header] = cached
        return cached

    return is_dep


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
    # A caller asking about many headers against one root set should use
    # `dependency_header_predicate` instead: this prepares the whole root set
    # for a single question, which is exactly the cost this module hoists.
    return prepare_dependency_header_roots(header_roots).is_dependency(source_header)

# Copyright 2026 Nikolay Petrov
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

"""The include-tree scan behind every header-parse cache key (ADR-061 ``extract``).

One owner for "which files under this include root can change what the parser
sees, and in what order do we hash them" -- read by ``dumper_ast_config.
_cache_key`` (the header-AST cache) and ``snapshot_cache._cache_key``/
``_hash_include_dir_headers`` (the whole-snapshot cache). Both fold the result
into a hash *in the returned order*, which is why the ordering contract is
stated as part of the function's own docstring rather than left implicit in
whatever ``sorted()`` happened to be called.

Split out of :mod:`abicheck.header_utils` (which still owns the suffix set and
the compiler-flag vocabulary) when the traversal grew its own implementation
notes: this is a filesystem-reading fact extractor, the ``extract`` layer's job,
while ``header_utils`` is the stdlib-only path/flag leaf every layer shares.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..header_utils import CACHE_HEADER_SUFFIXES

__all__ = ["iter_cache_header_files"]


def _path_suffix(name: str) -> str:
    """``PurePath(name).suffix``, without building a path object.

    Not ``os.path.splitext(name)[1]``, which is *not* the same function and
    disagrees on real, legal filenames: ``splitext`` skips leading dots, so
    ``"..h"``, ``"...h"`` and ``"..hpp"`` come back with no suffix at all,
    while ``PurePath`` reports ``".h"``/``".hpp"``. Since the entry set here
    *is* part of the cache key, that divergence silently dropped such a header
    from both the AST and snapshot keys -- an edit to a transitively included
    ``..h`` would then reuse stale cached evidence, which is the one direction
    this walk must never err in (Codex review, PR #1275). They also disagree
    on a trailing dot (``"a."``: ``splitext`` says ``"."``), harmless only
    because ``"."`` is in no suffix set.

    ``tests/test_cache_header_walk.py`` checks this against the real
    ``PurePath(...).suffix`` over a generated name space rather than the three
    names above, since the bug class is "two similar-looking stdlib functions
    are not the same function", not those spellings.
    """
    i = name.rfind(".")
    return name[i:] if 0 < i < len(name) - 1 else ""


def _cache_header_rel_parts(directory: Path) -> list[tuple[str, ...]]:
    """Every matching entry under *directory*, as a tuple of relative path parts.

    The scan half of :func:`iter_cache_header_files`, kept separate so the
    ordering contract below can be stated (and tested) independently of how
    the entries are found.

    Deliberately ``os.scandir``-based rather than ``Path.rglob("*")``: the
    cache-key walk is re-run for every include root on every key computation,
    and ``rglob`` materializes (and later sorts) one ``Path`` object per
    *entry visited*, not per entry kept -- on an installed ``/usr/include``
    tree (22k matching entries) that dominated the key's cost. Carrying plain
    strings through the traversal and building a ``Path`` only for the
    surviving, already-ordered entries measured ~3x faster on that tree with
    a byte-identical cache key. (Measured note, since it is the non-obvious
    half: swapping in ``scandir`` while *keeping* the ``Path``-object sort was
    **slower** than ``rglob`` -- the win is in not building and comparing
    those objects, not in the directory-reading API.)

    Two traversal behaviours are matched to ``rglob("*")``'s on purpose,
    because the cache key's meaning depends on them:

    * **Symlinked directories are not descended into.** ``rglob``'s ``**``
      does not follow them (``recurse_symlinks=False``), which is also what
      makes a symlink loop under an include root terminate rather than hang.
      A symlink to a *file* is still an ordinary matching entry.
    * **A directory whose own name ends in a header suffix matches.** The
      pattern is ``*``, not "regular files only", so such a directory
      contributes its own path/mtime to the key exactly as before. Narrowing
      that here would silently drop a hashed input.
    """
    root = str(directory)
    stack: list[tuple[str, tuple[str, ...]]] = [(root, ())]
    found: list[tuple[str, ...]] = []
    suffix_of = _path_suffix
    join = os.path.join
    while stack:
        base, rel = stack.pop()
        try:
            with os.scandir(base) as entries:
                batch = list(entries)
        except OSError:
            # Same degradation as ``rglob``, which swallows every ``OSError``
            # raised while listing -- an unreadable sub-directory (and a
            # missing or unreadable *root*) contributes nothing rather than
            # propagating. Verified against ``Path("nope").rglob("*")``, which
            # yields nothing rather than raising; the ``except OSError`` guards
            # around the callers (``snapshot_cache._hash_include_dir_headers``,
            # ``snapshot_cache._cache_key``) stay correct either way.
            continue
        for entry in batch:
            name = entry.name
            child_rel = (*rel, name)
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                stack.append((join(base, name), child_rel))
            if suffix_of(name).lower() in CACHE_HEADER_SUFFIXES:
                found.append(child_rel)
    return found


def iter_cache_header_files(directory: Path) -> list[Path]:
    """Header-like files under *directory* whose edits should bust the AST cache.

    Recurses *directory* and returns the files whose suffix is in
    :data:`CACHE_HEADER_SUFFIXES` (the generous superset — includes ``.inl``/
    ``.tcc`` template bodies), sorted for a deterministic cache key. Used by
    ``dumper._cache_key``'s include-dir mtime walk.

    The order is **component-wise**, matching ``sorted(directory.rglob("*"))``
    exactly -- ``PurePath`` compares normcased *parts*, not whole path
    strings, so ``a/b/c.h`` sorts before ``a/b.h`` (``"b" < "b.h"``) where a
    plain string sort would place it after. Consumers fold these paths into a
    hash in this order (``dumper_ast_config._cache_key``,
    ``snapshot_cache._hash_include_dir_headers``), so a "simplification" to
    ``sorted(str(p) ...)`` would change every existing cache key on disk
    while looking like a no-op.
    """
    normcase = os.path.normcase
    ordered = sorted(
        _cache_header_rel_parts(directory),
        key=lambda rel: tuple(normcase(part) for part in rel),
    )
    return [directory.joinpath(*rel) for rel in ordered]

# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Soname matching + filesystem alias recovery for the bundle layer.

Split out of ``bundle.py`` (a leaf module, no other imports needed) purely to
keep that file under the AI-readiness file-size hard cap — see
``bundle.py``'s own ``_detect_intra_dep_removed``/
``_detect_unresolved_intra_dependency`` (soname matching) and
``_compute_resolution_graph`` (alias recovery), which are this module's
only callers, plus :mod:`abicheck.bundle_facts` (G38 Phase 2), which uses
``filesystem_alias_basenames`` directly to capture real on-disk aliases
while a library's own file still exists, for later metadata-only replay.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "filesystem_alias_basenames",
    "hard_link_alias_basenames",
    "soname_matches_providers",
    "soname_stem",
]

# Strips a trailing ``.so`` + version suffix (``libfoo.so.1.2.3`` -> ``libfoo``).
_SONAME_VERSION_SUFFIX_RE = re.compile(r"\.so(?:\.\d+)*$")
# An explicit numeric major version after ``.so`` (``libfoo.so.1``, not the
# version-generic ``libfoo``/``libfoo.so``).
_EXPLICIT_MAJOR_VERSION_RE = re.compile(r"\.so\.\d")


def soname_stem(soname: str) -> str:
    """Stem, so ``libmkl_core`` matches the real ``libmkl_core.so.2``."""
    return _SONAME_VERSION_SUFFIX_RE.sub("", soname)


def soname_matches_providers(soname: str, providers: set[str]) -> bool:
    """True when soname (a real DT_NEEDED entry) is covered by providers
    (built-in + .abicheck.yml's `bundle.system_providers:` -- CLI cleanup
    phase two, PR J; formerly --bundle-system-providers): exact, then stem
    match.

    Stem fallback only applies against a version-*generic* provider entry
    (``libfoo``/``libfoo.so``, no explicit major) -- a provider entry that
    itself names a specific major (``libfoo.so.1``) requires an exact
    match, so it can't also silently match an unrelated major
    (``libfoo.so.2``) (Codex review): a caller who explicitly pinned one
    version did not thereby approve every other one, and this allow-list
    doubles as the provider-migration assertion in ``bundle.py``.
    """
    if soname in providers:
        return True
    stem = soname_stem(soname)
    return any(
        not _EXPLICIT_MAJOR_VERSION_RE.search(p)
        and (stem == p or stem == soname_stem(p))
        for p in providers
    )


def hard_link_alias_basenames(path: Path) -> list[str]:
    """Basenames of *other* directory entries hard-linked to ``path``.

    ``discover_artifact_set()`` dedupes candidate paths on filesystem
    identity (``st_dev``/``st_ino``) and keeps only one representative path
    per inode -- so if a provider library has multiple hard-linked names
    (e.g. ``libfoo.so.1`` and ``libfoo.so.1.0.0``), any alias basename other
    than the representative's own is otherwise discarded entirely and never
    reaches ``soname_to_name``. Recover them by scanning the representative
    path's own directory for siblings sharing its identity. Best-effort: a
    permission error or non-existent path/parent yields no aliases rather
    than raising, matching this module's existing conservative-on-failure
    behavior for filesystem probes.
    """
    try:
        target_stat = path.stat()
    except OSError:
        return []
    aliases = []
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.name == path.name:
            continue
        try:
            entry_stat = entry.stat()
        except OSError:
            continue
        if (entry_stat.st_dev, entry_stat.st_ino) == (
            target_stat.st_dev,
            target_stat.st_ino,
        ):
            aliases.append(entry.name)
    return aliases


def resolved_basename(path: Path) -> str:
    """*path*'s resolved-symlink-target basename, or its own basename if
    resolution fails (a broken symlink, a permissions error, ...).

    Shared by :func:`filesystem_alias_basenames` (below) and
    :func:`abicheck.bundle_facts.capture_bundle_facts`'s
    ``library_filenames`` capture: both need "the real, versioned on-disk
    name" for a library that may itself be a dev symlink (``libfoo.so`` ->
    ``libfoo.so.1``) -- a bare ``path.name`` would capture the symlink's
    own, unversioned name instead (Codex review, fresh evidence).
    """
    try:
        return path.resolve().name
    except OSError:
        return path.name


def filesystem_alias_basenames(path: Path) -> tuple[str, ...]:
    """Real, on-disk alias basenames for *path*: its resolved symlink
    target's basename (if different from *path*'s own) plus any
    hard-linked sibling basenames sharing its inode (see
    :func:`hard_link_alias_basenames`).

    Exposed standalone (rather than only inline in
    ``bundle._compute_resolution_graph``'s own ``probe_filesystem=True``
    branch) so a caller with real files on disk *at capture time* (G38
    Phase 2's :func:`abicheck.bundle_facts.capture_bundle_facts`) can
    persist the result and replay the identical resolution later, when the
    original binaries may no longer exist or be reachable on the loading
    machine (see ``bundle.build_bundle_snapshot_from_metadata``'s
    ``extra_aliases`` parameter). Best-effort: an unreadable/missing path
    yields no aliases.
    """
    aliases: list[str] = []
    resolved_name = resolved_basename(path)
    if resolved_name != path.name:
        aliases.append(resolved_name)
    for alias in hard_link_alias_basenames(path):
        if alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)

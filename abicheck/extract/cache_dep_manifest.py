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

"""Dependency manifests for the header-parse caches (ADR-062 D11).

The one owner of reading, writing and verifying the files a cached parse
*actually* read -- ccache's "direct mode" manifest. A cache key folds the
project inputs by content and the compiler's system include directories by
spelling only; the system headers the parse opened are recorded here, next
to the entry, each with its content digest
(:func:`~.cache_input_digest.file_digest`). A key hit is served only while
:func:`verify` holds for its manifest, so a lookup costs a ``stat`` per file
the parse read, not a digest of every file under ``/usr/include``.

A manifest has three parts:

* ``files`` -- ``path -> content digest`` of every file the compiler's
  depfile (``-MD -MF``) lists. A missing, unreadable or changed file fails
  verification.
* ``absent`` -- paths that did not exist when the entry was stored and would
  shadow a recorded file if created: the same relative name in a search
  directory searched *before* the one the file was found in. Creating one
  fails verification (a new header in an earlier ``-isystem`` directory).
* ``trees`` -- ``dir -> tree digest`` for a parse whose frontend wrote no
  depfile: the full-content hash of each system directory the keys used to
  fold (the pre-manifest behaviour, kept only as a fallback).

:func:`recording` gathers the manifests of every parse that ran or was served
while it is open, so the whole-snapshot dump cache can store their union.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..storage.atomic_file import atomic_write
from ..storage.cache_integrity import DEPS_SUFFIX
from .cache_input_digest import file_digest, flush_digest_store, tree_digest

__all__ = [
    "DepManifest",
    "ParseDeps",
    "build_manifest",
    "entry_valid",
    "depfile_args",
    "fallback_manifest",
    "note",
    "parse_depfile",
    "recall",
    "recording",
    "read_depfile",
    "read_sidecar",
    "remember",
    "sidecar_path",
    "verify",
    "write_sidecar",
]

_FORMAT = 1


@dataclass(frozen=True)
class DepManifest:
    """The files one or more parses read, as recorded at store time."""

    files: Mapping[str, str] = field(default_factory=dict)
    absent: frozenset[str] = frozenset()
    trees: Mapping[str, str] = field(default_factory=dict)

    def union(self, other: DepManifest) -> DepManifest | None:
        """Both manifests' records; ``None`` if they disagree on a file."""
        files = dict(self.files)
        for path, digest in other.files.items():
            if files.setdefault(path, digest) != digest:
                return None
        trees = dict(self.trees)
        for path, digest in other.trees.items():
            if trees.setdefault(path, digest) != digest:
                return None
        absent = self.absent | other.absent
        if absent & files.keys():
            return None
        return DepManifest(files, absent, trees)

    def to_json(self) -> bytes:
        doc = {
            "format": _FORMAT,
            "files": dict(sorted(self.files.items())),
            "absent": sorted(self.absent),
            "trees": dict(sorted(self.trees.items())),
        }
        return json.dumps(doc, separators=(",", ":")).encode()

    @classmethod
    def from_json(cls, data: bytes) -> DepManifest | None:
        """The manifest *data* encodes, or ``None`` when it is not one."""
        try:
            doc = json.loads(data)
        except ValueError:
            return None
        if not isinstance(doc, dict) or doc.get("format") != _FORMAT:
            return None
        files, absent, trees = doc.get("files"), doc.get("absent"), doc.get("trees")
        if not (isinstance(files, dict) and isinstance(trees, dict)):
            return None
        if not (_str_map(files) and _str_map(trees)):
            return None
        if not (isinstance(absent, list) and all(isinstance(p, str) for p in absent)):
            return None
        return cls(dict(files), frozenset(absent), dict(trees))


def _str_map(value: dict[object, object]) -> bool:
    return all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


# --------------------------------------------------------------------------
# Depfiles
# --------------------------------------------------------------------------


def depfile_args(depfile: Path) -> list[str]:
    """The flags that make a clang-based frontend write *depfile*.

    ``-MD`` keeps the main action (``-fsyntax-only``/castxml's output) and
    adds a dependency list; both clang and castxml accept it.
    """
    return ["-MD", "-MF", str(depfile)]


def parse_depfile(text: str) -> list[str]:
    """Every prerequisite a Make-syntax depfile lists, in order, de-duplicated.

    Handles line continuations (``\\`` newline), escaped spaces (``\\ ``),
    ``\\#`` and ``$$``, several rules, and a Windows drive letter in a path
    (``C:\\x`` is not a rule separator: a target ends at a ``:`` followed
    by whitespace or the end of the line). A backslash before anything
    else is a literal (a Windows path separator).
    """
    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    out: dict[str, None] = {}
    for line in joined.splitlines():
        tokens = _split_tokens(line)
        try:
            colon = next(i for i, (_, sep) in enumerate(tokens) if sep)
        except StopIteration:
            continue
        for token, _ in tokens[colon + 1 :]:
            if token:
                out.setdefault(token, None)
    return list(out)


def _split_tokens(line: str) -> list[tuple[str, bool]]:
    """``(token, is_rule_separator)`` pairs of one logical depfile line."""
    tokens: list[tuple[str, bool]] = []
    cur: list[str] = []
    i, n = 0, len(line)

    def flush() -> None:
        if cur:
            tokens.append(("".join(cur), False))
            cur.clear()

    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if ch == "\\" and nxt in (" ", "#", "\t"):
            cur.append(nxt)
            i += 2
            continue
        if ch == "$" and nxt == "$":
            cur.append("$")
            i += 2
            continue
        if ch == ":" and (nxt == "" or nxt in " \t") and not _has_sep(tokens):
            flush()
            tokens.append(("", True))
            i += 1
            continue
        if ch in " \t":
            flush()
            i += 1
            continue
        cur.append(ch)
        i += 1
    flush()
    return tokens


def _has_sep(tokens: list[tuple[str, bool]]) -> bool:
    return any(sep for _, sep in tokens)


def read_depfile(depfile: Path) -> list[str] | None:
    """The absolute prerequisites *depfile* lists, or ``None`` without one."""
    try:
        text = depfile.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if not text.strip():
        return None
    return [os.path.normpath(os.path.abspath(p)) for p in parse_depfile(text)]


# --------------------------------------------------------------------------
# Building and verifying
# --------------------------------------------------------------------------


def _norm_dirs(search_dirs: Iterable[str]) -> list[str]:
    return list(
        dict.fromkeys(os.path.normpath(os.path.abspath(d)) for d in search_dirs)
    )


def _shadow_candidates(path: str, dirs: list[str]) -> list[str]:
    """Paths in search dirs earlier than *path*'s own that would shadow it."""
    for idx, d in enumerate(dirs):
        prefix = d.rstrip(os.sep) + os.sep
        if path.startswith(prefix):
            rel = path[len(prefix) :]
            return [os.path.join(earlier, rel) for earlier in dirs[:idx]]
    return []


def build_manifest(
    deps: Iterable[str],
    *,
    search_dirs: Iterable[str] = (),
    exclude: Callable[[str], bool] = lambda _p: False,
    started_ns: int | None = None,
) -> DepManifest | None:
    """The manifest for a parse that read *deps*, or ``None`` if unusable.

    *exclude* drops the run's own temporary files (the aggregate header).
    ``None`` -- store nothing -- when a dependency cannot be read, or was
    modified at or after *started_ns* (the parse may have read the earlier
    bytes, so no digest taken now describes what it parsed).
    """
    dirs = _norm_dirs(search_dirs)
    files: dict[str, str] = {}
    candidates: set[str] = set()
    for dep in deps:
        if exclude(dep):
            continue
        try:
            if started_ns is not None:
                st = os.stat(dep)
                if max(st.st_mtime_ns, st.st_ctime_ns) >= started_ns:
                    return None
            files[dep] = file_digest(dep)
        except OSError:
            return None
        candidates.update(_shadow_candidates(dep, dirs))
    absent = frozenset(c for c in candidates - files.keys() if not os.path.lexists(c))
    flush_digest_store()
    return DepManifest(files, absent, {})


def fallback_manifest(search_dirs: Iterable[str]) -> DepManifest:
    """The pre-manifest identity for a parse with no depfile: every system
    directory's full content digest."""
    return DepManifest(
        {}, frozenset(), {d: tree_digest(d) for d in _norm_dirs(search_dirs)}
    )


def verify(manifest: DepManifest | None) -> bool:
    """Whether every file *manifest* records is unchanged on disk now."""
    if manifest is None:
        return False
    for path, digest in manifest.files.items():
        try:
            if file_digest(path) != digest:
                return False
        except OSError:
            return False
    for path in manifest.absent:
        if os.path.lexists(path):
            return False
    for path, digest in manifest.trees.items():
        if tree_digest(path) != digest:
            return False
    return True


class ParseDeps:
    """One cache-producing parse's dependency capture, first run to manifest.

    :meth:`args` is appended to each frontend run (a retry overwrites the
    depfile, so the last run's list is the one read); :meth:`manifest` then
    builds the entry's manifest -- or, when the frontend wrote no depfile
    (*enabled* false, or an injected runner that ignores the flags), the
    full-content fallback over *search_dirs*.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.started_ns = time.time_ns()
        fd, name = tempfile.mkstemp(prefix="abicheck-deps-", suffix=".d")
        os.close(fd)
        self.depfile = Path(name)

    def args(self) -> list[str]:
        self.depfile.unlink(missing_ok=True)
        return depfile_args(self.depfile) if self.enabled else []

    def manifest(
        self, *, search_dirs: Iterable[str], exclude: Callable[[str], bool]
    ) -> DepManifest | None:
        deps = read_depfile(self.depfile) if self.enabled else None
        if deps is None:
            return fallback_manifest(search_dirs)
        return build_manifest(
            deps, search_dirs=search_dirs, exclude=exclude, started_ns=self.started_ns
        )

    def close(self) -> None:
        self.depfile.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Sidecars
# --------------------------------------------------------------------------


def sidecar_path(entry: Path) -> Path:
    """Where the manifest of the cache entry *entry* lives."""
    return entry.with_name(entry.name + DEPS_SUFFIX)


def write_sidecar(entry: Path, manifest: DepManifest) -> bool:
    """Write *entry*'s manifest; ``False`` (store nothing) when it fails."""
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(sidecar_path(entry), manifest.to_json())
    except OSError:
        return False
    return True


def read_sidecar(entry: Path) -> DepManifest | None:
    """*entry*'s manifest, or ``None`` when missing or corrupt (a miss)."""
    try:
        data = sidecar_path(entry).read_bytes()
    except OSError:
        return None
    return DepManifest.from_json(data)


def entry_valid(entry: Path) -> bool:
    """The lookup gate: *entry* has a manifest and every file in it is
    unchanged. A passing manifest is remembered for :func:`recall`."""
    manifest = read_sidecar(entry)
    if manifest is None or not verify(manifest):
        return False
    remember(str(entry), manifest)
    return True


# --------------------------------------------------------------------------
# In-process bookkeeping: which manifest describes a served entry, and
# which parses ran inside an open recording.
# --------------------------------------------------------------------------

_LOCK = threading.Lock()
#: ``entry id -> manifest``, so a result served from the request's shared
#: acquisition table (no disk read, no parse in this context) can still name
#: its dependencies. Bounded; an evicted id is recalled from its sidecar.
_KNOWN: dict[str, DepManifest] = {}
_KNOWN_MAX = 512
#: Manifests noted while at least one :func:`recording` is open; ``None``
#: marks a parse whose dependencies are unknown.
_JOURNAL: list[DepManifest | None] = []
_ACTIVE = [0]


def remember(entry_id: str, manifest: DepManifest) -> None:
    with _LOCK:
        _KNOWN.pop(entry_id, None)
        _KNOWN[entry_id] = manifest
        while len(_KNOWN) > _KNOWN_MAX:
            _KNOWN.pop(next(iter(_KNOWN)))


def recall(entry_id: str, entry: Path | None = None) -> DepManifest | None:
    """The manifest last remembered for *entry_id*, else *entry*'s sidecar."""
    with _LOCK:
        hit = _KNOWN.get(entry_id)
    if hit is not None or entry is None:
        return hit
    return read_sidecar(entry)


def note(manifest: DepManifest | None) -> None:
    """Report that a parse result with *manifest*'s dependencies was used.

    Process-wide rather than per context: a parse may run on a pool thread
    the recording's context never reaches. A concurrent dump's parses can
    therefore join another recording's union -- extra files, so extra
    misses at worst, never a missing dependency.
    """
    with _LOCK:
        if _ACTIVE[0]:
            _JOURNAL.append(manifest)


@dataclass
class Recording:
    """The parses noted since :func:`recording` opened."""

    start: int
    items: list[DepManifest | None] | None = None

    def manifest(self) -> DepManifest | None:
        """The union of every noted manifest; ``None`` when one was unknown
        or two disagree (store nothing)."""
        if self.items is None:
            with _LOCK:
                items = list(_JOURNAL[self.start :])
        else:
            items = self.items
        acc = DepManifest()
        for item in items:
            if item is None:
                return None
            merged = acc.union(item)
            if merged is None:
                return None
            acc = merged
        return acc


@contextmanager
def recording() -> Iterator[Recording]:
    """Collect the manifests of every parse used inside the block."""
    with _LOCK:
        _ACTIVE[0] += 1
        rec = Recording(len(_JOURNAL))
    try:
        yield rec
    finally:
        with _LOCK:
            rec.items = _JOURNAL[rec.start :]
            _ACTIVE[0] -= 1
            if not _ACTIVE[0]:
                _JOURNAL.clear()

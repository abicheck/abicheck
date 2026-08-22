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

"""Product baseline archive — one file for a whole multi-library product.

Per-library ``dump`` snapshots (``.json.zst``) do not scale to a product
shipping several interdependent shared libraries: they must be produced and
stored one per library (three libraries, three release assets), and — the
part that actually matters — ``scan --against <snapshot>`` compares exactly
one library against exactly one snapshot, so cross-DSO ABI breakage (a
symbol one library imports from a sibling library disappearing) is
structurally invisible to it: no single per-library invocation ever sees
both sides of that dependency edge. :mod:`abicheck.bundle` (ADR-023) is
built for precisely this — but it needs *binaries* on both sides (it reads
``DT_NEEDED``/``.gnu.version_r``/``.gnu.version_d`` straight from the ELF
files), so a bundle-aware comparison against a stored baseline needs the old
side's binaries available on disk, not a header/DWARF-derived snapshot.

This module is the storage format for that: :func:`pack_product_baseline`
archives an entire product directory (every shared library plus whatever
else ships alongside it — debug info, headers) into one deterministic
``.tar.zst`` file, with a small JSON manifest (:class:`ProductBaselineManifest`)
recording which archived files are libraries and which relative directories
hold the product's public headers. :func:`unpack_product_baseline` reverses
it, reproducing a directory that ``abicheck compare``'s directory-mode
operand (bundle analysis, ADR-023, on by default there) can run against
directly — covering every library, and every cross-library edge between
them, in one invocation instead of one per library.

This is a storage/transport format only: :func:`pack_product_baseline`/
:func:`unpack_product_baseline` pack and unpack a directory tree, nothing
more — library-only surface, no CLI command. :func:`compare_product_directories`
is the other half: given the two directories a caller just unpacked, it
runs the actual bundle-aware comparison (per-library diff via
:mod:`abicheck.service_compare_pipeline`, cross-library findings via
:mod:`abicheck.bundle`) — the plain-Python counterpart of directory-mode
``compare <old_dir> <new_dir>``, requiring no CLI subprocess.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat as stat_module
import tarfile
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from .errors import SnapshotError
from .package import TarExtractor

if TYPE_CHECKING:
    from .bundle import BundleDiffResult
from .snapshot_io import ZSTD_LEVEL_BASELINE

#: Name the manifest is stored under inside the archive — deliberately not a
#: plausible library/header file name, so it can never collide with real
#: product content.
MANIFEST_MEMBER_NAME = "abicheck-product-baseline.json"

#: Manifest schema discriminator. Every reader of this format is new code
#: introduced alongside it (unlike, say, ``run-plan.json``'s own schema
#: string — see AGENTS.md's "Known gaps" entry on that document — there is
#: no already-shipped reader this needs to protect against yet), so today
#: this is purely a self-description; :func:`unpack_product_baseline` still
#: checks it, so a *future* MAJOR bump has a real rejection point from the
#: day it's introduced rather than needing one retrofitted later.
PRODUCT_BASELINE_SCHEMA = "abicheck.product-baseline/v1"

#: File suffixes recognized as a shared library on pack. Informational only
#: — every regular file under the source directory is archived regardless
#: of suffix; this only decides which archived files are itemized in
#: :attr:`ProductBaselineManifest.libraries`.
_LIBRARY_SUFFIXES = (".so", ".dylib", ".dll")


#: A versioned SONAME real file, e.g. "libfoo.so.1.2.3" -- no suffix in
#: _LIBRARY_SUFFIXES matches it, since ".3" isn't a recognized library
#: extension. Anchored at the end and requires every dotted segment after
#: ".so" to be purely numeric, so a conventional split-debug companion
#: like "libfoo.so.1.debug" -- which a real ".so."-substring check
#: misclassifies as a library, since "debug" isn't checked at all -- is
#: correctly excluded (Codex review, fresh evidence).
_VERSIONED_SONAME = re.compile(r"\.so(\.\d+)+$", re.IGNORECASE)


def _is_shared_library(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(_LIBRARY_SUFFIXES):
        return True
    return _VERSIONED_SONAME.search(name) is not None


@dataclass(frozen=True)
class LibraryEntry:
    """One shared library recorded in a :class:`ProductBaselineManifest`."""

    name: str
    path: str  # POSIX-style, relative to the archive/product root
    sha256: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LibraryEntry:
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            sha256=str(data.get("sha256", "")),
            size=_safe_int(data.get("size")),
        )


@dataclass(frozen=True)
class ProductBaselineManifest:
    """Describes one packed product baseline archive."""

    schema: str = PRODUCT_BASELINE_SCHEMA
    product: str = ""
    libraries: tuple[LibraryEntry, ...] = ()
    #: Header roots, relative to the product root, that a follow-on
    #: ``compare -H`` invocation should pass — recorded
    #: here so a single archive carries both sides of the multilib
    #: comparison's own header contract, rather than requiring the caller
    #: to remember (or re-derive) it separately per product.
    header_roots: tuple[str, ...] = ()
    #: Total number of members in the archive, manifest included — purely
    #: informational (a quick sanity count), not load-bearing for unpacking.
    file_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "product": self.product,
            "libraries": [lib.to_dict() for lib in self.libraries],
            "header_roots": list(self.header_roots),
            "file_count": self.file_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductBaselineManifest:
        raw_libraries = data.get("libraries")
        raw_header_roots = data.get("header_roots")
        return cls(
            schema=str(data.get("schema", PRODUCT_BASELINE_SCHEMA)),
            product=str(data.get("product", "")),
            libraries=tuple(
                LibraryEntry.from_dict(item)
                for item in (raw_libraries if isinstance(raw_libraries, list) else [])
                if isinstance(item, dict)
            ),
            header_roots=tuple(
                str(item)
                for item in (
                    raw_header_roots if isinstance(raw_header_roots, list) else []
                )
            ),
            file_count=_safe_int(data.get("file_count")),
        )


# ── Local helpers ────────────────────────────────────────────────────────
#
# Deliberately not importing snapshot_io._zstd_module/_atomic_write_bytes
# across the module boundary — both are leading-underscore internals, and
# this codebase's established convention (see
# abicheck/buildsource/baseline_publish.py's own
# ``_resolve_under_root``/docstring) is to duplicate a small safety/utility
# helper locally rather than reach across a module boundary for one.
# ZSTD_LEVEL_BASELINE (public) is imported directly.


def _zstd_module() -> Any:
    try:
        import zstandard
    except ImportError as exc:  # pragma: no cover - core dependency, see pyproject.toml
        raise SnapshotError(
            "product baseline archives require the 'zstandard' package, "
            "which is a core abicheck dependency (pyproject.toml) — "
            "reinstall abicheck ('pip install abicheck') to restore it."
        ) from exc
    return zstandard


def _resolve_under_root(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, refusing an absolute path or an escape."""
    if Path(rel).is_absolute():
        return None
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        return None
    return root / rel


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _scaffold_dirs_for_mkdir(base: Path, leaf_parent: Path) -> set[Path]:
    """The directories under *base* that ``leaf_parent.mkdir(parents=True)``
    is about to create -- i.e. every ancestor of *leaf_parent*, up to and
    excluding *base* itself, that doesn't exist yet. Used to tell an
    output-only directory chain (created purely to hold OUTPUT, carrying
    no real product content of its own) apart from genuine pre-existing
    product content, when deciding whether SOURCE_DIR has anything to
    pack (see :func:`pack_product_baseline`'s own "no files found" check)."""
    created: set[Path] = set()
    if leaf_parent != base and not leaf_parent.is_relative_to(base):
        return created
    node = leaf_parent
    while node != base and not node.exists():
        created.add(node)
        node = node.parent
    return created


def _safe_int(value: Any) -> int:
    """Defensively coerce a manifest numeric field to ``int``, the same
    "never abort on a hand-edited/malformed pack" convention every other
    ``from_dict()`` in this codebase follows (see this module's own
    docstring reference, and AGENTS.md's "every dataclass carries
    to_dict()/from_dict() with defensive .get() parsing" convention) —
    a non-numeric ``size``/``file_count`` degrades to ``0`` instead of
    raising ``ValueError`` past ``unpack_product_baseline``'s
    :class:`SnapshotError` handling."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _discover_paths(source_dir: Path) -> list[Path]:
    """Every regular file and symlink under *source_dir*, sorted by its
    archive-relative POSIX path for determinism. A symlinked directory is
    archived as a symlink leaf, not descended into — matching how it will
    come back out of :func:`unpack_product_baseline` (the safe extractor
    restores the symlink itself, then refuses to write anything through
    it). A device/FIFO/socket entry is silently skipped: it cannot appear
    in a product baseline meaningfully, and ``TarExtractor``'s own safety
    checks reject it on the read side anyway."""
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(source_dir, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            candidate = current / name
            if candidate.is_symlink():
                paths.append(candidate)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            candidate = current / name
            try:
                st = candidate.lstat()
            except OSError:
                # Vanished between os.walk() listing it and this stat (a
                # concurrent build/cleanup process) -- nothing to archive.
                continue
            if stat_module.S_ISREG(st.st_mode) or stat_module.S_ISLNK(st.st_mode):
                paths.append(candidate)
    paths.sort(key=lambda p: _relative_posix(p, source_dir))
    return paths


def _discover_empty_dirs(source_dir: Path, paths: Sequence[Path]) -> list[Path]:
    """Every real (non-symlink) directory under *source_dir* with nothing
    from *paths* archived beneath it -- these need an explicit tar
    directory member, since ``tarfile`` only creates intermediate
    directories implicitly for a *file*'s own path components. Without
    this, a directory with no file or symlink under it (most notably an
    explicitly declared ``--header-root`` with nothing in it yet) would
    silently vanish on unpack even though the manifest still names it
    (Codex review, fresh evidence). Only leaf empty directories are
    returned -- a leaf's own tar member is enough for extraction to
    recreate its whole parent chain, the same way a file's member already
    does."""
    covered: set[Path] = set()
    for p in paths:
        parent = p.parent
        while True:
            covered.add(parent)
            if parent == source_dir:
                break
            parent = parent.parent

    empty_dirs: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(source_dir, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        if current != source_dir and current not in covered and not dirnames:
            empty_dirs.append(current)
    empty_dirs.sort(key=lambda p: _relative_posix(p, source_dir))
    return empty_dirs


class _HashingReader:
    """Wrap a binary file object, updating *hasher* with every byte read —
    lets :func:`pack_product_baseline` hash a library's content while
    streaming it into the tar member, instead of reading it twice."""

    def __init__(self, fh: IO[bytes], hasher: Any) -> None:
        self._fh = fh
        self._hasher = hasher

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        if chunk:
            self._hasher.update(chunk)
        return chunk


def _add_member(
    tf: tarfile.TarFile, path: Path, arcname: str, source_dir: Path
) -> LibraryEntry | None:
    info = tf.gettarinfo(str(path), arcname=arcname)
    # Deterministic metadata: two packs of byte-identical content produce a
    # byte-identical archive, regardless of who built it or when.
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    # Mode too: an umask difference between two builders (0644 vs. 0664),
    # or an unrelated permission bit, would otherwise produce two
    # different archives from byte-identical content -- keep only the
    # owner-executable bit, which is what actually distinguishes a
    # program/shared library from data here.
    if not info.issym():
        info.mode = 0o755 if info.mode & 0o100 else 0o644

    if info.issym():
        target = info.linkname
        if os.path.isabs(target):
            # An absolute target can't round-trip: it names a path under
            # *this* pack's source_dir, which won't exist at that same
            # absolute location once unpacked into a different staging
            # directory -- TarExtractor's own symlink-escape check would
            # then (correctly) refuse the archive at unpack time, even
            # though the target was genuinely inside source_dir when this
            # was packed (Codex review, fresh evidence: pack succeeds,
            # its own paired unpack can never read the result).
            raise SnapshotError(
                f"symlink {arcname!r} has an absolute target {target!r} -- "
                "only relative symlink targets can be packed portably"
            )
        link_abs = path.parent / target
        try:
            link_abs.resolve().relative_to(source_dir.resolve())
        except ValueError as exc:
            raise SnapshotError(
                f"symlink {arcname!r} targets {target!r}, which escapes "
                f"{source_dir} — refusing to pack it"
            ) from exc
        tf.addfile(info)
        return None

    if info.islnk():
        # gettarinfo() converts a second (and later) path sharing an
        # inode with an already-archived one into a hardlink reference
        # (TarFile tracks (dev, ino) -> arcname internally when
        # dereference=False, the default this module uses) -- a member
        # carrying no data of its own, just info.linkname pointing at the
        # first archived path. Falling through to the "not info.isreg()"
        # guard below would silently drop it from the archive entirely
        # (isreg() is False for a hardlink member too), losing the file on
        # round-trip rather than merely losing its hardlink-ness.
        #
        # A hardlink member's own info.size is 0 (no data bytes follow it
        # in the tar stream), so it cannot supply a LibraryEntry's size the
        # way the first-archived copy's streamed info.size does -- and if
        # this path's own name is itself library-named, skipping it here
        # would silently omit it from the manifest even though it round-
        # trips correctly as file content (Codex review, fresh evidence:
        # two hardlinked library names, only the first-archived one gets a
        # LibraryEntry). Hash and stat the real file directly instead --
        # its content is identical to the already-archived copy's by
        # definition of being a hardlink to the same inode.
        entry = None
        if _is_shared_library(path.name):
            hasher = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
            entry = LibraryEntry(
                name=path.name,
                path=arcname,
                sha256=hasher.hexdigest(),
                size=path.stat().st_size,
            )
        tf.addfile(info)
        return entry

    if not info.isreg():  # pragma: no cover - _discover_paths already filters
        return None

    if _is_shared_library(path.name):
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            tf.addfile(info, fileobj=_HashingReader(fh, hasher))
        return LibraryEntry(
            name=path.name, path=arcname, sha256=hasher.hexdigest(), size=info.size
        )

    with open(path, "rb") as fh:
        tf.addfile(info, fileobj=fh)
    return None


def _add_directory_member(tf: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = tf.gettarinfo(str(path), arcname=arcname)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755
    tf.addfile(info)


def _add_manifest_member(
    tf: tarfile.TarFile, manifest: ProductBaselineManifest
) -> None:
    payload = (
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    info = tarfile.TarInfo(name=MANIFEST_MEMBER_NAME)
    info.size = len(payload)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    tf.addfile(info, io.BytesIO(payload))


def pack_product_baseline(
    source_dir: Path | str,
    output: Path | str,
    *,
    product: str = "",
    header_roots: Sequence[str] = (),
    zstd_level: int = ZSTD_LEVEL_BASELINE,
) -> ProductBaselineManifest:
    """Pack every file under *source_dir* into one deterministic
    ``.tar.zst`` product baseline archive at *output*.

    *header_roots* names the product's public-header directories, relative
    to *source_dir* — recorded in the manifest so a later
    ``compare -H`` invocation against the unpacked archive doesn't have to
    rediscover them. Each must resolve under
    *source_dir* and exist; raises :class:`SnapshotError` otherwise, the
    same way an escaping/missing header root fails on write elsewhere in
    this codebase (see ``buildsource.baseline_publish``'s identical guard).

    Writes atomically: *output* either ends up as a complete, valid archive
    or is left untouched — a failure partway through never leaves a
    truncated file at the destination path.
    """
    # Normalized to an absolute path up front, not just where the output-
    # exclusion logic below happens to need it: _scaffold_dirs_for_mkdir()'s
    # own containment check (source_path / output_path.parent) is a plain
    # lexical is_relative_to() -- a caller mixing spellings (an absolute
    # SOURCE_DIR with a relative OUTPUT under the same tree, or vice versa)
    # would make that check spuriously fail, silently reintroducing the
    # empty-source bypass this module's own scaffold-cleanup fix exists to
    # close (CodeRabbit review, fresh evidence). os.path.abspath(), not
    # .resolve() -- OUTPUT's own final path component must stay lexical,
    # matching the symlinked-OUTPUT exclusion logic a few lines down.
    source_path = Path(os.path.abspath(str(source_dir)))
    if not source_path.is_dir():
        raise SnapshotError(
            f"product baseline source is not a directory: {source_path}"
        )
    output_path = Path(os.path.abspath(str(output)))
    if not output_path.name.lower().endswith(".tar.zst"):
        # Not merely a naming preference: unpack_product_baseline() (via
        # TarExtractor.extract()) decides whether to run the archive
        # through the zstd decompressor purely from this suffix — Python's
        # stdlib tarfile has no zstd auto-detection the way it does for
        # gzip/bz2/xz. A different suffix here would silently produce an
        # archive unpack_product_baseline() can't read back correctly.
        raise SnapshotError(
            f"product baseline output must end with '.tar.zst': {output_path}"
        )
    # Created *before* discovery, not just before the write below: if
    # OUTPUT lives under a not-yet-existing subdirectory of SOURCE_DIR
    # (e.g. SOURCE_DIR/artifacts/base.tar.zst), creating this directory
    # only after _discover_paths()/_discover_empty_dirs() ran would mean
    # the first pack never sees `artifacts/` at all (it doesn't exist
    # yet), while a second pack -- run after this same mkdir call already
    # created it -- sees an empty `artifacts/` directory (its only file,
    # OUTPUT itself, is excluded below) and adds an explicit directory
    # member for it. Two runs of the identical invocation would then
    # disagree on file_count and produce different archive bytes (Codex
    # review, fresh evidence). Creating it up front makes every run see
    # the same input tree -- `artifacts/` is discovered as an empty
    # directory member consistently, from the very first pack.
    #
    # Captured *before* the mkdir call below: an originally-empty
    # SOURCE_DIR whose only content is this newly-created output-only
    # directory chain must still be rejected as "nothing to pack" -- the
    # scaffold directory itself carries no real product content, and
    # counting it as such would silently bypass the empty-source check
    # entirely (Codex review, fresh evidence: `pack` on a genuinely empty
    # SOURCE_DIR succeeded with zero libraries once its output lived
    # under a not-yet-existing subdirectory).
    output_scaffold_dirs = _scaffold_dirs_for_mkdir(source_path, output_path.parent)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_header_roots: list[str] = []
    for rel in header_roots:
        resolved = _resolve_under_root(source_path, rel)
        if resolved is None or not resolved.exists():
            raise SnapshotError(
                f"header root {rel!r} is absolute, escapes {source_path}, "
                "or does not exist"
            )
        resolved_header_roots.append(Path(rel).as_posix())

    paths = _discover_paths(source_path)
    # Exclude OUTPUT itself when it lives under SOURCE_DIR — otherwise a
    # rerun of `pack SOURCE_DIR SOURCE_DIR/baseline.tar.zst` would embed
    # the *previous* archive as an input member, growing on every
    # invocation and violating the determinism this format promises.
    # Computed from the two paths, not existence, so this excludes the
    # output slot even on a first-ever pack (before the file exists).
    #
    # Two, layered concerns here, not one:
    #  - if OUTPUT itself is a symlink (e.g. left over from a prior run
    #    pointing somewhere else), we must exclude it by its own lexical
    #    name, not by whatever it points to -- resolving OUTPUT's own leaf
    #    would follow it to its *target*'s path, excluding the wrong file
    #    while leaving the symlink itself, keyed by its own lexical name,
    #    still in `paths` (Codex review, fresh evidence).
    #  - if SOURCE_DIR itself is a symlink alias (e.g. `pack /tmp/src-link
    #    OUTPUT` where `/tmp/src-link -> /tmp/src`) and OUTPUT is spelled
    #    through the real, non-aliased directory, a purely lexical
    #    (os.path.abspath()) comparison never shares a common prefix with
    #    SOURCE_DIR's own alias path, so relative_to() always raises and
    #    nothing gets excluded -- a rerun grows the archive exactly the
    #    way the plain in-tree-output case already did before that fix
    #    (Codex review, fresh evidence, distinct from the leaf-symlink
    #    case above).
    # Resolving *only* OUTPUT's parent (and SOURCE_DIR in full) sees
    # through a directory-level alias for containment purposes, while
    # OUTPUT's own final path component stays lexical -- exactly the
    # combination both cases need at once.
    source_real = source_path.resolve()
    try:
        output_parent_real = output_path.parent.resolve()
    except OSError:  # pragma: no cover - defensive, e.g. a permission error
        output_parent_real = Path(os.path.abspath(str(output_path.parent)))
    output_canonical = output_parent_real / output_path.name
    try:
        output_rel = _relative_posix(output_canonical, source_real)
    except ValueError:
        output_rel = None
    if output_rel is not None:
        paths = [p for p in paths if _relative_posix(p, source_path) != output_rel]
    empty_dirs = _discover_empty_dirs(source_path, paths)
    # A scaffold directory we just created solely to hold OUTPUT doesn't
    # count as real content for this check -- it always ends up empty
    # once OUTPUT itself is excluded above, and an originally-empty
    # SOURCE_DIR must still be rejected regardless of where OUTPUT was
    # asked to go (see the mkdir call above for the full reasoning).
    real_empty_dirs = [d for d in empty_dirs if d not in output_scaffold_dirs]
    if not paths and not real_empty_dirs:
        # The scaffold directories the mkdir() call above just created
        # must not survive this rejection: they're still empty (nothing
        # was ever packed), and leaving them behind would make an
        # identical repeat call see them as already-existing -- silently
        # different from the first call's own view of the world, since
        # _scaffold_dirs_for_mkdir() only ever reports a directory that
        # doesn't exist yet. Left uncleaned, a second identical `pack()`
        # call would then treat that directory as real, pre-existing
        # content and succeed with an otherwise-empty archive instead of
        # reproducing the same rejection (Codex review, fresh evidence).
        # Deepest-first, best-effort: still empty by construction (only
        # this call created them, and nothing was packed), so an OSError
        # here (a concurrent process, a permission change) is left as a
        # secondary problem rather than masking the real "nothing to
        # pack" error.
        for scaffold_dir in sorted(
            output_scaffold_dirs, key=lambda d: len(d.parts), reverse=True
        ):
            try:
                scaffold_dir.rmdir()
            except OSError:
                pass
        raise SnapshotError(
            f"no files found under {source_path} to pack into a product baseline"
        )

    # MANIFEST_MEMBER_NAME is reserved: a real source entry at that path
    # would collide with the generated manifest member added after every
    # other entry, and the generated one -- added last -- would silently
    # win on extraction (tarfile writes members in order; a later member
    # of the same name overwrites an earlier one), replacing the source
    # entry's real content with the archive's own manifest. This must
    # reject a *directory* at the reserved path too, not just a file: the
    # earlier file-only check missed both an empty directory (never in
    # `paths`, which holds only files/symlinks) and a non-empty one
    # (whose own children are in `paths`, prefixed by the reserved name,
    # but the directory entry itself never is) -- either shape lets
    # packing succeed while writing a tar that requires the same path to
    # be both a directory (for the source entries) and a regular file
    # (for the generated manifest, added last), which the paired unpack
    # then fails on with an unhandled IsADirectoryError (Codex review,
    # fresh evidence).
    manifest_child_prefix = MANIFEST_MEMBER_NAME + "/"
    collision = next(
        (
            p
            for p in paths
            if _relative_posix(p, source_path) == MANIFEST_MEMBER_NAME
            or _relative_posix(p, source_path).startswith(manifest_child_prefix)
        ),
        None,
    )
    if collision is None:
        # Same prefix test as the `paths` scan above, not just an exact
        # match: a directory at the reserved path containing only empty
        # subdirectories (e.g. MANIFEST_MEMBER_NAME/sub/, itself empty)
        # is absent from `paths` (no files anywhere under it) AND isn't
        # itself in `empty_dirs` (it has a subdirectory, so it isn't a
        # leaf) -- only its nested empty leaf is, under the reserved
        # name as a *prefix*, not an exact match (CodeRabbit review,
        # fresh evidence).
        collision = next(
            (
                d
                for d in empty_dirs
                if _relative_posix(d, source_path) == MANIFEST_MEMBER_NAME
                or _relative_posix(d, source_path).startswith(manifest_child_prefix)
            ),
            None,
        )
    if collision is not None:
        raise SnapshotError(
            f"{collision} collides with the reserved product baseline manifest "
            f"member name ({MANIFEST_MEMBER_NAME!r}) -- rename or remove it "
            "before packing"
        )

    # Resolve the compressor before mkstemp() hands out an open descriptor:
    # an invalid zstd_level (a public parameter of this function) must fail
    # before there is a descriptor to leak -- the except BaseException
    # block below unlinks the temp *file*, but never closes an fd that
    # failed to reach os.fdopen()'s ownership transfer.
    zstandard = _zstd_module()
    cctx = zstandard.ZstdCompressor(level=zstd_level, write_checksum=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=".abicheck-product-baseline-", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        libraries: list[LibraryEntry] = []
        with os.fdopen(fd, "wb") as raw_out:
            with cctx.stream_writer(raw_out, closefd=False) as compressor:
                with tarfile.open(fileobj=compressor, mode="w|") as tf:
                    for path in paths:
                        arcname = _relative_posix(path, source_path)
                        entry = _add_member(tf, path, arcname, source_path)
                        if entry is not None:
                            libraries.append(entry)
                    for empty_dir in empty_dirs:
                        arcname = _relative_posix(empty_dir, source_path)
                        _add_directory_member(tf, empty_dir, arcname)
                    manifest = ProductBaselineManifest(
                        product=product,
                        libraries=tuple(sorted(libraries, key=lambda e: e.path)),
                        header_roots=tuple(resolved_header_roots),
                        file_count=len(paths) + len(empty_dirs) + 1,
                    )
                    _add_manifest_member(tf, manifest)
        os.replace(tmp_path, output_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass
        raise
    return manifest


def unpack_product_baseline(
    archive: Path | str, dest_dir: Path | str
) -> ProductBaselineManifest:
    """Extract *archive* (as written by :func:`pack_product_baseline`) into
    *dest_dir*, and return its manifest.

    *dest_dir* must not already exist, or must be empty — this never merges
    into or overwrites a directory that might already hold unrelated
    content. Extraction reuses :class:`abicheck.package.TarExtractor`'s
    security-validated ``.tar.zst`` extraction (path traversal, symlink
    escape, and device/FIFO rejection), the same code path package
    extraction already relies on, rather than a second, independently
    written unpacker.

    Extracts into a staging directory first and only publishes it to
    *dest_dir* once the manifest is confirmed present, parseable, and at a
    supported schema version — a missing/corrupt/unsupported archive
    raises :class:`SnapshotError` with *dest_dir* left exactly as it was
    (still absent, or still the empty directory it started as), so a retry
    with a corrected archive doesn't first have to clean up a partial
    extraction by hand.
    """
    archive_path = Path(archive)
    if not archive_path.is_file():
        raise SnapshotError(f"product baseline archive not found: {archive_path}")
    if not archive_path.name.lower().endswith(".tar.zst"):
        raise SnapshotError(
            f"product baseline archive must end with '.tar.zst': {archive_path}"
        )
    dest_path = Path(dest_dir)
    if dest_path.is_symlink():
        # Rejected outright rather than handled: publishing later replaces
        # dest_path with the staging directory via dest_path.rmdir() +
        # os.replace(), and rmdir() operates on the symlink itself (POSIX
        # rmdir() never follows a final symlink component) -- even a
        # symlink to a genuinely empty directory would pass the checks
        # below and then raise NotADirectoryError at publish time,
        # unhandled by the CLI's SnapshotError-only catch, leaving the
        # already-validated staging directory behind uncleaned (Codex
        # review, fresh evidence).
        raise SnapshotError(f"unpack destination must not be a symlink: {dest_path}")
    if dest_path.exists():
        if not dest_path.is_dir():
            raise SnapshotError(f"unpack destination is not a directory: {dest_path}")
        if any(dest_path.iterdir()):
            raise SnapshotError(f"unpack destination is not empty: {dest_path}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(
        tempfile.mkdtemp(
            dir=str(dest_path.parent), prefix=".abicheck-product-baseline-unpack-"
        )
    )
    try:
        try:
            TarExtractor().extract(archive_path, staging)
        except SnapshotError:
            raise
        except Exception as exc:
            # TarExtractor.extract() can raise ExtractionSecurityError (a
            # sibling AbicheckError, not a SnapshotError -- a malicious
            # archive triggering the symlink-escape/path-traversal/device
            # checks), tarfile.TarError (malformed tar), or a zstandard
            # decompression error (corrupt/truncated .tar.zst) -- none of
            # which the CLI's `except SnapshotError` catches, so any of
            # them would otherwise surface as an unhandled traceback
            # instead of the documented exit-64 usage error.
            raise SnapshotError(
                f"{archive_path}: failed to extract product baseline archive: {exc}"
            ) from exc

        manifest_path = staging / MANIFEST_MEMBER_NAME
        if not manifest_path.is_file():
            raise SnapshotError(
                f"{archive_path} does not look like an abicheck product baseline "
                f"archive (missing {MANIFEST_MEMBER_NAME})"
            )
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotError(
                f"{archive_path}: corrupt product baseline manifest"
            ) from exc
        if not isinstance(raw, dict):
            raise SnapshotError(f"{archive_path}: corrupt product baseline manifest")
        # ProductBaselineManifest.from_dict() defensively *defaults* a
        # missing "schema" key to PRODUCT_BASELINE_SCHEMA, per this
        # codebase's established "never abort on a hand-edited/malformed
        # pack" from_dict() convention -- correct for every *other* field,
        # but applied to `schema` itself it defeats the one check this
        # whole discriminator exists for: an archive whose manifest is any
        # parseable mapping without a `schema` key at all (e.g. `{}`)
        # would silently pass _check_schema_supported() and get published
        # as a recognized baseline (Codex review, fresh evidence). Checked
        # explicitly, before the defensive from_dict() default ever
        # applies, so a manifest that doesn't genuinely self-identify as
        # this format is rejected rather than defaulted into looking like
        # one.
        if not isinstance(raw.get("schema"), str) or not raw["schema"]:
            raise SnapshotError(
                f"{archive_path}: product baseline manifest is missing its "
                "schema discriminator -- not a genuine abicheck product "
                "baseline archive"
            )

        manifest = ProductBaselineManifest.from_dict(raw)
        _check_schema_supported(manifest.schema, archive_path)

        # The manifest is untrusted input (the archive could come from
        # anywhere) -- header_roots is meant to be re-joined against the
        # caller's own unpack destination and handed straight to a
        # header-parsing tool, so a corrupt or adversarial manifest
        # declaring an absolute or escaping root ("../../etc", "/etc")
        # must be rejected here, the same way pack_product_baseline()
        # itself refuses to record one on write. from_dict()'s own
        # defensive str() coercion accepts and returns any string
        # unchanged, so this check has to happen here, not there (Codex
        # review, fresh evidence).
        for rel in manifest.header_roots:
            resolved = _resolve_under_root(staging, rel)
            if resolved is None or not resolved.exists():
                raise SnapshotError(
                    f"{archive_path}: manifest declares an invalid header "
                    f"root {rel!r} (absolute, escapes the product root, "
                    "or does not exist)"
                )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    try:
        # tempfile.mkdtemp() creates staging with mode 0700 regardless of
        # the process umask -- deliberately, for its own general-purpose
        # "private scratch dir" contract, but wrong for a directory this
        # function is about to publish as the product's own extracted
        # content: a caller (or a later CI step/container stage) running
        # as a different user would then be unable to even traverse it.
        # Apply the ordinary umask-derived permissions an extraction would
        # normally get instead.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(staging, 0o777 & ~umask)

        # dest_path, if it existed, was already confirmed empty above, so
        # removing it and renaming staging into its place is safe (and
        # portable: os.replace()/os.rename() cannot atomically replace an
        # existing directory on every platform, but replacing a path that
        # no longer exists works everywhere). This whole block -- not just
        # extraction/validation above -- is wrapped so a failure here
        # (a concurrent process populating dest_path, a permission error
        # on chmod/rmdir/replace) still cleans up staging instead of
        # leaving a `.abicheck-product-baseline-unpack-*` directory behind
        # (Codex review, fresh evidence). One residual, inherent to
        # rmdir()+replace() not being atomic: if rmdir() succeeds but
        # os.replace() then fails, the caller's pre-existing empty
        # dest_path is already gone and cannot be restored -- the same
        # cross-platform limitation the comment above already accepts,
        # just now surfaced as a clear SnapshotError instead of silently
        # leaving stray state.
        if dest_path.exists():
            dest_path.rmdir()
        os.replace(staging, dest_path)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise SnapshotError(
            f"{archive_path}: failed to publish the unpacked product "
            f"baseline to {dest_path}: {exc}"
        ) from exc
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _check_schema_supported(schema: str, archive_path: Path) -> None:
    prefix = "abicheck.product-baseline/v"
    if not schema.startswith(prefix):
        raise SnapshotError(
            f"{archive_path}: unrecognized product baseline schema {schema!r}"
        )
    major_text = schema[len(prefix) :]
    try:
        major = int(major_text)
    except ValueError:
        raise SnapshotError(
            f"{archive_path}: unrecognized product baseline schema {schema!r}"
        ) from None
    supported_major = int(PRODUCT_BASELINE_SCHEMA.rsplit("/v", 1)[1])
    if major > supported_major:
        raise SnapshotError(
            f"{archive_path}: product baseline schema {schema!r} is newer than "
            f"this abicheck build supports ({PRODUCT_BASELINE_SCHEMA!r}) — "
            "upgrade abicheck to read it."
        )


def compare_product_directories(
    old_dir: Path | str,
    new_dir: Path | str,
    *,
    header_roots: Sequence[str] = (),
    lang: str = "c++",
    frontend: str = "auto",
    policy: str = "strict_abi",
    include_private: bool = True,
    manifest_path: Path | str | None = None,
    system_providers: Iterable[str] | None = None,
    cohorts: list[str] | None = None,
) -> BundleDiffResult:
    """Compare every shared library two directories have in common, plus
    the cross-library (bundle-level, ADR-023) edges between them — in one
    plain, importable call.

    This is the differentiator a stored whole-product baseline exists for:
    unpack two archives with :func:`unpack_product_baseline`, then pass the
    two resulting directories straight to this function. It reproduces
    what ``abicheck compare <old_dir> <new_dir>`` computes internally for a
    directory pair (per-library diff via
    :func:`abicheck.service_compare_pipeline.run_compare`, cross-library
    findings via :func:`abicheck.bundle.compare_bundle`) — before this
    function existed, the only way to get that combined result without
    shelling out to the CLI was to hand-assemble the same three steps
    yourself (discover + match libraries by canonical name, run a
    per-library compare over each matched pair, build two
    :class:`~abicheck.bundle.BundleSnapshot`\\ s and call
    :func:`~abicheck.bundle.compare_bundle`); the CLI's own version of that
    assembly (``cli_compare_release_helpers._run_bundle_analysis``) is both
    private and Click-coupled (it reports failures via ``click.echo``
    rather than raising), so it was never reusable from plain Python
    either.

    Deliberately not in :mod:`abicheck.bundle` alongside
    :func:`~abicheck.bundle.compare_bundle`: it needs the per-pair compare
    engine (:mod:`abicheck.service_compare_pipeline`), and that module's
    own import graph already reaches back into :mod:`abicheck.bundle`
    (``service_scan`` calls :func:`~abicheck.bundle.audit_bundle`) — living
    here instead of there avoids that import cycle.

    *header_roots* names header directories relative to *old_dir*/*new_dir*
    — the exact shape :attr:`ProductBaselineManifest.header_roots` already
    records, so a caller that just unpacked an archive can pass its
    manifest's own ``header_roots`` straight through. A root missing on
    either side is silently skipped, matching this format's own tolerance
    for a library that ships no public headers.

    Every library discovered in *both* directories (matched by canonical
    library name — see :func:`abicheck.bundle.discover_artifact_set`,
    called once per side) is compared; a library present on only one side
    never reaches the per-library pass, but bundle analysis still reports
    it via ``bundle_library_added``/``bundle_library_removed``. Unlike the
    CLI's ``compare-release`` engine, a failure here is never swallowed
    into a warning — this is a library call, so a per-library compare
    failure or a bundle-analysis failure propagates directly (whatever the
    failing step itself raises — :class:`SnapshotError`,
    :class:`~abicheck.bundle.ArtifactSetError`, ...); a caller wanting the
    CLI's own "report degradation, keep going" behavior should catch what
    it needs.
    """
    from .bundle import (
        build_bundle_snapshot,
        compare_bundle,
        discover_artifact_set,
        load_manifest,
    )
    from .package import discover_shared_libraries
    from .service_compare_pipeline import run_compare

    old_root = Path(old_dir)
    new_root = Path(new_dir)
    roots = list(header_roots)

    old_map = discover_artifact_set(
        discover_shared_libraries(old_root, include_private=include_private),
        explicit=False,
    )
    new_map = discover_artifact_set(
        discover_shared_libraries(new_root, include_private=include_private),
        explicit=False,
    )

    def _resolved_headers(root: Path) -> list[Path]:
        return [candidate for rel in roots if (candidate := root / rel).is_dir()]

    old_headers = _resolved_headers(old_root)
    new_headers = _resolved_headers(new_root)

    per_library_results = []
    for key in sorted(set(old_map) & set(new_map)):
        result = run_compare(
            old_map[key],
            new_map[key],
            old_headers=old_headers,
            new_headers=new_headers,
            lang=lang,
            frontend=frontend,
            policy=policy,
        )
        per_library_results.append(result.diff)

    old_snapshot = build_bundle_snapshot(old_map)
    new_snapshot = build_bundle_snapshot(new_map)

    manifest = None
    if manifest_path is not None:
        manifest = load_manifest(Path(manifest_path))

    return compare_bundle(
        old_snapshot,
        new_snapshot,
        per_library_results,
        manifest=manifest,
        system_providers=system_providers,
        cohorts=cohorts,
    )

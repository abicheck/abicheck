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
import struct
import tarfile
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from .binary_utils import _pe_is_dll_content, resolve_linker_script
from .errors import SnapshotError
from .package import TarExtractor, _is_elf_shared_object

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

#: A genuine SHA-256 digest as lowercase hex -- what pack_product_baseline()
#: always writes. Used by unpack_product_baseline() to reject a missing or
#: malformed LibraryEntry.sha256 outright rather than silently skip the
#: checksum comparison it's meant to gate (see that function's own comment).
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")

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


#: Mach-O ``mach_header``/``mach_header_64`` ``filetype`` values that are a
#: dynamically-loadable image, not a plain executable -- the values that
#: made a ``.so``-shaped ABI question meaningful for the file at all (a
#: framework binary or plugin bundle is exactly the extensionless-Mach-O
#: case this predicate exists to catch). MH_EXECUTE (2) is deliberately
#: excluded: an executable is not a shared library regardless of content.
_MACHO_LIBRARY_FILETYPES = frozenset({6, 8, 9})  # MH_DYLIB, MH_BUNDLE, MH_DYLIB_STUB


def _macho_is_library_content(path: Path) -> bool:
    """True when *path* is a Mach-O dynamic library, bundle, or dylib
    stub, identified from its header ``filetype`` field rather than its
    filename -- the Mach-O counterpart of
    :func:`abicheck.package._is_elf_shared_object`, for an extensionless
    framework binary (``Foo.framework/Foo``) or a nonstandard-extension
    plugin (``.node``) that a suffix check alone never catches (Codex
    review, fresh evidence).

    Covers a *thin* (single-architecture) Mach-O via a direct header read
    (no dependency needed for the common case), and a *fat* (universal)
    archive via :class:`macholib.MachO.MachO` -- a real, if narrow, gap in
    the thin-only version of this check: a universal framework binary
    (the common case for a distributed macOS framework, which routinely
    ships x86_64 + arm64 slices in one file) has fat magic, not a thin
    one, and `macholib` -- already a core, unconditional dependency
    (`macho_metadata.py`) -- already knows how to walk its ``fat_arch``
    slices (Codex review, fresh evidence). A fat archive is recognized as
    a library when *any* slice's filetype is a library type: real-world
    fat binaries are built from one source and their slices agree in
    practice, so requiring unanimous agreement would only risk a
    false-negative on an otherwise-ordinary universal library for no real
    safety benefit.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
                byte_order: str | None = ">"
            elif magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
                byte_order = "<"
            elif magic in (
                b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca",
                b"\xca\xfe\xba\xbf",
                b"\xbf\xba\xfe\xca",
            ):
                byte_order = None  # fat/universal -- handled via macholib below
            else:
                return False  # not Mach-O at all
            if byte_order is not None:
                f.seek(12)  # magic(4) + cputype(4) + cpusubtype(4)
                raw_filetype = f.read(4)
    except OSError:
        return False
    if byte_order is not None:
        if len(raw_filetype) < 4:
            return False
        (filetype,) = struct.unpack(f"{byte_order}I", raw_filetype)
        return filetype in _MACHO_LIBRARY_FILETYPES

    try:
        from macholib.MachO import MachO  # type: ignore[import-untyped]

        macho = MachO(str(path))
    except Exception:  # noqa: BLE001 -- macholib has no single documented
        # exception type for a malformed/truncated fat archive (see the
        # identical broad catch in macho_metadata.py's own SymbolTable
        # parse); this is a best-effort discovery predicate, never a hard
        # failure -- the same "OSError -> False" degrade the thin-header
        # read above already applies.
        return False
    return any(
        int(getattr(header.header, "filetype", -1)) in _MACHO_LIBRARY_FILETYPES
        for header in macho.headers
    )


def _is_library_path(path: Path) -> bool:
    """A shared library, by filename suffix or real content (ELF, Mach-O,
    or PE/COFF).

    Shared by every place that decides "is this a library" -- packing's own
    manifest-entry classification (:func:`_add_member`, both its regular-
    file and hardlink-member branches) and :func:`_discover_library_map`'s
    discovery walk. Previously each checked filename suffix alone (via
    :func:`_is_shared_library`); an extensionless ELF DSO -- a plugin named
    without a conventional ``.so`` suffix, real on Linux -- was discovered
    by neither at first, and even after discovery gained the content-aware
    fallback, packing's own manifest classification still didn't, so such a
    file was archived (``_discover_paths`` archives every regular file
    regardless of suffix) but produced no ``LibraryEntry`` — the returned
    and persisted manifest falsely reported the product had no such library
    (Codex review, fresh evidence). Factored into one predicate so
    discovery and manifest classification can never drift apart on this
    question again.

    The ELF content fallback (:func:`~abicheck.package._is_elf_shared_object`)
    had no Mach-O/PE counterpart, so a macOS framework binary
    (``Foo.framework/Foo``, conventionally extensionless) or a Windows
    ``.pyd`` extension module never entered either library map even though
    both are real, supported native-binary formats — a framework-only
    product silently compared as if it shipped no libraries at all (Codex
    review, fresh evidence). :func:`_macho_is_library_content`/
    :func:`_pe_is_dll_content` close that for both thin and fat
    (universal) Mach-O and for any PE image respectively.

    A ``.debug`` split-debug sidecar (the conventional
    ``objcopy --only-keep-debug`` output, e.g. ``libfoo.so.1.debug``) is
    excluded outright, ahead of every content check: it is itself a valid
    ELF file that retains its original binary's ``ET_DYN`` header (objcopy
    strips sections/symbols, not the header), so the content-aware ELF
    fallback above would otherwise discover it as a second, independent
    "library" alongside the real DSO it was split from. A release that
    merely omits or relocates the sidecar then read as a breaking removal
    of a library that, from the shipped DSO's own perspective, never
    changed at all (Codex review, fresh evidence). ``_is_shared_library``
    already excludes this filename from the suffix check (``_VERSIONED_
    SONAME`` requires every post-``.so`` segment to be purely numeric, and
    ``debug`` is not) — this guard closes the identical gap for the
    content-based fallbacks, which have no notion of filename at all.
    """
    if path.name.lower().endswith(".debug"):
        return False
    is_library = (
        _is_shared_library(path.name)
        or _is_elf_shared_object(path)
        or _macho_is_library_content(path)
        or _pe_is_dll_content(path)
    )
    if not is_library:
        return False
    # A GNU ld INPUT()/GROUP() linker script (`libfoo.so -> INPUT(libfoo.
    # so.1)`) is library-suffix-named but carries no binary content.
    # `_discover_library_map` already excluded it separately -- but
    # `_add_member` (packing) called this predicate directly with no
    # equivalent exclusion, so packing and comparison ended up with
    # contradictory inventories for the identical tree (Codex review,
    # fresh evidence). Centralized here, matching this function's own
    # "one predicate, no drift" docstring promise. `resolve_linker_script`
    # itself guards against misclassifying real binary content whose
    # bytes happen to contain linker-script-shaped text (see its own
    # docstring) -- no need to duplicate that check here.
    _, is_linker_script = resolve_linker_script(path)
    return not is_linker_script


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
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
    except RuntimeError:
        # A self-referential symlink loop makes Path.resolve() raise
        # RuntimeError instead of returning a value -- pathlib's own loop
        # detection can fire unconditionally (Codex review, fresh
        # evidence). Callers expect a clean None, not an uncaught
        # exception.
        return None
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
        if _is_library_path(path):
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

    if _is_library_path(path):
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
    # Validated against the tree as it exists *before* the output-parent
    # scaffold below can fabricate anything: a header root is only ever
    # legitimate if it named a real, pre-existing directory in the
    # product. Doing this after the scaffold mkdir would let a header
    # root that happens to name the freshly-created (empty) output-parent
    # chain pass validation on the strength of a directory that mkdir()
    # just manufactured -- e.g. `output=SOURCE/include/base.tar.zst,
    # header_roots=["include"]` would silently accept `include/` as a
    # real header root and persist an empty, fabricated header tree in
    # the manifest, with a later comparison running without the header
    # evidence the caller actually asked for (Codex review, fresh
    # evidence -- the earlier output-parent-determinism fix below didn't
    # close this interaction).
    if isinstance(header_roots, str):
        # A bare str satisfies the declared Sequence[str] annotation (it's
        # a sequence of its own characters), so the natural single-root
        # spelling header_roots="include" -- a typo for the intended
        # ["include"] -- would otherwise iterate character-by-character
        # below: 'i', 'n', 'c', 'l', 'u', 'd', 'e', each checked as its own
        # "header root". Depending on the tree, that either rejects a
        # real, intended include/ with a misleading error, or (if
        # matching single-character directories happen to exist) silently
        # persists the wrong header roots into the manifest.
        # compare_product_directories()'s own `_roots_for_library` already
        # rejects this same shape on the comparison side (`HeaderRootsSpec`
        # is the identical Sequence[str] | Mapping[str, Sequence[str]]
        # type), but that guard doesn't cover this, the packing entry
        # point (Codex review, fresh evidence).
        raise SnapshotError(
            "header_roots must be a sequence of paths, not a bare string: "
            f"{header_roots!r}"
        )
    resolved_header_roots: list[str] = []
    for rel in header_roots:
        resolved = _resolve_under_root(source_path, rel)
        # Must be a directory, not merely exist: compare_product_
        # directories() only ever includes a header root when
        # `.is_dir()` is true, silently dropping anything else -- a
        # header root recorded here as a regular file would round-trip
        # through the manifest but never actually reach a comparison
        # (Codex review, fresh evidence).
        if resolved is None or not resolved.is_dir():
            raise SnapshotError(
                f"header root {rel!r} is absolute, escapes {source_path}, "
                "or is not an existing directory"
            )
        resolved_header_roots.append(Path(rel).as_posix())

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
    # Two, layered aliasing concerns, resolved once and reused below for
    # both the scaffold-dir computation and the output-exclusion logic:
    #  - if OUTPUT itself is a symlink, it must be handled by its own
    #    lexical name, not by whatever it points to -- resolving OUTPUT's
    #    own leaf would follow it to its *target*'s path (Codex review,
    #    fresh evidence).
    #  - if SOURCE_DIR itself is a symlink alias (`pack /tmp/src-link
    #    OUTPUT` where `/tmp/src-link -> /tmp/src`) and OUTPUT is spelled
    #    through the real, non-aliased directory, a purely lexical
    #    comparison never shares a common prefix with SOURCE_DIR's own
    #    alias path, so nothing is recognized as being inside SOURCE_DIR
    #    at all (Codex review, fresh evidence, distinct from the leaf-
    #    symlink case above). This bit the scaffold-dir computation too:
    #    it used to receive the *un-resolved* pair, so its own containment
    #    check silently returned an empty set for this exact mismatch --
    #    the freshly-created output-only directory then read as genuine
    #    pre-existing content, letting an otherwise-empty SOURCE_DIR
    #    bypass the "nothing to pack" rejection (Codex review, fresh
    #    evidence, second round).
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

    # _scaffold_dirs_for_mkdir()/_discover_empty_dirs() below both need a
    # `leaf_parent`/root spelled *lexically* relative to `source_path`
    # (the same spelling `paths`/`empty_dirs` themselves use), not the
    # resolved `output_parent_real` -- so the alias is translated back
    # into source_path's own spelling instead.
    if output_rel is not None:
        output_rel_parent = os.path.dirname(output_rel)
        leaf_parent_lexical = (
            source_path / output_rel_parent if output_rel_parent else source_path
        )
    else:
        leaf_parent_lexical = output_path.parent

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
    output_scaffold_dirs = _scaffold_dirs_for_mkdir(source_path, leaf_parent_lexical)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    paths = _discover_paths(source_path)
    # Exclude OUTPUT itself when it lives under SOURCE_DIR — otherwise a
    # rerun of `pack SOURCE_DIR SOURCE_DIR/baseline.tar.zst` would embed
    # the *previous* archive as an input member, growing on every
    # invocation and violating the determinism this format promises.
    # Computed from the two paths, not existence, so this excludes the
    # output slot even on a first-ever pack (before the file exists).
    if output_rel is not None:
        paths = [p for p in paths if _relative_posix(p, source_path) != output_rel]
    empty_dirs = _discover_empty_dirs(source_path, paths)

    # The scaffold directories the mkdir() call above just created must not
    # survive *any* failure path below that leaves them empty: an in-tree
    # output parent created solely to hold OUTPUT never carries real source
    # content by construction, so leaving one behind after a rejection would
    # make an identical repeat call see it as already-existing -- silently
    # different from the first call's own view of the world, since
    # _scaffold_dirs_for_mkdir() only ever reports a directory that doesn't
    # exist yet. Originally only wired into the empty-source rejection below
    # (Codex review, fresh evidence) -- a *different* rejection further down
    # (e.g. a reserved manifest-name collision) left the identical scaffold
    # behind too, since only that one call site cleaned up; a retry after
    # fixing the collision then treated the leftover scaffold as real,
    # pre-existing content instead of reproducing an equivalent check
    # (Codex review, fresh evidence, second round). Factored into one helper
    # so every failure path after the mkdir() call — not just the one this
    # was first noticed on — cleans up the same way: deepest-first,
    # best-effort (still empty by construction whenever nothing was
    # actually packed, so an OSError here — a concurrent process, a
    # permission change — is left as a secondary problem rather than
    # masking the real error).
    def _cleanup_output_scaffold_dirs() -> None:
        for scaffold_dir in sorted(
            output_scaffold_dirs, key=lambda d: len(d.parts), reverse=True
        ):
            try:
                scaffold_dir.rmdir()
            except OSError:
                pass

    # A scaffold directory we just created solely to hold OUTPUT doesn't
    # count as real content for this check -- it always ends up empty
    # once OUTPUT itself is excluded above, and an originally-empty
    # SOURCE_DIR must still be rejected regardless of where OUTPUT was
    # asked to go (see the mkdir call above for the full reasoning).
    real_empty_dirs = [d for d in empty_dirs if d not in output_scaffold_dirs]
    if not paths and not real_empty_dirs:
        _cleanup_output_scaffold_dirs()
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
        _cleanup_output_scaffold_dirs()
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
    # tempfile.mkstemp() always creates its file mode 0600, deliberately,
    # for its own general-purpose "private scratch file" contract -- wrong
    # for a release asset this function is about to publish under
    # `output_path`. os.replace() carries the temp file's own mode across
    # (POSIX rename never touches permissions), so left alone a packed
    # archive would be unreadable by anyone but its creator even under an
    # ordinary 0022 umask, and repacking an existing group/world-readable
    # baseline would silently strip its access (Codex review, fresh
    # evidence). Preserve an existing destination's own mode when
    # overwriting it (a caller may have deliberately set one); otherwise
    # fall back to the ordinary umask-derived file permissions a freshly
    # written file would normally get.
    if output_path.exists():
        target_mode = stat_module.S_IMODE(output_path.stat().st_mode)
    else:
        umask = os.umask(0)
        os.umask(umask)
        target_mode = 0o666 & ~umask
    try:
        zstandard = _zstd_module()
        cctx = zstandard.ZstdCompressor(level=zstd_level, write_checksum=True)
    except BaseException:
        # Nothing has been written yet (no temp file exists at this
        # point) -- the same best-effort scaffold cleanup as every other
        # failure path after the mkdir() call above.
        _cleanup_output_scaffold_dirs()
        raise
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(output_path.parent),
            prefix=".abicheck-product-baseline-",
            suffix=".tmp",
        )
    except BaseException:
        # mkstemp() itself can fail (ENOSPC, EMFILE, a permission race) --
        # nothing has been written yet, so the same best-effort scaffold
        # cleanup as every other failure path after the mkdir() call above
        # (Codex review, fresh evidence: this call sat between the two
        # existing try/except blocks, uncovered by either).
        _cleanup_output_scaffold_dirs()
        raise
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, target_mode)
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
        # The temp file above is unlinked first, so a scaffold directory
        # that held nothing but it is empty again by the time this runs --
        # safe to attempt the same best-effort cleanup every other failure
        # path after the mkdir() call above already does.
        _cleanup_output_scaffold_dirs()
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
    # Captured before publication, not derived from the umask at that
    # point: a caller may have pre-created dest_path with deliberate,
    # non-default permissions (a private 0700 scratch dir, a shared 0775
    # group directory), and publication below replaces dest_path outright
    # -- if it always re-derived the mode from the process umask instead,
    # a pre-created private directory would silently become world-
    # traversable (or a shared one would silently lose its group bit)
    # the moment this function ran (Codex review, fresh evidence).
    existing_dest_mode = (
        stat_module.S_IMODE(dest_path.stat().st_mode) if dest_path.exists() else None
    )
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
            if resolved is None or not resolved.is_dir():
                raise SnapshotError(
                    f"{archive_path}: manifest declares an invalid header "
                    f"root {rel!r} (absolute, escapes the product root, "
                    "or is not an existing directory)"
                )
        # LibraryEntry.path carries the identical untrusted-manifest risk
        # as header_roots above -- it's documented as "relative to the
        # archive/product root" but from_dict()'s defensive str()
        # coercion accepts and returns any string unchanged, so an
        # archive declaring "../../outside.so" or "/tmp/other.so" would
        # otherwise publish the extraction and hand the caller a manifest
        # whose advertised library inventory resolves outside the
        # unpacked baseline entirely (Codex review, fresh evidence).
        for lib in manifest.libraries:
            lib_resolved = _resolve_under_root(staging, lib.path)
            if lib_resolved is None or not lib_resolved.is_file():
                raise SnapshotError(
                    f"{archive_path}: manifest declares an invalid library "
                    f"path {lib.path!r} (absolute, escapes the product "
                    "root, or is not an existing file)"
                )
            # This check only confirmed the path *exists*, not that its
            # bytes are what the manifest claims -- a stale/tampered
            # archive (e.g. a truncated libfoo.so with the original,
            # larger size/sha256 still on the manifest) would otherwise
            # publish unverified, and a later comparison would silently
            # analyze corrupted content (Codex review, fresh evidence).
            # Mirrors pack_product_baseline()'s own hashing (`_add_member`)
            # -- read once, in chunks, not loaded whole into memory.
            #
            # Size is compared unconditionally, sha256 validated as a
            # genuine 64-hex digest before comparison -- neither is
            # skippable for a 0/"" value. pack_product_baseline() always
            # records real values, so 0/"" can only mean a hand-crafted
            # manifest with nothing to compare (no real library is 0
            # bytes) or an attacker who zeroed the fields specifically to
            # bypass this check -- unlike this codebase's usual "don't
            # abort on missing, only on wrong" convention, a value this
            # check's own security purpose depends on must not be
            # skippable by simply omitting it (Codex review, fresh
            # evidence: an earlier revision's `if lib.size and ...`/
            # `if lib.sha256:` truthiness guards let exactly that happen).
            actual_size = lib_resolved.stat().st_size
            if actual_size != lib.size:
                raise SnapshotError(
                    f"{archive_path}: library {lib.path!r} size mismatch "
                    f"(manifest declares {lib.size} bytes, extracted "
                    f"content is {actual_size} bytes) -- archive may be "
                    "corrupt or tampered with"
                )
            if not _SHA256_HEX_RE.fullmatch(lib.sha256):
                raise SnapshotError(
                    f"{archive_path}: library {lib.path!r} has a missing "
                    "or malformed sha256 checksum in the manifest -- "
                    "archive may be corrupt or tampered with"
                )
            hasher = hashlib.sha256()
            with open(lib_resolved, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != lib.sha256:
                raise SnapshotError(
                    f"{archive_path}: library {lib.path!r} checksum "
                    "mismatch -- archive may be corrupt or tampered "
                    "with"
                )
        # The verification loop above only examines manifest-declared
        # entries -- an archive that omits a LibraryEntry entirely (or
        # corrupts "libraries" to a non-list value from_dict() degrades to
        # ()) has that library's real content never verified, republishing
        # tampered bytes under a manifest that claims it doesn't exist
        # (Codex review, fresh evidence). Cross-checked against what was
        # actually extracted via _discover_library_map(), the same walk
        # compare_product_directories() uses, so "is this a library" can't
        # drift between packing, this check, and a later comparison.
        #
        # Compared by filesystem identity (dev, ino), not path string:
        # pack_product_baseline() never gives a dev-symlink alias
        # (`liba.so -> liba.so.1.2.3`) its own LibraryEntry -- only the
        # real target is declared -- while _discover_library_map()'s own
        # dedup can surface *either* alias as survivor depending on walk
        # order. A path-string comparison misfires on an ordinary,
        # unmodified archive whenever the symlink is the survivor (self-
        # caught: the first revision of this fix did exactly that and
        # failed this module's own round-trip tests). Path.stat() follows
        # symlinks, so both sides resolve to the real target's identity.
        declared_identities = set()
        for lib in manifest.libraries:
            lib_resolved = _resolve_under_root(staging, lib.path)
            assert lib_resolved is not None  # already validated above
            st = lib_resolved.stat()
            declared_identities.add((st.st_dev, st.st_ino))
        extracted_libraries = _discover_library_map(staging, include_private=True)
        undeclared = []
        for rel_path, real_path in sorted(extracted_libraries.items()):
            try:
                st = real_path.stat()
            except OSError:
                continue
            if (st.st_dev, st.st_ino) not in declared_identities:
                undeclared.append(rel_path)
        if undeclared:
            raise SnapshotError(
                f"{archive_path}: extracted content contains "
                f"{len(undeclared)} library file(s) the manifest never "
                f"declared (e.g. {undeclared[0]!r}) -- archive may be "
                "corrupt or tampered with"
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
        # If the caller's own dest_path already existed with a deliberate
        # mode, preserve it instead of overwriting it with an umask-
        # derived guess -- see the mode capture above for why silently
        # replacing it would be a real permission regression, not just a
        # cosmetic difference.
        if existing_dest_mode is not None:
            target_dir_mode = existing_dest_mode
        else:
            umask = os.umask(0)
            os.umask(umask)
            target_dir_mode = 0o777 & ~umask
        os.chmod(staging, target_dir_mode)

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
    # Only major >= 1 has ever been a real, shipped format -- v1 is both
    # the first and (today) the only one this build implements. The check
    # above alone accepts any major <= supported_major, including v0 or a
    # negative major that no version of this codebase ever wrote, so a
    # malformed or foreign manifest spelling one of those would still be
    # deserialized with the v1 field layout and published as a supported
    # baseline (Codex review, fresh evidence).
    if major < 1:
        raise SnapshotError(
            f"{archive_path}: unrecognized product baseline schema {schema!r}"
        )


#: Either a flat list of header-root directories applied to every library
#: (the original shape), or a ``{library_key: [roots...]}`` mapping scoping
#: roots to one library at a time -- see
#: :func:`compare_product_directories`'s own docstring and
#: ``docs/contribute/plans/product-baseline-per-library-header-roots.md``
#: for the design rationale. The library key is whatever
#: :func:`_discover_library_map` produced for that side (a path relative to
#: that side's own root).
HeaderRootsSpec = Sequence[str] | Mapping[str, Sequence[str]]


def _roots_for_library(spec: HeaderRootsSpec, library_key: str) -> Sequence[str]:
    """Resolve one library's own header roots out of a :data:`HeaderRootsSpec`.

    A ``Mapping`` is looked up by *library_key*, defaulting to no roots at
    all for a library the mapping doesn't mention (never a fallback to
    some other library's roots — a library legitimately shipping no public
    headers is the common case this format already tolerates elsewhere, not
    a misconfiguration to paper over). A flat ``Sequence[str]`` (the
    original, pre-per-library shape) applies unchanged to every library, so
    passing a bare list keeps working exactly as it always has.

    A bare ``str`` is rejected outright rather than silently accepted: it
    satisfies the declared ``Sequence[str]`` type (a `str` is a sequence of
    its own characters), so `header_roots="include"` — a natural typo for
    the intended single-root-list shape — previously returned unchanged and
    was iterated character-by-character by the caller, each single-character
    candidate failing ``.is_dir()`` and silently running the comparison with
    zero header evidence (Codex/CodeRabbit review, fresh evidence). The same
    applies to a per-library mapping value spelled as a bare string
    (``{"lib/a.so": "include"}``) — that value is returned unchanged too and
    hits the identical character-iteration failure.
    """
    if isinstance(spec, Mapping):
        value = spec.get(library_key, ())
        if isinstance(value, str):
            raise SnapshotError(
                "per-library header roots must be a sequence of paths, not "
                f"a bare string, for library {library_key!r}: {value!r}"
            )
        return value
    if isinstance(spec, str):
        raise SnapshotError(
            "header roots must be a sequence of paths or a per-library "
            f"mapping, not a bare string: {spec!r}"
        )
    return spec


def _discover_library_map(root: Path, *, include_private: bool) -> dict[str, Path]:
    """Discover every shared library under *root*, keyed by a path relative
    to *root* (POSIX-style) rather than its bare filename.

    Deliberately not :func:`abicheck.bundle.discover_artifact_set`'s
    canonical (SONAME-major-stripped) key: that function exists for the
    one-sided ``--artifact-set`` audit case, where two real files
    canonicalizing to the same bare name (``libfoo.so.1``/``libfoo.so.2``
    both reducing to ``libfoo.so``) is treated as a genuine ambiguity and
    rejected outright. For a *product* that intentionally ships two
    SONAME majors side by side, that's not an ambiguity to reject — it's
    two libraries this function must discover and compare independently
    (Codex review, fresh evidence: a product with parallel majors
    couldn't be compared via :func:`compare_product_directories` at all).

    Keyed by *relative path*, not bare filename: two distinct DSOs sharing
    a basename in different directories (``plugins/a/plugin.so`` and
    ``plugins/b/plugin.so`` — an ordinary plugin-host layout) previously
    collided on the same dict key, silently dropping one of them from
    both the per-library comparison and either bundle snapshot (Codex
    review, fresh evidence). A relative-path key disambiguates that case
    for free while leaving the common case (one library per basename)
    unaffected, since :func:`~abicheck.bundle.build_bundle_snapshot`'s own
    resolution graph indexes libraries by their real SONAME/on-disk
    filename separately from this dict's own key.

    Format-neutral, unlike :func:`abicheck.package.discover_shared_libraries`
    (ELF-only): this walks the tree directly and matches by suffix using the
    same :func:`_is_shared_library` predicate :func:`pack_product_baseline`
    already uses to classify a :class:`LibraryEntry` — so a PE ``.dll`` or
    Mach-O ``.dylib`` a product baseline archive genuinely carries is
    discovered here too, not silently invisible to every comparison this
    function drives (Codex review, fresh evidence: a Windows/macOS product
    baseline compared as empty/no-change with no per-library comparisons
    ever run, despite packing recognizing and archiving those files). The
    suffix check is supplemented, not replaced, by
    :func:`abicheck.package._is_elf_shared_object`'s content-aware ELF
    ``ET_DYN`` sniff: an extensionless ELF DSO (a plugin named without a
    conventional ``.so`` suffix, real on Linux) previously dropped out of
    both the per-library and bundle-level comparison entirely — two changed
    products silently comparing as ``NO_CHANGE`` — since a filename-only
    check has no way to recognize it (Codex review, fresh evidence). The DLL/
    Mach-O suffix checks are unaffected; only the ELF side gains the
    content-aware fallback, matching what
    :func:`abicheck.package.discover_shared_libraries` already does for its
    own ELF-only walk.

    A real hardlink/symlink alias pointing at the identical file is still
    collapsed to one entry — using the same ``(dev, ino)`` filesystem
    identity :func:`~abicheck.bundle.discover_artifact_set` keys on — so a
    conventional ``libfoo.so -> libfoo.so.1`` dev symlink doesn't
    double-count as two separate libraries. *include_private* is accepted
    for API symmetry with the ELF-only discovery it replaces, but has no
    effect here: every discovered file already carries a recognizable
    library suffix or a recognized ELF ``ET_DYN`` header, so there is no
    "real DSO with an unconventional name at a conventional path" case left
    for a public/private directory split to disambiguate.

    The directory-walk order is sorted (both ``dirnames`` and ``filenames``,
    in place), not just the filenames within one directory: ``os.walk``
    otherwise yields sibling directories in filesystem order, which is
    unspecified — if a symlink alias and its target live in different
    directories, the surviving representative for their shared identity
    depended on which directory the walk reached first, so two runs over the
    identical tree could produce different ``library_map`` keys (CodeRabbit
    review).
    """
    del include_private  # accepted for API symmetry only; see docstring.

    root_resolved = root.resolve()
    seen_identity: set[Path | tuple[int, int]] = set()
    library_map: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if not _is_library_path(path):
                continue
            # A GNU ld INPUT()/GROUP() linker script (the conventional
            # `libfoo.so -> INPUT(libfoo.so.1)` SDK-install pattern) is
            # library-suffix-named but carries no binary content of its
            # own -- discovering it alongside its real target (libfoo.so.1,
            # discovered independently under its own name) would produce
            # two DiffResults for the identical library, and if the target
            # were unavailable a non-library script could abort the whole
            # comparison as "cannot detect format". The real target -- if
            # present in this same tree -- is already discovered on its
            # own merits by this same walk, so the script itself must be
            # excluded, never resolved to a duplicate entry -- now handled
            # by `_is_library_path` itself (the `continue` above), so
            # every caller shares one exclusion instead of this walk
            # re-checking a fact its own dependency already established
            # (Codex review, fresh evidence: `_add_member`, the packing
            # path, called `_is_library_path` directly with no equivalent
            # check of its own, so a linker script was archived as its own
            # `LibraryEntry` even though this walk already excluded it).
            try:
                real = path.resolve()
            except OSError:
                real = path
            else:
                # A library-shaped symlink (os.walk(followlinks=False)
                # still lists a symlink-to-a-*file* under filenames, only
                # a symlink-to-a-*directory* is skipped) whose target
                # resolves outside root entirely -- e.g. `libfoo.so ->
                # /usr/lib/libfoo.so` -- is rejected rather than
                # discovered: comparing it would silently analyze a host
                # file that isn't part of the product at all, making the
                # result machine-dependent and potentially hiding a
                # missing shipped library. Matches the containment
                # discipline pack/unpack already enforce via
                # `_resolve_under_root` for a manifest-declared path
                # (Codex review, fresh evidence). A conventional in-tree
                # dev symlink (`libfoo.so -> libfoo.so.1`, same directory
                # or elsewhere under root) still resolves within root and
                # is unaffected.
                if real != root_resolved and not real.is_relative_to(root_resolved):
                    continue
            try:
                st = real.stat()
                identity: Path | tuple[int, int] = (st.st_dev, st.st_ino)
            except OSError:
                identity = real
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            library_map[_relative_posix(path, root)] = path
    return library_map


def compare_product_directories(
    old_dir: Path | str,
    new_dir: Path | str,
    *,
    header_roots: HeaderRootsSpec = (),
    old_header_roots: HeaderRootsSpec | None = None,
    new_header_roots: HeaderRootsSpec | None = None,
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
    manifest's own ``header_roots`` straight through, applied to *both*
    sides *and every library*. A well-formed root (a relative path
    contained within the product directory) that simply doesn't exist for
    a given library/side is silently skipped, matching this format's own
    tolerance for a library that ships no public headers -- but a
    structurally invalid root (absolute, or escaping the product
    directory) is a caller/config error and raises :class:`SnapshotError`
    outright, rather than being silently dropped and running that side's
    per-library compare with its header evidence quietly missing (Codex
    review, fresh evidence).

    *header_roots* (and *old_header_roots*/*new_header_roots*) also accept
    a ``{library_key: [roots...]}`` mapping instead of a flat list, for a
    product whose libraries don't all share one header space (e.g.
    ``liba.so``'s public headers live under ``include/liba/`` and
    ``libb.so``'s under ``include/libb/`` — handing both roots to every
    library risks an ODR collision between two independently-versioned
    header trees that happen to declare a same-named type differently). A
    library with no entry in such a mapping gets no headers for that side
    — not a fallback to another library's roots. The mapping is keyed by
    the same identity :func:`_discover_library_map` produces (a path
    relative to that side's own root, e.g. ``"lib/liba.so"``) — the only
    identity a caller can actually observe without reimplementing
    discovery. See ``docs/contribute/plans/
    product-baseline-per-library-header-roots.md`` for the design
    rationale and what's deliberately still out of scope (a shared
    header-AST cache across libraries, and a cross-library type graph).

    *old_header_roots*/*new_header_roots* override *header_roots*
    independently per side, for a product that relocated its public
    headers between releases (e.g. old ships ``include``, new ships
    ``sdk/include``) — a single shared root list cannot express that, and
    silently resolving only the side that happens to match would compare
    one side without its real header evidence rather than reporting the
    intended whole-product comparison. Each defaults to *header_roots*
    when not given, so passing one manifest's roots to *header_roots*
    alone still works for the common case where both sides agree.

    Every library discovered in *both* directories (matched by relative
    path — see :func:`_discover_library_map`, called once per side) is
    compared. A library whose relative path changed but whose *canonical*
    name (SONAME major stripped, e.g. a ``lib/libfoo.so.1`` -> ``lib/
    libfoo.so.2`` bump with no unversioned alias carried across) matches
    exactly one candidate on the other side, counting the *complete*
    per-side discovery (not just what exact-path matching left unpaired),
    is paired too — a release that only bumps a SONAME major would
    otherwise never reach
    :func:`~abicheck.service_compare_pipeline.run_compare` at all (the
    exact-path intersection is empty), silently losing every symbol/type
    change between the two versions (Codex review, fresh evidence). This
    canonical fallback only ever pairs an *unambiguous* match — a
    canonical name shared by more than one candidate on either side stays
    unpaired regardless of whether one of those candidates happens to
    already be exact-matched (an exact match consuming one member of an
    otherwise-ambiguous group must not make the *remaining* members look
    artificially unambiguous — a product shipping parallel majors
    old={.1,.2}/new={.2,.3} must not silently treat the unrelated .1/.3 as
    one evolving library just because .2/.2 happened to match exactly;
    Codex review, fresh evidence), the same ambiguity-safe-fallback
    discipline this codebase's other identity resolvers already follow
    (see ``finding_identity.py``'s own docstring). A library present on
    only one side even after this fallback never reaches the per-library
    pass, but bundle analysis still reports it via
    ``bundle_library_added``/``bundle_library_removed`` when a surviving
    sibling actually depends on it, plus a standalone
    ``bundle_library_removed`` finding this function adds itself for any
    other vanished library (see below) — a genuine SONAME bump also being
    a structural bundle-level event, not only a per-library ABI question.
    Unlike the CLI's ``compare-release`` engine, a failure here is never
    swallowed into a warning — this is a library call, so a per-library
    compare failure or a bundle-analysis failure propagates directly
    (whatever the failing step itself raises — :class:`SnapshotError`,
    ...); a caller wanting the CLI's own "report degradation, keep going"
    behavior should catch what it needs.

    Bundle-level cross-referencing (``bundle_intra_dep_signature_changed``,
    ``bundle_intra_type_changed``, ``bundle_provider_changed``) keys by
    each library's own bare filename, matching
    :func:`~abicheck.bundle.compare_bundle`'s own pre-existing internal
    convention (its ``diff_by_library`` index, and therefore the CLI's
    ``compare-release`` engine too) — *not* the relative-path identity
    :func:`_discover_library_map` uses for per-library pairing above. Using
    the relative-path identity for the bundle snapshot itself would (and,
    before this was fixed, silently did) break that cross-referencing for
    any library not sitting directly at the discovery root, and would
    treat a library that simply *moved* directories between releases
    (``lib/provider.so`` -> ``lib64/provider.so``) as an unrelated
    removal-plus-addition even though the canonical fallback above
    already pairs it correctly for its own per-library diff (Codex
    review, fresh evidence). Bare-filename keying is not itself a new
    limitation: two distinct libraries sharing an identical bare filename
    in different directories already collide inside
    :func:`~abicheck.bundle.compare_bundle`'s own ``diff_by_library``
    index regardless of what identity this function hands it, so this
    isn't a regression relative to the CLI's own bundle analysis — closing
    that fully needs a directory-qualified identity threaded through
    ``compare_bundle`` itself, out of scope here.
    """
    from .binary_utils import _canonical_library_key
    from .bundle import (
        BundleFinding,
        build_bundle_snapshot,
        compare_bundle,
        load_manifest,
    )
    from .checker_policy import ChangeKind
    from .service_compare_pipeline import run_compare

    old_root = Path(old_dir)
    new_root = Path(new_dir)
    if not old_root.is_dir():
        raise SnapshotError(f"product directory is not a directory: {old_root}")
    if not new_root.is_dir():
        raise SnapshotError(f"product directory is not a directory: {new_root}")
    old_roots_spec = old_header_roots if old_header_roots is not None else header_roots
    new_roots_spec = new_header_roots if new_header_roots is not None else header_roots

    def _validate_header_roots_spec(root: Path, spec: HeaderRootsSpec) -> None:
        """Validate every root declared in *spec* against *root*, independent
        of whether any library pair below actually references it.

        `_resolved_headers` below performs the identical containment check,
        but only for roots a *matched* pair asks for -- when discovery
        produces zero pairs that loop never runs, so an invalid root
        (absolute, escaping, bare-string typo) was silently accepted
        instead of raising `SnapshotError` (Codex review, fresh evidence).
        Also catches a mapping key naming a library that never ends up
        paired, which `_resolved_headers` can never reach either way.
        """
        if isinstance(spec, str):
            raise SnapshotError(
                "header roots must be a sequence of paths or a per-library "
                f"mapping, not a bare string: {spec!r}"
            )
        if isinstance(spec, Mapping):
            all_roots: list[tuple[str, str]] = []
            for library_key, value in spec.items():
                if isinstance(value, str):
                    raise SnapshotError(
                        "per-library header roots must be a sequence of "
                        f"paths, not a bare string, for library "
                        f"{library_key!r}: {value!r}"
                    )
                all_roots.extend((library_key, rel) for rel in value)
        else:
            all_roots = [(None, rel) for rel in spec]  # type: ignore[misc]
        for library_key, rel in all_roots:
            if _resolve_under_root(root, rel) is None:
                context = (
                    f" for library {library_key!r}" if library_key is not None else ""
                )
                raise SnapshotError(
                    f"header root {rel!r}{context} is absolute or escapes "
                    f"the product directory ({root}) -- header roots must "
                    "be relative paths contained within the product "
                    "directory."
                )

    _validate_header_roots_spec(old_root, old_roots_spec)
    _validate_header_roots_spec(new_root, new_roots_spec)

    old_map = _discover_library_map(old_root, include_private=include_private)
    new_map = _discover_library_map(new_root, include_private=include_private)

    def _resolved_headers(
        root: Path, spec: HeaderRootsSpec, library_key: str
    ) -> list[Path]:
        roots = _roots_for_library(spec, library_key)
        resolved = []
        for rel in roots:
            candidate = _resolve_under_root(root, rel)
            if candidate is None:
                # A structurally invalid root -- absolute, or escaping
                # root -- is a caller/config error, not a library that
                # legitimately ships no public headers: silently dropping
                # it (the pre-existing behavior) still ran the per-library
                # compare, just with that side's header evidence quietly
                # missing, risking a false-green result for an API/
                # header-only break the header evidence would have caught
                # (Codex review, fresh evidence). Reject outright, the
                # same containment discipline pack/unpack already enforce
                # for a manifest-declared path.
                raise SnapshotError(
                    f"header root {rel!r} for library {library_key!r} is "
                    "absolute or escapes the product directory "
                    f"({root}) -- header roots must be relative paths "
                    "contained within the product directory."
                )
            if candidate.is_dir():
                resolved.append(candidate)
            # A well-formed root that just doesn't exist is tolerated --
            # the library ships no public headers here.
        return resolved

    exact_matches = sorted(set(old_map) & set(new_map))
    # (old_key, old_path, new_key, new_path) -- both sides' discovered
    # identity is kept alongside the path so per-library header roots
    # resolve against the identity the caller observed, not a
    # shared/canonical one that may not exist for a fallback pair below.
    pairs: list[tuple[str, Path, str, Path]] = [
        (k, old_map[k], k, new_map[k]) for k in exact_matches
    ]

    # Canonical (SONAME-major-stripped) fallback for the libraries exact
    # matching left unpaired -- keyed by (relative directory, canonical
    # name), dropped entirely once a second contributor appears anywhere
    # in that *same directory's* per-side discovery. Directory-scoped
    # rather than a bare global canonical key: two independent libraries
    # sharing a basename in different directories (plugins/a/libfoo.so.1,
    # plugins/b/libfoo.so.1) that each bump their SONAME major used to
    # land in one shared, cross-directory bucket -- ambiguous on both
    # sides, so neither pair was ever compared even though each pairing
    # is unambiguous once directory is considered (Codex review, fresh
    # evidence). The discovery key is already a relative path.
    def _canonical_groups(
        discovered: dict[str, Path],
    ) -> dict[tuple[str, str], list[str]]:
        groups: dict[tuple[str, str], list[str]] = {}
        for key, path in discovered.items():
            rel_dir = key.rsplit("/", 1)[0] if "/" in key else ""
            groups.setdefault((rel_dir, _canonical_library_key(path)), []).append(key)
        return groups

    old_canonical = _canonical_groups(old_map)
    new_canonical = _canonical_groups(new_map)
    already_paired_old = {k for k, _, _, _ in pairs}
    already_paired_new = {k for _, _, k, _ in pairs}
    for canonical_key in sorted(set(old_canonical) & set(new_canonical)):
        old_candidates = old_canonical[canonical_key]
        new_candidates = new_canonical[canonical_key]
        if len(old_candidates) != 1 or len(new_candidates) != 1:
            continue
        old_key, new_key = old_candidates[0], new_candidates[0]
        if old_key in already_paired_old or new_key in already_paired_new:
            continue  # already covered by an exact match
        pairs.append((old_key, old_map[old_key], new_key, new_map[new_key]))

    per_library_results = []
    for old_key, old_path, new_key, new_path in pairs:
        old_headers = _resolved_headers(old_root, old_roots_spec, old_key)
        new_headers = _resolved_headers(new_root, new_roots_spec, new_key)
        result = run_compare(
            old_path,
            new_path,
            old_headers=old_headers,
            new_headers=new_headers,
            lang=lang,
            frontend=frontend,
            policy=policy,
        )
        # DiffResult.library is always the *old* side's bare filename,
        # which for a canonical-fallback pair (SONAME/dylib bump) isn't
        # the new side's own filename. compare_bundle()'s consumer/
        # provider detectors key off new.metadata's new-side bare
        # filename and exclude a provider from its own scan by that key
        # -- left as old.library, the provider's new binary would never
        # match and would falsely read as a consumer of its own change
        # (Codex review, fresh evidence). Stamp the new side's identity.
        result.diff.library = new_path.name
        per_library_results.append(result.diff)

    # Bundle-level identity is each library's own bare filename, not the
    # relative-path identity above -- see this function's own docstring
    # for why (matches compare_bundle()'s pre-existing diff_by_library
    # convention; a directory move no longer reads as remove+add). A
    # collision between two distinct libraries sharing an identical bare
    # filename in different directories is last-wins here, the same
    # pre-existing limitation compare_bundle()'s own diff_by_library
    # index already has regardless of this dict's construction.
    old_bundle_map = {p.name: p for p in old_map.values()}
    new_bundle_map = {p.name: p for p in new_map.values()}

    # A canonically-paired library (SONAME-major bump, dylib-version bump,
    # PE case-fold -- or a discovery-dedup representative mismatch, e.g. a
    # dev symlink `libfoo.so -> libfoo.so.1` present only on one side) can
    # have two different bare filenames even though `pairs` above already
    # determined it's one library. Left alone, old/new_bundle_map (keyed
    # by bare filename) would register that one library under two keys
    # and compare_bundle() would report a spurious removal+addition (or
    # BREAKING, if a sibling imports it) for a library that never changed
    # (Codex review, fresh evidence). Normalize the old side's key to the
    # new side's bare filename, matching the DiffResult.library
    # convention above -- but only when old_path survived
    # old_bundle_map's own last-wins collapse, and only when the
    # destination slot isn't already occupied by a *different* old-side
    # library, or the rekey would silently evict it (CodeRabbit review,
    # fresh evidence).
    for _old_key, old_path, _new_key, new_path in pairs:
        if old_path.name == new_path.name:
            continue
        occupant = old_bundle_map.get(new_path.name)
        if occupant is not None and occupant is not old_path:
            continue
        if old_bundle_map.get(old_path.name) is old_path:
            del old_bundle_map[old_path.name]
            old_bundle_map[new_path.name] = old_path

    old_snapshot = build_bundle_snapshot(old_bundle_map)
    new_snapshot = build_bundle_snapshot(new_bundle_map)

    manifest = None
    if manifest_path is not None:
        manifest = load_manifest(Path(manifest_path))

    bundle_result = compare_bundle(
        old_snapshot,
        new_snapshot,
        per_library_results,
        manifest=manifest,
        system_providers=system_providers,
        cohorts=cohorts,
        policy=policy,
    )

    # compare_bundle()'s own BUNDLE_LIBRARY_REMOVED detection deliberately
    # only fires when a surviving sibling actually imports the removed
    # library -- a standalone removal (no internal consumer) is by design
    # left to the CLI's separate --fail-on-removed-library flow, which
    # this library-only entry point has no equivalent of. Left as-is, a
    # release that drops its one public library (or any library nothing
    # else in the product imports) would silently return NO_CHANGE here
    # (Codex review, fresh evidence) -- a false-green whole-product
    # compatibility gate. Report it directly: any bare filename present in
    # the old bundle map and absent from the new one, that compare_bundle()
    # didn't already report.
    already_reported = {
        f.provider_library
        for f in bundle_result.bundle_findings
        if f.kind == ChangeKind.BUNDLE_LIBRARY_REMOVED and f.provider_library
    }
    already_reported_added = {
        f.provider_library
        for f in bundle_result.bundle_findings
        if f.kind == ChangeKind.BUNDLE_LIBRARY_ADDED and f.provider_library
    }
    # Unmatched-by-identity, not bare-filename set difference: every key in
    # old_map/new_map is either paired above (an exact relative-path match
    # or the canonical SONAME/dylib-version/case-insensitive fallback) or
    # genuinely has no counterpart on the other side -- unlike
    # old_bundle_map/new_bundle_map (bare-filename-keyed, last-wins on a
    # collision), old_map/new_map are relative-path-keyed and collision-
    # free, so this is exact even when two distinct libraries share a
    # basename. Computing "was this library removed/added" from the
    # collapsed bare-filename maps instead -- the original shape of this
    # fallback -- silently missed exactly that case: old containing both
    # plugins/a/plugin.so and plugins/b/plugin.so, new retaining only the
    # first, collapsed both sides to the identical single key "plugin.so",
    # so the set difference found nothing removed even though a whole
    # library plainly vanished (Codex review, fresh evidence).
    paired_old_keys = {old_key for old_key, _, _, _ in pairs}
    paired_new_keys = {new_key for _, _, new_key, _ in pairs}

    def _is_bundle_collapse_survivor(
        candidate_map: dict[str, Path], bundle_map: dict[str, Path], key: str
    ) -> bool:
        """True when *key*'s path is the one that survived
        old_bundle_map/new_bundle_map's own last-wins bare-filename
        collapse -- i.e. the one path compare_bundle() actually analyzed
        for this bare name. Excluding an unmatched key from the
        standalone-removal/addition fallback is only sound when this is
        true: `already_reported`/`already_reported_added` are bare-name
        sets, so when two unmatched libraries share a basename
        (plugins/a/plugin.so, plugins/b/plugin.so) and only ONE of them
        actually vanished/appeared, a bare-name membership check alone
        would suppress BOTH -- including the one compare_bundle() never
        analyzed at all (the collapse discarded it before compare_bundle()
        ever saw it), silently losing a real, distinct removal/addition
        (Codex review, fresh evidence).
        """
        path = candidate_map[key]
        return bundle_map.get(path.name) == path

    # One entry per unmatched *key* (relative path), not deduped by bare
    # name -- two distinct unmatched libraries sharing a basename in
    # different directories (plugins/a/plugin.so and plugins/b/plugin.so)
    # must each be reported when both vanish, not collapsed into a single
    # finding the way a `set` of names alone would (Codex review, fresh
    # evidence: pairing already correctly distinguishes the two by
    # relative path, but this reporting step was still projecting down to
    # bare names before iterating, silently losing the second removal).
    unmatched_old_items = sorted(
        (k, old_map[k].name)
        for k in old_map
        if k not in paired_old_keys
        and not (
            old_map[k].name in already_reported
            and _is_bundle_collapse_survivor(old_map, old_bundle_map, k)
        )
    )
    unmatched_new_items = sorted(
        (k, new_map[k].name)
        for k in new_map
        if k not in paired_new_keys
        and not (
            new_map[k].name in already_reported_added
            and _is_bundle_collapse_survivor(new_map, new_bundle_map, k)
        )
    )
    # A library matched to a surviving library on the new side -- whether by
    # an exact relative-path match or the canonical (SONAME-major-stripped,
    # case-insensitive) fallback above -- is not a standalone removal even
    # though its own bare filename may be absent from new_bundle_map: a
    # SONAME major bump (`libfoo.so.1` -> `libfoo.so.2`) and a case-only DLL
    # rename (`Foo.dll` -> `foo.dll`) both pair via the canonical fallback
    # while their bare filenames genuinely differ (Codex review, fresh
    # evidence, the DLL case; the SONAME-major case is the identical
    # mechanism generalized) -- already excluded above, since a paired key
    # is never in `unmatched_old_items`/`unmatched_new_items` to begin with.
    for key, name in unmatched_old_items:
        bundle_result.bundle_findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_LIBRARY_REMOVED,
                symbol=name,
                description=(
                    f"Library {name} ({key}) removed from the product; no "
                    "surviving sibling imports it, but a whole-product "
                    "comparison must still report it."
                ),
                provider_library=name,
            )
        )

    # Symmetric gap to the removal one above, for the identical reason:
    # compare_bundle()'s own BUNDLE_LIBRARY_ADDED detection reads new
    # library names straight off BundleSnapshot.libraries, which
    # build_bundle_snapshot() only ever populates from *ELF* metadata
    # (non-ELF inputs are skipped with a warning -- the bundle layer is
    # Linux/ELF-only by design). A new DLL/dylib added to the product
    # therefore never reaches that detection at all, regardless of any
    # sibling-consumer gating -- an empty old directory versus a new
    # directory containing foo.dll still returned NO_CHANGE with no
    # findings (Codex review, fresh evidence).
    for key, name in unmatched_new_items:
        bundle_result.bundle_findings.append(
            BundleFinding(
                kind=ChangeKind.BUNDLE_LIBRARY_ADDED,
                symbol=name,
                description=f"New library {name} ({key}) appears in the product.",
                provider_library=name,
            )
        )
    return bundle_result

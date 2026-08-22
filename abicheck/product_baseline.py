# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

"""Product baseline archive — one file for a whole multi-library product.

Per-library ``dump`` snapshots (``.json.zst``) do not scale to a product shipping
several interdependent shared libraries: one release asset per library, and --
the part that actually matters -- ``scan --against <snapshot>`` compares exactly
one library against exactly one snapshot, so cross-DSO ABI breakage (a symbol one
library imports from a sibling library disappearing) is structurally invisible to
it. :mod:`abicheck.bundle` (ADR-023) is built for precisely this -- but it needs
*binaries* on both sides (it reads ``DT_NEEDED``/``.gnu.version_r``/``.gnu.version_d``
straight from the ELF files), so a bundle-aware comparison against a stored
baseline needs the old side's binaries on disk, not a header/DWARF-derived snapshot.

This module is the storage format for that: :func:`pack_product_baseline` archives
an entire product directory (every shared library plus whatever else ships
alongside it -- debug info, headers) into one deterministic ``.tar.zst`` file,
with a small JSON manifest (:class:`ProductBaselineManifest`) recording which
archived files are libraries and which relative directories hold the product's
public headers. :func:`unpack_product_baseline` reverses it, reproducing a
directory ``abicheck compare``'s directory-mode operand (bundle analysis,
ADR-023) can run against directly -- covering every library and every
cross-library edge in one invocation instead of one per library. Library-only
surface, no CLI command. :func:`compare_product_directories` is the other half:
given the two directories a caller just unpacked, it runs the actual bundle-aware
comparison (per-library diff via :mod:`abicheck.service_compare_pipeline`,
cross-library findings via :mod:`abicheck.bundle`) -- the plain-Python
counterpart of directory-mode ``compare <old_dir> <new_dir>``, requiring no CLI
subprocess.
"""

from __future__ import annotations

import errno
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
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import IO, TYPE_CHECKING, Any

from .binary_utils import _pe_is_dll_content, resolve_linker_script
from .errors import SnapshotError
from .package import TarExtractor, _is_elf_shared_object

if TYPE_CHECKING:
    from .bundle import BundleDiffResult
from .snapshot_io import ZSTD_LEVEL_BASELINE

#: Name the manifest is stored under -- not a plausible library/header name,
#: so it never collides with real product content.
MANIFEST_MEMBER_NAME = "abicheck-product-baseline.json"

#: Manifest schema discriminator. Purely self-description today; checked by
#: :func:`unpack_product_baseline` so a *future* MAJOR bump has a rejection point.
PRODUCT_BASELINE_SCHEMA = "abicheck.product-baseline/v1"

#: A genuine SHA-256 hex digest, rejected if missing/malformed on unpack.
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")

#: json.loads() raises RecursionError, not JSONDecodeError, on deep nesting.
_MANIFEST_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError)

#: Bounds the manifest member specifically -- independent of, and much
#: smaller than, TarExtractor's decompressed-tar-wide ceiling (8 GiB), which
#: does nothing to stop one compressible manifest entry from allocating
#: several times its own size across read_text()/json.loads() (Codex).
DEFAULT_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_MANIFEST_BYTES_ENV = "_ABICHECK_PRODUCT_BASELINE_MAX_MANIFEST_BYTES"


def _max_manifest_bytes() -> int:
    override = os.environ.get(_MAX_MANIFEST_BYTES_ENV)
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    return DEFAULT_MAX_MANIFEST_BYTES


#: File suffixes recognized as a shared library on pack -- decides what's
#: itemized in :attr:`ProductBaselineManifest.libraries`, not what's archived.
_LIBRARY_SUFFIXES = (".so", ".dylib", ".dll")

#: A versioned SONAME, e.g. "libfoo.so.1.2.3" -- no _LIBRARY_SUFFIXES suffix
#: matches it. Segments after ".so" must be numeric, so "libfoo.so.1.debug" is excluded.
_VERSIONED_SONAME = re.compile(r"\.so(\.\d+)+$", re.IGNORECASE)


def _is_shared_library(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(_LIBRARY_SUFFIXES):
        return True
    return _VERSIONED_SONAME.search(name) is not None


#: Mach-O ``filetype`` values that are a dynamically-loadable image, not a
#: plain executable. MH_EXECUTE (2) is deliberately excluded.
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
        # identical catch in macho_metadata.py's SymbolTable parse).
        return False
    return any(
        int(getattr(header.header, "filetype", -1)) in _MACHO_LIBRARY_FILETYPES
        for header in macho.headers
    )


def _is_library_path(path: Path) -> bool:
    """A shared library, by filename suffix or real content (ELF, Mach-O,
    or PE/COFF).

    Shared by every place that decides "is this a library" -- packing's own
    manifest-entry classification (:func:`_add_member`) and
    :func:`_iter_discovered_libraries`'s walk (Codex review, fresh
    evidence: an extensionless ELF DSO was previously archived but
    produced no ``LibraryEntry``).

    The ELF content fallback had no Mach-O/PE counterpart, closed by
    :func:`_macho_is_library_content`/:func:`_pe_is_dll_content`.

    A ``.debug`` split-debug sidecar is excluded outright, ahead of
    every content check: it retains its original binary's ``ET_DYN``
    header, so the ELF fallback would otherwise discover it as a second
    "library" (Codex review, fresh evidence).
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
    # A GNU ld INPUT()/GROUP() linker script is library-suffix-named but carries
    # no binary content -- centralized so packing/comparison share one exclusion
    # (see resolve_linker_script's own docstring for the binary-content guard).
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
    #: ``compare -H`` invocation should pass — recorded here so a single
    #: archive carries both sides of the multilib comparison's own header
    #: contract, rather than requiring the caller to remember it separately.
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


# ── Local helpers ──────────────────────────────────────────────────────── Deliberately not importing snapshot_io._zstd_module/_atomic_write_bytes across the module boundary -- both are leading-underscore internals, and this codebase's convention (see baseline_publish.py's ``_resolve_under_root``) is to duplicate a small helper locally. ZSTD_LEVEL_BASELINE is imported.


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
    except (RuntimeError, ValueError):
        # RuntimeError: a symlink loop. ValueError: an embedded NUL byte
        # in *rel* raises the same way (Codex). Callers expect None either way.
        return None
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        return None
    return root / rel


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _case_insensitive_target_resolves(parent: Path, target: str) -> bool:
    """True if dangling *target*, walked component-by-component from
    *parent*, resolves case-insensitively -- every component, not just the
    basename (an intermediate one can be the mismatched one) (Codex)."""
    current = parent
    used_fold = False
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        exact = current / part
        # Lexical existence (Codex): a component present under its own exact
        # spelling -- even a symlink whose own chain is broken -- is an ordinary
        # dangling target, not a case-fold hazard; `.exists()` follows the chain.
        if os.path.lexists(exact):
            current = exact
            continue
        if not current.is_dir():
            return False
        try:
            entries = list(current.iterdir())
        except OSError:
            return False
        folded_part = unicodedata.normalize("NFC", part).casefold()
        match = next(
            (
                e
                for e in entries
                if unicodedata.normalize("NFC", e.name).casefold() == folded_part
            ),
            None,
        )
        if match is None:
            return False
        used_fold, current = True, match
    return used_fold


def _umask_derived_mode(parent: Path, base_mode: int, *, directory: bool) -> int:
    """Return base_mode & ~umask -- learned from a real, immediately removed
    probe entry under *parent*, not from `os.umask(new)` (POSIX's only getter,
    which briefly zeroes the process-wide umask and races with any concurrent
    file creation in an unrelated thread) and not cached (a process-lifetime
    cache goes stale across a later `os.umask()` call elsewhere) -- two
    independent Codex findings on the same prior fix."""
    probe = parent / f".abicheck-umask-probe-{uuid.uuid4().hex}"
    try:
        if directory:
            os.mkdir(probe, base_mode)
        else:
            os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, base_mode))
        return stat_module.S_IMODE(probe.stat().st_mode)
    finally:
        try:
            probe.rmdir() if directory else probe.unlink()
        except OSError:
            pass


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


def _output_parent_chain(base: Path, leaf_parent: Path) -> set[Path]:
    """Every ancestor of *leaf_parent*, up to and excluding *base*,
    regardless of whether it exists -- the archive-content-exclusion
    counterpart of :func:`_scaffold_dirs_for_mkdir` above, which reports
    only what doesn't exist *yet*, wrong for this purpose: a rerun sees
    OUTPUT's parent as already-existing (the first run's own mkdir made
    it), so an existence-gated set excludes it on run one and silently
    includes it on run two (Codex review, fresh evidence).
    """
    chain: set[Path] = set()
    if leaf_parent != base and not leaf_parent.is_relative_to(base):
        return chain
    node = leaf_parent
    while node != base:
        chain.add(node)
        node = node.parent
    return chain


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


def _windows_relative_target_escapes(arcname: str, target: str) -> bool:
    """Would *target* (a relative symlink target for the archive member at
    *arcname*) escape the archive root if interpreted with Windows path
    semantics (backslash separators)?

    Purely lexical -- walks a virtual depth counter from *arcname*'s
    directory, decrementing on ``..``, incrementing on a named
    component; negative depth means the target walks above root. Only
    *adds* rejections a POSIX-only check misses: a backslash ``..``
    traversal is one opaque filename on POSIX (Codex review, fresh
    evidence).
    """
    depth = len(PureWindowsPath(arcname).parent.parts)
    for part in PureWindowsPath(target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        else:
            depth += 1
    return False


#: Windows reserved device names -- matched against a component's *base*
#: name (before the first ``.``), case-insensitively: ``aux.txt`` is
#: reserved exactly as ``AUX`` is (Codex review, fresh evidence).
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
    # Superscript 1-3 (U+00B9/B2/B3): legacy code-page COM/LPT renderings
    # Windows still treats as equivalent to the plain-ASCII names (Codex).
    | {f"COM{d}" for d in "¹²³"}
    | {f"LPT{d}" for d in "¹²³"}
)

#: Characters Windows never allows in a path component (beyond the
#: separators and drive/root markers already checked separately below).
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*') | {chr(c) for c in range(32)}


def _validate_portable_arcname(arcname: str) -> None:
    """Reject an archive member name that isn't safely portable across
    platforms.

    POSIX imposes no restriction on filename characters, so a real
    on-disk file literally named ``C:library.dll`` or
    ``dir\\..\\outside.so`` (one POSIX component, embedded backslashes)
    packs unchanged, then reads as drive-anchored or a real ``..``
    traversal under Windows path semantics -- the same gap this module's
    symlink-target validation already closes, reached via the member's
    own name (Codex review, fresh evidence).

    A component that decomposes safely under Windows separators can
    still be a name Windows can't represent -- a reserved device name, a
    forbidden character, or a trailing dot/space it silently strips
    (Codex review, fresh evidence).
    """
    # A backslash in one real POSIX component is always reinterpreted as a separator on Windows, even when the decomposition is neither anchored nor traversing (`foo\bar.so` restores as `foo/bar.so`) -- checked unconditionally, ahead of every other check (Codex).
    if "\\" in arcname:
        raise SnapshotError(
            f"{arcname!r} contains a backslash, which Windows always "
            "treats as a path separator -- refusing to pack it, since "
            "the archive could not be safely unpacked portably"
        )
    wpath = PureWindowsPath(arcname)
    if wpath.drive or wpath.root:
        raise SnapshotError(
            f"{arcname!r} is anchored under Windows path semantics "
            "(a drive letter or rooted path) -- refusing to pack it, "
            "since the archive could not be safely unpacked portably"
        )
    if ".." in wpath.parts:
        # Unconditional, not "does it escape the root": TarExtractor
        # rejects any ".." component outright regardless of depth.
        raise SnapshotError(
            f"{arcname!r} contains a '..' component when interpreted "
            "with Windows path separators -- refusing to pack it for "
            "portability"
        )
    for part in wpath.parts:
        base = part.split(".", 1)[0].upper()
        if base in _WINDOWS_RESERVED_NAMES:
            raise SnapshotError(
                f"{arcname!r} contains {part!r}, a reserved Windows "
                "device name -- refusing to pack it for portability"
            )
        if _WINDOWS_FORBIDDEN_CHARS.intersection(part):
            raise SnapshotError(
                f"{arcname!r} contains {part!r}, which has a character "
                "Windows forbids in a path component -- refusing to "
                "pack it for portability"
            )
        if part != part.rstrip(". "):
            raise SnapshotError(
                f"{arcname!r} contains {part!r}, which Windows would "
                "silently rename by stripping its trailing dot/space -- "
                "refusing to pack it for portability"
            )


def _add_member(
    tf: tarfile.TarFile, path: Path, arcname: str, source_dir: Path
) -> LibraryEntry | None:
    _validate_portable_arcname(arcname)
    info = tf.gettarinfo(str(path), arcname=arcname)
    # Deterministic metadata: two packs of byte-identical content produce a
    # byte-identical archive, regardless of who built it or when.
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    # Mode too: an umask difference between two builders would otherwise produce
    # two archives from byte-identical content -- keep only the executable bit.
    # Checked across all three permission classes (0o111), not just owner
    # (0o100): a source file executable only for group/other (e.g. 0o055)
    # would otherwise be silently normalized to non-executable (Codex).
    if not info.issym():
        info.mode = 0o755 if info.mode & 0o111 else 0o644

    if info.issym():
        target = info.linkname
        # An absolute (or Windows-anchored) target can't round-trip -- TarExtractor refuses it at unpack. os.path.isabs() alone doesn't recognize a Windows drive-absolute/UNC target on POSIX; PureWindowsPath.is_absolute() isn't enough either -- False for drive="", root="\\" or drive="C:", root="" -- so any nonempty drive or root is rejected too (Codex).
        wpath = PureWindowsPath(target)
        if os.path.isabs(target) or wpath.drive or wpath.root:
            raise SnapshotError(
                f"symlink {arcname!r} has an absolute target {target!r} -- "
                "only relative symlink targets can be packed portably"
            )
        # Same reasoning as _validate_portable_arcname's own check: a backslash
        # always reinterprets as a Windows separator, even when it doesn't
        # escape root (Codex).
        if "\\" in target:
            raise SnapshotError(
                f"symlink {arcname!r} targets {target!r}, which contains "
                "a backslash Windows always treats as a path separator "
                "-- refusing to pack it for portability"
            )
        # A self-referential symlink loop is detected via a raw stat() -- not Path.resolve() raising, which Python 3.13's realpath()-backed rewrite stopped doing in non-strict mode (CI caught this). stat() still raises OSError (ELOOP); a dangling target raises ENOENT instead, left alone.
        try:
            target_stat = path.stat()
        except OSError as exc:
            target_stat = None
            if exc.errno == errno.ELOOP:
                raise SnapshotError(
                    f"symlink {arcname!r} targets {target!r}, which forms "
                    "a symlink loop — refusing to pack it"
                ) from exc
        link_abs = path.parent / target
        try:
            link_real = link_abs.resolve()
            link_real.relative_to(source_dir.resolve())
        except ValueError as exc:
            raise SnapshotError(
                f"symlink {arcname!r} targets {target!r}, which escapes "
                f"{source_dir} — refusing to pack it"
            ) from exc
        # No `except RuntimeError` here: the stat() check above already follows
        # the whole chain from `path` (same location `link_abs` computes), so
        # any loop is already caught there.
        if _windows_relative_target_escapes(arcname, target):
            raise SnapshotError(
                f"symlink {arcname!r} targets {target!r}, which would "
                f"escape {source_dir} when interpreted with Windows path "
                "separators -- refusing to pack it for portability"
            )
        # A dangling target here (case-sensitive host) can still resolve on a case-insensitive one via a case/Unicode-fold match somewhere along its path: no LibraryEntry recorded here, but live on Windows/macOS, where unpack's own discovery walk finds it as undeclared and rejects an honest archive (Codex). Same fold reasoning as the member-collision check above -- reject rather than resolve two ways.
        if target_stat is None and _case_insensitive_target_resolves(
            path.parent, target
        ):
            raise SnapshotError(
                f"symlink {arcname!r} targets {target!r}, which does not "
                "exist here but matches a sibling once case/Unicode-"
                "folded -- it would resolve differently on a case-"
                "insensitive filesystem; refusing to pack it for portability"
            )
        # A library-shaped symlink whose target isn't independently library- shaped got no LibraryEntry, rejected as tampered. `liba.so -> liba.so.1.2.3` is left alone. S_ISREG guards a directory-targeting symlink (open() would raise IsADirectoryError).
        entry = None
        if (
            target_stat is not None
            and stat_module.S_ISREG(target_stat.st_mode)
            and _is_library_path(path)
            and not _is_library_path(link_real)
        ):
            hasher = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
            entry = LibraryEntry(
                name=path.name,
                path=arcname,
                sha256=hasher.hexdigest(),
                size=target_stat.st_size,
            )
        tf.addfile(info)
        return entry

    if info.islnk():
        # gettarinfo() converts a second path sharing an inode into a hardlink reference -- falling through to "not info.isreg()" below would silently drop it from the archive. Its own info.size is 0, so it can't supply a LibraryEntry's size the way the first-archived copy's does -- hash/stat the real file directly instead (Codex).
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
    _validate_portable_arcname(arcname)
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
    to *source_dir* — recorded in the manifest so a later ``compare -H``
    invocation doesn't have to rediscover them. Each must resolve under
    *source_dir* and exist; raises :class:`SnapshotError` otherwise.

    Writes atomically: *output* either ends up as a complete, valid archive
    or is left untouched — a failure partway through never leaves a
    truncated file at the destination path.
    """
    # Normalized to an absolute path up front: _scaffold_dirs_for_mkdir()'s own containment check is a plain lexical is_relative_to() -- mixed spellings would make it spuriously fail (CodeRabbit review, fresh evidence). os.path.abspath(), not .resolve() -- OUTPUT's own final component must stay lexical, matching the symlinked-OUTPUT exclusion logic below.
    source_path = Path(os.path.abspath(str(source_dir)))
    if not source_path.is_dir():
        raise SnapshotError(
            f"product baseline source is not a directory: {source_path}"
        )
    output_path = Path(os.path.abspath(str(output)))
    if not output_path.name.lower().endswith(".tar.zst"):
        # Not merely a naming preference: unpack_product_baseline() decides
        # whether to run zstd decompression purely from this suffix.
        raise SnapshotError(
            f"product baseline output must end with '.tar.zst': {output_path}"
        )
    # Validated before the output-parent scaffold below can fabricate anything
    # -- else a header root naming the freshly-created chain would pass on a
    # directory mkdir() just manufactured (Codex review, fresh evidence).
    if isinstance(header_roots, str):
        # A bare str satisfies Sequence[str], so header_roots="include" would
        # iterate character-by-character (Codex review, fresh evidence).
        raise SnapshotError(
            "header_roots must be a sequence of paths, not a bare string: "
            f"{header_roots!r}"
        )
    resolved_header_roots: list[str] = []
    for rel in header_roots:
        resolved = _resolve_under_root(source_path, rel)
        # Must be a directory: compare_product_directories() only includes a
        # header root when `.is_dir()` is true, so one recorded here as a file
        # would round-trip but never reach a comparison.
        if resolved is None or not resolved.is_dir():
            raise SnapshotError(
                f"header root {rel!r} is absolute, escapes {source_path}, "
                "or is not an existing directory"
            )
        resolved_header_roots.append(Path(rel).as_posix())

    # Created *before* discovery: doing so only after would make a first pack and a second (already-created dir) disagree on file_count and archive bytes; also captured before the mkdir call below, for the same "nothing to pack" reasoning. Two aliasing concerns, resolved once: OUTPUT itself may be a symlink (its own lexical name, not target), and SOURCE_DIR itself may be a symlink alias with OUTPUT spelled through the real directory. Resolving *only* OUTPUT's parent (and SOURCE_DIR in full) fixes both, leaf stays lexical.
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

    # _scaffold_dirs_for_mkdir()/_discover_empty_dirs() need a `leaf_parent`/root spelled *lexically* relative to `source_path` (matching `paths`/`empty_dirs` themselves), not the resolved `output_parent_real` -- translated back into source_path's own spelling instead.
    if output_rel is not None:
        output_rel_parent = os.path.dirname(output_rel)
        leaf_parent_lexical = (
            source_path / output_rel_parent if output_rel_parent else source_path
        )
    else:
        leaf_parent_lexical = output_path.parent

    # Same reasoning as above, applied to the actual mkdir: capture the scaffold
    # set *before* creating it, so an originally-empty SOURCE_DIR whose only
    # content is this output-only chain still rejects as "nothing to pack" below.
    output_scaffold_dirs = _scaffold_dirs_for_mkdir(source_path, leaf_parent_lexical)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    paths = _discover_paths(source_path)
    # Exclude OUTPUT itself when it lives under SOURCE_DIR — otherwise a rerun of `pack SOURCE_DIR SOURCE_DIR/baseline.tar.zst` would embed the *previous* archive as an input member, growing on every invocation and violating the determinism this format promises. Computed from the two paths, not existence, so this excludes the output slot even on a first-ever pack.
    if output_rel is not None:
        paths = [p for p in paths if _relative_posix(p, source_path) != output_rel]
    empty_dirs = _discover_empty_dirs(source_path, paths)

    # The scaffold directories the mkdir() call above just created must not survive *any* failure path below that leaves them empty -- otherwise an identical repeat call sees them as already-existing, pre-existing content (a second review round found one rejection path further down, not just the empty-source one, needed the identical cleanup). One shared helper so every failure path cleans up the same way: deepest-first, best-effort.
    def _cleanup_output_scaffold_dirs() -> None:
        for scaffold_dir in sorted(
            output_scaffold_dirs, key=lambda d: len(d.parts), reverse=True
        ):
            try:
                scaffold_dir.rmdir()
            except OSError:
                pass

    # A scaffold directory created solely to hold OUTPUT doesn't count as
    # real content for this check (see the mkdir call above).
    real_empty_dirs = [d for d in empty_dirs if d not in output_scaffold_dirs]
    if not paths and not real_empty_dirs:
        _cleanup_output_scaffold_dirs()
        raise SnapshotError(
            f"no files found under {source_path} to pack into a product baseline"
        )
    # A separate, existence-independent exclusion for what actually gets archived below -- see _output_parent_chain()'s own docstring. A declared header root is excluded from this chain: real product content the manifest promises, not scaffolding (Codex). Built in the same lexical space empty_dirs/output_parent_chain use, not the `.resolve()`d dirs above.
    output_parent_chain = _output_parent_chain(source_path, leaf_parent_lexical)
    declared_header_root_dirs = {source_path / rel for rel in resolved_header_roots}
    archived_empty_dirs = [
        d
        for d in empty_dirs
        if d not in output_parent_chain or d in declared_header_root_dirs
    ]
    # Known gap: an *undeclared*, pre-existing empty dir holding OUTPUT is still excluded like pure scaffolding -- structurally indistinguishable by content, and existence can't separate them (OUTPUT's own dir survives after a first pack, so a rerun would misclassify scaffold as real content). Needs a declaration mechanism beyond header_roots.

    # MANIFEST_MEMBER_NAME is reserved: a real source entry there would collide with the generated manifest member, added last, silently winning on extraction. Must reject a *directory* at the reserved path too -- an empty one is never in `paths`, and a non-empty one's own directory entry never is either, only its children -- either shape let packing write a tar requiring the same path to be both a directory and a regular file.
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
        # Same prefix test as the `paths` scan above: a directory at the reserved
        # path holding only empty subdirs is absent from both `paths` and
        # `empty_dirs` -- only its nested empty leaf is.
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

    # A case-sensitive host can archive two names that fold to the same identity on Windows -- checked per path component (`Lib/a.so` vs `lib/b.so` unify), NFC-normalized first: NFC vs NFD "café.so" casefold to different keys on their own, though APFS/HFS+ treat them as one filename (Codex).
    all_arcnames = [_relative_posix(p, source_path) for p in paths]
    all_arcnames.extend(_relative_posix(d, source_path) for d in archived_empty_dirs)
    all_arcnames.append(MANIFEST_MEMBER_NAME)
    case_folded: dict[str, str] = {}
    for arcname in all_arcnames:
        parts = arcname.split("/")
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            folded = unicodedata.normalize("NFC", prefix).casefold()
            prior = case_folded.get(folded)
            if prior is not None and prior != prefix:
                _cleanup_output_scaffold_dirs()
                raise SnapshotError(
                    f"{prefix!r} collides with {prior!r} once case-folded "
                    "-- the archive could not be safely unpacked on a "
                    "case-insensitive filesystem (e.g. Windows); rename "
                    "one of them before packing"
                )
            case_folded[folded] = prefix

    # Resolve the compressor before mkstemp() hands out an open descriptor: an invalid zstd_level must fail before there is a descriptor to leak. mkstemp() always creates mode 0600, and os.replace() carries that across -- left alone, a packed archive would be unreadable by anyone but its creator even under an ordinary 0022 umask (Codex). Preserve an existing destination's mode when overwriting, else fall back to the ordinary umask-derived perms.
    if output_path.exists():
        target_mode = stat_module.S_IMODE(output_path.stat().st_mode)
    else:
        target_mode = _umask_derived_mode(output_path.parent, 0o666, directory=False)
    try:
        zstandard = _zstd_module()
        cctx = zstandard.ZstdCompressor(level=zstd_level, write_checksum=True)
    except BaseException:
        # Nothing has been written yet (no temp file exists at this point) --
        # the same best-effort scaffold cleanup as every other failure path
        # after the mkdir() call above.
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
        # nothing written yet, so the same best-effort scaffold cleanup as
        # every other failure path after the mkdir() call above (Codex).
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
                    # archived_empty_dirs, not empty_dirs: an output-only scaffold dir (see the mkdir call above) must not be archived either, or an unpacked baseline gains a directory never part of the original product (Codex review, fresh evidence).
                    for empty_dir in archived_empty_dirs:
                        arcname = _relative_posix(empty_dir, source_path)
                        _add_directory_member(tf, empty_dir, arcname)
                    manifest = ProductBaselineManifest(
                        product=product,
                        libraries=tuple(sorted(libraries, key=lambda e: e.path)),
                        header_roots=tuple(resolved_header_roots),
                        file_count=len(paths) + len(archived_empty_dirs) + 1,
                    )
                    _add_manifest_member(tf, manifest)
        os.replace(tmp_path, output_path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass
        # The temp file above is unlinked first, so a scaffold dir that
        # held nothing but it is empty again -- safe for the same
        # best-effort cleanup every other failure path already does.
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
    security-validated ``.tar.zst`` extraction rather than a second,
    independently written unpacker.

    Extracts into a staging directory first and only publishes it to
    *dest_dir* once the manifest is confirmed present, parseable, and at
    a supported schema version — a missing/corrupt/unsupported archive
    raises :class:`SnapshotError` with *dest_dir* left exactly as it was.
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
        # Rejected outright: publishing later replaces dest_path via dest_path.rmdir() + os.replace(), and rmdir() operates on the symlink itself (never follows a final symlink component) -- even a symlink to a genuinely empty dir would pass the checks below then raise NotADirectoryError at publish, unhandled by the CLI's SnapshotError- only catch, leaving staging uncleaned (Codex).
        raise SnapshotError(f"unpack destination must not be a symlink: {dest_path}")
    # Captured before publication, not derived from the umask: a caller may have pre-created dest_path with deliberate permissions, and publication replaces it outright -- re-deriving from the umask would silently widen or narrow those permissions instead (Codex).
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
            # TarExtractor.extract() can raise ExtractionSecurityError (a sibling AbicheckError, not a SnapshotError -- a malicious archive triggering the symlink-escape/path-traversal/device checks), tarfile.TarError (malformed tar), or a zstandard decompression error (corrupt/truncated .tar.zst) -- none of which the CLI's `except SnapshotError` catches, so any of them would otherwise surface as an unhandled traceback instead of the documented exit-64 usage error.
            raise SnapshotError(
                f"{archive_path}: failed to extract product baseline archive: {exc}"
            ) from exc

        manifest_path = staging / MANIFEST_MEMBER_NAME
        if not manifest_path.is_file():
            raise SnapshotError(
                f"{archive_path} does not look like an abicheck product baseline "
                f"archive (missing {MANIFEST_MEMBER_NAME})"
            )
        manifest_limit = _max_manifest_bytes()
        manifest_size = manifest_path.stat().st_size
        if manifest_size > manifest_limit:
            raise SnapshotError(
                f"{archive_path}: product baseline manifest is {manifest_size} "
                f"bytes, exceeding the {manifest_limit}-byte safety limit -- "
                "archive may be corrupt or malicious"
            )
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except _MANIFEST_ERRORS as exc:
            raise SnapshotError(
                f"{archive_path}: corrupt product baseline manifest"
            ) from exc
        if not isinstance(raw, dict):
            raise SnapshotError(f"{archive_path}: corrupt product baseline manifest")
        # from_dict() defensively *defaults* a missing "schema" key --
        # applied to `schema` it defeats the check: any mapping without
        # one (e.g. `{}`) would pass _check_schema_supported() (Codex).
        if not isinstance(raw.get("schema"), str) or not raw["schema"]:
            raise SnapshotError(
                f"{archive_path}: product baseline manifest is missing its "
                "schema discriminator -- not a genuine abicheck product "
                "baseline archive"
            )

        manifest = ProductBaselineManifest.from_dict(raw)
        _check_schema_supported(manifest.schema, archive_path)

        # The manifest is untrusted -- header_roots is re-joined against the unpack destination and handed to a header-parsing tool, so an adversarial "../../etc" must be rejected the same way pack refuses to record one; from_dict()'s defensive str() coercion accepts any string unchanged, so this check happens here.
        for rel in manifest.header_roots:
            resolved = _resolve_under_root(staging, rel)
            if resolved is None or not resolved.is_dir():
                raise SnapshotError(
                    f"{archive_path}: manifest declares an invalid header "
                    f"root {rel!r} (absolute, escapes the product root, "
                    "or is not an existing directory)"
                )
        # LibraryEntry.path carries the identical untrusted-manifest risk as header_roots above -- from_dict()'s defensive str() coercion accepts any string unchanged, so an archive declaring "../../outside.so" would otherwise resolve outside the unpacked baseline. Cached by resolved (dev, ino) identity, not path: an untrusted archive can declare many entries hardlinked/aliased to one physical payload, and hashing each entry's full content independently turns N declared aliases into N full reads of the same bytes -- a cumulative-hashing CPU cost an attacker controls directly via manifest size (Codex).
        library_hash_cache: dict[tuple[int, int], str] = {}
        for lib in manifest.libraries:
            lib_resolved = _resolve_under_root(staging, lib.path)
            if lib_resolved is None or not lib_resolved.is_file():
                raise SnapshotError(
                    f"{archive_path}: manifest declares an invalid library "
                    f"path {lib.path!r} (absolute, escapes the product "
                    "root, or is not an existing file)"
                )
            # The checks above only confirm the declared path *exists* -- an archive can declare an ordinary file (README.txt) as a LibraryEntry, with a correct size/digest, and this validation would accept it (Codex review, fresh evidence). Validate with the same predicate packing/discovery use -- against the un-fully-resolved path (a symlink left as itself), since packing can now declare a library-shaped symlink whose real target is ordinary data; checking lib_resolved would re-derive the target's own identity and reject the entry packing just legitimately created.
            lib_symlink_aware = staging / lib.path
            if not _is_library_path(lib_symlink_aware):
                raise SnapshotError(
                    f"{archive_path}: manifest declares {lib.path!r} as a "
                    "library, but it is not library-shaped"
                )
            # This only confirmed the path *exists*, not that its bytes match the manifest -- mirrors pack's own hashing. Size and sha256 are both compared unconditionally (neither skippable for a 0/"" value, unlike this codebase's usual "don't abort on missing" convention -- an earlier revision's truthiness guards let a zeroed field bypass this check) (Codex).
            lib_stat = lib_resolved.stat()
            actual_size = lib_stat.st_size
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
            content_identity = (lib_stat.st_dev, lib_stat.st_ino)
            digest = library_hash_cache.get(content_identity)
            if digest is None:
                hasher = hashlib.sha256()
                with open(lib_resolved, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                library_hash_cache[content_identity] = digest
            if digest != lib.sha256:
                raise SnapshotError(
                    f"{archive_path}: library {lib.path!r} checksum "
                    "mismatch -- archive may be corrupt or tampered "
                    "with"
                )
        # The verification loop above only examines declared entries -- cross-checked against what discovery actually extracted. A symlink alias compares by identity (dev, ino); a hardlink alias by path string, since it gets its own declared entry (Codex).
        declared_identities = set()
        declared_paths = set()
        for lib in manifest.libraries:
            lib_resolved = _resolve_under_root(staging, lib.path)
            assert lib_resolved is not None  # already validated above
            st = lib_resolved.stat()
            declared_identities.add((st.st_dev, st.st_ino))
            declared_paths.add(lib.path)
        # Every raw discovered path, not _discover_library_map()'s own deduplicated map: an undeclared hardlink alias sorting after a declared library's symlink alias (same identity) is invisible to that map's dedup, missing exactly the path this exists to catch (Codex).
        undeclared = []
        for rel_path, path, identity in sorted(
            _iter_discovered_libraries(staging), key=lambda item: item[0]
        ):
            if path.is_symlink():
                # A symlink alias is checked by identity, not path string, regardless of whether it has its own LibraryEntry (a "standalone" symlink now gets one -- see _add_member's symlink branch): the declared-identities loop above resolves lib.path through to its real target's (dev, ino) either way. A non-tuple identity means its own stat() failed for a reason other than ELOOP (raised above) -- skipped, not flagged, matching the pre-existing tolerant behavior.
                if isinstance(identity, tuple) and identity not in declared_identities:
                    undeclared.append(rel_path)
            else:
                # A hardlink alias DOES get its own LibraryEntry when honestly
                # packed -- identity alone can't distinguish "expected alias"
                # from "unverified extra name". Its path must be declared.
                if rel_path not in declared_paths:
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
        # mkdtemp() creates staging mode 0700 regardless of umask -- wrong for
        # a directory about to be published. Preserve dest_path's own mode if
        # it already existed with a deliberate one.
        if existing_dest_mode is not None:
            target_dir_mode = existing_dest_mode
        else:
            target_dir_mode = _umask_derived_mode(staging, 0o777, directory=True)
        os.chmod(staging, target_dir_mode)

        # dest_path, confirmed empty above, is removed and staging renamed into its place (os.replace() can't atomically replace an existing dir everywhere, but a gone path works anywhere). Residual: if rmdir() succeeds but replace() fails, dest_path can't be restored -- surfaced as SnapshotError, not silently.
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
    # Only major >= 1 has ever been a real, shipped format -- v1 is both the first and (today) the only one this build implements. The check above alone accepts any major <= supported_major, including v0 or a negative major no version of this codebase ever wrote, so a malformed or foreign manifest spelling one of those would still deserialize with the v1 field layout and publish as a supported baseline (Codex).
    if major < 1:
        raise SnapshotError(
            f"{archive_path}: unrecognized product baseline schema {schema!r}"
        )


#: Either a flat list of header-root directories applied to every library, or
#: a ``{library_key: [roots...]}`` mapping scoping roots to one library --
#: see :func:`compare_product_directories`'s docstring.
HeaderRootsSpec = Sequence[str] | Mapping[str, Sequence[str]]


def _roots_for_library(spec: HeaderRootsSpec, library_key: str) -> Sequence[str]:
    """Resolve one library's own header roots out of a :data:`HeaderRootsSpec`.

    A ``Mapping`` is looked up by *library_key*, defaulting to no roots at
    all for a library the mapping doesn't mention (never a fallback to
    some other library's roots). A flat ``Sequence[str]`` (the original,
    pre-per-library shape) applies unchanged to every library.

    A bare ``str`` is rejected outright: it satisfies the declared
    ``Sequence[str]`` type, so `header_roots="include"` previously ran
    character-by-character (Codex/CodeRabbit review, fresh evidence).
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
    canonical (SONAME-major-stripped) key, and keyed by relative path
    rather than bare filename: two real files/DSOs sharing a canonical
    name or basename are two libraries a product shipping two SONAME
    majors, or the same name in different directories, must discover
    independently (Codex review, fresh evidence).

    Format-neutral, unlike :func:`abicheck.package.discover_shared_libraries`
    (ELF-only): matches by suffix using :func:`_is_shared_library`, so a PE
    ``.dll`` or Mach-O ``.dylib`` is discovered too, supplemented by
    :func:`abicheck.package._is_elf_shared_object`'s content-aware ELF
    ``ET_DYN`` sniff for an extensionless ELF DSO.

    A real hardlink/symlink alias pointing at the identical file is still
    collapsed to one entry, by ``(dev, ino)`` identity. *include_private*
    is API-symmetry-only, no effect here. Built from
    :func:`_iter_discovered_libraries`'s own raw per-path walk -- a
    caller needing every raw path, not just the deduplicated survivor,
    should call that directly instead.
    """
    del include_private  # accepted for API symmetry only; see docstring.

    library_map: dict[str, Path] = {}
    seen_identity: set[Path | tuple[int, int]] = set()
    for rel_path, path, identity in _iter_discovered_libraries(root):
        if identity in seen_identity:
            continue
        seen_identity.add(identity)
        library_map[rel_path] = path
    return library_map


def _iter_discovered_libraries(
    root: Path,
) -> Iterable[tuple[str, Path, Path | tuple[int, int]]]:
    """Yield ``(relative_path, path, identity)`` for every library-shaped
    file under *root*, *not* deduplicated by identity -- the walk
    :func:`_discover_library_map` builds its own deduplicated map from.

    A caller needing every path sharing an identity -- not just whichever
    sorts first and survives dedup -- must iterate this directly: an
    undeclared hardlink alias sorting after a declared library's own
    symlink alias is invisible to the deduplicated map (Codex review,
    fresh evidence). Directory-walk order is sorted so results are
    deterministic across runs (CodeRabbit review).
    """
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if not _is_library_path(path):
                continue
            # A GNU ld INPUT()/GROUP() linker script is library-suffix-named but carries no binary content of its own -- excluded by `_is_library_path` itself (the `continue` above), so every caller shares one exclusion (Codex review, fresh evidence).
            try:
                real = path.resolve()
            except (OSError, RuntimeError):
                # RuntimeError alongside OSError: a symlink loop makes
                # Path.resolve() raise RuntimeError on some Python versions.
                real = path
            else:
                # A library-shaped symlink whose target resolves outside root entirely (e.g. `libfoo.so -> /usr/lib/libfoo.so`) is rejected rather than discovered -- would silently analyze a host file not part of the product (Codex review, fresh evidence). An in-tree dev symlink still resolves within root and is unaffected.
                if real != root_resolved and not real.is_relative_to(root_resolved):
                    continue
            try:
                st = real.stat()
                identity: Path | tuple[int, int] = (st.st_dev, st.st_ino)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    # A library-shaped self-referential symlink loop is not a real library -- silently discovering it let a loop-only product compare as a spurious bundle_library_removed/added finding instead of being rejected outright (Codex review, fresh evidence).
                    raise SnapshotError(
                        f"{root}: {_relative_posix(path, root)!r} is a "
                        "self-referential symlink loop, not a real library"
                    ) from exc
                # Any other stat() failure (ENOENT for a dangling target) means no real file backs this symlink -- skip it, the same as an out-of-root target already is (Codex review, fresh evidence).
                continue
            yield _relative_posix(path, root), path, identity


def _bundle_map_by_bare_name(
    library_map: dict[str, Path], root: Path
) -> dict[str, Path]:
    """Rekey *library_map* (relative-path -> Path) by each library's bare
    filename, the identity build_bundle_snapshot()/compare_bundle() use.

    Last-wins on a collision, *not* rejected outright: compare_product_
    directories()'s own standalone add/remove detection below already
    handles this shape correctly via the collision-free old_map/new_map
    directly, and several existing tests pin that as intended, supported
    behavior. What can't be fixed without a larger redesign is
    compare_bundle()'s own *graph*-level analysis -- bare-name keyed
    throughout bundle.py's public surface -- which still only ever sees
    whichever of the two colliding libraries survives here (Codex
    review, fresh evidence). A warning at least surfaces the gap.
    """
    bundle_map: dict[str, Path] = {}
    for path in library_map.values():
        prior = bundle_map.get(path.name)
        if prior is not None:
            import warnings

            warnings.warn(
                f"{root}: {_relative_posix(path, root)!r} and "
                f"{_relative_posix(prior, root)!r} share the bare filename "
                f"{path.name!r} -- compare_bundle()'s own dependency-edge "
                "analysis can only see one of them "
                f"({_relative_posix(path, root)!r}); a real intra-bundle "
                "dependency on or drift in the other is invisible to it "
                "(standalone add/remove detection is unaffected)",
                UserWarning,
                stacklevel=2,
            )
        bundle_map[path.name] = path
    return bundle_map


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
    unpaired even if an exact match already consumed one member of that
    group (old={.1,.2}/new={.2,.3} must not treat the unrelated .1/.3 as
    one evolving library just because .2/.2 matched exactly; Codex
    review, fresh evidence), the same ambiguity-safe-fallback discipline
    this codebase's other identity resolvers follow (see
    ``finding_identity.py``'s own docstring). A library present on
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
        of whether any library pair below actually references it -- unlike
        `_resolved_headers` below, which only checks roots a *matched* pair
        asks for and never runs when discovery produces zero pairs (Codex).
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
            resolved = _resolve_under_root(root, rel)
            context = f" for library {library_key!r}" if library_key is not None else ""
            if resolved is None:
                raise SnapshotError(
                    f"header root {rel!r}{context} is absolute or escapes "
                    f"the product directory ({root}) -- header roots must "
                    "be relative paths contained within the product "
                    "directory."
                )
            if resolved.exists() and not resolved.is_dir():
                # Nonexistent tolerated (no headers here); an *existing*
                # non-directory was previously dropped silently (Codex).
                raise SnapshotError(
                    f"header root {rel!r}{context} exists but is not a "
                    f"directory ({resolved}) -- header roots must name a "
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
                # Defense-in-depth: `_validate_header_roots_spec` above already rejects an absolute/escaping root before any pair is matched, so this should be unreachable in practice -- kept in case a future caller reaches `_resolved_headers` without going through validation.
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
    # (old_key, old_path, new_key, new_path) -- both sides' discovered identity is kept alongside the path so per-library header roots resolve against the identity the caller observed, not a shared/canonical one that may not exist for a fallback pair below.
    pairs: list[tuple[str, Path, str, Path]] = [
        (k, old_map[k], k, new_map[k]) for k in exact_matches
    ]

    # Canonical (SONAME-major-stripped) fallback for the libraries exact matching left unpaired -- two ambiguity-safe passes, each grouping the *complete* per-side discovery, so consuming one member of an ambiguous group never makes the rest look artificially unambiguous. Pass 1 is directory-scoped: two independent libraries sharing a basename in different directories that each bump their own SONAME major must not land in one shared, cross-directory bucket. Pass 2 is a bare, global fallback over whatever pass 1 left unpaired -- a library that moves directories between releases has no shared directory to key on (Codex).
    def _canonical_groups(
        discovered: dict[str, Path], *, scope_by_directory: bool
    ) -> dict[tuple[str, str], list[str]]:
        groups: dict[tuple[str, str], list[str]] = {}
        for key, path in discovered.items():
            rel_dir = (
                (key.rsplit("/", 1)[0] if "/" in key else "")
                if scope_by_directory
                else ""
            )
            groups.setdefault((rel_dir, _canonical_library_key(path)), []).append(key)
        return groups

    already_paired_old = {k for k, _, _, _ in pairs}
    already_paired_new = {k for _, _, k, _ in pairs}
    for scope_by_directory in (True, False):
        old_canonical = _canonical_groups(
            old_map, scope_by_directory=scope_by_directory
        )
        new_canonical = _canonical_groups(
            new_map, scope_by_directory=scope_by_directory
        )
        for canonical_key in sorted(set(old_canonical) & set(new_canonical)):
            old_candidates = old_canonical[canonical_key]
            new_candidates = new_canonical[canonical_key]
            if len(old_candidates) != 1 or len(new_candidates) != 1:
                continue
            old_key, new_key = old_candidates[0], new_candidates[0]
            if old_key in already_paired_old or new_key in already_paired_new:
                continue  # already covered by an earlier, more specific pass
            pairs.append((old_key, old_map[old_key], new_key, new_map[new_key]))
            already_paired_old.add(old_key)
            already_paired_new.add(new_key)

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
        # DiffResult.library is the *old* side's bare filename, not the new one, for a canonical-fallback pair (SONAME/dylib bump) -- compare_bundle()'s provider detectors key off the new-side name and would falsely read the provider as its own consumer (Codex).
        result.diff.library = new_path.name
        per_library_results.append(result.diff)

    # Bundle-level identity is each library's own bare filename, not the relative-path identity above (matches compare_bundle()'s pre- existing diff_by_library convention and DT_NEEDED's own bare-name resolution). See _bundle_map_by_bare_name()'s own docstring for the last-wins-plus-warning tradeoff this makes on a same-basename collision (Codex review, fresh evidence).
    old_bundle_map = _bundle_map_by_bare_name(old_map, old_root)
    new_bundle_map = _bundle_map_by_bare_name(new_map, new_root)

    # A canonically-paired library (SONAME-major bump, dylib-version bump, PE case-fold) can have two different bare filenames even though `pairs` above already determined it's one library. Left alone, old/new_bundle_map would register it under two keys and compare_bundle() would report a spurious removal+addition for a library that never changed (Codex review, fresh evidence). Normalize the old side's key to the new side's -- but only when old_path survived the last-wins collapse, and only when the destination isn't already occupied by a *different* old-side library, or the rekey would silently evict it (CodeRabbit review).
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

    # compare_bundle()'s own BUNDLE_LIBRARY_REMOVED detection deliberately only fires when a surviving sibling actually imports the removed library -- a standalone removal (no internal consumer) is by design left to the CLI's separate --fail-on-removed-library flow, which this library-only entry point has none of. Left as-is, a release dropping its one public library would silently return NO_CHANGE (Codex review) -- a false-green whole-product compatibility gate. Report it directly: any bare filename present in the old bundle map and absent from the new one, that compare_bundle() didn't already report.
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
    # Unmatched-by-identity, not bare-filename set difference: every key in old_map/new_map is either paired above or genuinely has no counterpart -- unlike old/new_bundle_map (bare-filename-keyed, last-wins), old_map/new_map are relative-path-keyed and collision- free, exact even when two distinct libraries share a basename. Computing removed/added from the collapsed bare-filename maps instead silently missed the second of two same-basename libraries vanishing (Codex review, fresh evidence).
    paired_old_keys = {old_key for old_key, _, _, _ in pairs}
    paired_new_keys = {new_key for _, _, new_key, _ in pairs}

    def _is_bundle_collapse_survivor(
        candidate_map: dict[str, Path], bundle_map: dict[str, Path], key: str
    ) -> bool:
        """True when *key*'s path is the one that survived the bundle
        map's own last-wins bare-filename collapse -- the one
        compare_bundle() actually analyzed. Excluding an unmatched key
        from the standalone fallback is sound only when this is true:
        otherwise a bare-name membership check alone would suppress a
        real, distinct removal/addition compare_bundle() never even saw
        (Codex review, fresh evidence).
        """
        path = candidate_map[key]
        return bundle_map.get(path.name) == path

    # One entry per unmatched *key* (relative path), not deduped by bare name -- two distinct unmatched libraries sharing a basename in different directories (plugins/a/plugin.so and plugins/b/plugin.so) must each be reported when both vanish, not collapsed into a single finding the way a `set` of names alone would (Codex review, fresh evidence: pairing already correctly distinguishes the two by relative path, but this reporting step was still projecting down to bare names before iterating, silently losing the second removal).
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
    # A library matched to a surviving library on the new side -- whether by an exact relative-path match or the canonical (SONAME-major-stripped, case-insensitive) fallback above -- is not a standalone removal even though its own bare filename may be absent from new_bundle_map: a SONAME major bump (`libfoo.so.1` -> `libfoo.so.2`) and a case-only DLL rename (`Foo.dll` -> `foo.dll`) both pair via the canonical fallback while their bare filenames genuinely differ (Codex review, fresh evidence, the DLL case; the SONAME-major case is the identical mechanism generalized) -- already excluded above, since a paired key is never in `unmatched_old_items`/`unmatched_new_items` to begin with.
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

    # Symmetric gap to the removal one above, for the identical reason: compare_bundle()'s own BUNDLE_LIBRARY_ADDED detection reads new library names straight off BundleSnapshot.libraries, which build_bundle_snapshot() only ever populates from *ELF* metadata (non-ELF inputs are skipped with a warning -- the bundle layer is Linux/ELF-only by design). A new DLL/dylib added to the product therefore never reaches that detection at all, regardless of any sibling-consumer gating -- an empty old directory versus a new directory containing foo.dll still returned NO_CHANGE with no findings (Codex review, fresh evidence).
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

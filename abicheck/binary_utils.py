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

"""Shared binary format detection utilities.

Provides a single-file-open format detector for ELF, PE, and Mach-O
binaries, replacing duplicated detection logic in cli.py, appcompat.py,
and mcp_server.py.
"""

from __future__ import annotations

import re
from pathlib import Path

# Mach-O magic bytes — covers all variants:
# 32-bit BE/LE, 64-bit BE/LE, fat archive 32/64
_MACHO_MAGICS: frozenset[bytes] = frozenset(
    {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",  # 32-bit
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",  # 64-bit
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",  # fat archive 32
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",  # fat archive 64
    }
)

# Unix `ar` archive magics — shared by static libraries (`.a`) and MSVC/COFF
# import/static libraries (`.lib`). `!<arch>\n` is the standard archive;
# `!<thin>\n` is a GNU thin archive (`ar rcT`, members referenced by path).
# Both are member archives, not the kind of single linkable image abicheck
# analyses (see detect_archive / G8). Both magics are 8 bytes.
_ARCHIVE_MAGICS: frozenset[bytes] = frozenset({b"!<arch>\n", b"!<thin>\n"})
_ARCHIVE_MAGIC_LEN: int = 8
_LD_SCRIPT_RE = re.compile(r"\b(?:INPUT|GROUP|OUTPUT_FORMAT)\s*\(")
_LD_KEYWORDS = frozenset({"AS_NEEDED", "INPUT", "GROUP", "OUTPUT_FORMAT"})


def classify_magic(magic: bytes) -> str | None:
    """Classify binary format from the first 4 (or more) magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or ``None``.
    """
    if len(magic) >= 4 and magic[:4] == b"\x7fELF":
        return "elf"
    if len(magic) >= 2 and magic[:2] == b"MZ":
        return "pe"
    if len(magic) >= 4 and magic[:4] in _MACHO_MAGICS:
        return "macho"
    return None


def detect_binary_format(path: str | Path) -> str | None:
    """Detect binary format from file magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or ``None`` for unknown/unreadable.
    Uses a single file open and reads only 4 bytes.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return None
    return classify_magic(magic)


def resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld INPUT()/GROUP() script to a referenced library.

    Returns ``(target, is_linker_script)``. ``target`` is ``None`` when the file
    is not a linker script, or when no referenced library can be found.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return None, False
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    if not _LD_SCRIPT_RE.search(text):
        return None, False
    for group in re.findall(r"(?:INPUT|GROUP)\s*\(([^)]*)\)", text):
        for tok in group.replace(",", " ").split():
            if tok in _LD_KEYWORDS or tok.startswith(("-l", "-L", "(")):
                continue
            if ".so" not in tok and not tok.endswith(".a"):
                continue
            candidate = Path(tok)
            for cand in (candidate, path.parent / tok, path.parent / candidate.name):
                if cand.is_file():
                    return cand, True
    return None, True


def normalize_binary_input(path: Path) -> tuple[Path, str | None]:
    """Detect binary format, following resolvable GNU ld linker scripts."""
    fmt = detect_binary_format(path)
    if fmt is not None:
        return path, fmt
    target, is_ld = resolve_linker_script(path)
    if is_ld and target is not None:
        return target, detect_binary_format(target)
    return path, fmt


def detect_archive(path: str | Path) -> bool:
    """Return True if ``path`` is a Unix `ar` archive (`.a` / `.lib`).

    Static libraries and COFF import libraries are member archives sharing the
    ``!<arch>\\n`` magic; GNU *thin* archives (``ar rcT``) use ``!<thin>\\n``.
    abicheck analyses single linkable images (shared libraries / objects), so
    the service layer uses this to fail deliberately with guidance rather than
    late with a misleading "unknown format" error.
    """
    try:
        with open(path, "rb") as f:
            magic = f.read(_ARCHIVE_MAGIC_LEN)
    except OSError:
        return False
    return magic in _ARCHIVE_MAGICS


#: `auditwheel` (Linux) and `delocate` (macOS) rewrite each vendored library to
#: ``lib<name>-<hex>.so.<ver>`` / ``lib<name>-<hex>.dylib`` and rewrite its
#: SONAME/install-name to match, so the hash changes on every rebuild even
#: though the underlying dependency didn't. Restricted to a hyphen + 6-16 hex
#: chars immediately before ``.so``/``.dylib`` (or a numeric version
#: component leading to one) so ordinary hyphenated names — e.g.
#: ``libwebpdemux``, ``libbrotlicommon``, or a real ``-cafe`` (too short) —
#: are never touched (G9, ADR: docs/contribute/plans/g9-wheel-vendored-matching.md).
#: The lookahead ``(?=[0-9a-f]*[a-f])`` requires at least one non-decimal hex
#: letter in the run: without it, a purely-decimal 6-16-digit suffix (a
#: legitimate embedded build/version number, e.g. ``libfoo-100200.so.1`` vs.
#: ``libfoo-100300.so.1``) also matched and stripped to the same key,
#: silently hiding a real SONAME/dependency change as vendor-hash noise —
#: the exact false-negative an ABI-breaking-change detector must not produce
#: (self-review finding).
_VENDOR_HASH_RE = re.compile(
    r"-(?=[0-9a-f]*[a-f])[0-9a-f]{6,16}(?=\.(?:so|dylib)\b|\.\d)"
)


def strip_vendor_hash(name: str) -> str:
    """Strip an auditwheel/delocate content-hash suffix from a library name.

    Pairing on the unhashed stem lets ``compare-release`` diff two wheels'
    vendored libraries directly instead of reporting every one as
    removed+added noise every rebuild (G9), and lets SONAME/install-name
    diffing treat a hash-only rebuild as unchanged rather than a spurious
    ``SONAME_CHANGED``. A genuinely changed vendored dependency (e.g. a
    SONAME major bump) still surfaces as a real break — this only normalizes
    the filename/SONAME spelling, never the content.
    """
    return _VENDOR_HASH_RE.sub("", name)


#: Mach-O's version-before-extension convention: ``libfoo.1.dylib``,
#: ``libfoo.1.2.3.dylib`` -- the compatibility/current-version numbers ld64
#: encodes directly in the on-disk filename, distinct from ELF's
#: version-*after*-extension ``libfoo.so.1`` convention the regex below
#: already handles. Requires at least one numeric segment so a bare
#: ``libfoo.dylib`` (no version at all) is left untouched.
_DYLIB_VERSION_RE = re.compile(r"\.(?:\d+\.)*\d+\.dylib$")


def _canonical_library_key(path: Path) -> str:
    """Canonical key used to match libraries across releases.

    For ELF versioned names, canonicalize to ``*.so`` (e.g. ``libfoo.so.1.2`` → ``libfoo.so``).
    For Mach-O versioned names, canonicalize to ``*.dylib`` (e.g.
    ``libfoo.1.2.dylib`` → ``libfoo.dylib``) -- the standard ld64
    compatibility-version-in-filename convention, the Mach-O counterpart of
    the ELF handling above (Codex review, fresh evidence: a normal Mach-O
    major-version bump, e.g. ``libfoo.1.dylib`` -> ``libfoo.2.dylib``, never
    matched anything before this, since a plain ``.dylib`` filename fell
    through this function entirely unchanged).
    Vendored auditwheel/delocate hash suffixes are stripped first (G9) so the
    same bundled dependency pairs across rebuilds despite its hash changing.

    Lives in this leaf module (not ``cli_helpers_compare.py``, which re-exports
    it for back-compat) so ``bundle.py``'s ``discover_artifact_set`` (ADR-056)
    can use it without a ``bundle -> cli_helpers_compare -> service ->
    service_scan -> bundle`` import cycle (`service_scan.py` imports `bundle`
    for the ``--artifact-set`` audit path).

    A compressed snapshot's storage suffix (``.json.gz``/``.json.zst``, ADR-059)
    is stripped first, same as the vendor hash: the storage envelope is not
    part of a release's identity, so an old release publishing
    ``libfoo.abicheck.json`` and a new release publishing the identical
    snapshot as ``libfoo.abicheck.json.gz`` must still key-match as the same
    library rather than surfacing as an unrelated removal+addition pair.
    """
    from .snapshot_io import _COMPRESSED_SUFFIXES

    lower = strip_vendor_hash(path.name.lower())
    for suffix, _compression in _COMPRESSED_SUFFIXES:
        if lower.endswith(suffix):
            # Strip only the compression extension (".gz"/".zst"), keeping the
            # ".json" so a compressed and an uncompressed snapshot of the same
            # release still reduce to the same key.
            trailing_ext = suffix[len(".json") :]
            lower = lower[: -len(trailing_ext)]
            break
    m = re.search(r"\.so(?:\.|$)", lower)
    if m:
        return lower[: m.start() + 3]
    if _DYLIB_VERSION_RE.search(lower):
        return _DYLIB_VERSION_RE.sub(".dylib", lower)
    return lower

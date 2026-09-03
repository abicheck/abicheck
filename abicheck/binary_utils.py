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
import struct
from functools import partial
from pathlib import Path

# ``strip_vendor_hash``'s real home is ``model/binary_naming.py`` (ADR-061
# D1): a pure string transform with no I/O, living in ``model`` so
# ``compare``-layer detectors can use it without a forbidden
# ``compare -> extract`` edge. ``_canonical_library_key`` below still needs
# it; re-exported by value here so an existing
# ``from .binary_utils import strip_vendor_hash`` still resolves.
from .model.binary_naming import strip_vendor_hash as strip_vendor_hash

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

    Real ELF/PE/Mach-O magic bytes are checked first and are a positive,
    unambiguous binary-content signal: a genuine GNU ld linker script is
    plain text and never starts with one. Without this guard, a real binary
    whose first 8KiB happens to contain the literal text ``INPUT(``/
    ``GROUP(``/``OUTPUT_FORMAT(`` -- embedded strings, symbol names, or
    plain coincidence -- would be misclassified as a linker script by the
    regex probe below (Codex review, fresh evidence: reported for a real
    ELF DSO whose own content happened to contain
    ``INPUT(libdoesnotexist.so)``, silently excluding it from both
    :mod:`abicheck.product_baseline` packing and product comparison).
    """
    if detect_binary_format(path) is not None:
        return None, False
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


def resolve_linker_script_chain(path: Path, max_hops: int = 32) -> Path:
    """Follow a chain of GNU ld linker scripts to the final real artifact.

    ``resolve_linker_script`` only ever resolves one hop; a linker script
    can legitimately point at another linker script (e.g. a dev symlink
    ``libfoo.so`` -> ``libfoo.so.1``, itself a script pointing at the
    versioned ``libfoo.so.1.2.3``), which ``service.resolve_input`` already
    follows correctly via its own recursive self-call. A caller that only
    needs the final resolved path (not a full snapshot) -- e.g. hashing an
    operand for the same-binary coverage warning -- needs the identical
    multi-hop behavior rather than a single-hop copy of it (Codex review,
    fresh evidence). ``max_hops`` guards against a pathological cyclic
    chain; returns *path* itself once no further hop resolves (including
    immediately, for an ordinary non-script input).
    """
    current = path
    for _ in range(max_hops):
        target, is_ld = resolve_linker_script(current)
        if not is_ld or target is None:
            return current
        current = target
    return current


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


#: Mach-O's version-before-extension convention: ``libfoo.1.dylib``,
#: ``libfoo.1.2.3.dylib`` -- the compatibility/current-version numbers ld64
#: encodes directly in the on-disk filename, distinct from ELF's
#: version-*after*-extension ``libfoo.so.1`` convention the regex below
#: already handles. Requires at least one numeric segment so a bare
#: ``libfoo.dylib`` (no version at all) is left untouched. Case-insensitive
#: to match a ``.DYLIB`` extension spelling the same way the ELF ``.so``
#: match below does, but the *stem* is never case-folded -- see
#: :func:`_canonical_library_key`'s own docstring for why. The extension
#: itself is a capture group so its *matched* case can be preserved on
#: substitution -- a fixed lowercase replacement would normalize
#: ``libfoo.1.DYLIB`` to ``libfoo.dylib`` while leaving an unversioned
#: ``libfoo.DYLIB`` untouched, so a real version-drop pair (``libfoo.1.
#: DYLIB`` -> ``libfoo.DYLIB``) would land on two different-cased keys and
#: never be paired by the canonical fallback (Codex review, fresh
#: evidence).
_DYLIB_VERSION_RE = re.compile(r"\.(?:\d+\.)*\d+(\.dylib)$", re.IGNORECASE)

#: PE/COFF ``IMAGE_FILE_DLL`` bit in the COFF file header's ``Characteristics``
#: field -- set for a DLL, clear for a plain ``.exe``.
_PE_IMAGE_FILE_DLL = 0x2000


def _pe_is_dll_content(path: Path) -> bool:
    """True when *path* is a PE/COFF image with the ``IMAGE_FILE_DLL`` bit
    set in its COFF file header, identified from content rather than
    filename -- lets a DLL shipped under a nonstandard extension (a Python
    ``.pyd`` extension module) be recognized as a library
    (`product_baseline.py`'s `_is_library_path`) and case-folded for
    canonical matching (`_canonical_library_key` below) the same way a
    conventional ``.dll`` already is, since a suffix check alone never
    catches either (Codex review, fresh evidence).

    Lives in this leaf module, not `product_baseline.py` (its original
    home): `_canonical_library_key` needs it too, and `product_baseline.py`
    already imports `_canonical_library_key` from here -- the reverse
    import would cycle.
    """
    try:
        with open(path, "rb") as f:
            dos_header = f.read(64)
            if len(dos_header) < 64 or dos_header[0:2] != b"MZ":
                return False
            (pe_offset,) = struct.unpack_from("<I", dos_header, 0x3C)
            f.seek(pe_offset)
            sig = f.read(4)
            if sig != b"PE\x00\x00":
                return False
            coff_header = f.read(20)
    except (OSError, struct.error):
        return False
    if len(coff_header) < 20:
        return False
    (characteristics,) = struct.unpack_from("<H", coff_header, 18)
    return bool(characteristics & _PE_IMAGE_FILE_DLL)


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

    Case-folding is restricted to PE images, matching only the one format
    whose loader identity is genuinely case-insensitive (Windows).
    Whole-string lowercasing previously applied to *every* format alike,
    so an ELF ``libFoo.so`` -> ``libfoo.so`` case-only rename (or the
    identical Mach-O case) paired via this same canonical fallback and its
    removal/addition was silently suppressed — but a case-sensitive
    ELF/Mach-O loader cannot resolve a symbol-table-only case difference
    the way Windows' loader resolves a PE one: an existing consumer whose
    ``DT_NEEDED``/``LC_LOAD_DYLIB`` still names the old spelling fails to
    load the renamed file, a real break this canonical pairing must not
    hide (Codex review, fresh evidence). "Is this a PE image" is answered
    by content (:func:`_pe_is_dll_content`), not just the ``.dll`` suffix:
    a case-only rename of a PE library shipped under a nonstandard
    extension (a Python ``.pyd`` extension module) is just as
    case-insensitive on Windows as a ``.dll``'s, and a suffix-only check
    would miss it (Codex review, fresh evidence).

    A *stored snapshot* of a library (``Foo.dll.abicheck.json``, or its
    compressed ``.gz``/``.zst`` forms) carries its represented format in
    the filename, not in the (JSON) file content -- ``_pe_is_dll_content``
    reads the represented binary's own bytes, which for a snapshot are the
    JSON envelope, not a PE image, so it always answers False here.
    Matching the represented library's format (``.dll``/``.dylib`` version)
    is therefore done against the *represented* name -- the stored
    filename with one recognized wrapper suffix (the ``.abicheck.json``
    convention `docs/use/baseline-management.md` documents, or the plainer
    ``.json``/``.pl``/``.pm`` forms `_version_sort_key` already recognizes)
    stripped, not the raw stored-snapshot name (Codex review, fresh
    evidence, two rounds). The first round matched any ``\\.dll`` segment
    anywhere in the (unwrapped) name -- true for ``Foo.dll.abicheck.json``,
    but also true for an unrelated, genuinely case-sensitive ELF name that
    happens to literally contain ``.dll.`` (e.g. a compatibility shim named
    ``libFoo.dll.so``), wrongly case-folding it and hiding a real
    case-only-rename break. Stripping the wrapper suffix first and then
    requiring the represented name to genuinely *end* in ``.dll`` avoids
    that false positive while still recognizing a stored PE snapshot: the
    same mechanism also lets a versioned dylib snapshot
    (``libfoo.1.dylib.abicheck.json``) match `_DYLIB_VERSION_RE` below,
    which is anchored to the end of string and previously never matched
    through a wrapper suffix at all.

    Not closed by this: a stored snapshot of a PE library shipped under a
    nonstandard extension (``Foo.pyd.abicheck.json``) still isn't
    recognized -- unlike a live ``.pyd`` file, there is no PE content to
    probe, and ".pyd" is not a wrapper-suffix-stripped ``.dll``. Closing
    that would need reading the stored snapshot's own ``platform`` field
    (``model.AbiSnapshot.platform``) rather than its filename, a genuine
    new content-peek primitive (locating a field reliably regardless of a
    JSON snapshot's key order or compression, verified on its own) rather
    than a filename heuristic extension -- left as a known, narrower gap
    (Codex review, fresh evidence).
    """
    from .snapshot_io import _COMPRESSED_SUFFIXES

    name = strip_vendor_hash(path.name)
    name_lower = name.lower()
    for suffix, _compression in _COMPRESSED_SUFFIXES:
        if name_lower.endswith(suffix):
            # Strip only the compression extension (".gz"/".zst"), keeping the
            # ".json" so a compressed and an uncompressed snapshot of the same
            # release still reduce to the same key.
            trailing_ext = suffix[len(".json") :]
            name = name[: -len(trailing_ext)]
            name_lower = name_lower[: -len(trailing_ext)]
            break

    # The represented library's own name, with one recognized stored-
    # snapshot wrapper suffix stripped (if present) -- see this function's
    # own docstring for why format/case-folding decisions are made against
    # this, not the raw stored-snapshot name. A plain (unwrapped) binary
    # name passes through unchanged (no suffix matches), so every check
    # below still applies identically to a live .dll/.dylib file.
    represented = name_lower
    represented_cased = name
    for wrapper_suffix in (".abicheck.json", ".json", ".pl", ".pm"):
        if represented.endswith(wrapper_suffix):
            represented = represented[: -len(wrapper_suffix)]
            represented_cased = represented_cased[: -len(wrapper_suffix)]
            break

    if represented.endswith(".dll") or _pe_is_dll_content(path):
        return name_lower
    m = re.search(r"\.so(?:\.|$)", name, re.IGNORECASE)
    if m:
        return name[: m.start() + 3]
    m = _DYLIB_VERSION_RE.search(represented_cased)
    if m:
        # group(1) is the matched extension exactly as spelled (preserving
        # its case) -- see _DYLIB_VERSION_RE's own docstring for why a
        # fixed-case literal replacement is wrong here. m.start() indexes
        # into `represented_cased`, which shares its prefix with `name` up
        # to the stripped wrapper suffix, so it's a valid index into `name`
        # too.
        return name[: m.start()] + m.group(1)
    # An *unversioned* dylib has no numeric segment for _DYLIB_VERSION_RE
    # to match at all (see its own docstring), so a stored snapshot of one
    # (`libfoo.dylib.abicheck.json`) fell through to here unchanged --
    # keeping its wrapper suffix -- while a *versioned* sibling
    # (`libfoo.1.dylib.abicheck.json`) matched above and had its wrapper
    # dropped by that branch's own `name[: m.start()] + m.group(1)`
    # construction. Two different canonical keys for a version-drop pair
    # of the same evolving library (Codex review, fresh evidence).
    # Scoped to exactly that case (the represented name genuinely ends in
    # ".dylib") -- an earlier revision of this fix returned
    # `represented_cased` unconditionally here, which also stripped the
    # wrapper from a name with *no* recognized binary extension at all
    # (e.g. a bare "libfoo.abicheck.json" for an extensionless plugin),
    # changing its canonical key from the whole wrapped name to just
    # "libfoo" and breaking existing snapshot-matching behavior for that
    # unrelated case.
    if represented.endswith(".dylib"):
        return represented_cased
    return name


def _version_sort_key(
    path: Path, canonical_key: str
) -> tuple[list[tuple[int, int | str]], str]:
    """Build a version-aware sort key for ambiguous library candidates.

    Uses the vendor-hash-stripped name (G9) so an auditwheel/delocate content
    hash never enters the comparison — otherwise the hash's digits/letters can
    outrank the real SONAME version tokens and :func:`build_match_map` picks a
    stale duplicate over the newer one (Codex review, PR #551).

    Lives here (not ``cli_helpers_compare.py``, which re-exports it for
    back-compat, mirroring :func:`_canonical_library_key`'s own precedent) so
    ``bundle_side_input.py``'s stored-facts driver (ADR-061: classified
    ``workflows``, which may not import a ``frontends``-legacy module) can
    use it directly.
    """
    lower = strip_vendor_hash(path.name.lower())
    # ADR-059 (Codex review): strip a compressed snapshot's storage suffix
    # (".json.gz"/".json.zst") up front, before anything else touches
    # `lower` -- _canonical_library_key already groups a plain and a
    # compressed snapshot of the same release under one bucket, but this
    # function ranks candidates *within* that bucket to pick which one
    # wins, and `lower` feeds both the token comparison below AND the
    # raw-string tie-break returned at the end. Left unstripped, a ".gz"/
    # ".zst" tail becomes an extra alphabetic sort token (and an
    # alphabetically-later raw string) that always outranks a plain
    # ".json" -- and ".zst" always outranks ".gz" -- regardless of which
    # file is actually current. A stale compressed sibling left over from
    # a previous release could then silently win over a freshly-written
    # plain/differently-compressed snapshot. (Two candidates differing
    # only by encoding now reduce to the same sort key -- genuinely
    # ambiguous, indistinguishable from the filename alone, and already
    # surfaced by `build_match_map`'s own "Ambiguous match" warning for
    # any multi-candidate bucket.)
    from .snapshot_io import _COMPRESSED_SUFFIXES

    for suffix, _compression in _COMPRESSED_SUFFIXES:
        if lower.endswith(suffix):
            lower = lower[: -len(suffix[len(".json") :])]
            break
    remainder = lower
    if canonical_key.endswith(".so") and canonical_key in lower:
        remainder = lower[lower.find(canonical_key) + len(canonical_key) :]
    # strip known wrapper extensions for snapshots/dumps
    for suffix in (".json", ".pl", ".pm"):
        if remainder.endswith(suffix):
            remainder = remainder[: -len(suffix)]
            break
    remainder = remainder.lstrip("._-")
    tokens = re.findall(r"\d+|[a-z]+", remainder)
    parsed: list[tuple[int, int | str]] = []
    for tok in tokens:
        if tok.isdigit():
            parsed.append((1, int(tok)))
        else:
            parsed.append((0, tok))
    return parsed, lower


def build_match_map(paths: list[Path]) -> tuple[dict[str, Path], list[str]]:
    """Build key->path map with version-aware duplicate resolution.

    Raises :class:`~abicheck.errors.AmbiguousLibraryMatchError` for a genuine
    top-of-ranking tie (Codex review, PR #699, second round on the same fix):
    stripping a compressed snapshot's storage suffix (ADR-059) from the sort
    key makes two candidates differing *only* by encoding -- e.g. a plain
    ``libfoo.abicheck.json`` and a stale ``libfoo.abicheck.json.zst`` left
    over from a previous release -- reduce to an *identical* sort key, not
    merely "multiple candidates present". ``sorted()``'s stability then
    means the winner is decided by each candidate's position in the
    original, lexically-sorted input list -- itself alphabetically biased
    toward whichever compression suffix sorts last (``.zst`` after ``.gz``
    after plain) -- so silently picking ``ordered[-1]`` and only warning
    would deterministically prefer a stale compressed sibling over a newer
    one every time. There is no information left in the filename to break a
    genuine tie correctly, so this fails closed instead of guessing; a real
    multi-version bucket (each candidate's sort key genuinely differs) is
    unaffected and still resolves with a warning, exactly as before.

    A plain, Click-free primitive (see :class:`~abicheck.errors.
    AmbiguousLibraryMatchError`'s own docstring for why) -- lives here, not
    ``cli_helpers_compare.py``, so ``bundle_side_input.py`` can call it
    without a ``frontends``-legacy import (ADR-061: ``workflows`` may not
    import ``frontends``). ``cli_helpers_compare._build_match_map`` is now a
    thin wrapper translating :class:`AmbiguousLibraryMatchError` into
    ``click.ClickException`` with the identical message, so every existing
    CLI-facing caller (``compare``/``compare-release``) is unaffected.
    """
    from .errors import AmbiguousLibraryMatchError

    buckets: dict[str, list[Path]] = {}
    for p in paths:
        buckets.setdefault(_canonical_library_key(p), []).append(p)

    mapping: dict[str, Path] = {}
    warnings: list[str] = []
    for key, vals in buckets.items():
        # `partial` binds this iteration's key rather than closing over the
        # loop variable (the sort runs eagerly here, but the explicit binding
        # keeps that independent of when the key function is called).
        sort_key = partial(_version_sort_key, canonical_key=key)
        ordered = sorted(vals, key=sort_key)
        if len(ordered) > 1 and sort_key(ordered[-1]) == sort_key(ordered[-2]):
            raise AmbiguousLibraryMatchError(
                f"Ambiguous match for '{key}': {[v.name for v in ordered]} "
                "are indistinguishable except by storage encoding -- cannot "
                "tell which is current. Remove the stale duplicate(s), or "
                "pass the intended file directly instead of a directory."
            )
        selected = ordered[-1]
        mapping[key] = selected
        if len(ordered) > 1:
            warnings.append(
                f"Ambiguous match for '{key}': {[v.name for v in ordered]}; using '{selected.name}'"
            )
    return mapping, warnings

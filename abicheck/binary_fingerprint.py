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

"""Lightweight binary fingerprinting for rename detection in stripped binaries.

Exploratory module (see ADR-003 extension).  Uses function size and code hash
from ELF .dynsym + .text to detect likely renames when symbol names change
but the underlying code is identical or near-identical.

This is NOT a reverse-engineering tool — it provides a secondary matching
signal to reduce false "removed + added" churn in symbols-only mode.

Integration point: feed ``RenameCandidate`` results into the diff engine to
convert "removed + added" pairs into "likely renamed" changes.

Trust boundary: callers are responsible for validating paths.  This module
opens files as given and does not sanitize or restrict path traversal.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import Section, SymbolTableSection

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FunctionFingerprint:
    """Fingerprint for a single exported function symbol.

    Attributes:
        name: Symbol name (mangled).
        size: st_size from .dynsym (code size in bytes, 0 if unknown).
        code_hash: SHA-256 hex digest of the function's code bytes from .text,
            or empty string if the code region could not be read.
        section_index: ELF section index the symbol resides in.
    """
    name: str
    size: int
    code_hash: str
    section_index: int = 0


@dataclass(frozen=True)
class SectionSummary:
    """Coarse-grained summary of an ELF section.

    Used for quick triage: if .text didn't change, ABI probably didn't either.
    """
    name: str
    size: int
    content_hash: str  # SHA-256 of raw section bytes


_EMPTY_HASH = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class BinarySummary:
    """Section-level summary of an entire binary.

    Provides a coarse "binary changed significantly" vs "binary barely changed"
    signal for triage before running full diff.
    """
    sections: dict[str, SectionSummary] = field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        """Return True if the summary includes a .text section."""
        return ".text" in self.sections

    def differs_from(self, other: BinarySummary) -> dict[str, tuple[str, str]]:
        """Return sections that differ between self and other.

        Returns dict of section_name → (old_hash, new_hash) for sections
        present in both but with different content hashes or sizes.
        Sections only in one binary are not included (they indicate
        structural changes).
        """
        diffs: dict[str, tuple[str, str]] = {}
        common = set(self.sections) & set(other.sections)
        for name in sorted(common):
            old_s = self.sections[name]
            new_s = other.sections[name]
            if old_s.content_hash != new_s.content_hash or old_s.size != new_s.size:
                diffs[name] = (old_s.content_hash, new_s.content_hash)
        return diffs

    @property
    def text_size(self) -> int | None:
        """Return .text section size, or None if absent."""
        s = self.sections.get(".text")
        return s.size if s is not None else None


@dataclass(frozen=True)
class RenameCandidate:
    """A pair of symbols that likely represent a rename (same code, different name).

    Attributes:
        old_name: Symbol name in the old binary (mangled).
        new_name: Symbol name in the new binary (mangled).
        confidence: Match confidence (0.0–1.0).
            1.0 = identical size AND code hash.
            0.8 = identical size, code hash unavailable.
            0.5 = size within tolerance, code hash differs or unavailable.
        old_fingerprint: Full fingerprint from old binary.
        new_fingerprint: Full fingerprint from new binary.
    """
    old_name: str
    new_name: str
    confidence: float
    old_fingerprint: FunctionFingerprint
    new_fingerprint: FunctionFingerprint


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum symbol size (bytes) to consider for fingerprinting.
# Tiny functions (trampolines, stubs) produce too many false matches.
_MIN_SYMBOL_SIZE = 8

# Maximum relative size difference for fuzzy matching (no code hash).
_SIZE_TOLERANCE_RATIO = 0.05  # 5%

# Sections to include in BinarySummary for ABI-relevant triage.
_ABI_SECTIONS = frozenset({
    ".text", ".rodata", ".data", ".bss", ".data.rel.ro",
    ".init_array", ".fini_array", ".dynamic",
})

# Maximum section size (bytes) to read into memory for hashing.
# Prevents OOM from crafted ELF files with enormous sh_size values.
_MAX_SECTION_SIZE = 256 * 1024 * 1024  # 256 MiB


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_function_fingerprints(
    binary_path: str | Path,
) -> dict[str, FunctionFingerprint]:
    """Extract function fingerprints from an ELF binary.

    Reads .dynsym for exported FUNC symbols with st_size > 0, then reads
    the corresponding code bytes from the section they reside in to compute
    a content hash.

    Returns a dict mapping symbol name → FunctionFingerprint.
    Returns an empty dict for non-ELF files or on parse errors.
    """
    try:
        with open(binary_path, "rb") as f:
            # Verify regular file after open to avoid TOCTOU (symlink/FIFO).
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("compute_function_fingerprints: not a regular file: %s", binary_path)
                return {}
            magic = f.read(4)
            if magic != b"\x7fELF":
                return {}
            f.seek(0)
            return _extract_fingerprints(f, Path(binary_path))
    except (OSError, ELFError) as exc:
        log.warning("compute_function_fingerprints: %s: %s", binary_path, exc)
        return {}


def compute_section_summary(binary_path: str | Path) -> BinarySummary:
    """Compute section-level summary for ABI-relevant ELF sections.

    Returns a BinarySummary with hashes for .text, .rodata, .data, etc.
    Useful for quick triage: if .text hash matches, code hasn't changed.
    """
    try:
        with open(binary_path, "rb") as f:
            # Verify regular file after open to avoid TOCTOU (symlink/FIFO).
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("compute_section_summary: not a regular file: %s", binary_path)
                return BinarySummary()
            magic = f.read(4)
            if magic != b"\x7fELF":
                return BinarySummary()
            f.seek(0)
            return _extract_section_summary(f)
    except (OSError, ELFError) as exc:
        log.warning("compute_section_summary: %s: %s", binary_path, exc)
        return BinarySummary()


def match_renamed_functions(
    old_fps: dict[str, FunctionFingerprint],
    new_fps: dict[str, FunctionFingerprint],
) -> list[RenameCandidate]:
    """Find likely renamed functions by matching fingerprints.

    Strategy:
    1. Identify symbols only in old (removed) and only in new (added).
    2. For each removed symbol, find added symbols with matching fingerprint.
    3. Exact match: same size AND same code_hash → confidence 1.0.
    4. Size-only match: same size, no code hash → confidence 0.8.
    5. Fuzzy match: size within 5%, no code hash → confidence 0.5.

    Uses a greedy 1:1 matching approach — each symbol matched at most once.
    Confidence values reflect processing order: a symbol matched at 0.8 in
    Pass 2 may have been 1.0 if a competing match had not consumed its
    partner in Pass 1.

    Complexity: Passes 1-2 are O(N) via hash/size indices.  Pass 3 is
    O(N*M) over remaining unmatched symbols — acceptable for typical
    shared libraries (< 10k symbols).

    Matches are returned sorted by confidence (highest first).
    """
    old_only = set(old_fps) - set(new_fps)
    new_only = set(new_fps) - set(old_fps)

    if not old_only or not new_only:
        return []

    # Filter out tiny symbols (stubs/trampolines produce false matches)
    old_candidates = {
        name: fp for name, fp in old_fps.items()
        if name in old_only and fp.size >= _MIN_SYMBOL_SIZE
    }
    new_candidates = {
        name: fp for name, fp in new_fps.items()
        if name in new_only and fp.size >= _MIN_SYMBOL_SIZE
    }

    if not old_candidates or not new_candidates:
        return []

    new_by_size, new_by_hash = _index_new_candidates(new_candidates)

    used_new: set[str] = set()
    candidates = _match_exact(old_candidates, new_by_hash, used_new)
    matched_old = {c.old_name for c in candidates}
    candidates += _match_size(old_candidates, new_by_size, used_new, matched_old)
    candidates += _match_fuzzy(old_candidates, new_candidates, used_new, matched_old)

    # Sort by confidence descending
    candidates.sort(key=lambda c: (-c.confidence, c.old_name))
    return candidates


def _index_new_candidates(
    new_candidates: dict[str, FunctionFingerprint],
) -> tuple[
    dict[int, list[tuple[str, FunctionFingerprint]]],
    dict[str, list[tuple[str, FunctionFingerprint]]],
]:
    """Build size→candidates and code_hash→candidates indices for fast lookup."""
    new_by_size: dict[int, list[tuple[str, FunctionFingerprint]]] = {}
    new_by_hash: dict[str, list[tuple[str, FunctionFingerprint]]] = {}
    for name, fp in new_candidates.items():
        new_by_size.setdefault(fp.size, []).append((name, fp))
        if fp.code_hash:
            new_by_hash.setdefault(fp.code_hash, []).append((name, fp))
    return new_by_size, new_by_hash


def _match_exact(
    old_candidates: dict[str, FunctionFingerprint],
    new_by_hash: dict[str, list[tuple[str, FunctionFingerprint]]],
    used_new: set[str],
) -> list[RenameCandidate]:
    """Pass 1: exact matches (same size + same code hash, unique match only)."""
    out: list[RenameCandidate] = []
    for old_name, old_fp in sorted(old_candidates.items()):
        if not old_fp.code_hash:
            continue
        exact_matches = [
            (n, fp) for n, fp in new_by_hash.get(old_fp.code_hash, [])
            if n not in used_new and fp.size == old_fp.size
        ]
        # Only match when unambiguous (one candidate), mirroring passes 2/3
        if len(exact_matches) == 1:
            new_name, new_fp = exact_matches[0]
            out.append(RenameCandidate(
                old_name=old_name,
                new_name=new_name,
                confidence=1.0,
                old_fingerprint=old_fp,
                new_fingerprint=new_fp,
            ))
            used_new.add(new_name)
    return out


def _match_size(
    old_candidates: dict[str, FunctionFingerprint],
    new_by_size: dict[int, list[tuple[str, FunctionFingerprint]]],
    used_new: set[str],
    matched_old: set[str],
) -> list[RenameCandidate]:
    """Pass 2: size-only matches (same size, unique among remaining candidates)."""
    out: list[RenameCandidate] = []
    for old_name, old_fp in sorted(old_candidates.items()):
        if old_name in matched_old:
            continue
        size_matches = [
            (n, fp) for n, fp in new_by_size.get(old_fp.size, [])
            if n not in used_new
        ]
        # Only match if there's exactly one candidate at this size
        # (ambiguous matches are not reliable)
        if len(size_matches) != 1:
            continue
        new_name, new_fp = size_matches[0]
        # If both have code hashes but they differ, skip
        if old_fp.code_hash and new_fp.code_hash and old_fp.code_hash != new_fp.code_hash:
            continue
        # 0.8 when either side lacks a code hash (size match only)
        out.append(RenameCandidate(
            old_name=old_name,
            new_name=new_name,
            confidence=0.8,
            old_fingerprint=old_fp,
            new_fingerprint=new_fp,
        ))
        used_new.add(new_name)
        matched_old.add(old_name)
    return out


def _fuzzy_partners(
    old_fp: FunctionFingerprint,
    new_candidates: dict[str, FunctionFingerprint],
    used_new: set[str],
) -> list[tuple[str, FunctionFingerprint]]:
    """New candidates whose size is within tolerance of ``old_fp`` and unused."""
    partners: list[tuple[str, FunctionFingerprint]] = []
    for new_name, new_fp in new_candidates.items():
        if new_name in used_new or new_fp.size == 0:
            continue
        # If both have code hashes but they differ, skip
        if old_fp.code_hash and new_fp.code_hash and old_fp.code_hash != new_fp.code_hash:
            continue
        size_diff = abs(old_fp.size - new_fp.size) / max(old_fp.size, new_fp.size)
        if size_diff <= _SIZE_TOLERANCE_RATIO:
            partners.append((new_name, new_fp))
    return partners


def _match_fuzzy(
    old_candidates: dict[str, FunctionFingerprint],
    new_candidates: dict[str, FunctionFingerprint],
    used_new: set[str],
    matched_old: set[str],
) -> list[RenameCandidate]:
    """Pass 3: fuzzy size matches (within tolerance, unique match only)."""
    out: list[RenameCandidate] = []
    for old_name, old_fp in sorted(old_candidates.items()):
        if old_name in matched_old or old_fp.size == 0:
            continue
        fuzzy_matches = _fuzzy_partners(old_fp, new_candidates, used_new)
        if len(fuzzy_matches) == 1:
            new_name, new_fp = fuzzy_matches[0]
            out.append(RenameCandidate(
                old_name=old_name,
                new_name=new_name,
                confidence=0.5,
                old_fingerprint=old_fp,
                new_fingerprint=new_fp,
            ))
            used_new.add(new_name)
            matched_old.add(old_name)
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_fingerprints(
    f: IO[bytes], binary_path: Path,
) -> dict[str, FunctionFingerprint]:
    """Extract fingerprints from an open ELF file."""
    elf = ELFFile(f)
    result: dict[str, FunctionFingerprint] = {}

    # Find .dynsym section
    dynsym: SymbolTableSection | None = None
    for section in elf.iter_sections():
        if isinstance(section, SymbolTableSection) and section.name == ".dynsym":
            dynsym = section
            break

    if dynsym is None:
        return result

    # Pre-load section data for code hashing.
    # Map section index → (section_offset, section_size, section_data).
    # Typically 1-2 entries (.text and occasionally .text.hot).
    section_cache: dict[int, tuple[int, int, bytes]] = {}

    for sym in dynsym.iter_symbols():
        # Only exported FUNC symbols
        if sym.entry.st_info.type != "STT_FUNC":
            continue
        if sym.entry.st_shndx in ("SHN_UNDEF", "SHN_ABS"):
            continue
        binding = sym.entry.st_info.bind
        if binding not in ("STB_GLOBAL", "STB_WEAK"):
            continue
        vis = sym.entry.st_other.visibility
        if vis in ("STV_HIDDEN", "STV_INTERNAL"):
            continue

        name = sym.name
        size = sym.entry.st_size
        shndx = sym.entry.st_shndx

        if not name or size < _MIN_SYMBOL_SIZE:
            continue

        # Compute code hash from the section data
        code_hash = _compute_code_hash(elf, sym, shndx, section_cache)

        result[name] = FunctionFingerprint(
            name=name,
            size=size,
            code_hash=code_hash,
            section_index=shndx if isinstance(shndx, int) else 0,
        )

    return result


def _compute_code_hash(
    elf: ELFFile,
    sym: object,
    shndx: int | str,
    section_cache: dict[int, tuple[int, int, bytes]],
) -> str:
    """Compute SHA-256 of the function's code bytes.

    Returns hex digest or empty string if the bytes can't be read
    (e.g., section not loaded, symbol spans outside section bounds,
    or section exceeds _MAX_SECTION_SIZE).
    """
    if not isinstance(shndx, int):
        return ""

    try:
        if shndx not in section_cache:
            section: Section = elf.get_section(shndx)
            if section.header.sh_type == "SHT_NOBITS":
                # .bss or similar — no actual data
                return ""
            if section.header.sh_size > _MAX_SECTION_SIZE:
                log.warning(
                    "_compute_code_hash: section %s (index %d) too large "
                    "(%d bytes > %d limit), skipping",
                    section.name, shndx, section.header.sh_size, _MAX_SECTION_SIZE,
                )
                return ""
            sec_data = section.data()
            section_cache[shndx] = (
                section.header.sh_addr,
                section.header.sh_size,
                sec_data,
            )

        sec_addr, sec_size, sec_data = section_cache[shndx]
        sym_addr = sym.entry.st_value
        sym_size = sym.entry.st_size

        # Calculate offset within section data
        offset = sym_addr - sec_addr
        if offset < 0 or offset + sym_size > len(sec_data):
            return ""

        code_bytes = sec_data[offset:offset + sym_size]
        return hashlib.sha256(code_bytes).hexdigest()

    except (IndexError, KeyError, ValueError, OSError) as exc:
        log.debug("_compute_code_hash: failed for symbol at shndx=%s: %s", shndx, exc)
        return ""


def _extract_section_summary(f: IO[bytes]) -> BinarySummary:
    """Extract section-level summary from an open ELF file."""
    elf = ELFFile(f)
    sections: dict[str, SectionSummary] = {}

    for section in elf.iter_sections():
        name = section.name
        if name not in _ABI_SECTIONS:
            continue
        size = section.header.sh_size
        if section.header.sh_type == "SHT_NOBITS":
            # .bss — no file content; use stable sentinel hash.
            # Size differences are caught by differs_from() via size comparison.
            content_hash = _EMPTY_HASH
        elif size > _MAX_SECTION_SIZE:
            log.warning(
                "compute_section_summary: section %s too large "
                "(%d bytes > %d limit), skipping",
                name, size, _MAX_SECTION_SIZE,
            )
            continue
        else:
            try:
                content_hash = hashlib.sha256(section.data()).hexdigest()
            except (OSError, ValueError):
                continue
        sections[name] = SectionSummary(
            name=name,
            size=size,
            content_hash=content_hash,
        )

    return BinarySummary(sections=sections)

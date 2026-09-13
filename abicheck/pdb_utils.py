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

"""PDB file location utilities for Windows PE binaries.

Extracts the PDB path and GUID from the PE debug directory
(IMAGE_DEBUG_TYPE_CODEVIEW / RSDS signature) using ``pefile``.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path, PureWindowsPath

import pefile  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

# CodeView signature bytes
_RSDS_SIG = b"RSDS"
_NB10_SIG = b"NB10"

# IMAGE_DEBUG_TYPE_CODEVIEW = 2
_DEBUG_TYPE_CODEVIEW = 2


def _is_network_path(p: str | Path) -> bool:
    """Return True if *p* looks like a UNC or network path."""
    s = str(p)
    # Normalise to backslashes for uniform checking
    s_norm = s.replace("/", "\\")
    if s_norm.startswith("\\\\"):
        return True
    # Also check Win32 extended UNC prefix \\?\UNC\
    if s_norm.startswith("\\\\?\\UNC\\"):
        return True
    # Fallback via PureWindowsPath — catches edge cases after normalisation
    try:
        anchor = PureWindowsPath(s_norm).anchor
        if anchor.startswith("\\\\"):
            return True
    except Exception:  # noqa: BLE001
        log.debug("PureWindowsPath parsing failed for %r", s, exc_info=True)
    return False


# Maximum CodeView debug directory entry size we're willing to allocate.
# PDB filenames are at most MAX_PATH (260) chars; RSDS header is 24 bytes.
_MAX_CODEVIEW_SIZE = 4096


def _resolve_absolute_pdb_path(
    pwin: PureWindowsPath,
    dll_path: Path,
    embedded_name: str,
    embedded: str,
) -> Path | None:
    """Try to resolve an absolute Windows PDB path to a local file."""
    try:
        dll_drive = PureWindowsPath(str(Path(dll_path).resolve())).drive
        emb_drive = pwin.drive
        if dll_drive and emb_drive and dll_drive.lower() != emb_drive.lower():
            log.debug(
                "locate_pdb: skipping embedded path on different drive %s", embedded
            )
            # Fall through to filename-only lookup
            return None
        # Construct a host-native Path for the filesystem check.
        if pwin.drive:
            candidate = (
                Path(*pwin.parts[1:]) if len(pwin.parts) > 1 else Path(embedded_name)
            )
            candidate = dll_path.parent / candidate
        else:
            candidate = Path(embedded)
        if candidate.is_file():
            return candidate
    except (ValueError, OSError):
        pass  # drive comparison failed; fall through to filename-only
    return None


def _resolve_relative_pdb_path(
    pwin: PureWindowsPath,
    dll_path: Path,
    embedded_name: str,
) -> Path | None:
    """Try to resolve a relative Windows PDB path against the DLL directory."""
    candidate = (
        dll_path.parent / Path(*pwin.parts)
        if pwin.parts
        else dll_path.parent / embedded_name
    )
    try:
        candidate.resolve().relative_to(dll_path.parent.resolve())
        if candidate.is_file():
            return candidate
    except (ValueError, OSError):
        pass  # traversal attempt or resolution error
    return None


def _resolve_embedded_pdb(
    dll_path: Path,
    allow_network: bool,
) -> Path | None:
    """Try to locate a PDB via the path embedded in the PE debug directory.

    Only considers the PDB path embedded in the PE's CodeView debug entry.
    The resolved path must be either:
    - An absolute path sharing the same drive/root as the DLL (prevents
      path traversal to unrelated filesystem locations), or
    - A relative path resolved against the DLL's directory.

    Network/UNC paths are always blocked unless *allow_network* is True.
    """
    embedded = _extract_pdb_path_from_pe(dll_path)
    if embedded is None:
        return None

    # Use PureWindowsPath for Windows-aware path parsing: drive/absolute checks
    # and basename extraction work correctly on all host platforms.
    pwin = PureWindowsPath(embedded)
    embedded_name: str = pwin.name or Path(embedded).name

    # Block network/UNC paths during auto-discovery
    if not allow_network and _is_network_path(embedded):
        log.debug(
            "locate_pdb: skipping network path %s (use --pdb-path to override)",
            embedded,
        )
        # Still try the filename-only fallback (always local)
        local = dll_path.parent / embedded_name
        if local.is_file():
            return local
        return None

    # Security: only trust absolute Windows paths (drive-letter or rooted) if
    # they share the same drive/root as the DLL itself.  Use PureWindowsPath so
    # that "C:\build\foo.pdb" is recognised as absolute on POSIX hosts too.
    if pwin.drive or pwin.is_absolute():
        result = _resolve_absolute_pdb_path(pwin, dll_path, embedded_name, embedded)
        if result is not None:
            return result
    else:
        result = _resolve_relative_pdb_path(pwin, dll_path, embedded_name)
        if result is not None:
            return result

    # Fall back to same filename in the DLL's directory (always local, no traversal)
    local = dll_path.parent / embedded_name
    if local.is_file():
        return local
    return None


def locate_pdb(
    dll_path: Path,
    *,
    pdb_path_override: Path | None = None,
    allow_network: bool = False,
) -> Path | None:
    """Find the PDB file for a PE binary.

    Search order:
    1. Explicit ``pdb_path_override`` (from --pdb-path CLI flag)
    2. PDB path embedded in PE debug directory (RSDS/NB10 CodeView entry)
    3. Same directory as the DLL, with ``.pdb`` extension

    When *allow_network* is False (default), any candidate whose path looks
    like a UNC/network share (``\\\\server\\...``) is silently skipped during
    auto-discovery.  An explicit *pdb_path_override* is always honoured
    regardless of *allow_network*.

    Returns the path if found (and the file exists), otherwise ``None``.
    """
    if pdb_path_override is not None:
        if pdb_path_override.is_file():
            return pdb_path_override
        log.warning(
            "locate_pdb: explicit pdb_path does not exist: %s", pdb_path_override
        )
        return None

    found = _resolve_embedded_pdb(dll_path, allow_network)
    if found is not None:
        return found

    # Fallback: same name with .pdb extension
    stem_pdb = dll_path.with_suffix(".pdb")
    if stem_pdb.is_file():
        return stem_pdb

    return None


def _parse_codeview_raw(raw: bytes) -> str | None:
    """Extract PDB path from raw CodeView debug data bytes."""
    if raw[:4] == _RSDS_SIG and len(raw) >= 24:
        # RSDS: 4 (sig) + 16 (GUID) + 4 (age) + filename
        pdb_name = raw[24:].split(b"\x00", 1)[0]
        return pdb_name.decode("utf-8", errors="replace")
    if raw[:4] == _NB10_SIG and len(raw) >= 16:
        # NB10: 4 (sig) + 4 (offset) + 4 (timestamp) + 4 (age) + filename
        pdb_name = raw[16:].split(b"\x00", 1)[0]
        return pdb_name.decode("utf-8", errors="replace")
    return None


def _parse_codeview_entry(data: object) -> str | None:
    """Extract PDB path from a pefile-parsed CodeView structure."""
    if not hasattr(data, "CvSignature"):
        return None
    sig_bytes = struct.pack("<I", data.CvSignature)
    if sig_bytes in (_RSDS_SIG, _NB10_SIG):
        if hasattr(data, "PdbFileName"):
            fname = data.PdbFileName
            if isinstance(fname, bytes):
                return fname.rstrip(b"\x00").decode("utf-8", errors="replace")
            return str(fname)
    return None


def _extract_pdb_path_from_pe(dll_path: Path) -> str | None:
    """Extract the PDB path string from a PE binary's debug directory.

    Parses IMAGE_DEBUG_DIRECTORY entries for IMAGE_DEBUG_TYPE_CODEVIEW,
    then reads the RSDS or NB10 CodeView header to get the PDB filename.
    """
    try:
        pe = pefile.PE(str(dll_path), fast_load=True)
    except Exception:  # noqa: BLE001
        return None

    try:
        pe.parse_data_directories(
            directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DEBUG"],
            ]
        )

        if not hasattr(pe, "DIRECTORY_ENTRY_DEBUG"):
            return None

        for dbg in pe.DIRECTORY_ENTRY_DEBUG:
            if dbg.struct.Type != _DEBUG_TYPE_CODEVIEW:
                continue

            data = dbg.entry
            if data is None:
                # Fall back to raw data via AddressOfRawData (RVA).
                size = dbg.struct.SizeOfData
                if size and size <= _MAX_CODEVIEW_SIZE and dbg.struct.AddressOfRawData:
                    raw: bytes = pe.get_data(dbg.struct.AddressOfRawData, size)
                    result = _parse_codeview_raw(raw)
                    if result is not None:
                        return result
                continue

            # pefile parses the CodeView data into a named structure
            result = _parse_codeview_entry(data)
            if result is not None:
                return result

        return None

    except Exception as exc:  # noqa: BLE001
        log.debug("_extract_pdb_path_from_pe: %s: %s", dll_path, exc)
        return None
    finally:
        pe.close()

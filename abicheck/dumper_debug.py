# SPDX-License-Identifier: Apache-2.0
"""Debug-format resolution for the ELF dump path.

Split out of ``dumper.py`` (which sits at the file-size cap) to keep the
kernel-binary heuristic and the DWARF/BTF/CTF selection logic in one coherent
place. ``dumper`` re-imports both names, so ``abicheck.dumper._is_kernel_binary``
and ``abicheck.dumper._resolve_debug_metadata`` remain valid patch targets.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_unified import DwarfSession

log = logging.getLogger(__name__)


def _is_kernel_binary(path: Path) -> bool:
    """Heuristic: is this a kernel binary (vmlinux, *.ko, *.ko.xz, *.ko.zst)?"""
    name = path.name
    if name == "vmlinux":
        return True
    suffixes = path.suffixes  # e.g. ['.ko', '.xz']
    suffix_str = "".join(suffixes)
    if suffix_str in (".ko", ".ko.xz", ".ko.zst", ".ko.gz"):
        return True
    # Check for .modinfo section (kernel module indicator)
    try:
        from elftools.elf.elffile import ELFFile
        with open(path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            return elf.get_section_by_name(".modinfo") is not None  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001
        return False


def _resolve_debug_metadata(
    so_path: Path,
    debug_format: str | None,
    *,
    _session_out: list[DwarfSession] | None = None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Resolve debug metadata using the specified or auto-detected format.

    Returns (dwarf_meta, dwarf_adv) — the same types as parse_dwarf().
    BTF/CTF data is converted to DwarfMetadata for checker compatibility.

    ``_session_out`` (internal): when a list is supplied and the resolved
    format is real DWARF, the still-open :class:`DwarfSession` is appended to
    it so a subsequent snapshot build can reuse the same open ``DWARFInfo``
    (F5b: avoid re-parsing every DIE). The caller must close it. BTF/CTF and
    no-debug paths leave the list untouched (session stays ``None``).
    """
    from .dwarf_advanced import AdvancedDwarfMetadata

    if debug_format == "btf":
        from .btf_metadata import parse_btf_metadata
        btf = parse_btf_metadata(so_path)
        if not btf.has_btf:
            log.warning("BTF requested but no .BTF section in %s", so_path)
        return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "ctf":
        from .ctf_metadata import parse_ctf_metadata
        ctf = parse_ctf_metadata(so_path)
        if not ctf.has_ctf:
            log.warning("CTF requested but no .ctf section in %s", so_path)
        return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "dwarf":
        from .dwarf_unified import parse_dwarf
        return parse_dwarf(so_path, _session_out=_session_out)

    if debug_format is not None:
        raise ValueError(
            f"Invalid debug_format {debug_format!r}; expected 'dwarf', 'btf', or 'ctf'."
        )

    # Auto-detect: kernel binaries prefer BTF, userspace prefers DWARF
    from .btf_metadata import has_btf_section, parse_btf_metadata
    from .ctf_metadata import has_ctf_section, parse_ctf_metadata
    from .dwarf_unified import parse_dwarf

    is_kernel = _is_kernel_binary(so_path)

    if is_kernel:
        # BTF > DWARF > CTF for kernel binaries
        if has_btf_section(so_path):
            btf = parse_btf_metadata(so_path)
            if btf.has_btf:
                log.info("Using BTF debug info from %s (kernel binary)", so_path)
                return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # DWARF > BTF > CTF for userspace (or kernel fallback)
    dwarf_meta, dwarf_adv = parse_dwarf(so_path, _session_out=_session_out)
    if dwarf_meta.has_dwarf:
        return dwarf_meta, dwarf_adv

    # Fallback to BTF if DWARF not available
    if has_btf_section(so_path):
        btf = parse_btf_metadata(so_path)
        if btf.has_btf:
            log.info("No DWARF, falling back to BTF in %s", so_path)
            return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # Fallback to CTF
    if has_ctf_section(so_path):
        ctf = parse_ctf_metadata(so_path)
        if ctf.has_ctf:
            log.info("No DWARF/BTF, falling back to CTF in %s", so_path)
            return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # No debug info at all — return empty DWARF metadata
    return dwarf_meta, dwarf_adv

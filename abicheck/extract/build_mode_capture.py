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

"""Capture a snapshot's :class:`~abicheck.model.build_mode_facts.BuildMode`
at dump time.

:func:`abicheck.build_mode.build_mode_from_signals` takes the compiler's
``DW_AT_producer``, the ELF ``.comment`` strings and the CU's
``DW_AT_language``; this module reads those raw signals from the binary
(:func:`read_elf_build_signals`) and records the result on the snapshot
(:func:`capture_elf_build_mode`), so ``compiler_family``, ``language_std`` and
the provenance strings are no longer always ``UNKNOWN`` on a fresh dump.

Where the signals come from, per format:

* **ELF** -- the image's own DWARF and ``.comment``; when the image carries no
  ``DW_AT_producer`` (a stripped release build), the detached debug file the
  dump resolved (``debug_info_path``: ``--debug-info``, a build-id tree or a
  path mirror) is read for it instead.
* **Mach-O** -- a linked image keeps no DWARF of its own, so the producer is
  read from its dSYM companion (``debug_info_path``, else the adjacent
  ``<image>.dSYM`` bundle) or from the ``__DWARF`` segment of an object file
  (:mod:`abicheck.extract.macho_build_signals`). ``LC_BUILD_VERSION`` tool
  entries are deliberately not used: ``ld`` writes them for the tools *it*
  knows about, not for the compiler that produced every object, so they do
  not identify the compiler family.
* **PE** -- nothing is captured from the image. The Rich header lists the
  MSVC tools behind every linked object, including the MSVC CRT objects a
  clang-cl build links too, so it does not identify the compiler that built
  the library; PDB ``S_COMPILE3`` records are outside this module's reach.
  ``build_mode`` stays ``None`` (unknown) rather than a guessed family.

Only CU *top* DIEs are read -- never the DIE trees -- so the cost is one DIE
per compilation unit. A read that fails leaves the snapshot's ``build_mode``
untouched (``None`` = unknown, the same value a pre-v5 stored baseline
carries), never a fabricated value.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from ..model import AbiSnapshot
from ..model.stack_protector import stack_protector_census
from .dwarf_producer import best_cu_from, decode_attr, read_cu_units

log = logging.getLogger(__name__)

__all__ = [
    "ElfBuildSignals",
    "capture_build_mode",
    "capture_elf_build_mode",
    "read_elf_build_signals",
]


@dataclass(frozen=True)
class ElfBuildSignals:
    """Raw build signals read from one ELF image (``None`` = not present)."""

    producer: str | None = None
    dwarf_language: int | None = None
    comment: str | None = None
    #: Every compiled unit's stack-protector setting
    #: (``model.stack_protector``); ``None`` when no unit was read.
    stack_protector: dict[str, int] | None = None


_NO_DWARF: tuple[str | None, int | None, dict[str, int] | None] = (None, None, None)

#: ELF symbol types that are code a compilation unit compiled. pyelftools
#: names type 10 (``STT_GNU_IFUNC``) ``STT_LOOS`` in some releases.
_CODE_SYMBOL_TYPES = frozenset({"STT_FUNC", "STT_GNU_IFUNC", "STT_LOOS"})


def _exported_code_addresses(elf: object) -> list[int] | None:
    """Addresses of the functions the image defines in ``.dynsym``; ``None``
    when it has no readable dynamic symbol table (coverage then unknown)."""
    section = elf.get_section_by_name(".dynsym")  # type: ignore[attr-defined]
    if section is None or section.header.sh_type != "SHT_DYNSYM":
        return None
    return [
        sym["st_value"]
        for sym in section.iter_symbols()
        if sym["st_info"]["type"] in _CODE_SYMBOL_TYPES
        and sym["st_shndx"] != "SHN_UNDEF"
        and sym["st_value"]
    ]


def _code_outside_units(
    exported: list[int] | None, ranges: list[tuple[int, int]] | None
) -> bool:
    """Whether some exported function lies in no unit's code range, or that
    cannot be established (no symbol table, unreadable ranges)."""
    if exported is None or ranges is None:
        return True
    merged: list[list[int]] = []
    for lo, hi in sorted(r for r in ranges if r[1] > r[0]):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    starts = [lo for lo, _hi in merged]
    for address in exported:
        i = bisect_right(starts, address) - 1
        if i < 0 or address >= merged[i][1]:
            return True
    return False


def _read_producer(
    elf: object, exported: list[int] | None = None
) -> tuple[str | None, int | None, dict[str, int] | None]:
    """The best CU's producer and language, and every unit's protector.
    *exported* is the image's exported function addresses (its own when the
    DWARF is detached), checked against the units' code ranges."""
    from ..dwarf_utils import has_real_dwarf_info

    if not has_real_dwarf_info(elf):
        return _NO_DWARF
    read = read_cu_units(elf.get_dwarf_info())  # type: ignore[attr-defined]
    producer, lang = best_cu_from(read.units)
    census = stack_protector_census(
        read.units,
        code_outside_units=_code_outside_units(exported, read.code_ranges),
    )
    return producer, lang, census


def _read_comment(elf: object) -> str | None:
    section = elf.get_section_by_name(".comment")  # type: ignore[attr-defined]
    if section is None:
        return None
    # dict.fromkeys de-duplicates while keeping first-seen order.
    parts = dict.fromkeys(
        text
        for chunk in section.data().split(b"\0")
        if (text := decode_attr(chunk)) is not None
    )
    return "\n".join(parts) or None


def _read_detached_producer(
    debug_path: Path, exported: list[int] | None = None
) -> tuple[str | None, int | None, dict[str, int] | None]:
    """The producer/language/protector census of a detached ELF debug file
    (all ``None`` when it cannot be read or carries no CU attributes)."""
    from elftools.elf.elffile import ELFFile

    try:
        with debug_path.open("rb") as fh:
            return _read_producer(ELFFile(fh), exported)  # type: ignore[no-untyped-call]
    except Exception as exc:  # noqa: BLE001 - detached debug is optional evidence
        log.debug("build_mode_capture: cannot read %s: %s", debug_path, exc)
        return _NO_DWARF


def read_elf_build_signals(
    path: Path, *, read_dwarf: bool = True, debug_path: Path | None = None
) -> ElfBuildSignals | None:
    """Read ``DW_AT_producer``/``DW_AT_language`` (best CU) and ``.comment``.

    ``read_dwarf=False`` (shallow ``symbols_only``/``debug_presence_only``
    dumps) skips the DWARF CU read entirely -- ``get_dwarf_info()`` loads
    every debug section -- and records only ``.comment``.

    *debug_path* is the detached debug file resolved for *path*; it is read
    only when the image itself yields no producer/language and DWARF reading
    is allowed (``read_dwarf=False`` reads neither file's DWARF).

    Returns ``None`` when the file cannot be read as ELF at all.
    """
    from elftools.elf.elffile import ELFFile

    try:
        with path.open("rb") as fh:
            elf = ELFFile(fh)  # type: ignore[no-untyped-call]
            comment = _read_comment(elf)
            producer, lang, census = _NO_DWARF
            exported: list[int] | None = None
            try:
                if read_dwarf:
                    exported = _exported_code_addresses(elf)
                    producer, lang, census = _read_producer(elf, exported)
            except Exception as exc:  # noqa: BLE001 - DWARF is optional evidence
                log.debug("build_mode_capture: DWARF read failed: %s", exc)
                producer, lang, census = _NO_DWARF
    except Exception as exc:  # noqa: BLE001
        log.debug("build_mode_capture: cannot read %s: %s", path, exc)
        return None
    if read_dwarf and producer is None and lang is None and debug_path is not None:
        producer, lang, census = _read_detached_producer(debug_path, exported)
    return ElfBuildSignals(
        producer=producer,
        dwarf_language=lang,
        comment=comment,
        stack_protector=census,
    )


def capture_build_mode(
    snapshot: AbiSnapshot,
    *,
    read_dwarf: bool = True,
    debug_info_path: Path | None = None,
) -> AbiSnapshot:
    """Populate ``snapshot.build_mode`` from whatever its format reliably
    records (see the module docstring); a PE snapshot is left alone."""
    if snapshot.platform == "elf":
        return capture_elf_build_mode(
            snapshot, read_dwarf=read_dwarf, debug_info_path=debug_info_path
        )
    if snapshot.platform == "macho":
        return _capture_macho_build_mode(
            snapshot, read_dwarf=read_dwarf, debug_info_path=debug_info_path
        )
    return snapshot


def _mangled_names(snapshot: AbiSnapshot) -> list[str]:
    from ..model.stdlib_evidence import stdlib_evidence_symbols

    return stdlib_evidence_symbols(snapshot)


def _record(snapshot: AbiSnapshot, signals: ElfBuildSignals) -> AbiSnapshot:
    from ..build_mode import build_mode_from_signals

    if signals.stack_protector is not None:
        snapshot.cu_stack_protector = signals.stack_protector
    mangled = _mangled_names(snapshot)
    if (
        signals.producer is None
        and signals.comment is None
        and signals.dwarf_language is None
        and not mangled
    ):
        return snapshot
    snapshot.build_mode = build_mode_from_signals(
        raw_producer=signals.producer,
        raw_comment=signals.comment,
        dwarf_language=signals.dwarf_language,
        mangled_symbols=mangled,
    )
    return snapshot


def _capture_macho_build_mode(
    snapshot: AbiSnapshot, *, read_dwarf: bool, debug_info_path: Path | None
) -> AbiSnapshot:
    if snapshot.build_mode is not None or not snapshot.source_path:
        return snapshot
    producer: str | None = None
    lang: int | None = None
    if read_dwarf:
        from .macho_build_signals import read_macho_producer

        producer, lang = read_macho_producer(
            Path(snapshot.source_path), debug_path=debug_info_path
        )
    return _record(snapshot, ElfBuildSignals(producer=producer, dwarf_language=lang))


def capture_elf_build_mode(
    snapshot: AbiSnapshot,
    *,
    read_dwarf: bool = True,
    debug_info_path: Path | None = None,
) -> AbiSnapshot:
    """Populate ``snapshot.build_mode`` from its ELF image's own signals, or
    from its detached debug file (*debug_info_path*) when the image carries
    no ``DW_AT_producer``.

    Left alone when the snapshot is not ELF, already carries a build mode, has
    no readable ``source_path``, or the image yields no signal at all.
    """
    if snapshot.platform != "elf" or snapshot.build_mode is not None:
        return snapshot
    if not snapshot.source_path:
        return snapshot
    signals = read_elf_build_signals(
        Path(snapshot.source_path), read_dwarf=read_dwarf, debug_path=debug_info_path
    )
    if signals is None:
        return snapshot
    return _record(snapshot, signals)

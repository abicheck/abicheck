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

"""Capture an ELF snapshot's :class:`~abicheck.model.build_mode_facts.BuildMode`
at dump time.

:func:`abicheck.build_mode.build_mode_from_signals` takes the compiler's
``DW_AT_producer``, the ELF ``.comment`` strings and the CU's
``DW_AT_language``; this module reads those raw signals from the binary
(:func:`read_elf_build_signals`) and records the result on the snapshot
(:func:`capture_elf_build_mode`), so ``compiler_family``, ``language_std`` and
the provenance strings are no longer always ``UNKNOWN`` on a fresh dump.

Only CU *top* DIEs are read -- never the DIE trees -- so the cost is one DIE
per compilation unit. A read that fails leaves the snapshot's ``build_mode``
untouched (``None`` = unknown, the same value a pre-v5 stored baseline
carries), never a fabricated value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..model import AbiSnapshot

log = logging.getLogger(__name__)

__all__ = ["ElfBuildSignals", "capture_elf_build_mode", "read_elf_build_signals"]


@dataclass(frozen=True)
class ElfBuildSignals:
    """Raw build signals read from one ELF image (``None`` = not present)."""

    producer: str | None = None
    dwarf_language: int | None = None
    comment: str | None = None


def _decode(raw: object) -> str | None:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    text = text.strip()
    return text or None


def _cu_rank(producer: str | None, lang: int | None) -> int:
    """Lower is better: a recognised C++ CU wins over a C or assembler helper
    CU linked into the same image."""
    from ..build_mode import detect_compiler_family, detect_cxx_standard
    from ..model.build_mode_facts import CompilerFamily, CxxStandard

    known = detect_compiler_family(producer, None)[0] is not CompilerFamily.UNKNOWN
    std = detect_cxx_standard(lang)
    if std not in (CxxStandard.UNKNOWN, CxxStandard.C):
        return 0 if known else 2
    if std is CxxStandard.C:
        return 1 if known else 3
    return 4 if known else 5


def _read_producer(elf: object) -> tuple[str | None, int | None]:
    from ..dwarf_utils import has_real_dwarf_info

    if not has_real_dwarf_info(elf):
        return None, None
    dwarf = elf.get_dwarf_info()  # type: ignore[attr-defined]
    best: tuple[int, str | None, int | None] | None = None
    for cu in dwarf.iter_CUs():
        try:
            top = cu.get_top_DIE()
        except Exception as exc:  # noqa: BLE001 - one bad CU must not hide the rest
            log.debug("build_mode_capture: skipping CU: %s", exc)
            continue
        prod_attr = top.attributes.get("DW_AT_producer")
        lang_attr = top.attributes.get("DW_AT_language")
        producer = _decode(prod_attr.value) if prod_attr is not None else None
        lang = lang_attr.value if lang_attr is not None else None
        lang = lang if isinstance(lang, int) else None
        if producer is None and lang is None:
            continue
        rank = _cu_rank(producer, lang)
        if best is None or rank < best[0]:
            best = (rank, producer, lang)
            if rank == 0:
                break
    if best is None:
        return None, None
    return best[1], best[2]


def _read_comment(elf: object) -> str | None:
    section = elf.get_section_by_name(".comment")  # type: ignore[attr-defined]
    if section is None:
        return None
    # dict.fromkeys de-duplicates while keeping first-seen order.
    parts = dict.fromkeys(
        text
        for chunk in section.data().split(b"\0")
        if (text := _decode(chunk)) is not None
    )
    return "\n".join(parts) or None


def read_elf_build_signals(
    path: Path, *, read_dwarf: bool = True
) -> ElfBuildSignals | None:
    """Read ``DW_AT_producer``/``DW_AT_language`` (best CU) and ``.comment``.

    ``read_dwarf=False`` (shallow ``symbols_only``/``debug_presence_only``
    dumps) skips the DWARF CU read entirely -- ``get_dwarf_info()`` loads
    every debug section -- and records only ``.comment``.

    Returns ``None`` when the file cannot be read as ELF at all.
    """
    from elftools.elf.elffile import ELFFile

    try:
        with path.open("rb") as fh:
            elf = ELFFile(fh)  # type: ignore[no-untyped-call]
            comment = _read_comment(elf)
            producer: str | None = None
            lang: int | None = None
            try:
                if read_dwarf:
                    producer, lang = _read_producer(elf)
            except Exception as exc:  # noqa: BLE001 - DWARF is optional evidence
                log.debug("build_mode_capture: DWARF read failed: %s", exc)
                producer, lang = None, None
    except Exception as exc:  # noqa: BLE001
        log.debug("build_mode_capture: cannot read %s: %s", path, exc)
        return None
    return ElfBuildSignals(producer=producer, dwarf_language=lang, comment=comment)


def capture_elf_build_mode(
    snapshot: AbiSnapshot, *, read_dwarf: bool = True
) -> AbiSnapshot:
    """Populate ``snapshot.build_mode`` from its ELF image's own signals.

    Left alone when the snapshot is not ELF, already carries a build mode, has
    no readable ``source_path``, or the image yields no signal at all.
    """
    if snapshot.platform != "elf" or snapshot.build_mode is not None:
        return snapshot
    if not snapshot.source_path:
        return snapshot
    signals = read_elf_build_signals(Path(snapshot.source_path), read_dwarf=read_dwarf)
    if signals is None:
        return snapshot
    from ..build_mode import build_mode_from_signals

    mangled = [f.mangled for f in snapshot.declarations.functions if f.mangled]
    mangled += [v.mangled for v in snapshot.declarations.variables if v.mangled]
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

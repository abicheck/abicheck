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

"""Pick the compiler producer of a DWARF image: the ``DW_AT_producer`` and
``DW_AT_language`` of its best-ranked compilation unit.

A leaf shared by the ELF reader (:mod:`abicheck.extract.build_mode_capture`)
and the Mach-O reader (:mod:`abicheck.extract.macho_build_signals`); both
hand it a pyelftools ``DWARFInfo``. Only CU *top* DIEs are read.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

__all__ = ["best_cu_producer", "decode_attr"]


def decode_attr(raw: object) -> str | None:
    """A DWARF/section string attribute as stripped text (``None`` if empty)."""
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


def best_cu_producer(dwarf: object) -> tuple[str | None, int | None]:
    """``(DW_AT_producer, DW_AT_language)`` of the best-ranked CU in *dwarf*
    (a pyelftools ``DWARFInfo``), reading CU top DIEs only."""
    best: tuple[int, str | None, int | None] | None = None
    for cu in dwarf.iter_CUs():  # type: ignore[attr-defined]
        try:
            top = cu.get_top_DIE()
        except Exception as exc:  # noqa: BLE001 - one bad CU must not hide the rest
            log.debug("dwarf_producer: skipping CU: %s", exc)
            continue
        prod_attr = top.attributes.get("DW_AT_producer")
        lang_attr = top.attributes.get("DW_AT_language")
        producer = decode_attr(prod_attr.value) if prod_attr is not None else None
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

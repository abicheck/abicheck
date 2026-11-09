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
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "CuUnits",
    "best_cu_from",
    "best_cu_producer",
    "cu_producers",
    "decode_attr",
    "read_cu_units",
]


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


#: Unit types that hold no compiled code (a ``-fdebug-types-section`` type unit).
_CODELESS_UNIT_TAGS = frozenset({"DW_TAG_type_unit"})

#: DWARF forms of ``DW_AT_high_pc`` that are an offset from ``DW_AT_low_pc``.
_HIGH_PC_OFFSET_FORMS = frozenset(
    {
        "DW_FORM_data1",
        "DW_FORM_data2",
        "DW_FORM_data4",
        "DW_FORM_data8",
        "DW_FORM_udata",
    }
)


@dataclass(frozen=True)
class CuUnits:
    """Every CU's ``(DW_AT_producer, DW_AT_language)`` and the code address
    ranges the units describe. A CU whose top DIE cannot be read is recorded
    as ``(None, None)`` (its producer is unknown, never omitted).
    ``code_ranges`` is ``None`` when some unit's ranges could not be read."""

    units: list[tuple[str | None, int | None]]
    code_ranges: list[tuple[int, int]] | None


def _unit_ranges(cu: Any, top: Any, range_lists: Any) -> list[tuple[int, int]]:
    """``[low, high)`` code ranges of one CU's top DIE (empty: no code).
    Raises when a range list it names cannot be read."""
    attrs = top.attributes
    low_attr = attrs.get("DW_AT_low_pc")
    base = low_attr.value if low_attr is not None else 0
    if "DW_AT_ranges" in attrs:
        if range_lists is None:
            raise ValueError("DW_AT_ranges without a range-list section")
        out = []
        for entry in range_lists.get_range_list_at_offset(
            attrs["DW_AT_ranges"].value, cu=cu
        ):
            if hasattr(entry, "base_address"):
                base = entry.base_address
                continue
            shift = 0 if entry.is_absolute else base
            out.append((entry.begin_offset + shift, entry.end_offset + shift))
        return out
    high_attr = attrs.get("DW_AT_high_pc")
    if low_attr is None or high_attr is None:
        return []
    high = high_attr.value
    if high_attr.form in _HIGH_PC_OFFSET_FORMS:
        high += base
    return [(base, high)]


def read_cu_units(dwarf: object) -> CuUnits:
    """Read every CU top DIE of *dwarf* (a pyelftools ``DWARFInfo``) once:
    its producer and language, and the code ranges it covers."""
    units: list[tuple[str | None, int | None]] = []
    ranges: list[tuple[int, int]] | None = []
    try:
        range_lists = dwarf.range_lists()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - optional evidence
        log.debug("dwarf_producer: range lists unreadable: %s", exc)
        range_lists, ranges = None, None
    for cu in dwarf.iter_CUs():  # type: ignore[attr-defined]
        try:
            top = cu.get_top_DIE()
        except Exception as exc:  # noqa: BLE001 - one bad CU must not hide the rest
            log.debug("dwarf_producer: unreadable CU: %s", exc)
            units.append((None, None))
            ranges = None
            continue
        if getattr(top, "tag", None) in _CODELESS_UNIT_TAGS:
            continue
        prod_attr = top.attributes.get("DW_AT_producer")
        lang_attr = top.attributes.get("DW_AT_language")
        producer = decode_attr(prod_attr.value) if prod_attr is not None else None
        lang = lang_attr.value if lang_attr is not None else None
        units.append((producer, lang if isinstance(lang, int) else None))
        if ranges is not None:
            try:
                ranges.extend(_unit_ranges(cu, top, range_lists))
            except Exception as exc:  # noqa: BLE001 - coverage then unknown
                log.debug("dwarf_producer: CU ranges unreadable: %s", exc)
                ranges = None
    return CuUnits(units=units, code_ranges=ranges)


def cu_producers(dwarf: object) -> list[tuple[str | None, int | None]]:
    """``(DW_AT_producer, DW_AT_language)`` of every CU in *dwarf*, in unit
    order (see :func:`read_cu_units`)."""
    return read_cu_units(dwarf).units


def best_cu_from(
    units: list[tuple[str | None, int | None]],
) -> tuple[str | None, int | None]:
    """The best-ranked ``(producer, language)`` of *units* (see
    :func:`_cu_rank`); ``(None, None)`` when none carries either."""
    best: tuple[int, str | None, int | None] | None = None
    for producer, lang in units:
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


def best_cu_producer(dwarf: object) -> tuple[str | None, int | None]:
    """``(DW_AT_producer, DW_AT_language)`` of the best-ranked CU in *dwarf*
    (a pyelftools ``DWARFInfo``), reading CU top DIEs only."""
    return best_cu_from(cu_producers(dwarf))

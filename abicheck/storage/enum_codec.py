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

"""Encode ELF/PE/Mach-O metadata enums as plain strings for JSON.

Split out of ``serialization.snapshot_to_dict()`` into a ``storage``-owned
leaf module for line-count headroom (``serialization.py`` is at this
repo's 2000-line AI-readiness hard cap) — no behavior change from the
inline version this replaces. ``dataclasses.asdict()`` leaves each of
these fields holding the raw enum member; ``json.dump()`` rejects that
directly. Takes no dependency beyond stdlib, fitting ADR-061's
``storage`` layer just as cleanly as ``fact_codec.py``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["encode_platform_enums"]


def encode_platform_enums(d: dict[str, Any]) -> None:
    """In-place: ElfMetadata/PeMetadata/MachoMetadata enum members -> strings."""
    if d.get("elf"):
        elf = d["elf"]
        for sym in elf.get("symbols", []):
            sym["binding"] = (
                sym["binding"]
                if isinstance(sym["binding"], str)
                else sym["binding"].value
            )
            sym["sym_type"] = (
                sym["sym_type"]
                if isinstance(sym["sym_type"], str)
                else sym["sym_type"].value
            )
        for imp in elf.get("imports", []):
            imp["binding"] = (
                imp["binding"]
                if isinstance(imp["binding"], str)
                else imp["binding"].value
            )
            imp["sym_type"] = (
                imp["sym_type"]
                if isinstance(imp["sym_type"], str)
                else imp["sym_type"].value
            )

    if d.get("pe"):
        for exp in d["pe"].get("exports", []):
            exp["sym_type"] = (
                exp["sym_type"]
                if isinstance(exp["sym_type"], str)
                else exp["sym_type"].value
            )

    if d.get("macho"):
        for exp in d["macho"].get("exports", []):
            exp["sym_type"] = (
                exp["sym_type"]
                if isinstance(exp["sym_type"], str)
                else exp["sym_type"].value
            )

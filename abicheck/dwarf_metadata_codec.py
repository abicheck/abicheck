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

"""``DwarfMetadata``/``AdvancedDwarfMetadata`` dict decoding for ``serialization.py``.

Split out into a leaf sibling module rather than inlined in
``serialization.py``: that module is already at (or over) this repo's
2000-line AI-readiness hard cap and carries a no-growth adoption-debt
baseline (``architecture/debt.yaml``), so a real, functional addition —
here, decoding the v28 debug-evidence provenance fields
(``evidence_source``/``evidence_state``/``cu_total``/``cu_failed``) — has
to move responsibility out rather than raise that baseline. This module
is deliberately *not* placed under ``storage/`` the way ``fact_codec.py``
was: ``DwarfMetadata``/``AdvancedDwarfMetadata`` are owned by the
``extract`` layer (``architecture/modules.yaml``), and ``storage`` may
only import ``model`` — a ``storage``-owned module importing them would
be a real, checked dependency-direction violation
(``scripts/check_architecture.py``'s ``dependency-direction`` check), not
merely a style preference. Staying an unclassified flat root module
(mirroring ``serialization.py``'s own status) keeps this decode logic
free to depend on ``extract``, matching what it actually decodes.
"""

from __future__ import annotations

from typing import Any


def legacy_dwarf_evidence_kwargs(d: dict[str, Any]) -> dict[str, Any]:
    """Shared ``evidence_state``/``cu_total``/``cu_failed`` defaults (v28).

    A legacy (pre-v28) DWARF block carries no debug-evidence provenance at
    all, so this fails closed rather than claiming a real parse: a
    ``has_dwarf`` block degrades to ``"presence_only"`` (v28's own cheapest
    tier, not ``"parsed"``), and no block at all degrades to
    ``"not_available"``. Spread into both :class:`DwarfMetadata` and
    :class:`AdvancedDwarfMetadata` construction below.
    """
    return {
        "evidence_state": d.get(
            "evidence_state",
            "presence_only" if d.get("has_dwarf", False) else "not_available",
        ),
        "cu_total": d.get("cu_total", 0),
        "cu_failed": d.get("cu_failed", 0),
    }


def decode_dwarf_metadata(d: dict[str, Any]) -> Any:
    """Decode a serialized ``DwarfMetadata`` block, including v28 evidence."""
    from .dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout

    structs = {
        name: StructLayout(
            name=s.get("name", name),
            byte_size=s.get("byte_size", 0),
            alignment=s.get("alignment", 0),
            fields=[
                FieldInfo(
                    name=f.get("name", ""),
                    type_name=f.get("type_name", "unknown"),
                    byte_offset=f.get("byte_offset", 0),
                    byte_size=f.get("byte_size", 0),
                    bit_offset=f.get("bit_offset", 0),
                    bit_size=f.get("bit_size", 0),
                )
                for f in s.get("fields", [])
            ],
            is_union=s.get("is_union", False),
        )
        for name, s in d.get("structs", {}).items()
    }
    enums = {
        name: EnumInfo(
            name=e.get("name", name),
            underlying_byte_size=e.get("underlying_byte_size", 0),
            members=e.get("members", {}),
        )
        for name, e in d.get("enums", {}).items()
    }
    return DwarfMetadata(
        structs=structs,
        enums=enums,
        base_types={k: int(v) for k, v in d.get("base_types", {}).items()},
        has_dwarf=d.get("has_dwarf", False),
        evidence_source=d.get("evidence_source", "unknown"),
        **legacy_dwarf_evidence_kwargs(d),
    )


def decode_dwarf_advanced_metadata(d: dict[str, Any]) -> Any:
    """Decode a serialized ``AdvancedDwarfMetadata`` block (v28 evidence too)."""
    from .dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo

    tc = d.get("toolchain", {})
    toolchain = ToolchainInfo(
        producer_string=tc.get("producer_string", ""),
        compiler=tc.get("compiler", ""),
        version=tc.get("version", ""),
        abi_flags=set(tc.get("abi_flags", [])),
        vector_abi_flags=set(tc.get("vector_abi_flags", [])),
    )
    return AdvancedDwarfMetadata(
        has_dwarf=d.get("has_dwarf", False),
        **legacy_dwarf_evidence_kwargs(d),
        target_arch=d.get("target_arch", ""),
        toolchain=toolchain,
        calling_conventions=d.get("calling_conventions", {}),
        value_abi_traits=d.get("value_abi_traits", {}),
        return_value_sizes=d.get("return_value_sizes", {}),
        return_memory_classified=set(d.get("return_memory_classified", [])),
        packed_structs=set(d.get("packed_structs", [])),
        all_struct_names=set(d.get("all_struct_names", [])),
        frame_registers=d.get("frame_registers", {}),
        callee_saved_regs={
            k: frozenset(v) for k, v in d.get("callee_saved_regs", {}).items()
        },
    )

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

"""Unified TypeMetadataSource protocol for all debug format readers.

All debug format metadata classes (DwarfMetadata, BtfMetadata, CtfMetadata)
implement this protocol so the checker's detectors can consume type
information without knowing the source format.

See ADR-007 for design rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .dwarf_metadata import EnumInfo, StructLayout


@dataclass
class FuncProto:
    """Function prototype extracted from debug info (BTF/CTF)."""
    name: str
    return_type: str
    params: list[tuple[str, str]]  # [(param_name, param_type), ...]
    # BTF_KIND_FUNC linkage (the record's vlen field): 0 = static,
    # 1 = global, 2 = extern. None when the source format doesn't encode
    # linkage (CTF). Legacy BTF encoders wrote 0 for every function, so a 0
    # is only trustworthy when the same blob also carries non-zero linkages
    # (see service._typeinfo_functions).
    linkage: int | None = None


def read_null_terminated_string(data: bytes, offset: int) -> str:
    """Read a null-terminated UTF-8 string from a binary section.

    Shared by BTF and CTF parsers for reading their string tables.
    """
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset)
    if end < 0:
        return data[offset:].decode("utf-8", errors="replace")
    return data[offset:end].decode("utf-8", errors="replace")


@runtime_checkable
class TypeMetadataSource(Protocol):
    """Common interface for all debug format readers.

    Implemented by: DwarfMetadata, BtfMetadata, CtfMetadata.
    The checker's detectors accept this protocol instead of a concrete class.
    """

    def get_struct_layout(self, name: str) -> StructLayout | None:
        """Look up a struct/union layout by name."""
        ...

    def get_enum_info(self, name: str) -> EnumInfo | None:
        """Look up an enum type by name."""
        ...

    @property
    def has_data(self) -> bool:
        """Whether this source has any type data available."""
        ...


def resolve_debug_metadata(
    *,
    dwarf: object | None = None,
    btf: object | None = None,
    ctf: object | None = None,
    prefer_btf: bool = False,
) -> TypeMetadataSource | None:
    """Select the best available debug metadata source.

    Priority (userspace, default):  DWARF > BTF > CTF
    Priority (kernel, prefer_btf):  BTF > DWARF > CTF

    Returns None if no source has data.
    """
    sources: list[object]
    if prefer_btf:
        sources = [btf, dwarf, ctf]
    else:
        sources = [dwarf, btf, ctf]

    for src in sources:
        if src is not None and isinstance(src, TypeMetadataSource) and src.has_data:
            return src

    return None

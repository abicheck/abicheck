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

"""CTF raw type model and name/size resolution, split out of ``ctf_metadata.py``
to keep that module under the architecture debt-no-growth ceiling (ADR-061),
and placed directly under its canonical owner package: parsing raw CTF type
data into resolved names/sizes is a "read a debug fact" responsibility
(``extract/``), not a flat-root addition.

Owns the constants and the raw ``CtfType`` record that both the low-level
parser (``_parse_types`` et al., still in ``ctf_metadata.py``) and the
resolver below agree on, plus ``_TypeResolver`` itself. ``ctf_metadata.py``
imports everything it needs from here; nothing here imports back, so there is
no cycle between the two modules.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..type_metadata import read_null_terminated_string

CTF_MAGIC = 0xCFF1
CTF_VERSION_2 = 2
CTF_VERSION_3 = 3

# CTF type kinds (encoded in ctt_info)
CTF_K_UNKNOWN = 0
CTF_K_INTEGER = 1
CTF_K_FLOAT = 2
CTF_K_POINTER = 3
CTF_K_ARRAY = 4
CTF_K_FUNCTION = 5
CTF_K_STRUCT = 6
CTF_K_UNION = 7
CTF_K_ENUM = 8
CTF_K_FORWARD = 9
CTF_K_TYPEDEF = 10
CTF_K_VOLATILE = 11
CTF_K_CONST = 12
CTF_K_RESTRICT = 13

# CTF integer encoding bits
CTF_INT_SIGNED = 0x01
CTF_INT_CHAR = 0x02
CTF_INT_BOOL = 0x04

# CTF header flags
CTF_F_COMPRESS = 0x01


@dataclass
class CtfType:
    """Raw parsed CTF type entry."""

    type_id: int
    name_off: int
    info: int
    size_or_type: int  # depends on kind
    extra: bytes = b""

    @property
    def kind(self) -> int:
        return (self.info >> 24) & 0x1F  # v3; v2 uses >> 11 & 0x1F

    @property
    def vlen(self) -> int:
        return self.info & 0xFFFF  # v3; v2 uses & 0x3FF

    @property
    def isroot(self) -> bool:
        return bool((self.info >> 31) & 1)  # v3; v2 uses >> 10 & 1


def _read_string(str_data: bytes, offset: int) -> tuple[str, bool]:
    """Read a null-terminated string from the CTF string table.

    Returns ``(name, valid)`` -- see ``read_null_terminated_string``'s own
    docstring for what ``valid=False`` means.
    """
    return read_null_terminated_string(str_data, offset)


class _TypeResolver:
    """Resolves CTF type references to names and sizes."""

    def __init__(
        self,
        types: list[CtfType],
        str_data: bytes,
        version: int,
        *,
        invalid_strings: list[bool] | None = None,
    ) -> None:
        self._types = types
        self._str = str_data
        self._version = version
        self._name_cache: dict[int, str] = {}
        self._size_cache: dict[int, int] = {}
        self._resolving_name: set[int] = set()
        self._resolving_size: set[int] = set()
        # See the matching comment in btf_type_resolver._TypeResolver.
        self._invalid_strings = invalid_strings

    def name(self, type_id: int) -> str:
        if type_id in self._name_cache:
            return self._name_cache[type_id]
        if type_id in self._resolving_name:
            return "..."
        self._resolving_name.add(type_id)
        try:
            result = self._resolve_name(type_id)
            self._name_cache[type_id] = result
            return result
        finally:
            self._resolving_name.discard(type_id)

    def size(self, type_id: int) -> int:
        if type_id in self._size_cache:
            return self._size_cache[type_id]
        if type_id in self._resolving_size:
            return 0
        self._resolving_size.add(type_id)
        try:
            result = self._resolve_size(type_id)
            self._size_cache[type_id] = result
            return result
        finally:
            self._resolving_size.discard(type_id)

    def _get(self, type_id: int) -> CtfType | None:
        if 0 <= type_id < len(self._types):
            return self._types[type_id]
        return None

    def _str_at(self, offset: int) -> str:
        name, valid = _read_string(self._str, offset)
        if self._invalid_strings is not None and not valid:
            self._invalid_strings.append(True)
        return name

    def _resolve_name_array(self, t: CtfType) -> str:
        """Return the name string for a CTF array type."""
        if self._version >= CTF_VERSION_3 and len(t.extra) >= 12:
            elem_type = struct.unpack_from("<I", t.extra, 0)[0]
            nelems = struct.unpack_from("<I", t.extra, 8)[0]
            return f"{self.name(elem_type)}[{nelems}]"
        if self._version < CTF_VERSION_3 and len(t.extra) >= 6:
            elem_type = struct.unpack_from("<H", t.extra, 0)[0]
            nelems = struct.unpack_from("<H", t.extra, 4)[0]
            return f"{self.name(elem_type)}[{nelems}]"
        return "[]"

    def _resolve_name_tagged(self, kind: int, tname: str) -> str | None:
        """Handle tagged aggregate/forward kinds; return None if not applicable."""
        if kind in (CTF_K_STRUCT, CTF_K_UNION):
            tag = "union" if kind == CTF_K_UNION else "struct"
            return tname if tname else f"<anon {tag}>"
        if kind == CTF_K_ENUM:
            return tname if tname else "<anon enum>"
        if kind == CTF_K_FORWARD:
            return tname if tname else "<fwd>"
        return None

    # Module-level qualifier map shared across all resolver instances.
    _CV_QUALIFIERS: dict[int, str] = {
        CTF_K_VOLATILE: "volatile",
        CTF_K_CONST: "const",
        CTF_K_RESTRICT: "restrict",
    }

    def _resolve_name_simple(
        self, kind: int, tname: str, size_or_type: int
    ) -> str | None:
        """Handle simple/named kinds; return None if kind is not handled here."""
        if kind == CTF_K_INTEGER:
            return tname if tname else "int"
        if kind == CTF_K_FLOAT:
            return tname if tname else "float"
        if kind == CTF_K_POINTER:
            return f"{self.name(size_or_type)} *"
        tagged = self._resolve_name_tagged(kind, tname)
        if tagged is not None:
            return tagged
        if kind == CTF_K_TYPEDEF:
            return tname if tname else self.name(size_or_type)
        if kind in self._CV_QUALIFIERS:
            return f"{self._CV_QUALIFIERS[kind]} {self.name(size_or_type)}"
        return None

    def _resolve_name(self, type_id: int) -> str:
        if type_id == 0:
            return "void"
        t = self._get(type_id)
        if t is None:
            # See the matching comment on __init__'s invalid_strings param.
            if self._invalid_strings is not None:
                self._invalid_strings.append(True)
            return f"<ctf:{type_id}>"

        kind = t.kind
        tname = self._str_at(t.name_off)

        simple = self._resolve_name_simple(kind, tname, t.size_or_type)
        if simple is not None:
            return simple
        if kind == CTF_K_ARRAY:
            return self._resolve_name_array(t)
        if kind == CTF_K_FUNCTION:
            return f"{self.name(t.size_or_type)}(...)"

        return f"<ctf_kind_{kind}:{type_id}>"

    def _resolve_size(self, type_id: int) -> int:
        if type_id == 0:
            return 0
        t = self._get(type_id)
        if t is None:
            # See the matching comment on __init__'s invalid_strings param.
            if self._invalid_strings is not None:
                self._invalid_strings.append(True)
            return 0

        kind = t.kind

        if kind in (CTF_K_STRUCT, CTF_K_UNION, CTF_K_ENUM):
            return t.size_or_type

        if kind == CTF_K_INTEGER:
            if len(t.extra) >= 4:
                enc: int = struct.unpack_from("<I", t.extra, 0)[0]
                nr_bits = enc & 0xFFFF
                return (nr_bits + 7) // 8
            return 0

        if kind == CTF_K_FLOAT:
            if len(t.extra) >= 4:
                enc_f: int = struct.unpack_from("<I", t.extra, 0)[0]
                nr_bits = enc_f & 0xFFFF
                return (nr_bits + 7) // 8
            return 0

        if kind == CTF_K_POINTER:
            return 8  # assume 64-bit

        if kind == CTF_K_ARRAY:
            if self._version >= CTF_VERSION_3 and len(t.extra) >= 12:
                elem_type: int
                nelems: int
                elem_type, _, nelems = struct.unpack_from("<III", t.extra, 0)
                return self.size(elem_type) * nelems
            if self._version < CTF_VERSION_3 and len(t.extra) >= 6:
                elem_type_v2: int
                nelems_v2: int
                elem_type_v2, _, nelems_v2 = struct.unpack_from("<HHH", t.extra, 0)
                return self.size(elem_type_v2) * nelems_v2
            return 0

        if kind in (CTF_K_TYPEDEF, CTF_K_VOLATILE, CTF_K_CONST, CTF_K_RESTRICT):
            return self.size(t.size_or_type)

        return 0

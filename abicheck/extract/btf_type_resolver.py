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

"""BTF raw type model and name/size resolution, split out of
``btf_metadata.py`` to keep that module under the architecture debt-no-growth
ceiling (ADR-061) -- mirrors ``ctf_metadata.py``'s own identical split into
``extract/ctf_type_resolver.py``. Placed directly under its canonical owner
package: parsing raw BTF type data into resolved names/sizes is a "read a
debug fact" responsibility (``extract/``), not a flat-root addition.

Owns the BTF constants and the raw ``BtfType`` record that both the
low-level parser (``_parse_types`` et al., still in ``btf_metadata.py``) and
the resolver below agree on, plus ``_TypeResolver`` itself.
``btf_metadata.py`` imports everything it needs from here; nothing here
imports back, so there is no cycle between the two modules.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from ..type_metadata import read_null_terminated_string

_T = TypeVar("_T")

BTF_MAGIC = 0xEB9F
BTF_VERSION = 1

# BTF type kinds (bits 24-28 of btf_type.info)
BTF_KIND_VOID = 0
BTF_KIND_INT = 1
BTF_KIND_PTR = 2
BTF_KIND_ARRAY = 3
BTF_KIND_STRUCT = 4
BTF_KIND_UNION = 5
BTF_KIND_ENUM = 6
BTF_KIND_FWD = 7
BTF_KIND_TYPEDEF = 8
BTF_KIND_VOLATILE = 9
BTF_KIND_CONST = 10
BTF_KIND_RESTRICT = 11
BTF_KIND_FUNC = 12
BTF_KIND_FUNC_PROTO = 13
BTF_KIND_VAR = 14
BTF_KIND_DATASEC = 15
BTF_KIND_FLOAT = 16
BTF_KIND_DECL_TAG = 17
BTF_KIND_TYPE_TAG = 18
BTF_KIND_ENUM64 = 19

# BTF_INT encoding bits
BTF_INT_SIGNED = 1 << 0
BTF_INT_CHAR = 1 << 1
BTF_INT_BOOL = 1 << 2


@dataclass
class BtfType:
    """Raw parsed BTF type entry."""

    type_id: int
    name_off: int
    info: int  # kind(5) | vlen(16) | kflag(1)
    size_or_type: int
    extra: bytes  # kind-specific trailing data

    @property
    def kind(self) -> int:
        return (self.info >> 24) & 0x1F

    @property
    def vlen(self) -> int:
        return self.info & 0xFFFF

    @property
    def kflag(self) -> int:
        return (self.info >> 31) & 1


def _read_string(str_data: bytes, offset: int) -> str:
    """Read a null-terminated string from the BTF string section."""
    return read_null_terminated_string(str_data, offset)


class _TypeResolver:
    """Resolves BTF type references to names and sizes."""

    def __init__(
        self, types: list[BtfType], str_data: bytes, *, pointer_size: int = 8
    ) -> None:
        self._types = types
        self._str = str_data
        self._pointer_size = pointer_size
        self._name_cache: dict[int, str] = {}
        self._size_cache: dict[int, int] = {}
        # Track resolution in progress for cycle detection
        self._resolving_name: set[int] = set()
        self._resolving_size: set[int] = set()

    def _resolve_cached(
        self,
        *,
        type_id: int,
        cache: dict[int, _T],
        resolving: set[int],
        cycle_value: _T,
        resolver: Callable[[int], _T],
    ) -> _T:
        if type_id in cache:
            return cache[type_id]
        if type_id in resolving:
            return cycle_value
        resolving.add(type_id)
        try:
            result = resolver(type_id)
            cache[type_id] = result
            return result
        finally:
            resolving.discard(type_id)

    def name(self, type_id: int) -> str:
        """Resolve a type ID to a human-readable type name."""
        result = self._resolve_cached(
            type_id=type_id,
            cache=self._name_cache,
            resolving=self._resolving_name,
            cycle_value="...",
            resolver=self._resolve_name,
        )
        return result

    def size(self, type_id: int) -> int:
        """Resolve a type ID to its byte size."""
        result = self._resolve_cached(
            type_id=type_id,
            cache=self._size_cache,
            resolving=self._resolving_size,
            cycle_value=0,
            resolver=self._resolve_size,
        )
        return result

    def _get(self, type_id: int) -> BtfType | None:
        if 0 <= type_id < len(self._types):
            return self._types[type_id]
        return None

    def _str_at(self, offset: int) -> str:
        return _read_string(self._str, offset)

    def _resolve_name(self, type_id: int) -> str:
        if type_id == 0:
            return "void"
        t = self._get(type_id)
        if t is None:
            return f"<btf:{type_id}>"

        kind = t.kind
        tname = self._str_at(t.name_off)

        named = self._resolve_named_kind(kind, t, tname)
        if named is not None:
            return named
        compound = self._resolve_compound_kind(kind, t, tname)
        if compound is not None:
            return compound
        return f"<btf_kind_{kind}:{type_id}>"

    def _resolve_named_kind(self, kind: int, t: BtfType, tname: str) -> str | None:
        """Names for kinds that resolve to a declared name or a kind-specific default."""
        default = self._named_kind_default(kind, t)
        if default is None:
            return None
        return tname if tname else default

    @staticmethod
    def _named_kind_default(kind: int, t: BtfType) -> str | None:
        """Fallback display name for a named kind when it has no declared name."""
        if kind == BTF_KIND_STRUCT:
            return "<anon struct>"
        if kind == BTF_KIND_UNION:
            return "<anon union>"
        if kind in (BTF_KIND_ENUM, BTF_KIND_ENUM64):
            return "<anon enum>"
        if kind == BTF_KIND_INT:
            return "int"
        if kind == BTF_KIND_FLOAT:
            return "float"
        if kind == BTF_KIND_FWD:
            return "<fwd union>" if t.kflag else "<fwd struct>"
        if kind == BTF_KIND_FUNC:
            return "<func>"
        if kind == BTF_KIND_VAR:
            return "<var>"
        return None

    def _resolve_compound_kind(self, kind: int, t: BtfType, tname: str) -> str | None:
        """Names for kinds built from a referenced type (pointers, qualifiers, …)."""
        if kind == BTF_KIND_PTR:
            return f"{self.name(t.size_or_type)} *"
        if kind == BTF_KIND_ARRAY:
            if len(t.extra) >= 12:
                elem_type, _, nelems = struct.unpack_from("<III", t.extra, 0)
                return f"{self.name(elem_type)}[{nelems}]"
            return "[]"
        if kind == BTF_KIND_TYPEDEF:
            return tname if tname else self.name(t.size_or_type)
        if kind == BTF_KIND_VOLATILE:
            return f"volatile {self.name(t.size_or_type)}"
        if kind == BTF_KIND_CONST:
            return f"const {self.name(t.size_or_type)}"
        if kind == BTF_KIND_RESTRICT:
            return f"restrict {self.name(t.size_or_type)}"
        if kind == BTF_KIND_FUNC_PROTO:
            return f"{self.name(t.size_or_type)}(...)"
        if kind == BTF_KIND_TYPE_TAG:
            return self.name(t.size_or_type)
        return None

    def _resolve_size(self, type_id: int) -> int:
        if type_id == 0:
            return 0
        t = self._get(type_id)
        if t is None:
            return 0

        kind = t.kind

        if kind in (BTF_KIND_STRUCT, BTF_KIND_UNION):
            return t.size_or_type  # size field

        if kind in (BTF_KIND_ENUM, BTF_KIND_ENUM64):
            return t.size_or_type  # size field

        if kind == BTF_KIND_INT:
            # INT encoding: bits 0-7 = nr_bits, bits 8-15 = unused, bits 16-23 = offset
            if len(t.extra) >= 4:
                enc: int = struct.unpack_from("<I", t.extra, 0)[0]
                nr_bits = enc & 0xFF
                return (nr_bits + 7) // 8
            return t.size_or_type

        if kind == BTF_KIND_FLOAT:
            return t.size_or_type

        if kind == BTF_KIND_PTR:
            return (
                self._pointer_size
            )  # derived from ELF class (4 for 32-bit, 8 for 64-bit)

        if kind == BTF_KIND_ARRAY:
            if len(t.extra) >= 12:
                elem_type: int
                nelems: int
                elem_type, _, nelems = struct.unpack_from("<III", t.extra, 0)
                return self.size(elem_type) * nelems
            return 0

        if kind in (
            BTF_KIND_TYPEDEF,
            BTF_KIND_VOLATILE,
            BTF_KIND_CONST,
            BTF_KIND_RESTRICT,
            BTF_KIND_TYPE_TAG,
        ):
            return self.size(t.size_or_type)

        return 0

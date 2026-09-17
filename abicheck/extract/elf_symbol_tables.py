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

"""pyelftools symbol-table vocabulary, in one place.

The `st_info.bind` / `st_info.type` / `st_other.visibility` spellings
pyelftools reports, mapped onto this project's own enums. A leaf: it owns
the mapping and nothing else, so the two modules that walk a symbol table
(`elf_metadata` for the entries, `extract/elf_symbol_versions` for the
version correlation) share the same objects instead of keeping a second
copy each -- the way `sycl_metadata` currently keeps its own
`_HIDDEN_VISIBILITIES`, which is exactly the drift this avoids.
"""

from __future__ import annotations

from ..model.elf_facts import SymbolBinding, SymbolType

__all__ = ["_BINDING_MAP", "_HIDDEN_VISIBILITIES", "_TYPE_MAP"]


_BINDING_MAP: dict[str, SymbolBinding] = {
    "STB_GLOBAL": SymbolBinding.GLOBAL,
    "STB_WEAK": SymbolBinding.WEAK,
    "STB_LOCAL": SymbolBinding.LOCAL,
    # STB_GNU_UNIQUE (bind value 10, GNU OS-specific range). pyelftools reports
    # it as "STB_GNU_UNIQUE"; older versions surface the raw OS range as
    # "STB_LOOS", which on Linux ELF coincides with STB_GNU_UNIQUE.
    "STB_GNU_UNIQUE": SymbolBinding.UNIQUE,
    "STB_LOOS": SymbolBinding.UNIQUE,
}

_TYPE_MAP: dict[str, SymbolType] = {
    "STT_FUNC": SymbolType.FUNC,
    "STT_OBJECT": SymbolType.OBJECT,
    "STT_TLS": SymbolType.TLS,
    "STT_GNU_IFUNC": SymbolType.IFUNC,
    # pyelftools < 0.33 reports STT_GNU_IFUNC (type=10, OS-specific range) as STT_LOOS.
    # On Linux ELF, STT_LOOS == STT_GNU_IFUNC, so we map it to IFUNC.
    "STT_LOOS": SymbolType.IFUNC,
    "STT_COMMON": SymbolType.COMMON,
    "STT_NOTYPE": SymbolType.NOTYPE,
}

_HIDDEN_VISIBILITIES = frozenset({"STV_HIDDEN", "STV_INTERNAL"})

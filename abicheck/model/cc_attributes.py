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

"""Calling-convention attribute vocabulary (ADR-061 D1).

Split out of ``diff_symbols.py`` (a whole-file ``compare``-classified module)
because :func:`is_cc_attribute` (and its backing ``CC_ATTRIBUTE_BASES`` set)
is a pure membership test with no I/O and no other dependency, needed by
``extract``'s ``tu_merge.py`` (deciding which contract-attribute token to keep
when merging translation-unit fragments) as well as by ``compare``'s own
``diff_symbols.py`` (routing a calling-convention flip to the dedicated
``CALLING_CONVENTION_CHANGED`` kind instead of a generic contract-attribute
change) -- the same shared-leaf shape ``qualified_name_segments.py`` and
``model/binary_naming.py`` already have, since ``extract`` may not import
``compare``.
"""

from __future__ import annotations

#: Calling-convention attribute base names. When one of these flips inside
#: ``contract_attributes`` it is a parameter-passing change, not a semantic
#: contract change, so ``diff_symbols.py`` routes it to the existing
#: BREAKING ``CALLING_CONVENTION_CHANGED`` kind instead.
CC_ATTRIBUTE_BASES = frozenset(
    {
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "regparm",
        "ms_abi",
        "sysv_abi",
        "vectorcall",
    }
)


def is_cc_attribute(token: str) -> bool:
    return token.split("(", 1)[0] in CC_ATTRIBUTE_BASES

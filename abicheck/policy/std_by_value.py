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

"""Whether a signature slot passes a standard-library type by value.

A ``std::`` container or string crossing the ABI boundary by value ties the
library's ABI to the standard library's layout (the
``public_api_exposes_stl_by_value`` anti-pattern, ``idioms``). The slot's
spelling usually says so; castxml spells it by its bare leaf (``string``), so
the slot's resolved type identities decide then.
"""

from __future__ import annotations

import re

from ..model import ParamKind
from ..model.fact import Fact

__all__ = ["is_std_by_value"]

_STD_SCALAR_ALIAS_RE = re.compile(
    r"^(?:const\s+|volatile\s+)*(?:::)?std::(?:size_t|ptrdiff_t|nullptr_t|max_align_t|streamsize|streamoff"
    r"|byte|u?int(?:_least|_fast)?(?:8|16|32|64)_t|u?intmax_t|u?intptr_t)"
    r"(?:\s+const|\s+volatile)*$"
)


def _resolves_to_std_record(identities: Fact[tuple[str, ...]] | None) -> bool:
    """Whether the header backend resolved a slot to a ``std::`` record.

    castxml spells a slot by its bare leaf (``string``, ``vector<int>``) but
    records the qualified identity it resolved (``std::basic_string<char>``),
    so the identity, not the spelling, says the slot is a library type.
    """
    if identities is None or not identities.is_present or not identities.value:
        return False
    return any(
        name.startswith("std::") and not _STD_SCALAR_ALIAS_RE.match(name)
        for name in identities.value
    )


def is_std_by_value(
    type_str: str,
    pointer_depth: int,
    kind: ParamKind,
    identities: Fact[tuple[str, ...]] | None = None,
) -> bool:
    """True when *type_str* names a ``std::`` type crossed **by value**.

    A pointer/reference to a ``std::`` type is fine (only the pointer crosses
    the boundary); it is passing/returning the container itself that is fragile.
    A ``std::`` alias of a fundamental type is not a library type at all. When
    the spelling carries no ``std::`` the slot's resolved *identities* (a
    castxml bare leaf) decide.
    """
    if _STD_SCALAR_ALIAS_RE.match(type_str.strip()):
        return False
    if "std::" not in type_str and not _resolves_to_std_record(identities):
        return False
    if pointer_depth >= 1 or "*" in type_str or "&" in type_str:
        return False
    if kind in (ParamKind.POINTER, ParamKind.REFERENCE, ParamKind.RVALUE_REF):
        return False
    return True

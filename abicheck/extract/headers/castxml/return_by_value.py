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

"""Whether a castxml return type is a record returned by value (``Function.return_by_value_fact``).

The answer is read from castxml's type graph, never from the spelling: the
return type's id leads through cv-qualifier, typedef and elaborated-type
wrappers to one concrete node. A ``Struct``/``Class``/``Union`` is a record
passed by value; a pointer, a reference, a fundamental or an enumeration is
not. Any other node (for example an unresolved id) is undetermined, so the
fact is left unset rather than guessed.
"""

from __future__ import annotations

from typing import Any

from ....model.fact import Fact

__all__ = ["castxml_return_by_value_fact"]

_RECORD_TAGS = frozenset({"Struct", "Class", "Union"})
_NOT_BY_VALUE_TAGS = frozenset(
    {
        "PointerType",
        "ReferenceType",
        "RvalueReferenceType",
        "FundamentalType",
        "Enumeration",
    }
)
_WRAPPER_TAGS = frozenset({"CvQualifiedType", "Typedef", "ElaboratedType"})
_MAX_DEPTH = 64


def castxml_return_by_value_fact(ctx: Any, type_id: str) -> Fact[bool] | None:
    """``Fact.present(True/False)`` for a return type id, or ``None`` if undetermined."""
    answer = _by_value(ctx, type_id, 0)
    return None if answer is None else Fact.present(answer)


def _by_value(ctx: Any, type_id: str, depth: int) -> bool | None:
    if depth > _MAX_DEPTH:
        return None
    el = ctx.resolve(type_id)
    if el is None:
        return None
    if el.tag in _RECORD_TAGS:
        return True
    if el.tag in _NOT_BY_VALUE_TAGS:
        return False
    if el.tag in _WRAPPER_TAGS:
        return _by_value(ctx, el.get("type", ""), depth + 1)
    return None

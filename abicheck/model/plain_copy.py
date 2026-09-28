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

"""A deep copy for plain-data model objects that shares what cannot change.

``copy.deepcopy`` is correct for a finding (``Change``) but generic: it runs
the reduce protocol and a memo lookup for every one of a ``Change``'s ~50
fields, and it copies immutable subtrees (enum members, strings, a frozen
``EntityId`` of tuples) that no caller could ever mutate. A release report
snapshots every finding (44k on a 28-library run), which made it the one
remaining ``deepcopy`` hot spot.

:func:`plain_deepcopy` gives the same guarantee the report envelope relies
on -- every *mutable* container reachable from the result is a fresh object,
so no later mutation of the original can reach the copy or vice versa --
while returning the original object for any subtree that holds nothing
mutable: atoms, enum members, ``frozenset``, and a tuple or frozen dataclass
whose own members are all returned unchanged. Anything it does not recognise
falls back to ``copy.deepcopy``, so an unexpected field type costs speed,
never correctness.

Unlike ``deepcopy``, two references to one mutable object inside the input
become two independent copies (there is no memo). Findings carry no such
aliasing and no cycles; a cycle would recurse without bound, which is why
this is scoped to plain data rather than offered as a general replacement.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
from typing import Any, TypeVar

__all__ = ["plain_deepcopy"]

_T = TypeVar("_T")

_ATOM_TYPES: frozenset[type] = frozenset(
    {str, int, float, bool, bytes, complex, type(None), frozenset, range}
)

# Per-class facts, computed once: (is_dataclass, is_frozen, field names).
_CLASS_INFO: dict[type, tuple[bool, bool, tuple[str, ...]]] = {}


def _class_info(cls: type) -> tuple[bool, bool, tuple[str, ...]]:
    info = _CLASS_INFO.get(cls)
    if info is None:
        if dataclasses.is_dataclass(cls):
            params = getattr(cls, "__dataclass_params__", None)
            info = (
                True,
                bool(params is not None and params.frozen),
                tuple(f.name for f in dataclasses.fields(cls)),
            )
        else:
            info = (False, False, ())
        _CLASS_INFO[cls] = info
    return info


def plain_deepcopy(obj: _T) -> _T:
    """An independent copy of *obj*, sharing only immutable subtrees."""
    return _copy(obj)  # type: ignore[no-any-return]


def _copy(obj: Any) -> Any:
    cls = type(obj)
    if cls in _ATOM_TYPES or isinstance(obj, enum.Enum):
        return obj
    if cls is list:
        return [_copy(x) for x in obj]
    if cls is dict:
        return {k: _copy(v) for k, v in obj.items()}
    if cls is set:
        return {_copy(x) for x in obj}
    if cls is tuple:
        items = [_copy(x) for x in obj]
        return obj if all(a is b for a, b in zip(items, obj)) else tuple(items)
    is_dc, frozen, names = _class_info(cls)
    if not is_dc:
        return copy.deepcopy(obj)
    state = getattr(obj, "__dict__", None)
    if state is not None:
        # Undeclared attributes a pass attached after construction ride along,
        # exactly as they would through deepcopy.
        copied = {
            k: v if type(v) in _ATOM_TYPES else _copy(v) for k, v in state.items()
        }
        if frozen and all(copied[k] is state[k] for k in state):
            return obj
        new = object.__new__(cls)
        new.__dict__.update(copied)
        return new
    values = {n: _copy(getattr(obj, n)) for n in names}
    if frozen and all(values[n] is getattr(obj, n) for n in names):
        return obj
    new = copy.copy(obj)
    for n, v in values.items():
        object.__setattr__(new, n, v)
    return new

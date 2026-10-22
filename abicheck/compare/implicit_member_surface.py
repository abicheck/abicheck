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

"""Implicit special members of a record that exists on one side only.

castxml emits every implicitly-declared special member of a class -- the
default/copy/move constructors, copy/move ``operator=`` and the destructor
(``artificial="1"``, recorded as ``Function.is_compiler_generated``) -- even
for a plain C struct parsed as C++. None of them is a user declaration, and
castxml gives the constructors and destructor no mangled name, so their
snapshot keys are abicheck's own placeholders (``__abicheck_ctor__T()``,
``~T``; ``model/synthetic_key.py``).

When the owning record itself is new (or gone), each such member is a pure
consequence of the record's ``TYPE_ADDED``/``TYPE_REMOVED``: the compiler
synthesizes it in every consumer that uses the type, nothing exports it,
and nothing about it can be compared. Left on the compared surface it was
reported as a string of ``FUNC_ADDED``/``INLINE_FUNCTION_REMOVED`` findings
keyed on those internal placeholder names (issue #1713) -- one per implicit
member of every new struct.

The members stay on the surface whenever the owner exists on both sides:
there, an implicit member appearing or disappearing is a real source-level
change of a surviving type (a ``const`` member deleting the implicit default
constructor, catalog case30), and that finding is kept.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, TypeVar

from ..diff_cxx_rules import owner_class_of
from ..model.surface_facts import is_binary_exported
from ..model.synthetic_key import is_synthetic_dtor_key, synthetic_ctor_owner

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, RecordType

_F = TypeVar("_F", bound="Function")


def implicit_member_owner(key: str, f: Function) -> str | None:
    """The record an implicit special member belongs to, or ``None``."""
    owner = synthetic_ctor_owner(key)
    if owner:
        return owner
    if is_synthetic_dtor_key(key):
        return key[1:] or None
    return owner_class_of(f)


def _record_names(types: Iterable[RecordType]) -> frozenset[str]:
    names: set[str] = set()
    for t in types:
        names.add(t.name)
        if t.qualified_name:
            names.add(t.qualified_name)
    return frozenset(names)


def is_unexported_implicit_member(f: Function) -> bool:
    """A compiler-synthesized special member no export table confirms."""
    return f.is_compiler_generated is True and not is_binary_exported(f)


def _is_orphan_implicit_member(
    key: str, f: Function, other_records: frozenset[str]
) -> bool:
    if not is_unexported_implicit_member(f):
        return False
    owner = implicit_member_owner(key, f)
    if owner is None:
        return False
    return owner not in other_records and owner.rsplit("::", 1)[-1] not in other_records


def drop_orphan_implicit_members(
    old_map: dict[str, _F],
    new_map: dict[str, _F],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[dict[str, _F], dict[str, _F]]:
    """Remove from each side the unexported compiler-generated members whose
    owning record the other side does not declare.

    A side with no record declarations at all (a binary-only or debug-info
    dump that recorded no types) proves no record absent, so the opposite
    side is left untouched against it.
    """
    old_types = old.declarations.types
    new_types = new.declarations.types
    if new_types:
        new_records = _record_names(new_types)
        old_map = {
            k: f
            for k, f in old_map.items()
            if not _is_orphan_implicit_member(k, f, new_records)
        }
    if old_types:
        old_records = _record_names(old_types)
        new_map = {
            k: f
            for k, f in new_map.items()
            if not _is_orphan_implicit_member(k, f, old_records)
        }
    return old_map, new_map

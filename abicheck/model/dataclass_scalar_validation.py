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

"""Reflection-driven validation of a dataclass's plain scalar ``str``/
``str | None`` fields against a raw (e.g. YAML-sourced) config dict (Codex
review, PR #1221, Finding 3).

Split out of ``environment_matrix.py`` into this leaf ``model`` module so any
other ``from_dict``-style config parser elsewhere in the codebase facing the
identical shape -- a frozen, hashable dataclass whose scalar fields are
assigned straight from a raw dict with no type check -- can reuse the same
primitive instead of hand-writing its own per-field ``isinstance`` check the
next time an untyped scalar reaches ``hash()`` and raises
``TypeError: unhashable type: ...`` far from where the bad value was
actually read.

Derived from :func:`typing.get_type_hints` (which resolves a module's
``from __future__ import annotations`` string annotations back into real
type objects) plus :func:`dataclasses.fields`, rather than a hand-maintained
list of field names per caller -- so a new scalar ``str``/``str | None``
field added to a dataclass in the future is validated automatically instead
of needing its own dedicated fix. Deliberately narrow in scope: only a
*plain scalar* string type is covered, since a container field (a list, a
mapping) or a nested-dataclass field needs its own shape-specific
validation that a blind ``isinstance`` check on the raw value could not
express.
"""
from __future__ import annotations

import dataclasses
import typing
from types import UnionType
from typing import Any


def scalar_str_field_types(cls: type[Any]) -> dict[str, tuple[type, ...]]:
    """Every field of dataclass *cls* whose annotation is plain ``str`` or
    ``str | None``, mapped to the ``isinstance`` tuple its raw config value
    must satisfy.
    """
    hints = typing.get_type_hints(cls)
    result: dict[str, tuple[type, ...]] = {}
    for f in dataclasses.fields(cls):
        hint = hints.get(f.name)
        if hint is str:
            result[f.name] = (str,)
        elif (
            typing.get_origin(hint) is UnionType
            and set(typing.get_args(hint)) == {str, type(None)}
        ):
            result[f.name] = (str, type(None))
    return result


def validate_scalar_str_fields(data: dict[str, Any], cls: type[Any]) -> None:
    """Reject a non-``str`` (non-``None``, where the field allows it) value
    for any of *cls*'s plain scalar ``str``/``str | None`` fields present in
    *data*.

    Runs unconditionally regardless of any caller-side "strict" toggle: a
    malformed *type* is a config error whether or not unknown-key checking
    is lenient -- only an *unknown key* has a meaningful lenient mode.
    """
    for field_name, allowed_types in scalar_str_field_types(cls).items():
        if field_name not in data:
            continue
        value = data[field_name]
        if not isinstance(value, allowed_types):
            optional_suffix = " or null" if type(None) in allowed_types else ""
            raise ValueError(
                f"'{field_name}' must be a string{optional_suffix}, got "
                f"{type(value).__name__}: {value!r}"
            )

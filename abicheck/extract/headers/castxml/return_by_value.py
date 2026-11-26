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

from collections.abc import Callable
from typing import Any
from xml.etree.ElementTree import Element

from ....model.fact import Fact

__all__ = ["castxml_return_by_value_fact"]

_RECORD_TAGS = frozenset({"Struct", "Class", "Union"})
_NOT_BY_VALUE_TAGS = frozenset(
    {
        "PointerType",
        "ReferenceType",
        "RValueReferenceType",
        "RvalueReferenceType",
        "FundamentalType",
        "Enumeration",
    }
)
_WRAPPER_TAGS = frozenset({"CvQualifiedType", "Typedef", "ElaboratedType"})
_MAX_DEPTH = 64


def castxml_return_by_value_fact(ctx: Any, type_id: str | None) -> Fact[bool] | None:
    """``Fact.present(True/False)`` for a return type id, or ``None`` if
    undetermined (including a function with no return type id)."""
    if not type_id:
        return None
    answer = _by_value(ctx.resolve, type_id, 0)
    return None if answer is None else Fact.present(answer)


def _by_value(
    resolve: Callable[[str], Element | None], type_id: str, depth: int
) -> bool | None:
    if depth > _MAX_DEPTH:
        return None
    el = resolve(type_id)
    if el is None:
        return None
    if el.tag in _RECORD_TAGS:
        return True
    if el.tag in _NOT_BY_VALUE_TAGS:
        return False
    if el.tag in _WRAPPER_TAGS:
        return _by_value(resolve, el.get("type", ""), depth + 1)
    return None


def has_exported_record_argument(root: Any, exported: frozenset[str]) -> bool:
    """Whether the exported call or one of its callbacks passes a record by value."""
    by_id = {el.get("id"): el for el in root if el.get("id")}
    from ....model.mangled_name import itanium_scope_components

    constructor_owners = {
        parts[-2].split("<", 1)[0]
        for name in exported
        if name.startswith("_Z") and any(code in name for code in ("C1", "C2", "C3"))
        if (parts := itanium_scope_components(name))
        and len(parts) >= 2
        and parts[-1] == "{ctor}"
    }

    def target(type_id: str) -> Element | None:
        el = by_id.get(type_id)
        for _ in range(_MAX_DEPTH):
            if el is None or el.tag not in _WRAPPER_TAGS:
                return el
            el = by_id.get(el.get("type", ""))
        return None

    def pointee(el: Element | None) -> Element | None:
        if el is not None and el.tag in {"PointerType", "ReferenceType"}:
            return target(el.get("type", ""))
        return el

    def callback(el: Element | None) -> bool:
        fn_type = pointee(el)
        return (
            fn_type is not None
            and fn_type.tag == "FunctionType"
            and (
                _by_value(by_id.get, fn_type.get("returns", ""), 0) is True
                or any(
                    arg.tag == "Argument"
                    and _by_value(by_id.get, arg.get("type", ""), 0) is True
                    for arg in fn_type
                )
            )
        )

    for fn in root:
        if fn.tag not in (
            "Function",
            "Method",
            "Constructor",
            "OperatorMethod",
            "OperatorFunction",
        ):
            continue
        if (
            exported
            and not (
                fn.tag == "Constructor"
                and not fn.get("mangled")
                and fn.get("name") in constructor_owners
            )
            and (fn.get("mangled") or fn.get("name")) not in exported
        ):
            continue
        result = target(fn.get("returns", ""))
        # The return classifier needs a complete layout to establish a
        # register return. A large or incomplete result cannot gain that
        # evidence from the call-trait sidecar alone.
        if (
            result is not None
            and result.tag in _RECORD_TAGS
            and result.get("incomplete") != "1"
        ):
            size = result.get("size")
            if size is None or int(size) <= 128:
                return True
        slots = [target(arg.get("type", "")) for arg in fn if arg.tag == "Argument"]
        if any(el is not None and el.tag in _RECORD_TAGS for el in slots):
            return True
        for el in [result, *slots]:
            if callback(el):
                return True
            record = pointee(el)
            if record is not None and record.tag in _RECORD_TAGS:
                for member in record.get("members", "").split():
                    field = by_id.get(member)
                    if (
                        field is not None
                        and field.tag == "Field"
                        and callback(target(field.get("type", "")))
                    ):
                        return True
    return False


def parameter_call_trait(
    ctx: Any, type_id: str, identities: tuple[str, ...]
) -> Fact[bool] | None:
    """Join only a record passed by value to Clang's classification."""
    if len(identities) != 1:
        return None
    trait = ctx.clang_semantics.record_call_traits.get(identities[0])
    if trait is None or _by_value(ctx.resolve, type_id, 0) is not True:
        return None
    return Fact.present(trait)

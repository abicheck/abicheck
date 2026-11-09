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

"""A castxml variable's alignment, and whether it is the declared one.

CastXML 0.7 (``--castxml-output=1``) writes the compiler's computed ``align``
on a record or a fundamental type, but nothing for an alignment specifier on a
``<Variable>`` or a ``<Typedef>``: ``extern int v __attribute__((aligned(64)))``
and ``extern int v`` produce the same element. The alignment this module reads
for a variable is therefore its type's *natural* alignment, a lower bound.

It is the declared alignment exactly when no alignment specifier can apply:
neither the variable's own file nor the file of a typedef its type names spells
one in its preprocessed code (macros expanded, the castxml run's own ``-E -dD``
sibling; ``preprocessed_text.alignment_files``). Then the value is recorded
``PRESENT``, and the address-placement veto in
``compare/declared_alignment`` may trust it. Otherwise it is recorded
``PARTIAL`` (a lower bound): ``VAR_ALIGNMENT_CHANGED`` still compares it, but
it never stands in for a declared alignment, so a real reduction is judged by
the DWARF or address evidence instead of vetoed by a value castxml could not
read. A document without the recorded file set (one cached before extractor
generation 51) knows no file to be free of a specifier: ``PARTIAL``.
"""

from __future__ import annotations

from typing import Any

from ....model.fact import Fact
from .context import CastxmlParserContext
from .location import optional_int_attr

__all__ = ["variable_alignment_fact"]

#: Type nodes a variable's alignment passes through to its natural one.
_THROUGH = ("CvQualifiedType", "Typedef", "ElaboratedType", "ArrayType")
_MAX_DEPTH = 10

_LOWER_BOUND = (
    "castxml records no alignment specifier on a variable or typedef, and "
    "this declaration's file (or a typedef's) spells one: the type's natural "
    "alignment is a lower bound"
)


def _file_name(ctx: CastxmlParserContext, el: Any) -> str:
    file_id = el.get("file", "")
    if not file_id:
        loc = ctx.resolve(el.get("location", ""))
        file_id = loc.get("file", "") if loc is not None else ""
    file_el = ctx.resolve(file_id) if file_id else None
    return file_el.get("name", "") if file_el is not None else ""


def _declaring_files(ctx: CastxmlParserContext, var_el: Any) -> list[str]:
    """The variable's file and the file of every typedef its type chain names."""
    files = [_file_name(ctx, var_el)]
    id_ = var_el.get("type", "")
    for _ in range(_MAX_DEPTH):
        el = ctx.resolve(id_) if id_ else None
        if el is None or el.tag not in _THROUGH:
            break
        if el.tag == "Typedef":
            files.append(_file_name(ctx, el))
        id_ = el.get("type", "")
    return files


def variable_alignment_fact(
    ctx: CastxmlParserContext, var_el: Any, natural_bits: int | None
) -> Fact[int | None]:
    """The alignment fact of castxml variable *var_el*.

    An ``align`` castxml wrote on the variable itself is declared. Otherwise
    *natural_bits* (the type's natural alignment) is declared only when no
    specifier can apply (module docstring), else a lower bound.
    """
    explicit = optional_int_attr(var_el, "align")
    if explicit is not None:
        return Fact.present(explicit)
    if natural_bits is None:
        return Fact.present(None)
    table = ctx.preprocessed
    # A declaring file castxml did not name cannot be ruled out either.
    if table is not None and not any(
        not name or table.spells_alignment(name)
        for name in _declaring_files(ctx, var_el)
    ):
        return Fact.present(natural_bits)
    return Fact.partial(natural_bits, _LOWER_BOUND, producer="castxml")

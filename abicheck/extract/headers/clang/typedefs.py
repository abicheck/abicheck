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

"""Typedef facts of the clang JSON-AST header frontend (schema v69).

Each typedef's target ABI fact is keyed like ``AbiSnapshot.typedefs_qualified``
and read from the desugared spelling, so a typedef of a builtin carries that
builtin's fact. The parser supplies its typedef entries, qualifier and builtin
layout through the :class:`TypedefSource` protocol, which keeps this module
free of an import of ``dumper_clang``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from ....model.abi_fundamental import AbiFundamental
from ....model.fact import Fact
from ...builtin_abi import builtin_type_fact
from .context import _Decl, is_builtin_file
from .qualifiers import desugared_qualtype

if TYPE_CHECKING:
    from ...builtin_abi import BuiltinAbi

__all__ = ["TypedefSource", "typedef_abi_facts", "typedef_underlying"]


class TypedefSource(Protocol):
    """What :func:`typedef_abi_facts` reads from the parser that owns the walk."""

    builtin_abi: BuiltinAbi | None

    @property
    def _typedefs(self) -> Sequence[_Decl]: ...

    def _qualified(self, entry: _Decl) -> str: ...


def typedef_underlying(node: dict[str, Any]) -> str:
    """The written underlying type of a typedef/alias (``qualType``, then sugar)."""
    type_obj = node.get("type")
    if not isinstance(type_obj, dict):
        return ""
    return str(type_obj.get("qualType") or type_obj.get("desugaredQualType") or "")


def typedef_abi_facts(parser: TypedefSource) -> dict[str, Fact[AbiFundamental]]:
    """Each user typedef's target ABI fact, keyed by its qualified name."""
    out: dict[str, Fact[AbiFundamental]] = {}
    for entry in parser._typedefs:
        if is_builtin_file(entry.file):
            continue
        if not str(entry.node.get("name", "")):
            continue
        fact = builtin_type_fact(desugared_qualtype(entry.node), parser.builtin_abi)
        if fact is not None:
            out[parser._qualified(entry)] = fact
    return out

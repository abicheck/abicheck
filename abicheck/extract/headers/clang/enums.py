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

"""Enum-entity parsing for the clang backend (ADR-061 D9).

Mirrors ``extract.headers.castxml.enums`` for the clang AST: reads the
``_Decl`` lists ``dumper_clang._ClangAstParser._walk`` already categorized
(no traversal of its own) and produces ``EnumType`` model objects, using
``context.py`` for everything below the entity level.

``parse_enums`` takes its constant-expression evaluator as an explicit
parameter rather than importing one: the real evaluator
(``dumper_clang._evaluated_int_value``) depends on ``dumper_clang_expr.py``,
which itself depends on ``diff_cxx_rules`` (classified ``compare``) for
``itanium_scope_components`` — importing it from here would give this
``extract``-classified package a real `extract -> compare`` edge. See
``context.py``'s own trailing comment for the full account. This keeps
``enums.py`` correct without duplicating that evaluator's logic (which
would drift) or reaching back into ``dumper_clang.py``'s private surface.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....model import EnumMember, EnumType, Fact
from ....model.identity import entity_id_for_enum
from .context import (
    _Decl,
    clang_deprecated_message,
    is_builtin_file,
    source_location as _source_location,
)

#: Evaluates a (possibly wrapped) clang constant-expression node to an int,
#: or ``None`` when it isn't one. Matches
#: ``dumper_clang._evaluated_int_value``'s signature exactly.
IntEvaluator = Callable[[dict[str, Any]], "int | None"]


def parse_enums(
    typedefs: list[_Decl], enums: list[_Decl], evaluate_int: IntEvaluator
) -> list[EnumType]:
    result: list[EnumType] = []
    typedef_names_by_enum_id: dict[str, str] = {}
    for entry in typedefs:
        node = entry.node
        if is_builtin_file(entry.file):
            continue
        typedef_name = str(node.get("name", ""))
        if not typedef_name:
            continue
        for child in node.get("inner", []) or []:
            if not isinstance(child, dict):
                continue
            owned = child.get("ownedTagDecl") or {}
            if owned.get("kind") == "EnumDecl" and owned.get("id"):
                typedef_names_by_enum_id[str(owned["id"])] = typedef_name

    for entry in enums:
        node = entry.node
        if is_builtin_file(entry.file):
            continue
        name = str(node.get("name", "")) or typedef_names_by_enum_id.get(
            str(node.get("id", "")), ""
        )
        if not name or name.startswith("__"):
            continue
        members: list[EnumMember] = []
        # C/C++ enumerator values auto-increment from the previous one
        # (starting at 0) unless an explicit initializer overrides them;
        # clang's JSON only carries the value on an explicit ConstantExpr, so
        # reconstruct the implicit ones here.
        next_value = 0
        for child in node.get("inner", []) or []:
            if not isinstance(child, dict) or child.get("kind") != "EnumConstantDecl":
                continue
            explicit = _enum_constant_value(child, evaluate_int)
            value = explicit if explicit is not None else next_value
            members.append(EnumMember(name=str(child.get("name", "")), value=value))
            next_value = value + 1
        # See RecordType.qualified_name (_build_record) for why this is
        # only set when it differs from the bare name; entry.scope is a
        # clean structural fact from clang's tree-shaped AST, so an empty
        # scope is a confirmed determination for the explicit
        # qualified_name_fact= construction below (ADR-063 Phase 5).
        enum_qualified_name = "::".join([*entry.scope, name]) if entry.scope else None
        result.append(
            EnumType(
                name=name,
                members=members,
                underlying_type=_enum_underlying(node),
                source_location=_source_location(entry),
                qualified_name=enum_qualified_name,
                qualified_name_fact=Fact.present(enum_qualified_name),
                # G31 Phase C: clang's EnumDecl carries a "scopedEnumTag"
                # key ("class"/"struct") only for an `enum class`/`enum
                # struct` -- absent (not merely false) for a plain C-style
                # enum, confirmed against real clang -ast-dump=json output.
                # Unlike is_standard_layout/is_trivially_copyable, a plain
                # EnumDecl always has a definitive answer here (there is
                # no "not collected" case for a real enum definition), so
                # this is a concrete bool, never None, on this backend.
                is_scoped="scopedEnumTag" in node,
                deprecated=clang_deprecated_message(node),
                # ADR-063 Phase 2: resolved from the walk's own typed scope
                # path. `name` may be the owning typedef's name for an
                # otherwise-unnamed enum, which is deliberately the same
                # spelling every other field here already uses.
                entity_id=entity_id_for_enum(entry.scope_path, name),
            )
        )
    return result


def _enum_underlying(node: dict[str, Any]) -> str:
    """The enum's fixed underlying type spelling, defaulting to ``int``."""
    fixed = node.get("fixedUnderlyingType")
    if isinstance(fixed, dict) and fixed.get("qualType"):
        return str(fixed["qualType"])
    return "int"


def _enum_constant_value(
    node: dict[str, Any], evaluate_int: IntEvaluator
) -> int | None:
    """The explicit value of an ``EnumConstantDecl``, or ``None`` if implicit."""
    for child in node.get("inner", []) or []:
        if not isinstance(child, dict):
            continue
        value = evaluate_int(child)
        if value is not None:
            return value
    return None

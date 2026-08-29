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

"""Shared parser state for the clang header-AST backend (ADR-061 D9).

Owns the one type every entity-parsing module in this package receives —
:class:`_Decl`, a categorized AST node plus its walk context (scope, file,
access, extern-C/friend/template flags) — and the small set of
node-inspection primitives more than one entity kind's parsing reads:
built-in-origin filtering, a declaration's own type spelling, its source
location, and its deprecation message. None of these open the AST document
or drive traversal themselves; ``dumper_clang.py``'s ``_walk`` still does
that, populating the categorized ``_Decl`` lists this package's entity
modules (``enums.py`` today) read.

Deliberately excludes ``dumper_clang._evaluated_int_value`` — see the
comment above where it would otherwise live for why moving it here would
recreate a real ``extract -> compare`` layering violation.
"""

from __future__ import annotations

from typing import Any

from ....name_classification import strip_anonymous_type_location

#: Pseudo-files clang attributes builtin / command-line declarations to.
BUILTIN_FILES = frozenset(
    {"<built-in>", "<builtin>", "<command line>", "<scratch space>"}
)


class _Decl:
    """A categorized clang AST decl node plus its walk context.

    ``__slots__`` keeps the per-decl overhead low on large headers.
    """

    __slots__ = (
        "access",
        "extern_c",
        "file",
        "in_friend",
        "in_template",
        "node",
        "scope",
    )

    def __init__(
        self,
        node: dict[str, Any],
        scope: tuple[str, ...],
        file: str,
        access: str,
        extern_c: bool = False,
        in_friend: bool = False,
        in_template: bool = False,
    ) -> None:
        self.node = node
        self.scope = scope
        self.file = file
        self.access = access
        # True when the decl sits inside an ``extern "C"`` linkage spec — an
        # authoritative C-linkage signal that beats the mangled==name heuristic.
        self.extern_c = extern_c
        # True when the decl is reached through a ``friend`` declaration: the
        # function is ADL-only ("hidden friend") and the diff treats it apart
        # from the ordinary public surface.
        self.in_friend = in_friend
        # True when the decl is the pattern body of a class template (e.g. the
        # CXXRecordDecl inside a ClassTemplateDecl): same kind and bare name as
        # an ordinary record, but its members reference dependent template-
        # parameter types with no fixed layout for any one instantiation. Kept
        # as a RecordType (its field *names*/*types* are still real public
        # surface — case17_template_abi's field-added detection relies on it)
        # but flagged so a name-based match (e.g. DWARF layout backfill)
        # never treats it as an ordinary concrete type (Codex review).
        self.in_template = in_template


def is_builtin_file(file: str) -> bool:
    return file in BUILTIN_FILES


def qualtype(node: dict[str, Any]) -> str:
    """A declaration's own ``type.qualType`` spelling -- the single choke
    point every field/param/variable/function type string in this module is
    built from (`_parse_fields`, `_parse_functions`'s own signature and
    param loop, `parse_variables`, `parse_constants`).

    Stripped via :func:`strip_anonymous_type_location`: verified against
    real Clang 18 (``-ast-dump=json``) that a lambda closure type embedded in
    a type spelling -- e.g. a class-template specialization instantiated
    with a lambda argument, ``Guard<decltype([]{})>`` -- prints its
    ``qualType`` as ``"(lambda at <path>:<line>:<col>)"`` (Clang's
    TypePrinter, the same diagnostic-style spelling castxml's own XML `name`
    attribute uses, confirmed on a `FieldDecl` whose declared type IS the
    lambda type parameter). Left unstripped, that absolute, checkout-
    dependent path leaks into `TypeField.type`/`Param.type`/`Variable.type`/
    `Function.return_type`, so two checkouts of the identical, unchanged
    declaration would produce two different type spellings and could
    manufacture a spurious finding on the field/param/variable/function
    carrying it -- the same class of bug `dumper_castxml.py`'s own
    `strip_anonymous_type_location` calls guard against for its `name`/
    `qualified_name` fields, just reached through this backend's type-string
    printer rather than its declaration-name attribute (which, unlike
    castxml's, never itself embeds a location -- confirmed empirically: a
    template specialization's own `name` node stays the bare template name,
    e.g. ``"Guard"``, never ``"Guard<(lambda at ...)>"``).
    """
    type_obj = node.get("type")
    if isinstance(type_obj, dict):
        return strip_anonymous_type_location(str(type_obj.get("qualType", "")))
    return ""


def node_line(node: dict[str, Any]) -> int:
    loc = node.get("loc")
    if isinstance(loc, dict):
        line = loc.get("line")
        if isinstance(line, int):
            return line
        # Mirror `dumper_clang._node_file`'s macro/expansion fallback so a decl
        # whose file comes from expansionLoc/spellingLoc gets its line from
        # the same place.
        for sub in ("expansionLoc", "spellingLoc"):
            s = loc.get(sub)
            if isinstance(s, dict) and isinstance(s.get("line"), int):
                return int(s["line"])
    return 0


def source_location(entry: _Decl) -> str | None:
    """``file:line`` for a decl, or the bare file when clang omits the line.

    clang makes ``loc.line`` sticky just like ``loc.file`` — a declaration
    nested on the same source line as its parent (e.g. a ``static constexpr``
    member of a one-line ``struct``) often carries the inherited file but no
    ``line``. Dropping the whole location there would strip provenance and
    make ``_decl_is_public`` discard an otherwise-public constant/type, so
    the file is kept (``header_from_location`` tolerates a path with no
    ``:line`` suffix). Returns ``None`` only when there is no file at all.
    """
    if not entry.file:
        return None
    line = node_line(entry.node)
    return f"{entry.file}:{line}" if line else entry.file


def clang_deprecated_message(node: dict[str, Any]) -> str | None:
    """Deprecation message for *node*, or ``None`` if not deprecated (G31
    Phase C schema-completeness audit) — the direct-clang backend's
    counterpart to ``dumper_castxml._deprecation_marker``, matching its exact
    three-way convention (message text / ``""`` for a bare, messageless
    ``[[deprecated]]`` / ``None`` for not deprecated) so the two backends'
    ``Function.deprecated``/``Variable.deprecated``/``TypeField.deprecated``/
    ``RecordType.deprecated``/``EnumType.deprecated`` agree.

    Verified against real ``clang -ast-dump=json`` output (Clang 18) before
    wiring this up: unlike castxml (a compound ``attributes`` string plus a
    separate ``deprecation="..."`` XML attribute only for a non-empty
    message), clang emits a ``DeprecatedAttr`` child node under the
    declaration's own ``"inner"`` list — present for both the bare and
    messaged forms, with an optional ``message`` string key present *only*
    for the messaged form (confirmed empirically: a bare ``[[deprecated]]``'s
    ``DeprecatedAttr`` node carries no ``message`` key at all, not an empty
    string).
    """
    for child in node.get("inner", []) or []:
        if isinstance(child, dict) and child.get("kind") == "DeprecatedAttr":
            return str(child.get("message", ""))
    return None


# Deliberately NOT here: `dumper_clang._evaluated_int_value`. It walks
# clang's wrapper-expression chain via `_WRAPPER_EXPR_KINDS`
# (`dumper_clang_expr.py`), which itself imports `diff_cxx_rules`
# (classified `compare`) for `itanium_scope_components` — the exact
# "shared piece entangled with another layer" case `extract/AGENTS.md`
# names as the pattern to avoid rather than paper over with a new import.
# Moving `_evaluated_int_value` here would recreate that `extract -> compare`
# edge one module down. `enums.py.parse_enums` instead takes the evaluator
# as an explicit parameter, supplied by its one caller
# (`dumper_clang._ClangAstParser.parse_enums`, which already owns it) — the
# same "context is whatever the entity module actually needs, not
# whatever's convenient to import" principle this package's `context.py`
# modules apply everywhere else, just expressed as a parameter instead of
# a state field here since the value is a pure function, not parser state.

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

"""The clang header-AST backend's ``extern "C"`` recognition on Mach-O
(ADR-063 Phase 6, Codex review, fifteenth round, fresh evidence).

A new, dedicated file rather than added to ``test_dumper_clang.py`` --
that file already sits at its ``architecture/debt.yaml`` ``no_growth``
baseline, and this codebase's own convention is to move responsibility out
to a properly-owned module rather than raise a no-growth baseline for new
work (see the root ``AGENTS.md``'s "Files that are large" section).

A genuinely plain-C compilation unit has no ``LinkageSpecDecl`` at all
(that AST node only exists in C++'s grammar), so ``entry.extern_c`` --
which is set only by walking into one -- never becomes ``True`` for a
plain-C declaration. The bare-equality fallback
(``raw_mangled == name``, tested by ``test_dumper_clang.py``'s own
``test_parse_functions_extern_c_via_mangled_equals_name``) still recovers
this on most platforms, since clang's ``mangledName`` for a plain-C
declaration is otherwise the bare source name. Mach-O is the exception:
Darwin's linker prepends a leading underscore to every global symbol
(``"_foo"`` for source-level ``"foo"``), so clang's own ``mangledName``
for the identical plain-C declaration is ``"_foo"``, and the bare-equality
check never matches. Left unfixed, such a declaration's ``entity_id``
stayed tagged ``("mangled", "_foo")`` while castxml -- which observes no
``mangledName`` XML attribute at all for a plain-C ``Function``/global
``Variable`` -- tags the identical declaration ``("extern_c",)``,
so a hybrid merge's bare-``EntityId`` matching in
``extract.semantic_ir_merge.merge_semantic_ir`` never recognized the two
as one declaration and retained it TWICE in the merged ``semantic_ir``,
even though the flat ``functions``/``variables`` lists (which match on the
bare mangled string, not ``EntityId``) already unified it via
``dumper_hybrid._merge_functions``'s own ``clang_by_mangled`` lookup.

The fix (``extract.headers.clang.context.symbol_candidates``, the same
tolerant-match helper ``visibility()`` already uses for the identical
Mach-O underscore quirk) is reused rather than re-implemented, in both
``extract.headers.clang.functions.parse_functions`` and
``dumper_clang._ClangAstParser.parse_variables``.
"""

from __future__ import annotations

from abicheck.dumper_clang import _ClangAstParser


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def test_parse_functions_extern_c_via_macho_leading_underscore() -> None:
    """Without the ``symbol_candidates``-based fix, this function's
    ``entity_id`` stayed tagged ``("mangled", "_c_api")`` instead of
    matching castxml's ``("extern_c",)`` for the identical plain-C
    declaration."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "c_api",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_c_api",  # Darwin C linkage: "_" + name
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_extern_c is True
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("extern_c",)


def test_parse_variables_extern_c_via_macho_leading_underscore() -> None:
    """The variable-level sibling of the function case above."""
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "g_count",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "mangledName": "_g_count",  # Darwin C linkage: "_" + name
        }
    )
    (var,) = _ClangAstParser(root, set(), set()).parse_variables()
    assert var.entity_id is not None
    assert var.entity_id.extra == ("extern_c",)

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
(ADR-063 Phase 6, Codex review, fifteenth and sixteenth rounds, fresh
evidence each time).

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
``dumper_clang._ClangAstParser.parse_variables`` -- but gated on
``extract.headers.clang.context.is_darwin_target`` (sixteenth round, fresh
evidence): on a non-Darwin target, ``raw_mangled == "_" + name`` is not a
linker-decoration artifact at all -- it is exactly what a real, explicit
``asm("_foo")`` label looks like, a genuinely distinct mangled identity
castxml's own resolver also keeps tagged ``("mangled", "_foo")``. An
ungated version of the fifteenth round's fix misread that as C linkage
too, discarding the real mangled identity clang correctly observed.
"""

from __future__ import annotations

import pytest

from abicheck.dumper_clang import _ClangAstParser
from abicheck.extract.headers.clang.context import is_darwin_target

_DARWIN_TRIPLE = "arm64-apple-darwin"
_LINUX_TRIPLE = "x86_64-unknown-linux-gnu"


@pytest.mark.parametrize(
    "target_triple,expected",
    [
        ("arm64-apple-darwin", True),
        ("x86_64-apple-darwin20", True),
        ("arm64-apple-macosx13.0.0", True),
        ("x86_64-unknown-linux-gnu", False),
        ("aarch64-linux-android", False),
        ("x86_64-pc-windows-msvc", False),
        (None, False),
        ("", False),
    ],
)
def test_is_darwin_target(target_triple: str | None, expected: bool) -> None:
    assert is_darwin_target(target_triple) is expected


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
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
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
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.entity_id is not None
    assert var.entity_id.extra == ("extern_c",)


def test_parse_functions_leading_underscore_not_extern_c_off_darwin() -> None:
    """The Darwin gate is load-bearing, not cosmetic (sixteenth round,
    fresh evidence): on a non-Darwin target, a real, explicit
    ``asm("_foo")`` label genuinely produces ``mangledName == "_c_api"``
    for a real function named ``c_api`` with NO extern "C" linkage at all
    -- that is a real, distinct mangled identity, and must stay tagged
    ``("mangled", "_c_api")``, matching castxml's own resolver for the
    identical declaration. Misreading it as C linkage would DISCARD that
    real identity instead of reconciling it."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "c_api",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_c_api",  # a real asm("_c_api") label, not Darwin decoration
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_LINUX_TRIPLE
    ).parse_functions()
    assert fn.is_extern_c is False
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "_c_api")


def test_parse_variables_leading_underscore_not_extern_c_off_darwin() -> None:
    """The variable-level sibling of the non-Darwin case above."""
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "g_count",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "mangledName": "_g_count",  # a real asm("_g_count") label
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_LINUX_TRIPLE
    ).parse_variables()
    assert var.entity_id is not None
    assert var.entity_id.extra == ("mangled", "_g_count")


def test_parse_functions_leading_underscore_not_extern_c_without_target() -> None:
    """No ``target_triple`` at all (a synthetic/unit AST, or an unprobeable
    compiler) must default to the SAME conservative "not Darwin" answer as
    an explicit non-Darwin triple -- never assume Darwin decoration from
    the absence of evidence."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "c_api",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_c_api",
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(root, set(), set()).parse_functions()
    assert fn.is_extern_c is False
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "_c_api")

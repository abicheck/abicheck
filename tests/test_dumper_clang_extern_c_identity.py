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
(ADR-063 Phase 6, Codex review, fifteenth through nineteenth rounds, fresh
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

**Nineteenth round, fresh evidence, two independent findings on the SAME
commit** further narrowed the gate (the seventeenth/eighteenth rounds in
between were about a different artifact --
``extract.semantic_normalizer_artifacts``'s opaque-``FunctionType``
regex, documented in ``test_semantic_normalizer_artifacts.py`` instead):

  1. ``is_darwin_target`` checked only for an ``"apple"`` VENDOR
     substring, missing a valid triple like ``"x86_64-unknown-darwin"``
     (real Mach-O target behavior determined by the OS component, not
     the vendor). Fixed by splitting the triple on ``"-"`` and checking
     each component against known Darwin OS names
     (``darwin``/``macos``/``ios``/``tvos``/``watchos``) via
     ``startswith`` (to tolerate a trailing version suffix like
     ``"darwin20.6.0"``), in addition to the ``"apple"`` vendor check.

  2. The Darwin gate ALONE was not enough: a real, explicit
     ``asm("_foo")`` label is just as possible ON Darwin as off it, and
     the whole justification for this fallback -- "a genuinely plain-C
     compilation unit has no ``LinkageSpecDecl``" -- only holds for a
     declaration with NO enclosing scope at all (C has no namespaces, so
     a plain-C declaration is always global-scope). A NAMESPACED Darwin
     C++ declaration (``namespace n { void foo() asm("_foo"); }``) is
     never plain C regardless of platform, so both ``parse_functions``
     and ``parse_variables`` now also require ``not entry.scope`` --
     preserving both the genuine asm-label mangled identity AND the
     namespace a retag to ``("extern_c",)`` would otherwise have
     silently discarded (``entity_id_for_function``/
     ``entity_id_for_variable``'s ``is_extern_c`` branch always resolves
     ``scope=()``).
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
        ("arm64-apple-ios15.0", True),
        ("armv7-apple-tvos", True),
        ("armv7k-apple-watchos", True),
        # Not an "apple" vendor at all, but the OS component is real
        # Mach-O/Darwin behavior clang genuinely accepts and mangles for
        # (Codex review, nineteenth round, fresh evidence).
        ("x86_64-unknown-darwin", True),
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


def test_parse_functions_leading_underscore_not_extern_c_when_namespaced() -> None:
    """The Darwin gate ALONE is not enough (nineteenth round, fresh
    evidence): a real, explicit ``asm("_foo")`` label is just as possible
    ON Darwin as off it. ``namespace n { void foo() asm("_foo"); }`` is
    never plain C regardless of platform (C has no namespaces), so this
    NAMESPACED Darwin declaration must stay tagged ``("mangled",
    "_foo")`` -- retagging it ``("extern_c",)`` would silently discard
    both the real asm-label mangled identity AND the namespace
    (``entity_id_for_function``'s ``is_extern_c`` branch always resolves
    ``scope=()``)."""
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "n",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "foo",
                    "loc": {"line": 2},
                    "mangledName": "_foo",  # a real asm("_foo") label
                    "type": {"qualType": "void ()"},
                },
            ],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.is_extern_c is False
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "_foo")


def test_parse_variables_leading_underscore_not_extern_c_when_namespaced() -> None:
    """The variable-level sibling of the namespaced-Darwin case above."""
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "n",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "g_count",
                    "loc": {"line": 2},
                    "type": {"qualType": "int"},
                    "mangledName": "_g_count",  # a real asm("_g_count") label
                },
            ],
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.entity_id is not None
    assert var.entity_id.extra == ("mangled", "_g_count")

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
    ],
)
def test_is_darwin_target(target_triple: str | None, expected: bool) -> None:
    """A successfully-probed *target_triple* always wins outright -- these
    cases never reach the ``sys.platform`` fallback below (a non-empty
    string is always "truthy"), so they hold regardless of which OS
    actually runs this test."""
    assert is_darwin_target(target_triple) is expected


def test_is_darwin_target_never_guesses_darwin_from_bare_none() -> None:
    """A bare ``None``/``""`` *target_triple* means "no evidence" and must
    always answer ``False`` here, regardless of which OS actually runs
    this test suite -- including on the real ``macos-latest`` CI lane.
    This is deliberately NOT a ``sys.platform``-based guess: this same
    bare-``None`` shape is what a direct, no-pipeline-involved unit-test
    construction of ``_ClangAstParser`` (see e.g.
    ``test_parse_functions_leading_underscore_not_extern_c_without_target``
    below) also produces, and that test's own conservative "no evidence,
    no guess" contract would silently flip on a Darwin test runner if this
    function special-cased ``None`` by host platform (Codex review, fresh
    evidence -- an earlier revision did exactly that and broke it). The
    real dump pipeline's own probe-failure recovery lives one layer up, in
    ``dumper._run_clang``, which passes in a real, non-``None`` triple
    string instead of relying on this function to guess."""
    assert is_darwin_target(None) is False
    assert is_darwin_target("") is False


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
    # Real macOS CI review, fresh evidence: once `is_extern_c` is
    # confirmed True, the raw Darwin-decorated `mangled` field itself
    # must also normalize to the bare name -- disagreeing with the
    # binary's own already-stripped export table otherwise (see
    # `context.strip_darwin_itanium_decoration`'s docstring).
    assert fn.mangled == "c_api"


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
    assert var.mangled == "g_count"


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
    assert fn.mangled == "_c_api"


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
    assert var.mangled == "_g_count"


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
    assert fn.mangled == "_c_api"


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
    assert fn.mangled == "_foo"


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
    assert var.mangled == "_g_count"


# ── Function.mangled/Variable.mangled field normalization (macOS CI review,
# fresh evidence): the extern-"C" identity tests above cover the bare-name
# case (raw_mangled == "_" + name), where the fix's job is entirely about
# WHICH IDENTITY a declaration gets tagged with. This section covers the
# separate, previously-unfixed case -- a genuine, non-extern-"C" C++
# function/variable's own STORED `mangled` field value, which on Darwin
# clang's `mangledName` reports WITH the platform's extra leading
# underscore baked in on top of the real Itanium mangling (`"__ZN..."`),
# while castxml's own convention (and every documented "already
# normalized" contract this field has elsewhere -- macho_metadata.py,
# crosscheck_base._exported_symbol_names) never carries it. Verified here
# via synthetic AST nodes (this environment has no real macOS/clang-Darwin
# toolchain to compile against) -- see
# tests/test_crosscheck_language_mode_export_evidence.py for the real-
# compiler, real-binary Linux/ELF-Itanium coverage of the sibling
# language-mode-detection bug this same review round also fixed.


def test_parse_functions_mangled_field_strips_darwin_underscore_for_real_cxx_name() -> (
    None
):
    """A genuine (non-extern-"C") namespaced C++ function's own ``mangled``
    field must carry the pure Itanium spelling on Darwin, not clang's own
    Darwin-linker-decorated one -- otherwise it disagrees with castxml's
    identical declaration (breaking cross-backend/hybrid reconciliation)
    and with the binary's own already-normalized export table (breaking
    ``crosscheck.py``'s ``exported_not_public``/``public_not_exported``
    symbol correlation, the originally reported macOS CI failure)."""
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
                    "mangledName": "__ZN1n3fooEv",  # Darwin-decorated real Itanium
                    "type": {"qualType": "void ()"},
                },
            ],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.is_extern_c is False
    assert fn.mangled == "_ZN1n3fooEv"
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "_ZN1n3fooEv")


def test_parse_variables_mangled_field_strips_darwin_underscore_for_real_cxx_name() -> (
    None
):
    """The variable-level sibling of the function case above."""
    root = _tu(
        {
            "kind": "NamespaceDecl",
            "name": "n",
            "loc": {"file": "include/foo.h", "line": 1},
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "g",
                    "loc": {"line": 2},
                    "type": {"qualType": "int"},
                    "mangledName": "__ZN1n1gE",  # Darwin-decorated real Itanium
                },
            ],
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.mangled == "_ZN1n1gE"
    assert var.entity_id is not None
    assert var.entity_id.extra == ("mangled", "_ZN1n1gE")


def test_parse_functions_explicit_asm_label_unstripped_on_darwin() -> None:
    """Codex review, fresh evidence, empirically verified against a real
    Clang 18 install: a literal ``asm("__Zfake")`` label is exactly as
    ``"__Z..."``-shaped as a real, compiler-generated decorated Itanium
    mangling -- the two are indistinguishable from the string alone once
    a genuine Darwin target is confirmed. Real clang's own AST distinguishes
    them structurally: an explicit label carries an ``AsmLabelAttr`` child,
    which castxml's own convention preserves verbatim too (see
    ``tests/test_castxml_literal_double_underscore_mangled.py``), so the
    two backends must agree here as well. Without this gate, the fix for
    the two tests above would corrupt this label into ``"_Zfake"``,
    disagreeing with both castxml's identical declaration and the
    binary's own real, undecorated export-table entry."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "f",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "__Zfake",
            "type": {"qualType": "void ()"},
            "inner": [{"kind": "AsmLabelAttr"}],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.mangled == "__Zfake"
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "__Zfake")


def test_parse_variables_explicit_asm_label_unstripped_on_darwin() -> None:
    """The variable-level sibling of the function case above."""
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "g",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "mangledName": "__Zfake_g",
            "inner": [{"kind": "AsmLabelAttr"}],
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.mangled == "__Zfake_g"
    assert var.entity_id is not None
    assert var.entity_id.extra == ("mangled", "__Zfake_g")


def test_parse_functions_explicit_single_underscore_asm_label_not_treated_as_extern_c() -> None:
    """Codex review, fresh evidence, empirically verified against a real
    Clang 18 install (``void foo() asm("_foo");`` under
    ``--target=x86_64-apple-darwin`` reports both a literal ``mangledName``
    of ``"_foo"`` AND an ``AsmLabelAttr`` child): a single-underscore
    explicit asm label is exactly as ``symbol_candidates``-matchable as
    genuine Darwin extern-C bare-name decoration, so ``is_extern_c``'s
    Darwin-gated ``symbol_candidates`` fallback wrongly classified it as
    extern "C" even though the two tests above already keep the mangled
    name itself preserved (``"_foo"``, not stripped to ``"foo"``) -- the
    resulting ``EntityId`` still took the signature-free ``("extern_c",)``
    branch instead of ``("mangled", "_foo")``, diverging from castxml's own
    mangled-name identity for the identical declaration and risking a
    manufactured remove/add pair in a castxml/clang or hybrid comparison."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "foo",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_foo",
            "type": {"qualType": "void ()"},
            "inner": [{"kind": "AsmLabelAttr"}],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.mangled == "_foo"
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "_foo")


def test_parse_variables_explicit_single_underscore_asm_label_not_treated_as_extern_c() -> None:
    """The variable-level sibling of the function case above."""
    root = _tu(
        {
            "kind": "VarDecl",
            "name": "bar",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "int"},
            "mangledName": "_bar",
            "inner": [{"kind": "AsmLabelAttr"}],
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.mangled == "_bar"
    assert var.entity_id is not None
    assert var.entity_id.extra == ("mangled", "_bar")


def test_parse_functions_mangled_field_unaffected_off_darwin() -> None:
    """Control for the two tests above: the SAME doubly-underscored input
    is never stripped off Darwin -- there is no such linker convention to
    correct for there, so a literal ``"__ZN...``-shaped mangled name (an
    unusual but syntactically legal spelling on a non-Darwin target) must
    be preserved exactly as clang reported it."""
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
                    "mangledName": "__ZN1n3fooEv",
                    "type": {"qualType": "void ()"},
                },
            ],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_LINUX_TRIPLE
    ).parse_functions()
    assert fn.mangled == "__ZN1n3fooEv"
    assert fn.entity_id is not None
    assert fn.entity_id.extra == ("mangled", "__ZN1n3fooEv")


def test_parse_functions_mangled_field_unaffected_when_no_mangled_name_at_all() -> (
    None
):
    """The strip is gated on ``raw_mangled is not None`` -- a declaration
    that fell back to its bare source ``name`` (e.g. an uninstantiated
    function template, which carries no ``mangledName`` key at all) must
    never have a leading underscore stripped from ITS OWN identifier, even
    on Darwin: that underscore, if present, is part of the real source
    spelling, not linker decoration clang ever reported."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "_leading_underscore_name",
            "loc": {"file": "include/foo.h", "line": 1},
            "type": {"qualType": "void ()"},
            # Deliberately no "mangledName" key at all -- clang omits it
            # for e.g. an uninstantiated function template's own
            # FunctionDecl (see this file's module docstring); a plain
            # FunctionDecl missing the key exercises the identical
            # raw_mangled-is-None fallback path.
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.mangled == "_leading_underscore_name"


def test_parse_functions_extern_c_block_mangled_field_strips_darwin_underscore() -> (
    None
):
    """Regression (macOS CI, fresh evidence, second CodeRabbit round on
    this same PR): a REAL, explicit ``extern "C"`` block's decorated
    Darwin ``mangledName`` (``"_c_func"`` for source-level ``c_func``) was
    left unstripped in the stored ``Function.mangled`` field even though
    ``is_extern_c`` was already correctly ``True`` via ``entry.extern_c`` --
    unlike the plain-C bare-declaration case above (whose ``mangled`` field
    was already covered by the earlier ``__Z``-only fix's sibling
    ``symbol_candidates`` fallback, but never actually normalized in
    ``mangled`` itself, only in ``is_extern_c`` detection). This is the
    exact shape a real compiled Mach-O self-comparison hit: castxml emits
    the pure ``"c_func"``, clang's real linker-decorated ``mangledName``
    was left as ``"_c_func"``, so the two backends' stored identity
    disagreed even though both correctly recognized it as extern "C"."""
    root = _tu(
        {
            "kind": "LinkageSpecDecl",
            "language": "C",
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "c_func",
                    "loc": {"file": "include/foo.h", "line": 1},
                    "mangledName": "_c_func",
                    "type": {"qualType": "int (int)"},
                    "inner": [
                        {
                            "kind": "ParmVarDecl",
                            "name": "x",
                            "type": {"qualType": "int"},
                        }
                    ],
                }
            ],
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.mangled == "c_func"
    assert fn.is_extern_c is True


def test_parse_variables_extern_c_block_mangled_field_strips_darwin_underscore() -> (
    None
):
    """The variable-level sibling of the function case above."""
    root = _tu(
        {
            "kind": "LinkageSpecDecl",
            "language": "C",
            "inner": [
                {
                    "kind": "VarDecl",
                    "name": "c_var",
                    "loc": {"file": "include/foo.h", "line": 1},
                    "type": {"qualType": "int"},
                    "mangledName": "_c_var",
                }
            ],
        }
    )
    (var,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_variables()
    assert var.mangled == "c_var"
    assert var.entity_id is not None
    assert var.entity_id.extra == ("extern_c",)


def test_parse_functions_bare_c_mangled_field_strips_darwin_underscore_too() -> None:
    """Companion to ``test_parse_functions_extern_c_via_macho_leading_
    underscore`` (which only checked ``is_extern_c``/``entity_id``, not
    ``mangled`` itself): a plain-C bare declaration's stored ``mangled``
    field must ALSO be normalized to the pure spelling, matching castxml,
    the real Mach-O export table, and now the explicit-``extern "C"``-
    block case above -- not just the identity tag."""
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "c_api",
            "loc": {"file": "include/foo.h", "line": 1},
            "mangledName": "_c_api",
            "type": {"qualType": "void ()"},
        }
    )
    (fn,) = _ClangAstParser(
        root, set(), set(), target_triple=_DARWIN_TRIPLE
    ).parse_functions()
    assert fn.mangled == "c_api"


def test_extern_c_and_cxx_siblings_both_normalize_consistently_on_darwin() -> None:
    """Both declaration shapes in ONE translation unit, together: a real
    ``extern "C"`` function and a plain C++ function must both resolve to
    their platform-independent, undecorated identity on Darwin at once --
    not just individually in isolation (coordinator review: the two fixes
    landed as separate commits, so this proves they compose)."""
    root = _tu(
        {
            "kind": "LinkageSpecDecl",
            "language": "C",
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "c_func",
                    "loc": {"file": "include/foo.h", "line": 1},
                    "mangledName": "_c_func",
                    "type": {"qualType": "int (int)"},
                    "inner": [
                        {
                            "kind": "ParmVarDecl",
                            "name": "x",
                            "type": {"qualType": "int"},
                        }
                    ],
                }
            ],
        },
        {
            "kind": "FunctionDecl",
            "name": "plain_func",
            "loc": {"file": "include/foo.h", "line": 2},
            "mangledName": "__Z10plain_funci",
            "type": {"qualType": "int (int)"},
            "inner": [
                {"kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"}}
            ],
        },
    )
    funcs = {
        f.name: f
        for f in _ClangAstParser(
            root, set(), set(), target_triple=_DARWIN_TRIPLE
        ).parse_functions()
    }
    assert funcs["c_func"].mangled == "c_func"
    assert funcs["c_func"].is_extern_c is True
    assert funcs["plain_func"].mangled == "_Z10plain_funci"
    assert funcs["plain_func"].is_extern_c is False

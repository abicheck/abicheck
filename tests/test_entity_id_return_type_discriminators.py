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

"""Live-clang regressions for ``abicheck/extract/headers/clang/return_type.py``.

Split out of ``test_entity_id_template_discriminators.py`` (which itself
grew past the architecture gate's 1200-line test-file cap, having earlier
been split out of ``test_entity_id_carrier.py`` for the identical reason)
purely to keep that module legible -- no test content changed by this
split. Every test here pins ``return_type()``'s own PARSING correctness:
dependent/trailing/spiral (function-pointer-returning) return-type
declarator shapes, exception specifications, GNU attributes, quoted
literals, and relational operators inside template arguments -- as
opposed to the sibling module's focus on ``entity_id_for_function``'s
"sig" fallback discriminator (template-parameter kind/packness/rename).
Every case here was confirmed by direct compilation (``clang -Xclang
-ast-dump=json``) before being fixed, per this repo's AGENTS.md
discipline -- see each test's own docstring and PR #943's review history
for the individual finding.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_entity_id_carrier import _clang_parser, _one

from abicheck.dumper_clang import _ClangAstParser


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_trailing_return_type_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """Two templates differing ONLY in their TRAILING return type
    (``auto f(T) -> typename T::x`` vs. ``-> typename T::y``) are legal,
    coexisting overloads, but clang's own ``qualType`` spells the leading
    part as the bare placeholder ``auto`` for both -- confirmed by direct
    compilation -- so a discriminator built from only the leading spelling
    collapsed them onto one ``EntityId`` (Codex review, PR #943, the
    trailing-return-type sibling of the dependent-leading-return-type case
    above)."""
    a = _one(
        _clang_parser(
            "struct S1 { using x = int; };"
            " template<class T> auto f(T) -> typename T::x;",
            tmp_path,
            "trailreta",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct S2 { using y = double; };"
            " template<class T> auto f(T) -> typename T::y;",
            tmp_path,
            "trailretb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "typename T::x"
    assert b.return_type == "typename T::y"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_function_pointer_return_declarator_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """Two templates differing ONLY in the DECLARATOR SHAPE of a dependent
    return type are legal, coexisting overloads: `template<class T>
    typename T::x f(T);` and `template<class T> typename T::x (*f(T))(T);`
    both compile with no redefinition error (confirmed by direct
    compilation) -- the second returns a pointer to a function, spelled by
    clang as the SPIRAL declarator `typename T::x (*(T))(T)`. `_return_type`
    used to treat the FIRST top-level group as the parameter list
    outright, discarding everything after it, so both overloads' return
    type collapsed onto the identical `typename T::x` (Codex review, PR
    #943). Fixed by detecting the spiral shape (a leading `*`/`&` sigil
    inside the first group) and recursively excising just `f`'s own
    nested parameter list, keeping the wrapper -- see that function's own
    docstring for the several other confirmed cases this same fix had to
    keep correct at once."""
    a = _one(
        _clang_parser(
            "struct S { using x = int; }; template<class T> typename T::x f(T);",
            tmp_path,
            "fpreta",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct S { using x = int; }; template<class T> typename T::x (*f(T))(T);",
            tmp_path,
            "fpretb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "typename T::x"
    assert b.return_type == "typename T::x (*())(T)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_dependent_return_type_own_parens_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """A dependent return type containing its OWN parenthesized
    sub-expression (`decltype((T::x))`) must not have that group mistaken
    for a parameter-list wrapper: `template<class T> decltype((T::x))
    f(T);` and the `T::y` sibling both compile with no redefinition error
    (confirmed by direct compilation), but scanning FORWARD for the first
    top-level group (an earlier version of the fix above) treated
    `((T::x))` as if it wrapped the real parameter list, discarding the
    dependent operand entirely and collapsing both overloads onto the
    identical `EntityId` (Codex review, PR #943, on a later round)."""
    a = _one(
        _clang_parser(
            "struct S { using x = int; using y = double; };"
            " template<class T> decltype((T::x)) f(T);",
            tmp_path,
            "decltypeparena",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct S { using x = int; using y = double; };"
            " template<class T> decltype((T::y)) f(T);",
            tmp_path,
            "decltypeparenb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "decltype((T::x))"
    assert b.return_type == "decltype((T::y))"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_noexcept_expression_group_is_not_mistaken_for_return_type(
    tmp_path: Path,
) -> None:
    """An ordinary function's `noexcept(expr)` group must not be mistaken
    for a second parameter-list wrapper: `int f() noexcept(cond());`'s
    `qualType` is `"int () noexcept(cond())"`, a genuine two-top-level-
    group spelling (confirmed by direct compilation) that a naive
    scan-from-the-end rule (an earlier version of the fix above, before
    excluding a group preceded by `noexcept`/`throw`) would append onto
    `return_type` wholesale, polluting it with exception-specification
    text -- risking a spurious return-type-changed finding whenever only
    the `noexcept` condition changes (Codex review, PR #943, on a later
    round)."""
    fn = _one(
        _clang_parser(
            "constexpr bool cond() { return true; } int f() noexcept(cond());",
            tmp_path,
            "noexceptgroup",
        ).parse_functions(),
        name="f",
    )
    assert fn.return_type == "int"
    assert fn.is_noexcept is True


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_trailing_return_type_containing_parens_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """A TRAILING return type that itself contains parentheses
    (`auto f(T) -> decltype((T::x))`) must not have those parentheses
    mistaken for a second parameter-list group: `template<class T> auto
    f(T) -> decltype((T::x));` and the `T::y` sibling both compile with
    no redefinition error (confirmed by direct compilation), but locating
    "the" parameter-list group BEFORE ever checking for a top-level `->`
    (the fix directly above, before this correction) picked the
    `decltype`'s own `((T::x))` group instead of the real one before the
    arrow, reducing both overloads' return type to the identical
    `"auto (T) -> decltype"` and discarding the dependent operand
    entirely (Codex review, PR #943, on a later round)."""
    a = _one(
        _clang_parser(
            "struct S { using x = int; using y = double; };"
            " template<class T> auto f(T) -> decltype((T::x));",
            tmp_path,
            "trdecltypea",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct S { using x = int; using y = double; };"
            " template<class T> auto f(T) -> decltype((T::y));",
            tmp_path,
            "trdecltypeb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "decltype((T::x))"
    assert b.return_type == "decltype((T::y))"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_spiral_declarator_preserves_returned_function_parameter_list(
    tmp_path: Path,
) -> None:
    """A function-pointer/reference return type's OWN parameter list is
    real, distinguishing content that must be preserved, not discarded:
    `template<class T> typename S::x (*f(T))(int);` and the sibling
    returning a pointer to a function taking `double` instead both
    compile with no redefinition error (confirmed by direct compilation),
    but treating the spiral wrapper's trailing group as if it were `f`'s
    OWN parameter list (an earlier version of the spiral-declarator fix)
    discarded it entirely, reducing both overloads' return type to the
    identical `"typename S::x (*(T))"` (CodeRabbit review, PR #943)."""
    a = _one(
        _clang_parser(
            "struct S { using x = int; };"
            " template<class T> typename S::x (*f(T))(int);",
            tmp_path,
            "spiralparama",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct S { using x = int; };"
            " template<class T> typename S::x (*f(T))(double);",
            tmp_path,
            "spiralparamb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "typename S::x (*())(int)"
    assert b.return_type == "typename S::x (*())(double)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_quoted_literal_is_not_canonicalized_as_a_param_ref(
    tmp_path: Path,
) -> None:
    """A QUOTED LITERAL (`'N'`) must NOT be canonicalized as a reference to
    a template parameter merely because it collides in spelling.
    `template<char C> struct Literal {}; template<int N> void
    f(Literal<'N'>);` keeps the char literal `'N'` verbatim in clang's own
    `qualType` -- it does not resolve to the (here, unused) non-type
    parameter -- so substituting it anyway fingerprinted the parameter's
    own rename as a remove+add for an otherwise-identical declaration
    (Codex review, PR #943)."""
    header = (
        "template<char C> struct Literal {}; template<int %s> void f(Literal<'N'>);"
    )
    a = _one(
        _clang_parser(header % "N", tmp_path, "litparama").parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(header % "M", tmp_path, "litparamb").parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert a.entity_id.extra[1] == "Literal<'N'>"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_member_pointer_spiral_return_declarator_preserves_returned_function_parameter_list(
    tmp_path: Path,
) -> None:
    """A POINTER-TO-MEMBER-FUNCTION return type is another spiral
    declarator, but its wrapper prefix is a qualified `C::*`, not a bare
    `*`/`&`: `template<class T> int (C::*f(T))(int);` and the sibling
    returning a pointer to a member function taking `double` instead both
    compile with no redefinition error (confirmed by direct compilation),
    but a leading-sigil check restricted to bare `*`/`&` missed this
    shape entirely, falling through to the scan-from-the-end branch and
    discarding the returned function's own parameter list -- the
    identical hazard the ordinary pointer/reference spiral fix already
    closed, just for a class-qualified sigil (Codex review, PR #943, on
    a later round)."""
    a = _one(
        _clang_parser(
            "struct C {}; template<class T> int (C::*f(T))(int);",
            tmp_path,
            "memberptra",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct C {}; template<class T> int (C::*f(T))(double);",
            tmp_path,
            "memberptrb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id != b.entity_id
    assert a.return_type == "int (C::*())(int)"
    assert b.return_type == "int (C::*())(double)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_spiral_trailing_exception_spec_belongs_to_returned_function(
    tmp_path: Path,
) -> None:
    """A spiral (function-pointer-returning) function's TRAILING exception
    specification -- the one following the whole declarator, after the
    returned function's own parameter list -- describes the RETURNED
    function type, not the outer one, and must be KEPT in `return_type`,
    not stripped: `static_assert(!noexcept(f(0)))` and
    `static_assert(noexcept((*(decltype(f(0)))(0))))` both hold for
    `template<class T> int (*f(T))(int) noexcept(noexcept(T()));`
    (confirmed by direct compilation) -- `f` itself is not noexcept, but
    calling through the returned function pointer is. An earlier version of
    this fix (Codex review, PR #943) wrongly assumed any exception spec
    following a spiral return type's trailing group was the OUTER
    function's own, and stripped it -- silently hiding a real
    return-type difference between `noexcept(true)`/`noexcept(false)`
    overload-shaped return types. The distinct hazard this fix closed
    instead -- the OUTER function's own exception spec, spelled differently,
    directly after its own (not the returned function's) parameter list --
    is covered by `test_live_clang_spiral_return_own_exception_spec_excised`
    below."""
    fn = _one(
        _clang_parser(
            "template<class T> int (*f(T))(int) noexcept(noexcept(T()));",
            tmp_path,
            "spiralnoexcept",
        ).parse_functions(),
        name="f",
    )
    assert fn.return_type == "int (*())(int) noexcept(noexcept(T()))"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_spiral_return_own_exception_spec_excised(
    tmp_path: Path,
) -> None:
    """A spiral function's OWN exception specification -- attached directly
    after ITS OWN parameter list, before the returned function's own
    trailing group -- must not leak into `return_type`:
    `template<class T> int (*g(T) noexcept(noexcept(T())))(int);`'s
    `qualType` is `"int (*(T) noexcept(noexcept(T())))(int)"` (confirmed by
    direct compilation that `g(0)` itself IS noexcept there, unlike the
    returned-function-type case above) -- a complex condition is itself
    parenthesized, producing a second top-level group in the same position
    a genuine further-nested spiral level would, so a span-count-only rule
    mistook `g`'s own parameter list for a further wrapper needing recursion
    and preserved it (plus the whole exception spec) verbatim in the
    reported return type (Codex review, PR #943, on a later round)."""
    fn = _one(
        _clang_parser(
            "template<class T> int (*g(T) noexcept(noexcept(T())))(int);",
            tmp_path,
            "spiralownnoexcept",
        ).parse_functions(),
        name="g",
    )
    assert fn.return_type == "int (*())(int)"


def _clang_blocks_parser(
    header_text: str, tmp_path: Path, name: str
) -> _ClangAstParser:
    """Like `test_entity_id_carrier._clang_parser`, but with `-fblocks`
    enabled -- needed only for the block-pointer spiral-return case below,
    which cannot be reproduced without Clang's Blocks extension."""
    header = tmp_path / f"{name}.hpp"
    header.write_text(header_text)
    out = subprocess.run(
        [
            "clang",
            "-fblocks",
            "-x",
            "c++",
            "-std=c++17",
            "--target=x86_64-unknown-linux-gnu",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _ClangAstParser(json.loads(out.stdout), {"c_fn", "c_var"}, set())


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_block_pointer_spiral_return_declarator_preserves_returned_function_parameter_list(
    tmp_path: Path,
) -> None:
    """A function returning a Clang Blocks-extension block pointer is spelled
    as a spiral declarator using `^` instead of `*`/`&`: `int (^f(int))(int);`
    (with `-fblocks` enabled) has `qualType` `"int (^(int))(int)"` (confirmed
    by direct compilation) -- structurally identical to the pointer/reference
    and pointer-to-member spiral cases above, just with a different sigil,
    which `_is_spiral_wrapper_prefix` didn't recognize, falling through to the
    scan-from-the-end branch and discarding the returned block's own
    parameter list. Two sibling declarations differing only in that returned
    block's parameter type must not collapse onto the same `return_type`
    (Codex review, PR #943, on a later round)."""
    f = _one(
        _clang_blocks_parser(
            "int (^f(int))(int);",
            tmp_path,
            "blockspirala",
        ).parse_functions(),
        name="f",
    )
    g = _one(
        _clang_blocks_parser(
            "int (^g(int))(double);",
            tmp_path,
            "blockspiralb",
        ).parse_functions(),
        name="g",
    )
    assert f.return_type == "int (^())(int)"
    assert g.return_type == "int (^())(double)"
    assert f.return_type != g.return_type


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_string_literal_in_return_type_does_not_confuse_paren_scan(
    tmp_path: Path,
) -> None:
    """A dependent return type containing a quoted string/char literal whose
    contents happen to include an unbalanced paren character must not
    confuse the top-level paren scan: `template<class T> decltype("(")
    f(T);`'s `qualType` is `'decltype("(") (T)'` (confirmed by direct
    compilation) -- the literal's own `(` was previously counted as
    structural, so the bracket-depth scan never returned to zero and
    swallowed the real trailing `(T)` parameter-list group along with
    everything else, reducing `return_type` to the bare `"decltype"` and
    discarding the literal's own content entirely (CodeRabbit review, PR
    #943, on a later round). `f` (containing `"("`) and its `")"`-literal
    sibling `g` are legal, coexisting overloads and must resolve to
    distinct return types."""
    f = _one(
        _clang_parser(
            'template<class T> decltype("(") f(T);',
            tmp_path,
            "litparena",
        ).parse_functions(),
        name="f",
    )
    g = _one(
        _clang_parser(
            'template<class T> decltype(")") g(T);',
            tmp_path,
            "litparenb",
        ).parse_functions(),
        name="g",
    )
    assert f.return_type == 'decltype("(")'
    assert g.return_type == 'decltype(")")'
    assert f.return_type != g.return_type


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_decltype_dereferenced_cast_is_not_mistaken_for_spiral_declarator(
    tmp_path: Path,
) -> None:
    """A `decltype` operand that happens to start with a bare `*` sigil
    followed by a parenthesized group -- a dereferenced C-style cast, not a
    declarator -- must not be mistaken for a spiral (function-pointer-
    returning) declarator: `template<class T> decltype(*(typename T::x
    *)0) f(T);`'s `qualType` is `"decltype(*(typename T::x *)0) (T)"`
    (confirmed by direct compilation, alongside a legal, distinct `T::y`
    sibling) -- `_is_spiral_wrapper_prefix` matched the leading `*` and
    treated the whole dependent operand as a spiral wrapper, discarding it
    via `_excise_own_param_list` and collapsing both overloads onto the
    identical `"decltype (*()0) (T)"` (Codex review, PR #943, on a later
    round). The two must resolve to distinct return types, preserving the
    entire dependent expression verbatim."""
    s = "struct S { using x = int; using y = double; };\n"
    f = _one(
        _clang_parser(
            s + "template<class T> decltype(*(typename T::x *)0) f(T);",
            tmp_path,
            "decltypederefa",
        ).parse_functions(),
        name="f",
    )
    g = _one(
        _clang_parser(
            s + "template<class T> decltype(*(typename T::y *)0) g(T);",
            tmp_path,
            "decltypederefb",
        ).parse_functions(),
        name="g",
    )
    assert f.return_type == "decltype(*(typename T::x *)0)"
    assert g.return_type == "decltype(*(typename T::y *)0)"
    assert f.return_type != g.return_type


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_ordinary_functions_own_trailing_attribute_does_not_leak_into_return_type(
    tmp_path: Path,
) -> None:
    """A trailing GNU `__attribute__((...))` clause on an ORDINARY
    (non-pointer-returning) function describes the function itself, not
    its return type, and must not leak into `return_type`: `int f(int)
    __attribute__((sysv_abi));`'s `qualType` is `"int (int)
    __attribute__((sysv_abi))"` (confirmed by direct compilation) -- the
    scan-from-end fallback mistook the attribute's own argument-clause
    group for the real parameter list, reducing `return_type` to `"int
    (int) __attribute__"` instead of `"int"` (Codex review, PR #943, on
    a later round)."""
    ordinary = _one(
        _clang_parser(
            "int f(int) __attribute__((sysv_abi));",
            tmp_path,
            "attrordinary",
        ).parse_functions(),
        name="f",
    )
    assert ordinary.return_type == "int"


def _clang_i386_parser(header_text: str, tmp_path: Path, name: str) -> _ClangAstParser:
    """Like `test_entity_id_carrier._clang_parser`, but targeting
    ``i386-unknown-linux-gnu`` -- needed only for the calling-convention
    case below, where ``stdcall`` vs. ``cdecl`` is observable in clang's
    own `qualType` output only on a 32-bit x86 target (both collapse to
    the platform default on x86-64)."""
    header = tmp_path / f"{name}.hpp"
    header.write_text(header_text)
    out = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-std=c++17",
            "--target=i386-unknown-linux-gnu",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return _ClangAstParser(json.loads(out.stdout), {"c_fn", "c_var"}, set())


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_spiral_returns_own_calling_convention_attribute_is_preserved(
    tmp_path: Path,
) -> None:
    """A spiral (function-pointer-returning) declarator's trailing GNU
    attribute must be PRESERVED, not stripped, since it can be a real,
    ABI-affecting calling-convention difference on the RETURNED function
    type: on an ``i386`` target (where the distinction is observable),
    `int (__attribute__((stdcall)) *h())();` (returning a pointer to a
    stdcall function) and `int (*hc())();` (returning a pointer to an
    ordinary/cdecl function) produce DIFFERENT `qualType`s (confirmed by
    direct compilation: `"int (*())() __attribute__((stdcall))"` vs.
    `"int (*())()"`) for a genuine ABI difference -- stdcall and cdecl
    disagree on stack-cleanup responsibility, so erasing this would hide
    a real breaking change. Writing the identical attribute at the very
    END of the whole declaration instead produces the BYTE-IDENTICAL
    qualType (also confirmed by direct compilation), so clang's own
    printer cannot be used to tell whether such an attribute binds to the
    outer function or the returned one -- an earlier version of this
    function stripped it unconditionally, silently erasing this class of
    ABI difference (Codex review, PR #943, on a still later round)."""
    stdcall = _one(
        _clang_i386_parser(
            "int (__attribute__((stdcall)) *h())();",
            tmp_path,
            "callconvstdcall",
        ).parse_functions(),
        name="h",
    )
    cdecl = _one(
        _clang_i386_parser(
            "int (*hc())();",
            tmp_path,
            "callconvcdecl",
        ).parse_functions(),
        name="hc",
    )
    assert stdcall.return_type == "int (*())() __attribute__((stdcall))"
    assert cdecl.return_type == "int (*())()"
    assert stdcall.return_type != cdecl.return_type


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_relational_operator_in_template_argument_does_not_corrupt_bracket_depth(
    tmp_path: Path,
) -> None:
    """A dependent return type containing a paren-wrapped relational
    operator inside a template argument list must not corrupt the
    bracket-depth tracking used to locate the real parameter list (or a
    trailing return arrow): `template<class T> enable_if_t<(sizeof(T) <
    4), int> f(T);`'s `qualType` is `"enable_if_t<(sizeof(T) < 4), int>
    (T)"` (confirmed by direct compilation) -- an earlier version of the
    bracket-depth scan treated every `<`/`>` as a template bracket
    regardless of paren context, so the relational `<` (reached with
    bracket already 1 from `enable_if_t<`'s own opening) left the counter
    permanently stuck above zero, and the real trailing `(T)` was never
    recognized as a parameter list at all -- `return_type` retained the
    whole `"enable_if_t<(sizeof(T) < 4), int> (T)"` verbatim instead of
    stripping `(T)`, and additionally leaked a trailing `noexcept` on an
    otherwise-identical sibling declaration, corrupting both functions'
    identity (CodeRabbit review, PR #943, on a later round). The
    identical corruption affected the trailing-return-type arrow search
    when the relational operator appeared in a PARAMETER instead: `auto
    f(enable_if_t<(sizeof(T) < 4), int>) -> T;`'s qualType `"auto
    (enable_if_t<(sizeof(T) < 4), int>) -> T"` left the arrow search
    unable to find the real trailing `-> T` at all, falling back to the
    bare placeholder `"auto"`.

    A hand-rolled `enable_if`/`enable_if_t`, not `#include <type_traits>`,
    supplies the relational-operator-in-parens shape: a cross-target
    `--target=x86_64-unknown-linux-gnu` compile (this module's own
    convention, for host-independent `mangledName` spellings) has no
    guaranteed access to a full libstdc++/libc++ sysroot for that target
    on every CI runner OS, confirmed by a real failure on this exact test
    when it still used the system header (macOS CI: clang exited
    non-zero resolving `<type_traits>` for the foreign target)."""
    enable_if_prelude = (
        "template<bool B, class T = void> struct enable_if {};"
        " template<class T> struct enable_if<true, T> { using type = T; };"
        " template<bool B, class T = void>"
        " using enable_if_t = typename enable_if<B, T>::type;\n"
    )
    f = _one(
        _clang_parser(
            enable_if_prelude
            + "template<class T> enable_if_t<(sizeof(T) < 4), int> f(T);",
            tmp_path,
            "relopreturn",
        ).parse_functions(),
        name="f",
    )
    assert f.return_type == "enable_if_t<(sizeof(T) < 4), int>"

    arrow = _one(
        _clang_parser(
            enable_if_prelude + "template<class T> auto g(enable_if_t<(sizeof(T) < 4), "
            "int>) -> T;",
            tmp_path,
            "relopparam",
        ).parse_functions(),
        name="g",
    )
    assert arrow.return_type == "T"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_decltype_address_of_expression_is_not_mistaken_for_spiral_declarator(
    tmp_path: Path,
) -> None:
    """A `decltype` operand that happens to start with a bare `&` sigil
    followed by a parenthesized group -- an address-of a parenthesized
    member-access expression, not a declarator -- must not be mistaken for
    a spiral (reference-returning) declarator: `decltype(&(S::x)) f();`'s
    `qualType` is `"decltype(&(S::x)) ()"` (confirmed by direct
    compilation, alongside a legal, distinct `S::y` sibling) -- its EMPTY
    remainder after the nested group exactly matches a genuine
    reference-returning spiral declarator with no parameters (e.g. `int
    (&f())();`, `qualType` `"int (&())()"`), so a remainder-based check
    alone cannot tell them apart; only the fact that the group is
    `decltype`'s own operand does (Codex review, PR #943, on a later
    round, the address-of sibling of the dereferenced-cast case above).
    The two must resolve to distinct return types, preserving the entire
    dependent expression verbatim."""
    s = "struct S { int x; double y; };\n"
    f = _one(
        _clang_parser(
            s + "decltype(&(S::x)) f();",
            tmp_path,
            "decltypeaddrofa",
        ).parse_functions(),
        name="f",
    )
    g = _one(
        _clang_parser(
            s + "decltype(&(S::y)) g();",
            tmp_path,
            "decltypeaddrofb",
        ).parse_functions(),
        name="g",
    )
    assert f.return_type == "decltype(&(S::x))"
    assert g.return_type == "decltype(&(S::y))"
    assert f.return_type != g.return_type

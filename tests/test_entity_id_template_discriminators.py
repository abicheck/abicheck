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

"""Live-clang regressions for uninstantiated-template ``EntityId`` discriminators.

Split out of ``test_entity_id_carrier.py`` (which grew past the
architecture gate's 1200-line test-file cap) purely to keep that module's
own carrier-shape/cross-backend contract tests legible -- every test here
still exercises the identical ``entity_id_for_function`` "sig" fallback
that module's own docstring documents, just for the specific two-sided
hazard uninstantiated templates raise: two genuinely DISTINCT template
declarations colliding onto one ``EntityId`` (a missing discriminator),
and a pure, non-semantic parameter RENAME wrongly changing one (a
discriminator that isn't rename-blind). Every case here was confirmed by
direct compilation (``clang -Xclang -ast-dump=json``) before being fixed,
per this repo's AGENTS.md discipline -- see each test's own docstring and
PR #943's review history for the individual finding.

This module itself later grew past the architecture gate's 1200-line cap
too: the tests specifically pinning ``abicheck/extract/headers/clang/
return_type.py``'s own PARSING correctness (spiral declarators, exception
specs, GNU attributes, quoted literals, relational operators in template
arguments) were split out into ``test_entity_id_return_type_discriminators.py``
-- no test content changed by that split either, just which file hosts it.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
from test_entity_id_carrier import _clang_parser, _one

#: Two uninstantiated function templates sharing scope, leaf name, and an
#: identical (empty) ordinary parameter list, differing only in
#: template-parameter KIND (type vs. non-type). Confirmed by direct
#: compilation that clang emits no ``mangledName`` for either, so nothing but
#: the template-parameter-kind discriminator tells them apart (Codex review,
#: PR #943).
_TEMPLATE_PARAM_KIND_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f();
    template <int N> void f();
    }
    """
)

#: Two more legal overloads, differing only in template-parameter
#: *packness*, not kind: ``template<class T>`` vs. ``template<class... T>``.
#: The first version of ``function_template_param_kinds`` reduced both to
#: the identical ``("type",)``, missing this collision (Codex review, PR
#: #943).
_TEMPLATE_PARAM_PACKNESS_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f();
    template <class... T> void f();
    }
    """
)

#: A pure template-parameter RENAME, the opposite hazard: ``template<class
#: T, T N>`` and ``template<class U, U N>`` are identical, yet clang's own
#: ``qualType`` for the non-type parameter spells the dependent type
#: literally as the type parameter's own name (``"T"``/``"U"``) (Codex
#: review, PR #943).
_TEMPLATE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <class T, T N> void f();
    }
    """
)
_TEMPLATE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <class U, U N> void f();
    }
    """
)

#: Two more legal overloads, differing in a template-TEMPLATE parameter's
#: own NESTED parameter list: ``template<template<class> class TT>`` vs.
#: ``template<template<class, class> class TT>``. The first, non-recursive
#: version of this discriminator reduced both to the bare ``"template"``
#: tag (Codex review, PR #943).
_TEMPLATE_TEMPLATE_PARAM_NESTED_ARITY_COLLISION = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class TT> void f();
    template <template<class, class> class TT> void f();
    }
    """
)

#: A pure RENAME of a template-TEMPLATE parameter -- the ``TT``/``UU``
#: sibling of ``_TEMPLATE_PARAM_DEPENDENT_RENAME_A``/``B`` above: clang's
#: ``qualType`` for ``N`` spells the dependent type literally as ``TT``'s
#: own name (Codex review, PR #943).
_TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class TT, TT<int>* N> void f();
    }
    """
)
_TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <template<class> class UU, UU<int>* N> void f();
    }
    """
)

#: A pure RENAME affecting an ORDINARY parameter, not a non-type
#: parameter's own type: ``template<class T> void f(T);`` renamed to
#: ``U`` is identical, but clang's spelling names ``T`` literally
#: (Codex review, PR #943).
_TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <class T> void f(T);
    }
    """
)
_TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <class U> void f(U);
    }
    """
)

#: A pure RENAME of a non-type parameter referenced by a LATER non-type
#: parameter's dependent type (``decltype(N)``) (Codex review, PR #943).
_TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_A = textwrap.dedent(
    """
    namespace ns {
    template <int N, decltype(N) K> void f();
    }
    """
)
_TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_B = textwrap.dedent(
    """
    namespace ns {
    template <int M, decltype(M) K> void f();
    }
    """
)

#: A rename of an unused parameter named ``type``, colliding with the
#: generated ``"type-param-N"`` marker prefix (Codex review, PR #943).
_TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_A = textwrap.dedent(
    """
    namespace ns {
    template <class T, class type, T x> void f();
    }
    """
)
_TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_B = textwrap.dedent(
    """
    namespace ns {
    template <class T, class U, T x> void f();
    }
    """
)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_kind_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """``template<class T> void f()`` vs ``template<int N> void f()``
    share scope/leaf/params, and neither is mangled, so ``sig`` had
    nothing to distinguish them by (Codex review, PR #943)."""
    parser = _clang_parser(_TEMPLATE_PARAM_KIND_COLLISION, tmp_path, "tmplkind")
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2 and all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None  # narrows for mypy
        assert fn.entity_id.extra[0] == "sig" and fn.entity_id.extra[-2] == "tmpl"
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"type", "nontype:int"}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_packness_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """``template<class T> void f()`` vs ``template<class... T> void f()``
    are legal overloads; the kind-only fix above still reduced both to
    `("type",)` (Codex review, PR #943)."""
    parser = _clang_parser(_TEMPLATE_PARAM_PACKNESS_COLLISION, tmp_path, "tmplpack")
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2 and all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None  # narrows for mypy
        assert fn.entity_id.extra[0] == "sig" and fn.entity_id.extra[-2] == "tmpl"
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"type", "type..."}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A pure template-parameter RENAME must NOT change the ``EntityId``.
    ``template<class T, T N> void f();``/``template<class U, U N> void
    f();`` are identical, but clang's ``qualType`` for ``N`` spells ``T``
    literally (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_DEPENDENT_RENAME_A, tmp_path, "depa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_DEPENDENT_RENAME_B, tmp_path, "depb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_template_param_nested_arity_discriminates(
    tmp_path: Path,
) -> None:
    """``template<template<class> class TT>`` vs ``template<template<class,
    class> class TT>``; the earlier, non-recursive version reduced both to
    the bare ``"template"`` tag (Codex review, PR #943)."""
    parser = _clang_parser(
        _TEMPLATE_TEMPLATE_PARAM_NESTED_ARITY_COLLISION, tmp_path, "tmpltt"
    )
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2
    assert all(fn.entity_id is not None for fn in pair)
    for fn in pair:
        assert fn.entity_id is not None
        assert fn.entity_id.extra[0] == "sig", fn.entity_id
        assert fn.entity_id.extra[-2] == "tmpl", fn.entity_id
    assert pair[0].entity_id != pair[1].entity_id
    kinds = {fn.entity_id.extra[-1] for fn in pair if fn.entity_id is not None}
    assert kinds == {"template(type)", "template(type,type)"}


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_template_param_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A pure RENAME of a template-TEMPLATE parameter must NOT change the
    ``EntityId``. ``template<template<class> class TT, TT<int>* N> void
    f();`` renamed ``TT``->``UU`` is identical, but clang spells ``N``'s
    type literally (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_A, tmp_path, "ttdepa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_TEMPLATE_PARAM_DEPENDENT_RENAME_B, tmp_path, "ttdepb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0<int> *"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_template_param_rename_in_ordinary_param_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A rename affecting an ORDINARY parameter, not a non-type
    parameter's own type, must not change identity: ``template<class T>
    void f(T);``/``template<class U> void f(U);`` are identical, but
    clang's spelling names the parameter literally (Codex review, PR
    #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_A, tmp_path, "ordpa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_ORDINARY_PARAM_RENAME_B, tmp_path, "ordpb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[1] == "type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_nontype_param_dependent_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A rename of a non-type parameter referenced by a LATER non-type
    parameter's dependent type must NOT change identity -- ``decltype(N)``
    spells ``N`` literally (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_A, tmp_path, "ntdepa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_NONTYPE_PARAM_DEPENDENT_RENAME_B, tmp_path, "ntdepb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:decltype(type-param-0)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_rename_of_param_named_type_does_not_corrupt_a_prior_marker(
    tmp_path: Path,
) -> None:
    """Renaming an unused parameter named ``type`` must NOT corrupt a
    PRIOR parameter's generated marker (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            _TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_A, tmp_path, "gena"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            _TEMPLATE_PARAM_RENAME_COLLIDES_WITH_GENERATED_MARKER_B, tmp_path, "genb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and b.entity_id is not None
    assert a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_dependent_return_type_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """Two templates differing ONLY in a dependent return type are legal,
    coexisting overloads (clang accepts both with no redefinition error),
    but shared scope/leaf/params/kinds collided them (Codex review, PR
    #943); a rename reflected only in the return type must still match."""
    header = (
        "struct A { using x = int; using y = double; };"
        " template<class T> typename T::x f(T);"
        " template<class T> typename T::y f(T);"
    )
    pair = [
        fn
        for fn in _clang_parser(header, tmp_path, "rettmpl").parse_functions()
        if fn.name == "f"
    ]
    assert len(pair) == 2
    assert all(fn.entity_id is not None for fn in pair)
    assert pair[0].entity_id != pair[1].entity_id

    a = _one(
        _clang_parser(
            "struct A { using x = int; }; template<class T> typename T::x f(T);",
            tmp_path,
            "retrena",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "struct A { using x = int; }; template<class U> typename U::x f(U);",
            tmp_path,
            "retrenb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_return_type_top_level_cv_discriminates_overloaded_templates(
    tmp_path: Path,
) -> None:
    """A top-level by-value cv on a TEMPLATE's return type is a real
    discriminator, unlike an ordinary function (`const int f(int);` is a
    redefinition there). `template<class T> T f(T);` vs `const T f(T);`
    coexist (Codex review)."""
    header = "template<class T> T f(T); template<class T> const T f(T);"
    parser = _clang_parser(header, tmp_path, "retcv")
    pair = [fn for fn in parser.parse_functions() if fn.name == "f"]
    assert len(pair) == 2
    assert all(fn.entity_id is not None for fn in pair)
    assert pair[0].entity_id != pair[1].entity_id


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_enclosing_class_template_param_rename_does_not_change_identity(
    tmp_path: Path,
) -> None:
    """A rename of an ENCLOSING class template's own parameter must NOT
    change an ordinary (non-template) member's identity. `f` here is never
    itself a ``FunctionTemplateDecl``, so only the class's own parameter
    names -- accumulated by `_walk`, not just a direct function template's
    -- canonicalize its dependent ordinary parameter type (Codex review,
    PR #943)."""
    a = _one(
        _clang_parser(
            "template<class T> struct A { void f(T); };", tmp_path, "clstmpla"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "template<class U> struct A { void f(U); };", tmp_path, "clstmplb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_globally_qualified_name_is_not_canonicalized_as_a_param_ref(
    tmp_path: Path,
) -> None:
    """An EXPLICITLY globally-qualified name (`::T::X`) must NOT be
    canonicalized as a reference to a template parameter merely because it
    collides in spelling. `namespace T { struct X {}; } template<class T>
    void f(::T::X);` keeps `::T::X` verbatim in clang's own `qualType` --
    it does not resolve to the parameter -- so substituting it anyway
    fingerprinted the (unused) parameter's own rename as a remove+add for
    an otherwise-identical declaration (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            "namespace T { struct X {}; } template<class T> void f(::T::X);",
            tmp_path,
            "gqa",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "namespace T { struct X {}; } template<class U> void f(::T::X);",
            tmp_path,
            "gqb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert a.entity_id.extra[1] == "::T::X"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
@pytest.mark.parametrize(
    "access_expr",
    ["S{}.N", "((S*)0)->N"],
    ids=["dot", "arrow"],
)
def test_live_clang_member_access_name_is_not_canonicalized_as_a_param_ref(
    tmp_path: Path, access_expr: str
) -> None:
    """A MEMBER-ACCESS expression (`S{}.N` or `((S*)0)->N`) must NOT be
    canonicalized as a reference to a template parameter merely because it
    collides in spelling. `struct S { int N; }; template<int N>
    void f(decltype(<access_expr>));` keeps the member name `N` verbatim in
    clang's own `qualType` -- it does not resolve to the (here, unused)
    non-type template parameter -- so substituting it anyway fingerprinted
    the parameter's own rename as a remove+add for an otherwise-identical
    declaration (Codex review, PR #943, same collision shape as the
    globally-qualified-name case above, just for `.`/`->` instead of `::`)."""
    header = "struct S { int N; }; template<int %s> void f(decltype(%s));"
    a = _one(
        _clang_parser(
            header % ("N", access_expr), tmp_path, "membaccessa"
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            header % ("M", access_expr), tmp_path, "membaccessb"
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    # clang normalizes `(S*)` to `(S *)` in its own `qualType` spelling, so
    # assert the member name survived un-substituted rather than the exact
    # (backend-dependent) cast spacing.
    assert a.entity_id.extra[1].endswith("N)")
    assert "type-param" not in a.entity_id.extra[1]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_nested_template_template_param_sees_enclosing_names(
    tmp_path: Path,
) -> None:
    """A NESTED non-type parameter (inside a template-template parameter's
    own parameter list) can legally reference an ENCLOSING parameter's
    name: `template<class T, template<T> class TT> void f();` is valid
    C++ (confirmed by direct compilation), and clang's `qualType` for the
    nested, unnamed non-type parameter inside `TT` spells its type as the
    literal enclosing name `T`. A pure rename of the enclosing parameter
    (`T` -> `U`) must not change the identity, but the recursive descent
    used to start the nested list's own substitution scope empty, so the
    nested `nontype:T`/`nontype:U` entries never got canonicalized
    against the enclosing scope (Codex review, PR #943)."""
    a = _one(
        _clang_parser(
            "template<class T, template<T> class TT> void f();",
            tmp_path,
            "nesttta",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "template<class U, template<U> class TT> void f();",
            tmp_path,
            "nestttb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "template(nontype:type-param-0)"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_enclosing_class_template_param_rename_does_not_change_member_template_identity(
    tmp_path: Path,
) -> None:
    """A rename of an ENCLOSING class template's own parameter must NOT
    change a MEMBER FUNCTION TEMPLATE's identity either: `template<class
    T> struct A { template<T N> void f(); };` renamed to `template<class
    U> struct A { template<U N> void f(); };` is the identical
    declaration, and clang's `qualType` for the member template's own
    non-type parameter `N` spells its type literally as the enclosing
    class template's own parameter name (`"T"`/`"U"`) -- confirmed by
    direct compilation. The identical hazard the sibling test above
    fixes for an ordinary (non-template) member, one level further in:
    `function_template_param_kinds` must also be seeded with the
    enclosing class template's own parameter names, not just its own
    (Codex review, PR #943, on a later round)."""
    a = _one(
        _clang_parser(
            "template<class T> struct A { template<T N> void f(); };",
            tmp_path,
            "membtmpla",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            "template<class U> struct A { template<U N> void f(); };",
            tmp_path,
            "membtmplb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert a.entity_id.extra[-1] == "nontype:type-param-0"


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_qualified_member_template_name_is_not_canonicalized_as_a_param_ref(
    tmp_path: Path,
) -> None:
    """A QUALIFIED MEMBER-TEMPLATE name (`S::template N<int>()`, the
    disambiguated form a dependent qualified-id naming a template member
    requires) must NOT be canonicalized as a reference to a same-spelled
    template parameter merely because it collides in spelling. `struct
    Base { template<class T> static int N(); }; template<int N, class S>
    void f(decltype(S::template N<int>()));` keeps `S::template N<int>()`
    verbatim in clang's own `qualType` -- it does not resolve to the
    non-type parameter `N` -- so substituting it anyway fingerprinted a
    pure rename of that unrelated parameter (`N` -> `M`) as a remove+add
    for an otherwise-identical declaration, since the member-template
    call's own raw text is unaffected by that rename either way (Codex
    review, PR #943, on a later round -- the member-access case above's
    own sibling, one qualifier keyword further, which the existing
    `(?<!::)` lookbehind doesn't reach since `template` and a space sit
    between the `::` and the name)."""
    prelude = "struct Base { template<class T> static int N(); };\n"
    a = _one(
        _clang_parser(
            prelude
            + "template<int N, class S> void f(decltype(S::template N<int>()));",
            tmp_path,
            "membtmplnamea",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            prelude
            + "template<int M, class S> void f(decltype(S::template N<int>()));",
            tmp_path,
            "membtmplnameb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert "N<int>()" in a.entity_id.extra[1]
    assert "type-param" not in a.entity_id.extra[1].split("::template ")[1]


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_live_clang_user_defined_literal_suffix_is_not_canonicalized_as_a_param_ref(
    tmp_path: Path,
) -> None:
    """A USER-DEFINED LITERAL SUFFIX (`'x'_tag`) is an operator name, not a
    reference to a template parameter, even though it collides in
    spelling and sits immediately (no space) after the closing quote of
    the literal it attaches to -- outside the quoted-literal span itself,
    unlike the plain-quoted-literal case above. `struct X { char v; };
    constexpr X operator""_tag(char c) { return {c}; } template<int _tag>
    void f(decltype('x'_tag));` keeps `'x'_tag` verbatim in clang's own
    `qualType` -- it does not resolve to the (here, unused) non-type
    parameter -- so substituting it anyway fingerprinted the parameter's
    own rename as a remove+add for an otherwise-identical declaration
    (Codex review, PR #943, on a later round)."""
    prelude = (
        'struct X { char v; }; constexpr X operator""_tag(char c) { return {c}; }\n'
    )
    a = _one(
        _clang_parser(
            prelude + "template<int _tag> void f(decltype('x'_tag));",
            tmp_path,
            "udlsuffixa",
        ).parse_functions(),
        name="f",
    )
    b = _one(
        _clang_parser(
            prelude + "template<int _other> void f(decltype('x'_tag));",
            tmp_path,
            "udlsuffixb",
        ).parse_functions(),
        name="f",
    )
    assert a.entity_id is not None and a.entity_id == b.entity_id
    assert a.entity_id.extra[1] == "decltype('x'_tag)"

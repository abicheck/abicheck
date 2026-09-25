"""Cost-only changes to extraction helpers must not change their answers.

* ``_strip_bare_anonymous_type_location`` gained a substring prefilter;
  oracle: the regex substitution it guards, applied unconditionally.
* ``_index_template_param_defaults`` accepts the names index its caller
  already built instead of re-walking the document; oracle: the same
  function building that index itself, on real clang ASTs.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.extract.headers.clang.template_param_indexes import (
    build_template_param_indexes,
)
from abicheck.extract.headers.clang.templates import (
    _index_template_param_defaults,
    _index_template_param_kinds,
    _index_template_param_names,
    build_specialization_index,
)
from abicheck.model import graph_identity

_pieces = st.sampled_from(
    [
        "lambda at /a/b/foo.hpp:1:2",
        "unnamed struct at /x/y.h:3:4",
        "anonymous union at C:/w/z.h:5:6",
        "(lambda at /p/q.h:7:8)",
        '"lambda at /quoted/x.h:1:2"',
        "Tag<",
        ">",
        "ns::",
        "lambd",
        "anonymou",
        "at /no/marker.h:1:2",
        " ",
        "unnamed",
    ]
)


def _unfiltered(name: str) -> str:
    """The function body without the prefilter (the pre-change behaviour)."""
    spans = graph_identity._quoted_spans(name)

    def _replace(match):  # type: ignore[no-untyped-def]
        if any(start <= match.start() < end for start, end in spans):
            return match.group(0)
        marker, path, line, col = match.groups()
        disc = graph_identity._declaring_header_discriminator(path)
        return f"{marker}:{disc}:{line}:{col}"

    return graph_identity._BARE_ANON_TYPE_LOCATION_RE.sub(_replace, name)


@settings(max_examples=500, deadline=None)
@given(st.lists(_pieces, max_size=8).map("".join))
def test_prefilter_never_changes_the_result(name):
    assert graph_identity._strip_bare_anonymous_type_location(name) == _unfiltered(name)


_TEMPLATE_HEADER = """
namespace n {
template <class, class> struct A;
template <class X, class Y> struct A;
template <class T, class U = T> struct A { T t; U u; };
template <class T, int N = 3, template <class> class C = std::allocator> struct B {};
template <class T> struct Outer { template <class V, class W = V> struct Inner {}; };
A<int> a; Outer<char>::Inner<long> i;
}
"""


@pytest.fixture(scope="module")
def template_ast():
    if shutil.which("clang") is None:
        pytest.skip("clang not found")
    out = subprocess.run(
        [
            "clang",
            "-x",
            "c++",
            "-std=c++17",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            "-",
        ],
        input="#include <memory>\n" + _TEMPLATE_HEADER,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_defaults_with_shared_names_equal_self_built(template_ast):
    names = _index_template_param_names(template_ast)
    assert _index_template_param_defaults(template_ast, names) == (
        _index_template_param_defaults(template_ast)
    )
    # Non-vacuous: the fixture really exercises a dependent default.
    assert any(v and any(d is not None for d in v) for v in names.values())


def test_bundle_equals_the_three_independent_builders(template_ast):
    bundle = build_template_param_indexes(template_ast)
    as_tuples = {
        k: tuple(v) for k, v in _index_template_param_defaults(template_ast).items()
    }
    assert dict(bundle.defaults) == as_tuples
    assert dict(bundle.names) == {
        k: tuple(v) for k, v in _index_template_param_names(template_ast).items()
    }
    assert dict(bundle.kinds) == {
        k: tuple(v) for k, v in _index_template_param_kinds(template_ast).items()
    }


def test_specialization_index_unchanged_by_builder_order(template_ast):
    from abicheck.dumper_clang_vtable import is_record_definition

    fresh = build_specialization_index(
        template_ast, is_record_definition=is_record_definition
    )
    prebuilt = build_specialization_index(
        template_ast,
        is_record_definition=is_record_definition,
        param_kinds_by_qualname=_index_template_param_kinds(template_ast),
        param_defaults_by_qualname=_index_template_param_defaults(template_ast),
        param_names_by_qualname=_index_template_param_names(template_ast),
    )
    assert fresh == prebuilt
    assert fresh, "fixture produced no specializations"

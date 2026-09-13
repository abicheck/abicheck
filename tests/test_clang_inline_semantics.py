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

"""Bug class ``extraction.implicit_language_rule_read_off_one_explicit_key``.

The reported instance: on real public headers abicheck demanded that a
``constexpr`` constructor be exported by the library, warning that a consumer
would otherwise hit an undefined-symbol error. Consumers built against those
headers link and run fine under both GCC and Clang -- the definition comes
from the header.

The mechanism: the clang header backend set ``Function.is_inline`` from
``bool(node.get("inline"))``, and clang's JSON AST emits that key **only** for
the explicit ``inline`` keyword. C++ makes three further shapes implicitly
inline, and clang signals none of them through that key. castxml's frontend
resolves implicit inline before emitting, so it reported ``inline="1"`` for
all of them -- the two backends disagreed on the same declaration while
``scripts/backend_capabilities.py`` claimed full parity for the fact.

Why the class is wider than ``constexpr``. Fixing only ``constexpr`` (the
shape in the report) leaves an ordinary member defined in its class body --
``int get() const { return v_; }``, which carries neither ``inline`` nor
``constexpr`` in clang's JSON -- still false-positive. That residual is
asserted directly in ``test_constexpr_only_fix_would_leave_a_residual``, so
the narrower fix cannot be reintroduced without failing here.

Oracles, in order of independence:

* castxml -- a different frontend resolving the same language rule. This is
  the primary oracle and the one the class is really about; it is not derived
  from anything under test.
* real clang output -- the tables below are transcribed from measured
  ``-ast-dump=json`` attributes, not from what the parser wants to see.
* the export-obligation consumer, end to end through the CLI over a real
  ``g++`` library, which is where the report came from.

``is_inline`` remains a fact about the declaration's *linkage*: it is True for
``inline int f();`` with no body, and says nothing about what a consumer
emitted. See ``docs/contribute/known-gaps.md``'s linkage-blind-removal entry.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.dumper import _CastxmlParser, _ClangAstParser
from abicheck.extract.headers.clang.inline_semantics import (
    encloses_class_scope,
    is_effectively_inline,
)
from abicheck.model.identity import Anonymous, InlineNamespace, Namespace, Record

_RECORD = (Record("W"),)
_NAMESPACE = (Namespace("ns"),)
_NESTED = (Namespace("ns"), Record("W"))
_MEMBER_KINDS = (
    "CXXMethodDecl",
    "CXXConstructorDecl",
    "CXXDestructorDecl",
    "CXXConversionDecl",
)
_BODY = {"kind": "CompoundStmt", "inner": []}


def _node(kind, **attrs):
    node = {"kind": kind, "name": "f"}
    if attrs.pop("body", False):
        node["inner"] = [{"kind": "ParmVarDecl"}, _BODY]
    node.update(attrs)
    return node


# ── the primitive, over its whole input domain ──────────────────────────────


def test_encloses_class_scope_reads_only_the_innermost_segment():
    """A record *anywhere* outward is not the same as a record enclosing."""
    assert encloses_class_scope(_RECORD) is True
    assert encloses_class_scope(_NESTED) is True
    assert encloses_class_scope(()) is False
    assert encloses_class_scope(_NAMESPACE) is False
    assert encloses_class_scope((InlineNamespace("v1"),)) is False
    # A record enclosing a *namespace* is not an in-class definition.
    assert encloses_class_scope((Record("W"), Namespace("ns"))) is False
    assert encloses_class_scope((Record("W"), Anonymous("u", "k"))) is False


@pytest.mark.parametrize("scope", [(), _NAMESPACE, _RECORD, _NESTED])
@pytest.mark.parametrize("kind", ("FunctionDecl",) + _MEMBER_KINDS)
def test_explicit_inline_keyword_always_wins(kind, scope):
    """The one key clang does emit keeps working, in every position."""
    assert is_effectively_inline(_node(kind, inline=True), scope) is True


@pytest.mark.parametrize("scope", [(), _NAMESPACE, _RECORD, _NESTED])
@pytest.mark.parametrize("kind", ("FunctionDecl",) + _MEMBER_KINDS)
@pytest.mark.parametrize("extra", [{}, {"immediate": True}])
def test_constexpr_is_implicitly_inline_everywhere(kind, scope, extra):
    """``constexpr`` -- and ``consteval``, which clang spells with it too."""
    assert is_effectively_inline(_node(kind, constexpr=True, **extra), scope) is True


@pytest.mark.parametrize("kind", _MEMBER_KINDS)
def test_member_defined_in_class_body_is_implicitly_inline(kind):
    """The shape a ``constexpr``-only fix misses: no ``inline``, no ``constexpr``."""
    assert is_effectively_inline(_node(kind, body=True), _RECORD) is True
    assert is_effectively_inline(_node(kind, body=True), _NESTED) is True


@pytest.mark.parametrize("kind", _MEMBER_KINDS)
def test_defaulted_in_class_is_implicitly_inline(kind):
    """``= default`` is a definition, though clang attaches no CompoundStmt."""
    assert (
        is_effectively_inline(_node(kind, explicitlyDefaulted="default"), _RECORD)
        is True
    )


@pytest.mark.parametrize("kind", _MEMBER_KINDS)
def test_member_declared_but_not_defined_keeps_its_export_obligation(kind):
    """The negative half. Without it the fix could be "always True".

    A member *declared* in the class and defined out of line really does owe
    an exported symbol; reporting it inline would trade this false positive
    for a false negative on the check's whole reason to exist.
    """
    assert is_effectively_inline(_node(kind), _RECORD) is False


@pytest.mark.parametrize("kind", _MEMBER_KINDS)
def test_out_of_line_definition_is_not_implicitly_inline(kind):
    """Same node kind, same body, same mangled name -- only the scope differs.

    ``void W::f() {}`` written at namespace scope is not implicitly inline.
    The enclosing scope is the only thing that separates it from the in-class
    definition, which is why the primitive takes a scope path at all.
    """
    assert is_effectively_inline(_node(kind, body=True), ()) is False
    assert is_effectively_inline(_node(kind, body=True), _NAMESPACE) is False


def test_free_function_with_a_body_is_not_implicitly_inline():
    """A non-inline definition in a header is an ODR bug in the user's code,
    not something to silently reclassify as vague linkage."""
    assert is_effectively_inline(_node("FunctionDecl", body=True), ()) is False
    assert is_effectively_inline(_node("FunctionDecl", body=True), _NAMESPACE) is False
    # Even lexically inside a record, a plain FunctionDecl is not a member.
    assert is_effectively_inline(_node("FunctionDecl", body=True), _RECORD) is False


@pytest.mark.parametrize("kind", _MEMBER_KINDS)
def test_deleted_member_is_not_treated_as_a_definition(kind):
    """``= delete`` has no definition to inline, and clang spells it apart."""
    assert is_effectively_inline(_node(kind, explicitlyDeleted=True), _RECORD) is False
    assert (
        is_effectively_inline(_node(kind, explicitlyDefaulted="deleted"), _RECORD)
        is False
    )


def test_plain_declaration_is_never_inline():
    assert is_effectively_inline(_node("FunctionDecl"), ()) is False
    assert is_effectively_inline({"kind": "FunctionDecl"}, ()) is False
    # Missing/garbage `inner` must not raise.
    assert (
        is_effectively_inline({"kind": "CXXMethodDecl", "inner": None}, _RECORD)
        is False
    )


def test_exhaustive_domain_sweep_has_both_outcomes_and_is_order_free():
    """Vacuity guard over the whole cartesian product.

    A sweep whose oracle collapsed to a constant would pass every assertion
    above that happens to agree with it; this states that the domain really
    does produce both answers, and that the result never depends on dict
    insertion order (the node is a parsed JSON object, whose key order is the
    compiler's, not ours).
    """
    seen = set()
    domain = itertools.product(
        ("FunctionDecl",) + _MEMBER_KINDS,
        ((), _NAMESPACE, _RECORD, _NESTED),
        (None, True),
        (None, True),
        (False, True),
        (None, "default", "deleted"),
    )
    for kind, scope, inline, constexpr, body, defaulted in domain:
        attrs: dict[str, object] = {"body": body}
        if inline:
            attrs["inline"] = True
        if constexpr:
            attrs["constexpr"] = True
        if defaulted:
            attrs["explicitlyDefaulted"] = defaulted
        node = _node(kind, **attrs)
        got = is_effectively_inline(node, scope)
        seen.add(got)
        reversed_node = dict(reversed(list(node.items())))
        assert is_effectively_inline(reversed_node, scope) is got
    assert seen == {True, False}


# ── differential against castxml, the independent oracle ────────────────────

_CORPUS = """
#pragma once
namespace lib {
class W {
public:
  constexpr W(bool b) : v_(b) {}
  constexpr bool cget() const { return v_; }
  int get() const { return v_; }
  int get_ref() const noexcept { return v_; }
  W() = default;
  ~W() = default;
  W(const W&) = default;
  void declared_only() const;
  static int counter() { return 0; }
  operator bool() const { return v_; }
private:
  bool v_;
};
void free_declared(int);
inline void free_inline(int) {}
constexpr int free_constexpr(int x) { return x; }
consteval int free_consteval(int x) { return x; }
}
"""

_NEEDS_CLANG = pytest.mark.skipif(
    shutil.which("clang++") is None, reason="needs clang++ for a real JSON AST"
)
_NEEDS_CASTXML = pytest.mark.skipif(
    shutil.which("castxml") is None, reason="needs castxml as the independent oracle"
)
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Itanium-vs-MSVC mangling makes the two backends' keys incomparable elsewhere",
)


def _clang_functions(header):
    out = subprocess.run(
        [
            "clang++",
            "-std=c++20",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    parser = _ClangAstParser(
        json.loads(out.stdout),
        set(),
        set(),
        public_header_paths=[str(header)],
        no_binary_evidence=True,
    )
    return parser.parse_functions()


def _castxml_functions(header, tmp_path):
    xml = tmp_path / "cx.xml"
    subprocess.run(
        ["castxml", "--castxml-output=1", "-std=c++20", "-o", str(xml), str(header)],
        capture_output=True,
        text=True,
        check=True,
    )
    parser = _CastxmlParser(
        ET.parse(xml).getroot(),
        set(),
        set(),
        public_header_paths=[str(header)],
        no_binary_evidence=True,
    )
    return parser.parse_functions()


@_LINUX_ONLY
@_NEEDS_CLANG
@_NEEDS_CASTXML
@pytest.mark.integration
def test_two_backends_agree_on_is_inline_for_every_shared_declaration(tmp_path):
    """The class invariant, against an oracle outside this codebase.

    Keyed by mangled name, over every declaration both frontends resolve. A
    declaration the two spell differently (castxml's synthetic ctor/dtor keys)
    is a separate, pre-existing identity matter and is excluded rather than
    silently compared; the assertion below proves the comparison did not
    reduce to nothing.
    """
    header = tmp_path / "api.hpp"
    header.write_text(_CORPUS)

    def index(functions):
        out: dict[str, set[bool]] = {}
        for fn in functions:
            if fn.mangled and fn.mangled.startswith("_Z"):
                out.setdefault(fn.mangled, set()).add(fn.is_inline)
        return out

    clang_idx = index(_clang_functions(header))
    castxml_idx = index(_castxml_functions(header, tmp_path))
    shared = sorted(set(clang_idx) & set(castxml_idx))

    assert len(shared) >= 6, f"differential compared too little: {shared}"
    disagreements = {
        key: (sorted(castxml_idx[key]), sorted(clang_idx[key]))
        for key in shared
        if castxml_idx[key] != clang_idx[key]
    }
    assert not disagreements, f"backends disagree on is_inline: {disagreements}"
    # Both answers must occur, or agreement would be trivially satisfiable.
    assert {v for key in shared for v in clang_idx[key]} == {True, False}


# ── end to end, where the report came from ──────────────────────────────────

_E2E_HEADER = """
#pragma once
namespace svs {
class OptionalBool {
public:
  constexpr OptionalBool(bool v) : value_(v) {}
  constexpr bool cget() const { return value_; }
  int plain() const { return value_; }
  void exported_impl() const;
private:
  bool value_;
};
void real_api(int);
}
"""

_E2E_SOURCE = """
#include "svs.hpp"
namespace svs {
void OptionalBool::exported_impl() const {}
void real_api(int) {}
}
"""


@_LINUX_ONLY
@_NEEDS_CLANG
@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++ to build a library")
def test_header_defined_members_raise_no_export_obligation_end_to_end(tmp_path):
    """The user-facing result, through the real CLI over a real library.

    Nothing here is header-only: the library is built by ``g++`` and genuinely
    does not export the header-defined members, which is exactly the input
    that produced three ``public_not_exported`` findings at
    ``Confidence.HIGH``. ``exported_impl``/``real_api`` are exported and must
    stay unflagged, so the run cannot pass by disabling the check.
    """
    (tmp_path / "svs.hpp").write_text(_E2E_HEADER)
    (tmp_path / "svs.cpp").write_text(_E2E_SOURCE)
    lib = tmp_path / "libsvs.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-O2",
            "-g0",
            "-o",
            str(lib),
            str(tmp_path / "svs.cpp"),
        ],
        check=True,
        capture_output=True,
    )
    report = tmp_path / "out.json"
    env = dict(os.environ, ABICHECK_AST_FRONTEND="clang")
    runner = CliRunner(env=env)
    result = runner.invoke(
        main,
        [
            "compare",
            str(lib),
            str(lib),
            "--header",
            str(tmp_path / "svs.hpp"),
            "-o",
            f"json={report}",
        ],
        env=env,
    )
    assert report.exists(), result.output

    data = json.loads(report.read_text())
    flagged = [
        c for c in (data.get("changes") or []) if c.get("kind") == "public_not_exported"
    ]
    assert not flagged, [c.get("symbol") for c in flagged]
    assert data.get("verdict") == "NO_CHANGE", data.get("verdict")


@_LINUX_ONLY
@_NEEDS_CLANG
def test_constexpr_only_fix_would_leave_a_residual(tmp_path):
    """The class, not the instance: pins why ``constexpr`` alone is not enough.

    ``plain()`` is an ordinary member defined in the class body. Clang's JSON
    gives it neither ``inline`` nor ``constexpr``, so the narrower fix leaves
    it non-inline and still owing an export. Asserted against the measured
    clang attributes so the claim rests on what the compiler emits.
    """
    header = tmp_path / "api.hpp"
    header.write_text(_E2E_HEADER)
    by_name = {fn.name: fn for fn in _clang_functions(header)}

    plain = by_name["plain"]
    assert plain.is_inline is True
    # …and the narrow predicate, evaluated here rather than described in prose.
    node = next(n for n in _raw_function_nodes(header) if n.get("name") == "plain")
    assert not (node.get("inline") or node.get("constexpr"))


def _raw_function_nodes(header):
    out = subprocess.run(
        [
            "clang++",
            "-std=c++20",
            "-Xclang",
            "-ast-dump=json",
            "-fsyntax-only",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("kind") in ("FunctionDecl",) + _MEMBER_KINDS:
            yield node
        for child in node.get("inner") or []:
            yield from walk(child)

    return list(walk(json.loads(out.stdout)))

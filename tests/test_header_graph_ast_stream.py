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

"""The streamed AST projection must equal the whole-tree one, exactly.

Two separable claims, tested separately because they fail for different
reasons and one of them needs a real compiler:

1. **The scanner finds the right bytes.** ``stream_top_level_decls`` must
   yield exactly ``json.load(f)["inner"]``, for any document of clang's
   shape. Checked against the stdlib ``json`` module as an independent
   oracle over generated documents -- deliberately not against a second
   hand-written scanner, which would only agree with itself
   (``AGENTS.md``, "A matrix test needs an oracle, not just a type check").

2. **Streaming the readers over those bytes gives the same projection.**
   Checked against :func:`project_header_graph_ast` itself over real
   ``clang``-generated ASTs.

The second claim has a trap this suite is built around. A streamed walk
differs from a whole-tree walk only where clang's format is *order*
dependent across top-level siblings, and there are exactly two such places
(the sticky declaring file, and an anonymous tag and its declarator landing
either side of a boundary). A fixture that happens not to contain those
shapes makes the equivalence test pass while proving nothing about the code
that handles them -- which is not hypothetical: the first fixture used
during this module's development matched digests exactly with the
anonymous-tag threading **deleted**.
:class:`TestTheFixtureStillExercisesTheBoundaryRules` is the guard, asserting
that the compiled fixture really does contain both shapes, so the
equivalence test below cannot quietly stop testing what it is for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    project_header_graph_ast,
)
from abicheck.buildsource.header_graph_ast_stream import (
    ClangAstStreamError,
    project_header_graph_ast_file,
    stream_top_level_decls,
)

# A header built to contain, at **top level** (so each lands in its own
# element of the TU's `inner`, with the boundary falling between them):
#
#   * an anonymous `EnumDecl` followed by the `TypedefDecl` that names it;
#   * an anonymous `EnumDecl` followed by the `VarDecl` that declares it;
#   * enough plain declarations from one file that later ones carry no
#     `loc.file` of their own and inherit it from an earlier sibling.
_BOUNDARY_HEADER = """
#pragma once
namespace detail {
typedef int Handle;
struct Internal { int v; };
}
typedef enum : detail::Handle { kB0 = 0 } NamedByTypedef;
enum : detail::Handle { kA0 = 0 } g_declared_by_var;
struct Public {
    int plain;
    detail::Internal dep;
    enum : detail::Handle { kNested = 1 } nested_field;
};
int api_one(detail::Internal* p);
int api_two(const detail::Internal& p);
inline int api_three() { return api_one(nullptr) + kA0; }
"""


def _clang() -> str | None:
    return shutil.which("clang++")


def _dump_ast(tmp_path: Path, source: str) -> Path:
    """Compile *source* to a real ``clang -ast-dump=json`` document on disk."""
    clang = _clang()
    assert clang is not None
    header = tmp_path / "api.h"
    header.write_text(source)
    out = tmp_path / "ast.json"
    with out.open("wb") as fh:
        proc = subprocess.run(  # noqa: S603 - fixed argv, never shell=True
            [
                clang,
                "-Xclang",
                "-ast-dump=json",
                "-fsyntax-only",
                "-std=c++17",
                "-I",
                str(tmp_path),
                str(header),
            ],
            stdout=fh,
            stderr=subprocess.PIPE,
            check=False,
        )
    assert proc.returncode == 0, proc.stderr.decode()[:2000]
    return out


needs_clang = pytest.mark.skipif(
    _clang() is None or not sys.platform.startswith("linux"),
    reason="needs a real clang++ to produce an AST document (Linux-scoped)",
)


# --------------------------------------------------------------------------
# 1. The scanner, against the stdlib as oracle.
# --------------------------------------------------------------------------

#: JSON values that may appear *inside* a top-level element. Strings are
#: drawn from an alphabet that deliberately includes every byte the scanner
#: treats as structural, plus the backslash and quote whose interaction is
#: the one genuinely subtle part of `_STRING_END`.
_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**40), max_value=2**40),
    st.text(alphabet='{}[]"\\ \n\tabcé:,', max_size=24),
)
_VALUES = st.recursive(
    _SCALARS,
    lambda inner: st.one_of(
        st.lists(inner, max_size=3),
        st.dictionaries(
            st.text(alphabet='ab"\\{', min_size=1, max_size=5), inner, max_size=3
        ),
    ),
    max_leaves=12,
)
#: A top-level element is always a JSON **object** -- clang's own shape, and
#: the contract `stream_top_level_decls` documents.
_ELEMENTS = st.dictionaries(
    st.text(alphabet='ab"\\inner', min_size=1, max_size=6), _VALUES, max_size=4
)


def _write(tmp_path: Path, doc: object, *, indent: int | None) -> Path:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(doc, indent=indent), encoding="utf-8")
    return path


class TestScannerAgainstStdlibJson:
    """`stream_top_level_decls` yields exactly what `json.load` would."""

    @settings(max_examples=150, deadline=None)
    @given(
        elements=st.lists(_ELEMENTS, max_size=6),
        indent=st.sampled_from([None, 0, 2, 4]),
    )
    def test_yields_exactly_the_root_inner_array(
        self, elements: list[dict], indent: int | None, tmp_path_factory
    ) -> None:
        tmp_path = tmp_path_factory.mktemp("scan")
        doc = {"id": "0x1", "kind": "TranslationUnitDecl", "inner": elements}
        path = _write(tmp_path, doc, indent=indent)
        assert list(stream_top_level_decls(path)) == json.load(path.open())["inner"]

    def test_absent_inner_yields_nothing(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"id": "0x1", "kind": "TranslationUnitDecl"}, indent=2)
        assert list(stream_top_level_decls(path)) == []

    def test_empty_inner_yields_nothing(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"kind": "TranslationUnitDecl", "inner": []}, indent=2)
        assert list(stream_top_level_decls(path)) == []

    def test_a_string_value_spelled_inner_is_not_mistaken_for_the_key(
        self, tmp_path: Path
    ) -> None:
        """`"kind": "inner"` must not be taken for the array key.

        A `buf.find(b'"inner"')` scanner -- the obvious implementation --
        fails exactly here, and then silently projects the wrong bytes.
        """
        doc = {"kind": "inner", "name": "inner", "inner": [{"kind": "Real"}]}
        path = _write(tmp_path, doc, indent=2)
        assert list(stream_top_level_decls(path)) == [{"kind": "Real"}]

    def test_a_nested_inner_key_is_not_mistaken_for_the_root_one(
        self, tmp_path: Path
    ) -> None:
        doc = {"loc": {"inner": ["decoy"]}, "inner": [{"kind": "Real"}]}
        path = _write(tmp_path, doc, indent=2)
        assert list(stream_top_level_decls(path)) == [{"kind": "Real"}]

    @pytest.mark.parametrize(
        "spelling",
        [
            r'{"inner": [{"n": "a\"}]}"}]}',  # escaped quote then braces
            r'{"inner": [{"n": "trailing\\"}]}',  # string ending in a real backslash
            r'{"inner": [{"n": "[{]}"}]}',  # every structural byte, quoted
        ],
    )
    def test_structural_bytes_inside_strings_do_not_split_an_element(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """The whole scanner rests on `_STRING_END`; these are its hard cases."""
        path = tmp_path / "doc.json"
        path.write_text(spelling, encoding="utf-8")
        assert list(stream_top_level_decls(path)) == json.loads(spelling)["inner"]

    def test_an_element_larger_than_the_read_chunk_is_still_one_element(
        self, tmp_path: Path
    ) -> None:
        """Real ASTs are read in 1 MiB chunks and their largest single
        element was measured at 53 MiB, so element-spans-many-chunks is the
        normal case, not an edge case."""
        from abicheck.buildsource import header_graph_ast_stream as mod

        big = {"kind": "Big", "pad": "x" * (4 * mod._CHUNK)}
        path = _write(tmp_path, {"inner": [big, {"kind": "After"}]}, indent=2)
        assert list(stream_top_level_decls(path)) == [big, {"kind": "After"}]

    def test_a_truncated_document_raises_rather_than_yielding_a_partial_set(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "doc.json"
        path.write_text('{"inner": [{"kind": "A"}, {"kind": "B"', encoding="utf-8")
        with pytest.raises(ClangAstStreamError):
            list(stream_top_level_decls(path))

    @pytest.mark.parametrize(
        "spelling",
        ['{"inner": [1, 2]}', '{"inner": ["a"]}', '{"inner": [[{"k": 1}]]}'],
    )
    def test_a_non_object_top_level_element_raises_rather_than_misreading(
        self, tmp_path: Path, spelling: str
    ) -> None:
        """Element boundaries here are object braces, so a scalar or array
        element would make every later slice start in the wrong place. A
        silently wrong projection is worse than none, so this declines
        loudly and the caller falls back to an ordinary parse."""
        path = tmp_path / "doc.json"
        path.write_text(spelling, encoding="utf-8")
        with pytest.raises(ClangAstStreamError):
            list(stream_top_level_decls(path))

    def test_a_scalar_element_is_rejected_even_across_a_chunk_boundary(
        self, tmp_path: Path
    ) -> None:
        """The subtle half of the rule above.

        A bare scalar carries no structural byte at all, so it is invisible
        to the scanner's regex jumps -- and the "read more data" branch
        discards the bytes it has skipped at depth 0. Validating *after*
        that discard would drop such an element silently, which is why this
        case has to straddle the read chunk to be worth anything: the
        sibling test above passes against that bug.
        """
        from abicheck.buildsource import header_graph_ast_stream as mod

        pad = json.dumps({"kind": "Pad", "x": "y" * (mod._CHUNK + 7)})
        path = tmp_path / "doc.json"
        path.write_text(f'{{"inner": [{pad}, 12345]}}', encoding="utf-8")
        with pytest.raises(ClangAstStreamError):
            list(stream_top_level_decls(path))

        # Vacuity guard: the same shape with a real object element must
        # still stream both, or the assertion above is just "big documents
        # raise".
        ok = tmp_path / "ok.json"
        ok.write_text(f'{{"inner": [{pad}, {{"k": 1}}]}}', encoding="utf-8")
        assert len(list(stream_top_level_decls(ok))) == 2

    def test_a_document_that_is_not_an_object_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.json"
        path.write_text("   \n  ", encoding="utf-8")
        with pytest.raises(ClangAstStreamError):
            list(stream_top_level_decls(path))


# --------------------------------------------------------------------------
# 2. The projection, against the whole-tree implementation.
# --------------------------------------------------------------------------


def _assert_same_projection(
    streamed: HeaderGraphAstProjection, whole: HeaderGraphAstProjection
) -> None:
    """Field for field, with the edge lists compared in order.

    Order matters and is asserted: `_dedupe_edges` keeps first-seen order on
    both sides, and the graph builder folds the edges in the order it is
    given them, so an order difference is a real difference even when the
    sets agree.
    """
    assert streamed.type_files == whole.type_files
    assert streamed.entity_files == whole.entity_files
    assert streamed.type_edges == whole.type_edges
    assert streamed.call_edges == whole.call_edges


@needs_clang
class TestStreamedProjectionEqualsWholeTreeProjection:
    def test_on_a_header_containing_both_boundary_shapes(self, tmp_path: Path) -> None:
        ast_path = _dump_ast(tmp_path, _BOUNDARY_HEADER)
        streamed = project_header_graph_ast_file(ast_path)
        whole = project_header_graph_ast(json.loads(ast_path.read_text()))
        _assert_same_projection(streamed, whole)

    def test_the_projection_is_not_vacuously_empty(self, tmp_path: Path) -> None:
        """An equivalence test between two empty projections proves nothing.

        Separate from the comparison above on purpose: if the fixture ever
        stops compiling to a real surface, this fails with that as the
        message instead of the comparison passing silently.
        """
        streamed = project_header_graph_ast_file(_dump_ast(tmp_path, _BOUNDARY_HEADER))
        assert streamed.type_files
        assert streamed.type_edges
        assert streamed.call_edges
        assert streamed.entity_files

    def test_on_a_header_with_stl_template_machinery(self, tmp_path: Path) -> None:
        """The shape the memory work is actually about: a dependency-heavy
        header whose AST is dominated by instantiated template members."""
        ast_path = _dump_ast(
            tmp_path,
            "#pragma once\n#include <vector>\n#include <map>\n#include <string>\n"
            "namespace lib {\n"
            "struct S { int a; std::vector<int> v; std::map<std::string,int> m;\n"
            "           int get() const; void set(const std::vector<S>& xs); };\n"
            "std::vector<S> fn(const std::map<std::string,S>& in, S* o);\n"
            "}\n",
        )
        streamed = project_header_graph_ast_file(ast_path)
        whole = project_header_graph_ast(json.loads(ast_path.read_text()))
        _assert_same_projection(streamed, whole)


@needs_clang
class TestTheFixtureStillExercisesTheBoundaryRules:
    """Guard the equivalence test above against its own fixture drifting.

    Each assertion here names one cross-element rule in
    ``header_graph_ast_stream`` and fails if the compiled AST no longer
    contains the shape that rule exists for -- at which point the
    equivalence test would still pass while covering nothing.
    """

    @pytest.fixture
    def top_level(self, tmp_path: Path) -> list[dict]:
        ast_path = _dump_ast(tmp_path, _BOUNDARY_HEADER)
        return json.loads(ast_path.read_text())["inner"]

    def test_an_anonymous_enum_and_its_declarator_are_top_level_siblings(
        self, top_level: list[dict]
    ) -> None:
        pairs = [
            (a.get("kind"), b.get("kind"))
            for a, b in zip(top_level, top_level[1:])
            if a.get("kind") == "EnumDecl" and not a.get("name")
        ]
        assert ("EnumDecl", "TypedefDecl") in pairs, pairs
        assert ("EnumDecl", "VarDecl") in pairs, pairs

    def test_some_top_level_declaration_inherits_its_file_from_a_sibling(
        self, top_level: list[dict]
    ) -> None:
        """clang emits `loc.file` only when it changes, so a later sibling
        carries none -- the sticky-file state the stream must thread."""

        def states_a_file(node: dict) -> bool:
            loc = node.get("loc") or {}
            begin = (node.get("range") or {}).get("begin") or {}
            return bool(loc.get("file") or begin.get("file"))

        own = [states_a_file(n) for n in top_level]
        assert any(own), "no top-level declaration states a file at all"
        assert not all(own), "every top-level declaration states its own file"

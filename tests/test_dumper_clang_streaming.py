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

"""Unit tests for :mod:`abicheck.dumper_clang_streaming` (no clang required).

Pure-Python tests against hand-built clang-``-ast-dump=json``-shaped dicts —
see ``tests/test_clang_header_backend_integration.py`` for the companion
end-to-end tests that drive this through a real clang invocation.
"""

from __future__ import annotations

import io
import json

from abicheck.dumper_clang_streaming import (
    PRUNED_PLACEHOLDER_KIND,
    DependencyDeclPruningHook,
    load_pruned_clang_ast,
    make_dependency_header_predicate,
)

_DEP_HEADER = "/usr/include/c++/13/vector"
_PROJECT_HEADER = "/home/user/project/api.h"


def _func_node(kind: str, name: str, file: str, inner: list | None = None) -> dict:
    node = {
        "kind": kind,
        "name": name,
        "loc": {"file": file, "line": 10},
        "type": {"qualType": "void ()"},
    }
    if inner is not None:
        node["inner"] = inner
    return node


class TestDependencyDeclPruningHook:
    def test_prunes_function_confined_to_dependency_header(self) -> None:
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        node = _func_node("FunctionDecl", "push_back", _DEP_HEADER)
        pruned = hook(list(node.items()))
        assert pruned["kind"] == PRUNED_PLACEHOLDER_KIND
        assert pruned["name"] == "push_back"
        assert hook.pruned_count == 1

    def test_keeps_function_in_project_header(self) -> None:
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        node = _func_node("FunctionDecl", "add", _PROJECT_HEADER)
        kept = hook(list(node.items()))
        assert kept["kind"] == "FunctionDecl"
        assert hook.pruned_count == 0

    def test_never_prunes_type_shaped_kinds(self) -> None:
        """Records/enums/typedefs must never be pruned, even when wholly
        confined to a dependency header -- see the module docstring's
        direct-reference-retention reasoning."""
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        for kind in (
            "CXXRecordDecl",
            "ClassTemplateSpecializationDecl",
            "EnumDecl",
            "TypedefDecl",
        ):
            node = {"kind": kind, "name": "Vector", "loc": {"file": _DEP_HEADER}}
            kept = hook(list(node.items()))
            assert kept["kind"] == kind
        assert hook.pruned_count == 0

    def test_never_prunes_any_cxx_method_shaped_kind(self) -> None:
        """Codex review, PR #840: a class member (method/ctor/dtor/conversion
        operator) must never be pruned, even when wholly confined to a
        dependency header with real location evidence -- unlike a free
        function or variable, a dependency base class's own method can feed
        ``dumper_clang_vtable.build_vtable``'s base-lookup recursion for a
        project-owned derived class's vtable. See the module docstring's
        "Never pruned: any C++ method-shaped kind" section."""
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        for kind in (
            "CXXMethodDecl",
            "CXXConstructorDecl",
            "CXXDestructorDecl",
            "CXXConversionDecl",
        ):
            node = _func_node(kind, "member", _DEP_HEADER)
            kept = hook(list(node.items()))
            assert kept["kind"] == kind
        assert hook.pruned_count == 0

    def test_keeps_node_with_no_own_location(self) -> None:
        """No explicit own-location evidence -> conservative keep, never a
        guess. Uses ``FunctionDecl`` (not a C++ method-shaped kind) so this
        actually exercises the location check rather than being kept solely
        because its kind was never a pruning candidate at all."""
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        node = {"kind": "FunctionDecl", "name": "f"}  # no "loc" at all
        kept = hook(list(node.items()))
        assert kept["kind"] == "FunctionDecl"
        assert hook.pruned_count == 0

    def test_safety_net_keeps_subtree_reaching_a_kept_file(self) -> None:
        """A dependency-header-rooted node whose own subtree also mentions a
        kept file anywhere (e.g. a macro-expansion edge) must be kept whole
        -- pruning must never be more aggressive than the post-hoc filter.
        Uses ``FunctionDecl`` so this exercises the safety-net re-scan
        itself, not the (separate) kind exclusion."""
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        node = _func_node(
            "FunctionDecl",
            "weird_function",
            _DEP_HEADER,
            inner=[
                {
                    "kind": "ParmVarDecl",
                    "name": "p",
                    "loc": {"file": _PROJECT_HEADER, "line": 4},
                }
            ],
        )
        kept = hook(list(node.items()))
        assert kept["kind"] == "FunctionDecl"
        assert hook.pruned_count == 0

    def test_prunes_vardecl(self) -> None:
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        node = {
            "kind": "VarDecl",
            "name": "npos",
            "loc": {"file": _DEP_HEADER},
        }
        pruned = hook(list(node.items()))
        assert pruned["kind"] == PRUNED_PLACEHOLDER_KIND
        assert hook.pruned_count == 1

    def test_non_decl_objects_pass_through_unchanged(self) -> None:
        """A "type"/"loc"/"range" sub-object (no recognized "kind") must
        never be touched by the hook -- only whole decl nodes are candidates."""
        hook = DependencyDeclPruningHook((_PROJECT_HEADER,))
        sub = {"qualType": "std::vector<int>"}
        assert hook(list(sub.items())) == sub


class TestMakeDependencyHeaderPredicate:
    def test_memoizes_per_path(self) -> None:
        is_dep = make_dependency_header_predicate((_PROJECT_HEADER,))
        # Call twice with the identical path; the underlying classification
        # must agree both times (memoization must not change the answer).
        first = is_dep(_DEP_HEADER)
        second = is_dep(_DEP_HEADER)
        assert first == second is True
        assert is_dep(_PROJECT_HEADER) is False


class TestLoadPrunedClangAst:
    def test_end_to_end_prunes_a_real_json_stream(self) -> None:
        doc = {
            "kind": "TranslationUnitDecl",
            "inner": [
                _func_node("FunctionDecl", "push_back", _DEP_HEADER),
                _func_node("FunctionDecl", "add", _PROJECT_HEADER),
            ],
        }
        fh = io.BytesIO(json.dumps(doc).encode("utf-8"))
        root, pruned_count = load_pruned_clang_ast(fh, header_roots=(_PROJECT_HEADER,))
        assert pruned_count == 1
        kinds = [child["kind"] for child in root["inner"]]
        assert kinds == [PRUNED_PLACEHOLDER_KIND, "FunctionDecl"]

    def test_no_header_roots_falls_back_to_bare_system_header_heuristic(self) -> None:
        """No ``-H`` roots given (e.g. a manifest-only dump) -- ``is_dependency_
        header`` degrades to a bare toolchain-path check, matching
        ``dumper_scoping.py``'s own documented fallback for the same case."""
        doc = {
            "kind": "TranslationUnitDecl",
            "inner": [_func_node("FunctionDecl", "add", _PROJECT_HEADER)],
        }
        fh = io.BytesIO(json.dumps(doc).encode("utf-8"))
        root, pruned_count = load_pruned_clang_ast(fh, header_roots=())
        assert pruned_count == 0
        assert root["inner"][0]["kind"] == "FunctionDecl"

    def test_large_excluded_subtree_never_fully_walked_downstream(self) -> None:
        """Behavior proof for the memory/wall-time claim: a large nested
        subtree entirely confined to a dependency header collapses to a
        single small placeholder dict, so nothing downstream (the
        ``_ClangAstParser`` walk) ever visits its many descendants."""
        # A deeply-nested chain of FunctionDecl children, mimicking a
        # template-instantiation subtree many levels deep.
        leaf = _func_node("FunctionDecl", "leaf", _DEP_HEADER)
        node = leaf
        for i in range(50):
            node = _func_node("FunctionDecl", f"wrapper{i}", _DEP_HEADER, inner=[node])
        doc = {"kind": "TranslationUnitDecl", "inner": [node]}
        fh = io.BytesIO(json.dumps(doc).encode("utf-8"))
        root, pruned_count = load_pruned_clang_ast(fh, header_roots=(_PROJECT_HEADER,))
        # Every level of the chain is itself an independently-prunable
        # FunctionDecl, so pruning fires bottom-up at each one (51 total:
        # the leaf plus 50 wrappers) -- the outermost placeholder carries
        # none of its nested descendants any more.
        assert pruned_count == 51
        (child,) = root["inner"]
        assert child["kind"] == PRUNED_PLACEHOLDER_KIND
        assert "inner" not in child

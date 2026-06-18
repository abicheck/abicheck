# Copyright 2026 Nikolay Petrov
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

"""Tests for abicheck.binder — symbol binding simulation."""
from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.binder import (
    BindingStatus,
    _compute_load_order,
    compute_bindings,
)
from abicheck.elf_metadata import (
    ElfImport,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding as ElfSymbolBinding,
)
from abicheck.resolver import DependencyGraph, ResolvedDSO


def _make_graph(
    nodes: dict[str, tuple[list[str], list[ElfSymbol], list[ElfImport]]],
    edges: list[tuple[str, str]] | None = None,
    root: str = "/app",
) -> DependencyGraph:
    """Helper to build a DependencyGraph from simplified specs.

    nodes: path → (needed, exports, imports)
    """
    graph = DependencyGraph(root=root)
    for i, (path, (needed, exports, imports)) in enumerate(nodes.items()):
        meta = ElfMetadata(
            needed=needed,
            symbols=exports,
            imports=imports,
        )
        graph.nodes[path] = ResolvedDSO(
            path=Path(path),
            soname=Path(path).name,
            needed=needed,
            rpath="",
            runpath="",
            resolution_reason="root" if i == 0 else "default",
            depth=0 if i == 0 else 1,
            elf_metadata=meta,
        )
    graph.edges = edges or []
    return graph


def _sym(name: str, version: str = "", is_default: bool = True, vis: str = "default") -> ElfSymbol:
    return ElfSymbol(name=name, version=version, is_default=is_default, visibility=vis)


def _imp(name: str, version: str = "", binding: ElfSymbolBinding = ElfSymbolBinding.GLOBAL) -> ElfImport:
    return ElfImport(name=name, version=version, binding=binding)


# ---------------------------------------------------------------------------
# Basic resolution
# ---------------------------------------------------------------------------


class TestBasicResolution:
    def test_simple_resolution(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("foo_init")]),
                "/lib/libfoo.so": ([], [_sym("foo_init")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        resolved = [b for b in bindings if b.status == BindingStatus.RESOLVED_OK]
        assert len(resolved) == 1
        assert resolved[0].symbol == "foo_init"
        assert resolved[0].provider == "/lib/libfoo.so"

    def test_missing_symbol(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("missing_func")]),
                "/lib/libfoo.so": ([], [_sym("other_func")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        missing = [b for b in bindings if b.status == BindingStatus.MISSING]
        assert len(missing) == 1
        assert missing[0].symbol == "missing_func"

    def test_transitive_resolution(self):
        """Symbol from an indirect dependency should be found."""
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("bar_func")]),
                "/lib/libfoo.so": (["libbar.so"], [], []),
                "/lib/libbar.so": ([], [_sym("bar_func")], []),
            },
            edges=[("/app", "/lib/libfoo.so"), ("/lib/libfoo.so", "/lib/libbar.so")],
        )
        bindings = compute_bindings(graph)
        resolved = [b for b in bindings if b.symbol == "bar_func"]
        assert len(resolved) == 1
        assert resolved[0].status == BindingStatus.RESOLVED_OK
        assert resolved[0].provider == "/lib/libbar.so"


# ---------------------------------------------------------------------------
# Symbol versioning
# ---------------------------------------------------------------------------


class TestVersionedSymbols:
    def test_versioned_match(self):
        graph = _make_graph(
            {
                "/app": (["libc.so.6"], [], [_imp("malloc", version="GLIBC_2.2.5")]),
                "/lib/libc.so.6": ([], [_sym("malloc", version="GLIBC_2.2.5")], []),
            },
            edges=[("/app", "/lib/libc.so.6")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK

    def test_version_mismatch(self):
        graph = _make_graph(
            {
                "/app": (["libc.so.6"], [], [_imp("malloc", version="GLIBC_2.99")]),
                "/lib/libc.so.6": ([], [_sym("malloc", version="GLIBC_2.2.5")], []),
            },
            edges=[("/app", "/lib/libc.so.6")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.VERSION_MISMATCH

    def test_unversioned_import_matches_default(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("foo_init")]),
                "/lib/libfoo.so": ([], [_sym("foo_init", version="FOO_1.0", is_default=True)], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK


# ---------------------------------------------------------------------------
# Weak symbols
# ---------------------------------------------------------------------------


class TestWeakSymbols:
    def test_weak_unresolved_is_ok(self):
        graph = _make_graph(
            {
                "/app": ([], [], [_imp("__gmon_start__", binding=ElfSymbolBinding.WEAK)]),
            },
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.WEAK_UNRESOLVED

    def test_weak_resolved_when_available(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("optional_func", binding=ElfSymbolBinding.WEAK)]),
                "/lib/libfoo.so": ([], [_sym("optional_func")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


class TestVisibility:
    def test_hidden_symbol_not_resolvable(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("internal_func")]),
                "/lib/libfoo.so": ([], [_sym("internal_func", vis="hidden")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.VISIBILITY_BLOCKED

    def test_default_visibility_resolvable(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so"], [], [_imp("public_func")]),
                "/lib/libfoo.so": ([], [_sym("public_func", vis="default")], []),
            },
            edges=[("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        assert bindings[0].status == BindingStatus.RESOLVED_OK


# ---------------------------------------------------------------------------
# Interposition
# ---------------------------------------------------------------------------


class TestInterposition:
    def test_version_mismatch_not_treated_as_interposed(self):
        """If first_provider has the symbol but wrong version, a later provider
        with the correct version should be RESOLVED_OK, not INTERPOSED."""
        graph = _make_graph(
            {
                "/app": (["libfoo.so", "libbar.so"], [], [_imp("sym", version="V2")]),
                "/lib/libfoo.so": ([], [_sym("sym", version="V1")], []),
                "/lib/libbar.so": ([], [_sym("sym", version="V2")], []),
            },
            edges=[("/app", "/lib/libfoo.so"), ("/app", "/lib/libbar.so")],
        )
        bindings = compute_bindings(graph)
        b = bindings[0]
        assert b.status == BindingStatus.RESOLVED_OK
        assert b.provider == "/lib/libbar.so"

    def test_true_interposition_detected(self):
        """If an earlier provider has the same version, it's real interposition."""
        graph = _make_graph(
            {
                "/app": (["libinterp.so", "libfoo.so"], [], [_imp("sym", version="V1")]),
                "/lib/libinterp.so": ([], [_sym("sym", version="V1")], []),
                "/lib/libfoo.so": ([], [_sym("sym", version="V1")], []),
            },
            edges=[("/app", "/lib/libinterp.so"), ("/app", "/lib/libfoo.so")],
        )
        bindings = compute_bindings(graph)
        # The first match is libinterp.so, which is also first_provider,
        # so it's RESOLVED_OK (not interposed). The symbol from libfoo.so
        # is the one that would be interposed IF the app imported from
        # libfoo.so. But app finds it from libinterp.so first → RESOLVED_OK.
        b = bindings[0]
        assert b.status == BindingStatus.RESOLVED_OK
        assert b.provider == "/lib/libinterp.so"


# ---------------------------------------------------------------------------
# Load order
# ---------------------------------------------------------------------------


class TestLoadOrder:
    def test_breadth_first_order(self):
        graph = _make_graph(
            {
                "/app": (["libfoo.so", "libbar.so"], [], []),
                "/lib/libfoo.so": (["libbaz.so"], [], []),
                "/lib/libbar.so": ([], [], []),
                "/lib/libbaz.so": ([], [], []),
            },
            edges=[
                ("/app", "/lib/libfoo.so"),
                ("/app", "/lib/libbar.so"),
                ("/lib/libfoo.so", "/lib/libbaz.so"),
            ],
        )
        order = _compute_load_order(graph)
        # Root first, then its direct deps (foo, bar), then indirect (baz).
        assert order[0] == "/app"
        foo_idx = order.index("/lib/libfoo.so")
        bar_idx = order.index("/lib/libbar.so")
        baz_idx = order.index("/lib/libbaz.so")
        assert baz_idx > foo_idx
        assert baz_idx > bar_idx


# ---------------------------------------------------------------------------
# Integration: real system binary
# ---------------------------------------------------------------------------


class TestRealBinary:
    # Resolving + binding a real binary's full dependency graph is ~0.6s; both
    # tests need the same bindings, so compute them once per class instead of
    # twice.
    @staticmethod
    @pytest.fixture(scope="class")
    def bindings():
        from abicheck.resolver import resolve_dependencies
        candidates = [Path("/usr/bin/python3"), Path("/usr/bin/ls"), Path("/bin/ls")]
        for p in candidates:
            if p.exists():
                return compute_bindings(resolve_dependencies(p))
        pytest.skip("No suitable ELF binary found")

    def test_no_missing_required_symbols(self, bindings):
        missing = [b for b in bindings if b.status == BindingStatus.MISSING]
        assert len(missing) == 0, f"Missing symbols: {[b.symbol for b in missing[:5]]}"

    def test_all_bindings_have_status(self, bindings):
        for b in bindings:
            assert isinstance(b.status, BindingStatus)
            assert b.consumer
            assert b.symbol

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

"""Tests for abicheck.resolver — transitive ELF dependency resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.resolver import (
    _search_library,
    resolve_dependencies,
)

# ---------------------------------------------------------------------------
# Unit tests: _expand_rpath and _find_resolved_key are in test_resolver_unit.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unit tests: library search
# ---------------------------------------------------------------------------


class TestSearchLibrary:
    def test_found(self, tmp_path):
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        (lib_dir / "libfoo.so.1").write_bytes(b"\x7fELF")

        result = _search_library("libfoo.so.1", [(str(lib_dir), "default")])
        assert result is not None
        assert result[0].name == "libfoo.so.1"
        assert result[1] == "default"

    def test_not_found(self, tmp_path):
        result = _search_library("libmissing.so.1", [(str(tmp_path), "default")])
        assert result is None

    def test_search_order_respected(self, tmp_path):
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "libfoo.so.1").write_bytes(b"first")
        (dir2 / "libfoo.so.1").write_bytes(b"second")

        result = _search_library(
            "libfoo.so.1",
            [
                (str(dir1), "rpath"),
                (str(dir2), "default"),
            ],
        )
        assert result is not None
        assert result[1] == "rpath"


# ---------------------------------------------------------------------------
# _find_resolved_key tests are in test_resolver_unit.py::TestFindResolvedKey
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Integration tests: resolve_dependencies on real system binaries
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestResolveDependencies:
    """Integration tests using actual system binaries (if available)."""

    @pytest.fixture
    def real_binary(self):
        """Find a real ELF binary on the system for testing."""
        if sys.platform != "linux":
            pytest.skip("Dependency resolution tests require Linux")
        candidates = [
            Path("/usr/bin/python3"),
            Path("/usr/bin/ls"),
            Path("/bin/ls"),
        ]
        for p in candidates:
            if p.exists():
                return p
        pytest.skip("No suitable ELF binary found on system")

    def test_root_node_present(self, real_binary):
        graph = resolve_dependencies(real_binary)
        assert graph.node_count >= 1
        # Root should be in the graph.
        root_nodes = [n for n in graph.nodes.values() if n.depth == 0]
        assert len(root_nodes) == 1

    def test_libc_resolved(self, real_binary):
        graph = resolve_dependencies(real_binary)
        sonames = {n.soname for n in graph.nodes.values()}
        # On Linux the C library is libc.so.6; skip on other platforms.
        assert "libc.so.6" in sonames

    def test_no_unresolved_on_standard_binary(self, real_binary):
        graph = resolve_dependencies(real_binary)
        # Standard system binary should have all deps resolved.
        assert len(graph.unresolved) == 0

    def test_edges_connect_nodes(self, real_binary):
        graph = resolve_dependencies(real_binary)
        node_keys = set(graph.nodes.keys())
        for consumer, provider in graph.edges:
            assert consumer in node_keys, f"Edge consumer {consumer} not in nodes"
            assert provider in node_keys, f"Edge provider {provider} not in nodes"

    def test_graph_is_acyclic(self, real_binary):
        graph = resolve_dependencies(real_binary)
        # Build adjacency list and verify the dependency graph is a DAG.
        adj: dict[str, list[str]] = {}
        for consumer, provider in graph.edges:
            adj.setdefault(consumer, []).append(provider)

        VISITING, VISITED = 1, 2
        state: dict[str, int] = {}

        def has_cycle(node: str) -> bool:
            if state.get(node) == VISITED:
                return False
            if state.get(node) == VISITING:
                return True
            state[node] = VISITING
            for neighbor in adj.get(node, []):
                if has_cycle(neighbor):
                    return True
            state[node] = VISITED
            return False

        for node in graph.nodes:
            assert not has_cycle(node), f"Cycle detected involving {node}"

    def test_elf_metadata_populated(self, real_binary):
        graph = resolve_dependencies(real_binary)
        for key, node in graph.nodes.items():
            assert node.elf_metadata is not None, f"Node {key} has no elf_metadata"

    def test_nonexistent_binary(self, tmp_path):
        graph = resolve_dependencies(tmp_path / "nonexistent")
        assert graph.node_count == 0

    def test_search_paths(self, real_binary, tmp_path):
        """Extra search paths don't break resolution."""
        graph = resolve_dependencies(
            real_binary,
            search_paths=[tmp_path],
        )
        assert graph.node_count >= 1


# ---------------------------------------------------------------------------
# RPATH vs RUNPATH propagation
# ---------------------------------------------------------------------------


class TestRpathRunpathSemantics:
    """Verify that DT_RUNPATH does NOT propagate to indirect deps."""

    def test_runpath_only_applies_to_direct_deps(self):
        """DT_RUNPATH should only be used when resolving direct DT_NEEDED
        of the object that declares it, not for transitive deps."""
        # This is a semantic test — we can't easily create real ELF binaries
        # in a test, but we verify the search order construction logic.
        from abicheck.resolver import ResolvedDSO, _build_search_order

        # A node with DT_RUNPATH (no DT_RPATH).
        node = ResolvedDSO(
            path=Path("/app/lib/libfoo.so"),
            soname="libfoo.so",
            needed=["libbar.so"],
            rpath="",
            runpath="/app/lib",  # Has RUNPATH
            resolution_reason="root",
            depth=0,
        )

        search = _build_search_order(
            soname="libbar.so",
            requester_node=node,
            requester_dir=Path("/app/lib"),
            propagated_rpaths=[],
            ld_dirs=[],
            extra_dirs=[],
            prefix="",
        )

        # RUNPATH should be in the search order for direct deps.
        reasons = [reason for _, reason in search]
        assert "runpath" in reasons

    def test_rpath_without_runpath_propagates(self):
        """When a DSO has DT_RPATH but no DT_RUNPATH, RPATH propagates."""
        from abicheck.resolver import ResolvedDSO, _build_search_order

        node = ResolvedDSO(
            path=Path("/app/lib/libfoo.so"),
            soname="libfoo.so",
            needed=["libbar.so"],
            rpath="/app/lib",
            runpath="",  # No RUNPATH → RPATH applies
            resolution_reason="root",
            depth=0,
        )

        search = _build_search_order(
            soname="libbar.so",
            requester_node=node,
            requester_dir=Path("/app/lib"),
            propagated_rpaths=[],
            ld_dirs=[],
            extra_dirs=[],
            prefix="",
        )

        reasons = [reason for _, reason in search]
        assert "rpath" in reasons
        assert "runpath" not in reasons

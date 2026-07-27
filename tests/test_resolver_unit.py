"""Unit tests for abicheck.resolver module — targeting uncovered lines."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.elf_metadata import ElfMetadata
from abicheck.resolver import (
    DependencyGraph,
    ResolvedDSO,
    _build_search_order,
    _default_dirs_for_triple,
    _detect_target_triple,
    _expand_rpath,
    _find_resolved_key,
    _lib_token_for_triple,
    _merge_rpaths,
    _platform_token_for_triple,
    resolve_dependencies,
)

# ---------------------------------------------------------------------------
# _merge_rpaths
# ---------------------------------------------------------------------------

class TestMergeRpaths:
    def test_both_empty(self):
        assert _merge_rpaths([], []) == []

    def test_own_only(self):
        assert _merge_rpaths(["/a", "/b"], []) == ["/a", "/b"]

    def test_ancestor_only(self):
        assert _merge_rpaths([], ["/a", "/b"]) == ["/a", "/b"]

    def test_dedup_preserves_order(self):
        result = _merge_rpaths(["/a", "/b"], ["/b", "/c"])
        assert result == ["/a", "/b", "/c"]

    def test_identical_lists(self):
        assert _merge_rpaths(["/a"], ["/a"]) == ["/a"]


# ---------------------------------------------------------------------------
# _detect_target_triple
# ---------------------------------------------------------------------------

class TestDetectTargetTriple:
    def test_x86_64(self):
        assert _detect_target_triple("ld-linux-x86-64.so.2") == "x86_64-linux-gnu"

    def test_aarch64(self):
        assert _detect_target_triple("ld-linux-aarch64.so.1") == "aarch64-linux-gnu"

    def test_empty_string_fallback(self):
        assert _detect_target_triple("") == "x86_64-linux-gnu"

    def test_full_path(self):
        assert _detect_target_triple("/lib64/ld-linux-x86-64.so.2") == "x86_64-linux-gnu"

    def test_unknown_fallback(self):
        assert _detect_target_triple("unknown") == "x86_64-linux-gnu"


# ---------------------------------------------------------------------------
# _default_dirs_for_triple
# ---------------------------------------------------------------------------

class TestDefaultDirsForTriple:
    def test_x86_64_has_lib64(self):
        dirs = _default_dirs_for_triple("x86_64-linux-gnu")
        assert "/lib64" in dirs
        assert "/usr/lib64" in dirs
        assert "/lib/x86_64-linux-gnu" in dirs

    def test_arm_has_lib_not_lib64(self):
        dirs = _default_dirs_for_triple("arm-linux-gnueabihf")
        assert "/lib" in dirs
        assert "/lib/arm-linux-gnueabihf" in dirs
        # arm is 32-bit, no "64" in triple
        assert "/lib64" not in dirs


# ---------------------------------------------------------------------------
# _platform_token_for_triple / _lib_token_for_triple
# ---------------------------------------------------------------------------

class TestTokenHelpers:
    def test_platform_token_x86_64(self):
        assert _platform_token_for_triple("x86_64-linux-gnu") == "x86_64"

    def test_platform_token_aarch64(self):
        assert _platform_token_for_triple("aarch64-linux-gnu") == "aarch64"

    def test_lib_token_64(self):
        assert _lib_token_for_triple("x86_64-linux-gnu") == "lib64"

    def test_lib_token_32(self):
        assert _lib_token_for_triple("arm-linux-gnueabihf") == "lib"


# ---------------------------------------------------------------------------
# _expand_rpath
# ---------------------------------------------------------------------------

class TestExpandRpath:
    def test_origin_replacement(self):
        result = _expand_rpath("$ORIGIN/../lib", Path("/usr/bin"), "")
        assert result == ["/usr/bin/../lib"]

    def test_origin_braces_replacement(self):
        result = _expand_rpath("${ORIGIN}/lib", Path("/opt/app"), "")
        assert result == ["/opt/app/lib"]

    def test_lib_replacement(self):
        result = _expand_rpath("/usr/$LIB", Path("/usr/bin"), "",
                               lib_token="lib64")
        assert result == ["/usr/lib64"]

    def test_platform_replacement(self):
        result = _expand_rpath("/usr/$PLATFORM", Path("/usr/bin"), "",
                               platform_token="x86_64")
        assert result == ["/usr/x86_64"]

    def test_colon_separated(self):
        result = _expand_rpath("/a:/b:/c", Path("/bin"), "")
        assert result == ["/a", "/b", "/c"]

    def test_empty_entries_skipped(self):
        result = _expand_rpath("/a::/b", Path("/bin"), "")
        assert result == ["/a", "/b"]

    def test_sysroot_prefix_for_non_origin(self):
        result = _expand_rpath("/usr/lib", Path("/bin"), "/sysroot")
        assert result == ["/sysroot/usr/lib"]

    def test_sysroot_not_prepended_for_origin(self):
        result = _expand_rpath("$ORIGIN/lib", Path("/sysroot/opt"), "/sysroot")
        # $ORIGIN paths already contain the sysroot via origin_dir
        assert result == ["/sysroot/opt/lib"]


# ---------------------------------------------------------------------------
# _find_resolved_key
# ---------------------------------------------------------------------------

class TestFindResolvedKey:
    def _make_graph(self):
        graph = DependencyGraph(root="/usr/bin/app")
        graph.nodes["/usr/lib/libfoo.so.1"] = ResolvedDSO(
            path=Path("/usr/lib/libfoo.so.1"),
            soname="libfoo.so.1",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="default",
            depth=1,
        )
        return graph

    def test_found_by_soname(self):
        graph = self._make_graph()
        assert _find_resolved_key(graph, "libfoo.so.1") == "/usr/lib/libfoo.so.1"

    def test_found_by_basename(self):
        """Lookup by basename of the key path (not soname) when soname differs."""
        graph = DependencyGraph(root="/usr/bin/app")
        # soname differs from basename of the key path
        graph.nodes["/usr/lib/libfoo.so.1"] = ResolvedDSO(
            path=Path("/usr/lib/libfoo.so.1"),
            soname="libfoo.so.1.0.0",  # soname does NOT match the query
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="default",
            depth=1,
        )
        # Query matches the key's basename, not the soname
        assert _find_resolved_key(graph, "libfoo.so.1") == "/usr/lib/libfoo.so.1"

    def test_not_found(self):
        graph = self._make_graph()
        assert _find_resolved_key(graph, "libbar.so.1") is None


# ---------------------------------------------------------------------------
# _build_search_order
# ---------------------------------------------------------------------------

class TestBuildSearchOrder:
    def test_with_rpath_no_runpath(self):
        node = ResolvedDSO(
            path=Path("/usr/bin/app"),
            soname="app",
            needed=["libfoo.so"],
            rpath="/opt/lib",
            runpath="",
            resolution_reason="root",
            depth=0,
        )
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=node,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=[],
            ld_dirs=[],
            extra_dirs=[],
            prefix="",
            default_dirs=["/lib", "/usr/lib"],
        )
        reasons = [r for _, r in result]
        assert "rpath" in reasons
        assert "runpath" not in reasons

    def test_with_runpath(self):
        node = ResolvedDSO(
            path=Path("/usr/bin/app"),
            soname="app",
            needed=["libfoo.so"],
            rpath="",
            runpath="/opt/runpath",
            resolution_reason="root",
            depth=0,
        )
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=node,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=[],
            ld_dirs=[],
            extra_dirs=[],
            prefix="",
            default_dirs=["/lib", "/usr/lib"],
        )
        reasons = [r for _, r in result]
        assert "runpath" in reasons

    def test_with_ld_library_path(self):
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=None,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=[],
            ld_dirs=["/custom/ld"],
            extra_dirs=[],
            prefix="",
            default_dirs=["/lib"],
        )
        reasons = [r for _, r in result]
        assert "ld_library_path" in reasons

    def test_with_sysroot_prefix(self):
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=None,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=[],
            ld_dirs=["/custom/ld"],
            extra_dirs=[],
            prefix="/sysroot",
            default_dirs=["/lib"],
        )
        dirs = [d for d, _ in result]
        assert any("/sysroot" in d for d in dirs)

    def test_with_extra_dirs(self):
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=None,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=[],
            ld_dirs=[],
            extra_dirs=["/extra/path"],
            prefix="",
            default_dirs=["/lib"],
        )
        reasons = [r for _, r in result]
        assert "search_path" in reasons

    def test_propagated_rpaths_included(self):
        node = ResolvedDSO(
            path=Path("/usr/bin/app"),
            soname="app",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="root",
            depth=0,
        )
        result = _build_search_order(
            soname="libfoo.so",
            requester_node=node,
            requester_dir=Path("/usr/bin"),
            propagated_rpaths=["/propagated/dir"],
            ld_dirs=[],
            extra_dirs=[],
            prefix="",
            default_dirs=["/lib"],
        )
        reasons = [r for _, r in result]
        assert "rpath_propagated" in reasons


# ---------------------------------------------------------------------------
# resolve_dependencies — integration tests with mocked parse_elf_metadata
# ---------------------------------------------------------------------------

class TestResolveDependencies:
    def test_binary_not_found(self, tmp_path):
        """Non-existent binary returns empty graph."""
        result = resolve_dependencies(tmp_path / "nonexistent")
        assert result.node_count == 0
        assert result.unresolved == []

    def test_binary_with_no_deps(self, tmp_path):
        """Binary with no DT_NEEDED produces graph with just the root."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF_stub")

        root_meta = ElfMetadata(soname="app", needed=[], rpath="", runpath="")

        with patch("abicheck.resolver.parse_elf_metadata", return_value=root_meta):
            result = resolve_dependencies(binary)

        assert result.node_count == 1
        assert result.unresolved == []

    def test_binary_with_resolvable_dep(self, tmp_path):
        """Binary with a dependency that resolves in default dirs."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF_stub")

        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        libfoo = lib_dir / "libfoo.so.1"
        libfoo.write_bytes(b"\x7fELF_stub_foo")

        root_meta = ElfMetadata(
            soname="app", needed=["libfoo.so.1"], rpath="", runpath="",
        )
        foo_meta = ElfMetadata(
            soname="libfoo.so.1", needed=[], rpath="", runpath="",
        )

        call_count = 0
        def fake_parse(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return root_meta
            return foo_meta

        with patch("abicheck.resolver.parse_elf_metadata", side_effect=fake_parse):
            result = resolve_dependencies(
                binary,
                search_paths=[lib_dir],
            )

        assert result.node_count == 2
        assert result.unresolved == []

    def test_binary_with_unresolvable_dep(self, tmp_path):
        """Binary with a dependency that cannot be found."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF_stub")

        root_meta = ElfMetadata(
            soname="app", needed=["libmissing.so.1"], rpath="", runpath="",
        )

        with patch("abicheck.resolver.parse_elf_metadata", return_value=root_meta):
            result = resolve_dependencies(binary)

        assert result.node_count == 1
        assert len(result.unresolved) == 1
        assert result.unresolved[0][1] == "libmissing.so.1"

    @pytest.mark.skipif(sys.platform == "win32", reason="RPATH resolution is POSIX-only")
    def test_binary_with_rpath_propagation(self, tmp_path):
        """RPATH propagation through dependency chain (no RUNPATH)."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF_stub")

        rpath_dir = tmp_path / "rpathlibs"
        rpath_dir.mkdir()
        libfoo = rpath_dir / "libfoo.so.1"
        libfoo.write_bytes(b"\x7fELF_stub_foo")
        libbar = rpath_dir / "libbar.so.1"
        libbar.write_bytes(b"\x7fELF_stub_bar")

        root_meta = ElfMetadata(
            soname="app",
            needed=["libfoo.so.1"],
            rpath=str(rpath_dir),
            runpath="",
        )
        foo_meta = ElfMetadata(
            soname="libfoo.so.1",
            needed=["libbar.so.1"],
            rpath="",
            runpath="",
        )
        bar_meta = ElfMetadata(
            soname="libbar.so.1",
            needed=[],
            rpath="",
            runpath="",
        )

        call_count = 0
        def fake_parse(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return root_meta
            if call_count == 2:
                return foo_meta
            return bar_meta

        with patch("abicheck.resolver.parse_elf_metadata", side_effect=fake_parse):
            result = resolve_dependencies(binary)

        # Root + libfoo + libbar all resolved via propagated RPATH
        assert result.node_count == 3
        assert result.unresolved == []

    def test_recursive_queue_traversal(self, tmp_path):
        """Multiple levels of transitive dependencies are traversed."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF_stub")

        search_dir = tmp_path / "libs"
        search_dir.mkdir()
        liba = search_dir / "liba.so"
        liba.write_bytes(b"\x7fELF")
        libb = search_dir / "libb.so"
        libb.write_bytes(b"\x7fELF")
        libc_file = search_dir / "libc_custom.so"
        libc_file.write_bytes(b"\x7fELF")

        metas = {
            "root": ElfMetadata(soname="app", needed=["liba.so"], rpath="", runpath=""),
            "liba": ElfMetadata(soname="liba.so", needed=["libb.so"], rpath="", runpath=""),
            "libb": ElfMetadata(soname="libb.so", needed=["libc_custom.so"], rpath="", runpath=""),
            "libc": ElfMetadata(soname="libc_custom.so", needed=[], rpath="", runpath=""),
        }

        call_count = 0
        def fake_parse(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return metas["root"]
            elif call_count == 2:
                return metas["liba"]
            elif call_count == 3:
                return metas["libb"]
            return metas["libc"]

        with patch("abicheck.resolver.parse_elf_metadata", side_effect=fake_parse):
            result = resolve_dependencies(binary, search_paths=[search_dir])

        assert result.node_count == 4
        assert result.unresolved == []


# ---------------------------------------------------------------------------
# resolve_dependencies — max_file_size (ADR-021b D3)
# ---------------------------------------------------------------------------

class TestResolveDependenciesMaxFileSize:
    def test_no_limit_by_default(self, tmp_path):
        """max_file_size=None (the default) applies no size guard."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 4096)
        root_meta = ElfMetadata(soname="app", needed=[], rpath="", runpath="")

        with patch("abicheck.resolver.parse_elf_metadata", return_value=root_meta):
            result = resolve_dependencies(binary)

        assert result.node_count == 1

    def test_oversized_root_raises(self, tmp_path):
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 4096)

        with pytest.raises(ValueError, match="exceeds limit"):
            resolve_dependencies(binary, max_file_size=1)

    def test_oversized_transitive_dependency_raises(self, tmp_path):
        """A small root with a huge resolved dependency is still rejected --
        the size guard must apply to every DSO parsed, not just the root
        (ADR-021b D3: caller-controlled search_paths can otherwise steer
        resolution to an arbitrarily large file with no guard at all)."""
        binary = tmp_path / "app"
        binary.write_bytes(b"\x7fELF")  # tiny root

        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        libfoo = lib_dir / "libfoo.so.1"
        libfoo.write_bytes(b"\x7fELF" + b"\x00" * 4096)  # oversized dependency

        root_meta = ElfMetadata(
            soname="app", needed=["libfoo.so.1"], rpath="", runpath="",
        )
        foo_meta = ElfMetadata(soname="libfoo.so.1", needed=[], rpath="", runpath="")

        call_count = 0

        def fake_parse(path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return root_meta
            return foo_meta

        with patch("abicheck.resolver.parse_elf_metadata", side_effect=fake_parse):
            with pytest.raises(ValueError, match="exceeds limit"):
                resolve_dependencies(
                    binary, search_paths=[lib_dir], max_file_size=1024,
                )

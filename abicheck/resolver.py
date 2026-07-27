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

"""Transitive ELF dependency resolution with loader-accurate search order.

Implements the dynamic linker's search algorithm for DT_NEEDED resolution:
  1. DT_RPATH (only if DT_RUNPATH is absent in the *requesting* object)
  2. LD_LIBRARY_PATH
  3. DT_RUNPATH (only for *direct* DT_NEEDED of the requesting object)
  4. Default directories (/lib, /usr/lib, and platform-specific lib dirs)

Key correctness rule: DT_RUNPATH applies only to direct dependencies of the
object that declares it, **not** to transitive dependencies.  DT_RPATH, when
used (no DT_RUNPATH present), propagates through the entire dependency tree.

See ld.so(8) and ``man 8 ld-linux`` for the full specification.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .elf_metadata import ElfMetadata, parse_elf_metadata

log = logging.getLogger(__name__)


def _check_dso_size(path: Path, max_file_size: int | None) -> None:
    """Raise ValueError if *path* exceeds *max_file_size* bytes.

    Applied to every DSO this module parses (root and each transitively
    resolved dependency) when a caller supplies a limit -- a caller-controlled
    search_paths/ld_library_path/sysroot can otherwise steer resolution to an
    arbitrarily large file with no size guard of its own (ADR-021b D3, for
    callers like the MCP ``abi_deps`` tool that need one). The error message
    names only *path*'s basename, not its full resolved path -- MCP error
    responses are expected to stay free of filesystem-path detail (the same
    contract ``mcp_shared._check_file_size``'s label-only message keeps).
    """
    if max_file_size is None:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return  # let the caller's own existence/format checks report this
    if size > max_file_size:
        raise ValueError(
            f"{path.name} is {size / (1024 * 1024):.1f} MB, "
            f"exceeds limit of {max_file_size / (1024 * 1024):.0f} MB"
        )


@dataclass
class ResolvedDSO:
    """A single resolved shared object in the dependency graph."""
    path: Path                    # Resolved filesystem path
    soname: str                   # DT_SONAME (or basename if missing)
    needed: list[str]             # DT_NEEDED entries (raw soname strings)
    rpath: str                    # DT_RPATH
    runpath: str                  # DT_RUNPATH
    resolution_reason: str        # Why resolved here (rpath/runpath/ld_library_path/default/root)
    depth: int                    # Distance from root binary (0 = root)
    elf_metadata: ElfMetadata | None = None  # Full parsed metadata


@dataclass
class DependencyGraph:
    """The resolved transitive dependency closure of a root binary."""
    root: str                     # Root binary path
    nodes: dict[str, ResolvedDSO] = field(default_factory=dict)  # resolved_path → info
    edges: list[tuple[str, str]] = field(default_factory=list)   # (consumer_path, provider_path)
    unresolved: list[tuple[str, str]] = field(default_factory=list)  # (consumer_path, missing_soname)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def resolved_paths(self) -> list[str]:
        return sorted(self.nodes.keys())


# ---------------------------------------------------------------------------
# Target-aware default search directories
# ---------------------------------------------------------------------------

# Mapping from ELF machine / interpreter hints to multiarch triples.
_INTERP_ARCH_MAP: dict[str, str] = {
    "ld-linux-x86-64": "x86_64-linux-gnu",
    "ld-linux-aarch64": "aarch64-linux-gnu",
    "ld-linux-armhf": "arm-linux-gnueabihf",
    "ld-linux": "i386-linux-gnu",           # 32-bit x86
    "ld-linux-riscv64": "riscv64-linux-gnu",
    "ld-linux-s390x": "s390x-linux-gnu",
    "ld-linux-ppc64le": "powerpc64le-linux-gnu",
}

_FALLBACK_TRIPLE = "x86_64-linux-gnu"


def _detect_target_triple(interpreter: str) -> str:
    """Derive the multiarch triple from the ELF interpreter path."""
    if not interpreter:
        return _FALLBACK_TRIPLE
    base = interpreter.rsplit("/", 1)[-1]  # e.g. "ld-linux-x86-64.so.2"
    stem = base.split(".so")[0]             # e.g. "ld-linux-x86-64"
    for prefix, triple in _INTERP_ARCH_MAP.items():
        if stem.startswith(prefix):
            return triple
    return _FALLBACK_TRIPLE


def _default_dirs_for_triple(triple: str) -> list[str]:
    """Return loader default search directories for a given multiarch triple."""
    lib_qual = "lib64" if "64" in triple or "aarch64" in triple else "lib"
    return [
        f"/lib/{triple}",
        f"/usr/lib/{triple}",
        f"/{lib_qual}",
        f"/usr/{lib_qual}",
        "/lib",
        "/usr/lib",
    ]


def _platform_token_for_triple(triple: str) -> str:
    """Return the $PLATFORM token value for a given multiarch triple."""
    arch = triple.split("-")[0]
    return arch


def _lib_token_for_triple(triple: str) -> str:
    """Return the $LIB token value for a given multiarch triple."""
    if "64" in triple or "aarch64" in triple:
        return "lib64"
    return "lib"


def resolve_dependencies(
    binary: Path,
    search_paths: list[Path] | None = None,
    sysroot: Path | None = None,
    ld_library_path: str = "",
    max_file_size: int | None = None,
) -> DependencyGraph:
    """Resolve the transitive closure of DT_NEEDED dependencies.

    Args:
        binary: Path to the root ELF binary or shared library.
        search_paths: Additional directories to search (appended after defaults).
        sysroot: Prefix prepended to all search paths (for cross/container analysis).
        ld_library_path: Colon-separated list of directories (simulates $LD_LIBRARY_PATH).
        max_file_size: When given, every DSO resolved (root and each
            transitive dependency) is checked against this byte limit before
            being parsed, raising ``ValueError`` if exceeded -- for callers
            (e.g. the MCP ``abi_deps`` tool) that must bound how large a file
            caller-controlled search paths can steer resolution to. ``None``
            (the default) applies no limit, preserving prior behavior.

    Returns:
        A DependencyGraph with all resolved and unresolved dependencies.
    """
    graph = DependencyGraph(root=str(binary))
    extra_dirs = [str(p) for p in (search_paths or [])]
    ld_dirs = [d for d in ld_library_path.split(":") if d]
    prefix = str(sysroot) if sysroot else ""

    seed = _seed_root(binary, graph, prefix, max_file_size)
    if seed is None:
        return graph
    root_path, root_key, root_soname, default_dirs, platform_token, lib_token = seed

    visited_sonames: set[str] = {root_soname, root_path.name}
    queue: deque[tuple[str, str | None, int]] = deque()
    root_node = graph.nodes[root_key]
    for needed in root_node.needed:
        queue.append((needed, root_key, 1))

    # Propagated RPATHs from ancestor DSOs (only when DT_RUNPATH is absent).
    propagated_rpaths: dict[str, list[str]] = {}
    if root_node.rpath and not root_node.runpath:
        propagated_rpaths[root_key] = _expand_rpath(
            root_node.rpath, root_path.parent, prefix,
            platform_token=platform_token, lib_token=lib_token,
        )

    while queue:
        soname, requester_path, depth = queue.popleft()

        if soname in visited_sonames:
            resolved_key = _find_resolved_key(graph, soname)
            if resolved_key and requester_path:
                graph.edges.append((requester_path, resolved_key))
            continue

        visited_sonames.add(soname)
        requester_node = graph.nodes.get(requester_path) if requester_path else None
        requester_dir = Path(requester_path).parent if requester_path else root_path.parent

        search = _build_search_order(
            soname=soname, requester_node=requester_node,
            requester_dir=requester_dir,
            propagated_rpaths=propagated_rpaths.get(requester_path or "", []),
            ld_dirs=ld_dirs, extra_dirs=extra_dirs, prefix=prefix,
            default_dirs=default_dirs,
            platform_token=platform_token, lib_token=lib_token,
        )

        resolved = _search_library(soname, search)
        if resolved is None:
            graph.unresolved.append((requester_path or root_key, soname))
            continue

        resolved_path, reason = resolved
        resolved_key = str(resolved_path)

        if resolved_key in graph.nodes:
            if requester_path:
                graph.edges.append((requester_path, resolved_key))
            continue

        _register_resolved_dso(
            resolved_path, resolved_key, soname, reason, depth,
            requester_path, graph, queue, visited_sonames,
            propagated_rpaths, prefix, platform_token, lib_token,
            max_file_size,
        )

    return graph


def _register_resolved_dso(
    resolved_path: Path,
    resolved_key: str,
    soname: str,
    reason: str,
    depth: int,
    requester_path: str | None,
    graph: DependencyGraph,
    queue: deque[tuple[str, str | None, int]],
    visited_sonames: set[str],
    propagated_rpaths: dict[str, list[str]],
    prefix: str,
    platform_token: str,
    lib_token: str,
    max_file_size: int | None = None,
) -> None:
    """Parse a newly resolved DSO, add it to the graph, and enqueue its children."""
    _check_dso_size(resolved_path, max_file_size)
    meta = parse_elf_metadata(resolved_path)
    node = ResolvedDSO(
        path=resolved_path, soname=meta.soname or soname,
        needed=list(meta.needed), rpath=meta.rpath,
        runpath=meta.runpath, resolution_reason=reason,
        depth=depth, elf_metadata=meta,
    )
    graph.nodes[resolved_key] = node
    if requester_path:
        graph.edges.append((requester_path, resolved_key))

    # Propagate RPATH: merge with ancestor RPATHs.
    if meta.rpath and not meta.runpath:
        own_rpaths = _expand_rpath(
            meta.rpath, resolved_path.parent, prefix,
            platform_token=platform_token, lib_token=lib_token,
        )
        propagated_rpaths[resolved_key] = _merge_rpaths(
            own_rpaths, propagated_rpaths.get(requester_path or "", []),
        )
    elif requester_path and requester_path in propagated_rpaths:
        propagated_rpaths[resolved_key] = propagated_rpaths[requester_path]

    for needed_child in meta.needed:
        queue.append((needed_child, resolved_key, depth + 1))


def _merge_rpaths(own: list[str], ancestor: list[str]) -> list[str]:
    """Merge own RPATHs with ancestor RPATHs, deduplicating."""
    seen: set[str] = set()
    merged: list[str] = []
    for d in own + ancestor:
        if d not in seen:
            seen.add(d)
            merged.append(d)
    return merged


def _seed_root(
    binary: Path, graph: DependencyGraph, prefix: str,
    max_file_size: int | None = None,
) -> tuple[Path, str, str, list[str], str, str] | None:
    """Parse the root binary, add it to the graph, and return target config.

    Returns (root_path, root_key, root_soname, default_dirs,
    platform_token, lib_token) or None if the binary doesn't exist.

    When *prefix* (sysroot) is non-empty and *binary* is an absolute path
    that doesn't already start with the prefix, the root binary is looked
    up under the sysroot.
    """
    # When a sysroot is active, resolve the binary under it.
    if prefix and binary.is_absolute() and not binary.is_relative_to(Path(prefix)):
        root_path = Path(prefix) / str(binary).lstrip("/")
    else:
        root_path = binary
    root_path = root_path.resolve()
    if not root_path.exists():
        log.warning("resolve_dependencies: root binary not found: %s", root_path)
        return None

    _check_dso_size(root_path, max_file_size)
    root_meta = parse_elf_metadata(root_path)
    root_key = str(root_path)
    root_soname = root_meta.soname or root_path.name

    target_triple = _detect_target_triple(root_meta.interpreter)
    default_dirs = _default_dirs_for_triple(target_triple)
    platform_token = _platform_token_for_triple(target_triple)
    lib_token = _lib_token_for_triple(target_triple)

    graph.nodes[root_key] = ResolvedDSO(
        path=root_path,
        soname=root_soname,
        needed=list(root_meta.needed),
        rpath=root_meta.rpath,
        runpath=root_meta.runpath,
        resolution_reason="root",
        depth=0,
        elf_metadata=root_meta,
    )
    return root_path, root_key, root_soname, default_dirs, platform_token, lib_token


# ---------------------------------------------------------------------------
# Search order construction
# ---------------------------------------------------------------------------

def _build_search_order(
    soname: str,
    requester_node: ResolvedDSO | None,
    requester_dir: Path,
    propagated_rpaths: list[str],
    ld_dirs: list[str],
    extra_dirs: list[str],
    prefix: str,
    default_dirs: list[str] | None = None,
    platform_token: str = "x86_64",
    lib_token: str = "lib",
) -> list[tuple[str, str]]:
    """Build the ordered list of (directory, reason) to search for *soname*.

    Implements the ld.so search order:
      1. DT_RPATH of requester (only if requester has no DT_RUNPATH)
      2. LD_LIBRARY_PATH
      3. DT_RUNPATH of requester (only for *direct* deps of requester)
      4. Default directories + extra search paths
    """
    search: list[tuple[str, str]] = []

    if requester_node is not None:
        # Step 1: DT_RPATH — only if no DT_RUNPATH is present.
        if requester_node.rpath and not requester_node.runpath:
            for d in _expand_rpath(
                requester_node.rpath, requester_dir, prefix,
                platform_token=platform_token, lib_token=lib_token,
            ):
                search.append((d, "rpath"))
        # Also propagated RPATHs from ancestors.
        for d in propagated_rpaths:
            search.append((d, "rpath_propagated"))

    # Step 2: LD_LIBRARY_PATH.
    for d in ld_dirs:
        full = os.path.join(prefix, d.lstrip("/")) if prefix else d
        search.append((full, "ld_library_path"))

    # Step 3: DT_RUNPATH — only for direct DT_NEEDED.
    if requester_node is not None and requester_node.runpath:
        for d in _expand_rpath(
            requester_node.runpath, requester_dir, prefix,
            platform_token=platform_token, lib_token=lib_token,
        ):
            search.append((d, "runpath"))

    # Step 4: Default directories (target-aware).
    _dirs = default_dirs if default_dirs is not None else _default_dirs_for_triple(_FALLBACK_TRIPLE)
    for d in _dirs:
        full = os.path.join(prefix, d.lstrip("/")) if prefix else d
        search.append((full, "default"))

    # Extra user-specified search paths.
    for d in extra_dirs:
        full = os.path.join(prefix, d.lstrip("/")) if prefix else d
        search.append((full, "search_path"))

    return search


def _search_library(
    soname: str, search: list[tuple[str, str]],
) -> tuple[Path, str] | None:
    """Search for *soname* in the ordered directory list.

    Returns (resolved_path, reason) or None if not found.
    """
    for directory, reason in search:
        candidate = Path(directory) / soname
        if candidate.is_file():
            return candidate.resolve(), reason
    return None


# ---------------------------------------------------------------------------
# RPATH/RUNPATH expansion
# ---------------------------------------------------------------------------

def _expand_rpath(
    rpath: str,
    origin_dir: Path,
    prefix: str,
    *,
    platform_token: str = "x86_64",
    lib_token: str = "lib",
) -> list[str]:
    """Expand an RPATH/RUNPATH string into a list of directories.

    Handles:
      - Colon-separated paths
      - $ORIGIN / ${ORIGIN} → directory containing the DSO
      - $LIB / ${LIB} → target-aware lib directory name
      - $PLATFORM / ${PLATFORM} → target-aware platform string
      - Sysroot prefix (only for non-$ORIGIN absolute paths)
    """
    dirs: list[str] = []
    # Use POSIX-style origin — ELF paths always use forward slashes.
    origin = origin_dir.as_posix()
    for entry in rpath.split(":"):
        if not entry:
            continue
        expanded = entry
        has_origin = "$ORIGIN" in expanded or "${ORIGIN}" in expanded
        expanded = expanded.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
        expanded = expanded.replace("${LIB}", lib_token).replace("$LIB", lib_token)
        expanded = expanded.replace("${PLATFORM}", platform_token).replace("$PLATFORM", platform_token)
        # Only prepend sysroot prefix for non-$ORIGIN paths.
        # $ORIGIN expands to the actual DSO path (which already includes the
        # sysroot prefix if the DSO was found under the sysroot), so
        # prepending again would produce /sysroot/sysroot/... paths.
        if prefix and not has_origin:
            expanded = prefix.rstrip("/") + "/" + expanded.lstrip("/")
        dirs.append(expanded)
    return dirs


def _find_resolved_key(graph: DependencyGraph, soname: str) -> str | None:
    """Find the resolved path key for a given soname in the graph."""
    for key, node in graph.nodes.items():
        if node.soname == soname or Path(key).name == soname:
            return key
    return None

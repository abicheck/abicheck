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

"""Symbol binding simulation across a resolved dependency graph.

Simulates the dynamic linker's symbol resolution: for each imported (undefined)
symbol in each DSO, determines which provider DSO will satisfy the reference,
accounting for symbol versioning, visibility, and binding strength.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum

from .elf_metadata import ElfMetadata, SymbolBinding as ElfSymbolBinding
from .resolver import DependencyGraph

log = logging.getLogger(__name__)


class BindingStatus(str, Enum):
    RESOLVED_OK = "resolved_ok"
    MISSING = "missing"
    VERSION_MISMATCH = "version_mismatch"
    WEAK_UNRESOLVED = "weak_unresolved"       # Weak ref, no provider (OK at runtime)
    VISIBILITY_BLOCKED = "visibility_blocked"
    INTERPOSED = "interposed"                  # Resolved but via interposition


@dataclass
class SymbolBinding:
    """Result of resolving one imported symbol."""
    consumer: str            # DSO path that imports the symbol
    symbol: str              # Symbol name
    version: str             # Required version (or "")
    provider: str | None     # DSO path that provides it (None if missing)
    status: BindingStatus
    explanation: str          # Human-readable reason


def compute_bindings(
    graph: DependencyGraph,
    metadata: dict[str, ElfMetadata] | None = None,
    preload: list[str] | None = None,
) -> list[SymbolBinding]:
    """Compute symbol bindings across the resolved dependency graph.

    For each DSO in the graph, looks up its imported (undefined) symbols and
    determines which provider DSO satisfies each import, following the dynamic
    linker's breadth-first search order.

    Args:
        graph: A resolved dependency graph from ``resolve_dependencies()``.
        metadata: Optional pre-parsed metadata per resolved path. If None,
            uses the ``elf_metadata`` stored in each graph node.
        preload: Optional list of DSO paths to treat as LD_PRELOAD (searched first).

    Returns:
        A list of SymbolBinding entries for all imports across all DSOs.
    """
    bindings: list[SymbolBinding] = []

    # Build a global export index: for each DSO, its exported symbols.
    # Index: provider_path → { symbol_name → [(version, is_default, visibility)] }
    export_index: dict[str, dict[str, list[tuple[str, bool, str]]]] = {}
    for node_path, node in graph.nodes.items():
        meta = (metadata or {}).get(node_path) or node.elf_metadata
        if meta is None:
            continue
        sym_map: dict[str, list[tuple[str, bool, str]]] = {}
        for sym in meta.symbols:
            key = sym.name
            if key not in sym_map:
                sym_map[key] = []
            sym_map[key].append((sym.version, sym.is_default, sym.visibility))
        export_index[node_path] = sym_map

    # Determine search order: BFS from root gives the "breadth-first" loaded
    # order that the dynamic linker uses for symbol lookup.
    load_order = _compute_load_order(graph)
    preload_paths = list(preload or [])

    # Process each DSO's imports.
    for node_path, node in graph.nodes.items():
        meta = (metadata or {}).get(node_path) or node.elf_metadata
        if meta is None:
            continue

        for imp in meta.imports:
            binding = _resolve_import(
                consumer=node_path,
                sym_name=imp.name,
                required_version=imp.version,
                is_weak=(imp.binding == ElfSymbolBinding.WEAK),
                preload_paths=preload_paths,
                load_order=load_order,
                export_index=export_index,
            )
            bindings.append(binding)

    return bindings


def _compute_load_order(graph: DependencyGraph) -> list[str]:
    """Compute breadth-first load order from the root."""
    if not graph.nodes:
        return []

    order: list[str] = []
    visited: set[str] = set()
    queue: deque[str] = deque()

    root = graph.root
    # Find the root key in nodes (may differ from graph.root due to resolution).
    root_key = None
    for key in graph.nodes:
        if key == root or graph.nodes[key].depth == 0:
            root_key = key
            break
    if root_key is None:
        return list(graph.nodes.keys())

    queue.append(root_key)
    visited.add(root_key)

    # Build adjacency from edges.
    adj: dict[str, list[str]] = {}
    for consumer, provider in graph.edges:
        if consumer not in adj:
            adj[consumer] = []
        adj[consumer].append(provider)

    while queue:
        current = queue.popleft()
        order.append(current)
        for child in adj.get(current, []):
            if child not in visited:
                visited.add(child)
                queue.append(child)

    # Add any nodes not reachable via edges (shouldn't happen, but defensive).
    for key in graph.nodes:
        if key not in visited:
            order.append(key)

    return order


def _try_versioned_match(
    consumer: str,
    sym_name: str,
    required_version: str,
    provider_path: str,
    versions: list[tuple[str, bool, str]],
    first_provider: str | None,
    export_index: dict[str, dict[str, list[tuple[str, bool, str]]]],
) -> SymbolBinding | None:
    """Try to match a versioned symbol requirement against *provider_path*.

    Returns a SymbolBinding on match, or None to continue searching.
    """
    for ver, is_default, vis in versions:
        if vis in ("hidden", "internal"):
            continue
        if ver == required_version:
            # Detect interposition: only when a *different* provider
            # that also exports the *same version* appeared earlier
            # in the load order.
            if (
                first_provider is not None
                and first_provider != provider_path
                and _provider_has_version(
                    export_index, first_provider, sym_name, required_version,
                )
            ):
                return SymbolBinding(
                    consumer=consumer,
                    symbol=sym_name,
                    version=required_version,
                    provider=provider_path,
                    status=BindingStatus.INTERPOSED,
                    explanation=(
                        f"Symbol {sym_name}@{required_version} resolved from "
                        f"{provider_path} but {first_provider} also exports "
                        f"{sym_name}@{required_version} (interposition)"
                    ),
                )
            return SymbolBinding(
                consumer=consumer,
                symbol=sym_name,
                version=required_version,
                provider=provider_path,
                status=BindingStatus.RESOLVED_OK,
                explanation=f"Resolved {sym_name}@{required_version} from {provider_path}",
            )
    return None


def _try_unversioned_match(
    consumer: str,
    sym_name: str,
    provider_path: str,
    versions: list[tuple[str, bool, str]],
) -> SymbolBinding | None:
    """Try to match an unversioned symbol requirement against *provider_path*.

    Returns a SymbolBinding on match, or None to continue searching.
    """
    for ver, is_default, vis in versions:
        if vis in ("hidden", "internal"):
            continue
        if not ver or is_default:
            return SymbolBinding(
                consumer=consumer,
                symbol=sym_name,
                version="",
                provider=provider_path,
                status=BindingStatus.RESOLVED_OK,
                explanation=f"Resolved {sym_name} from {provider_path}",
            )
    return None


def _make_not_found_binding(
    consumer: str,
    sym_name: str,
    required_version: str,
    is_weak: bool,
    found_name_visible: bool,
    found_name_hidden_only: bool,
    first_provider: str | None,
    first_hidden_provider: str | None,
) -> SymbolBinding:
    """Create the appropriate SymbolBinding when no provider matched."""
    if found_name_hidden_only and not found_name_visible:
        return SymbolBinding(
            consumer=consumer, symbol=sym_name, version=required_version,
            provider=first_hidden_provider, status=BindingStatus.VISIBILITY_BLOCKED,
            explanation=(
                f"Symbol {sym_name} found in {first_hidden_provider} but all versions "
                "have hidden/internal visibility"
            ),
        )
    if found_name_visible:
        return SymbolBinding(
            consumer=consumer, symbol=sym_name, version=required_version,
            provider=first_provider, status=BindingStatus.VERSION_MISMATCH,
            explanation=(
                f"Symbol {sym_name} found but version {required_version!r} not matched "
                f"(first provider: {first_provider})"
            ),
        )
    if is_weak:
        return SymbolBinding(
            consumer=consumer, symbol=sym_name, version=required_version,
            provider=None, status=BindingStatus.WEAK_UNRESOLVED,
            explanation=f"Weak symbol {sym_name} unresolved (acceptable at runtime)",
        )
    return SymbolBinding(
        consumer=consumer, symbol=sym_name, version=required_version,
        provider=None, status=BindingStatus.MISSING,
        explanation=f"Symbol {sym_name} not found in any loaded DSO",
    )


def _provider_has_version(
    export_index: dict[str, dict[str, list[tuple[str, bool, str]]]],
    provider: str,
    sym_name: str,
    required_version: str,
) -> bool:
    """Check if *provider* exports *sym_name* with *required_version* visibly."""
    versions = export_index.get(provider, {}).get(sym_name, [])
    return any(
        ver == required_version and vis not in ("hidden", "internal")
        for ver, _, vis in versions
    )


def _resolve_import(
    consumer: str,
    sym_name: str,
    required_version: str,
    is_weak: bool,
    preload_paths: list[str],
    load_order: list[str],
    export_index: dict[str, dict[str, list[tuple[str, bool, str]]]],
) -> SymbolBinding:
    """Resolve a single imported symbol against the loaded DSO set."""
    # Search order: preload → global load order (BFS).
    search_order = preload_paths + load_order

    # Track whether we found the symbol name at all, with/without visibility.
    found_name_visible = False    # found with at least one visible version
    found_name_hidden_only = False  # found but all versions are hidden/internal
    first_provider = None         # first provider with a visible matching symbol
    first_hidden_provider = None  # first provider where symbol exists but is hidden

    for provider_path in search_order:
        # Skip self — a DSO doesn't provide its own imports.
        if provider_path == consumer:
            continue

        exports = export_index.get(provider_path, {})
        versions = exports.get(sym_name)
        if versions is None:
            continue

        # Classify visibility of this provider's versions of the symbol.
        has_visible = any(vis not in ("hidden", "internal") for _, _, vis in versions)
        all_hidden = all(vis in ("hidden", "internal") for _, _, vis in versions)

        if all_hidden:
            if first_hidden_provider is None:
                first_hidden_provider = provider_path
            if not found_name_visible:
                found_name_hidden_only = True
            continue  # hidden symbols cannot satisfy external refs

        if has_visible:
            found_name_visible = True
            found_name_hidden_only = False
            if first_provider is None:
                first_provider = provider_path

        # Check version compatibility.
        if required_version:
            match = _try_versioned_match(
                consumer, sym_name, required_version, provider_path,
                versions, first_provider, export_index,
            )
            if match is not None:
                return match
            # Symbol found but version doesn't match — keep searching.
        else:
            match = _try_unversioned_match(
                consumer, sym_name, provider_path, versions,
            )
            if match is not None:
                return match

    return _make_not_found_binding(
        consumer, sym_name, required_version, is_weak,
        found_name_visible, found_name_hidden_only,
        first_provider, first_hidden_provider,
    )

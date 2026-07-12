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

"""Runtime symbol-binding rebound diff — cross-environment provider comparison.

``binder.compute_bindings()`` already simulates, independently for each
resolved environment, which provider DSO satisfies each consumer's imported
symbol. Neither ``stack_checker``'s DSO-level diff (which only compares
whether a library was added/removed/content-changed) nor a per-library ABI
diff (which only compares one library's own export table across versions)
ever compares *bindings* themselves between the baseline and candidate
environments. That leaves a real gap: a symbol can silently rebind to a
different provider DSO — via dependency reordering, a sibling library
gaining or losing the export, or interposition drift — while every artifact
involved, taken alone, looks unchanged.

This module fills that gap: given the resolved dependency graphs and
``SymbolBinding`` lists for two environments (as already computed by
``stack_checker.check_stack``), it reports the symbols whose *effective*
runtime provider or weak-resolution status changed.
"""
from __future__ import annotations

from .binder import BindingStatus, SymbolBinding
from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .resolver import DependencyGraph

#: Binding statuses that count as "the symbol resolved to a live provider"
#: (as opposed to missing, version-mismatched, weak-unresolved, or blocked).
_RESOLVED_STATUSES = frozenset({BindingStatus.RESOLVED_OK, BindingStatus.INTERPOSED})


def _soname_map(graph: DependencyGraph) -> dict[str, str]:
    """Map each resolved DSO path in *graph* to its SONAME (or path if unset)."""
    return {path: (node.soname or path) for path, node in graph.nodes.items()}


def _identity(soname_map: dict[str, str], path: str | None) -> str | None:
    """Resolve *path* to a stable cross-environment identity (its SONAME)."""
    if path is None:
        return None
    return soname_map.get(path, path)


def diff_runtime_bindings(
    baseline_graph: DependencyGraph,
    candidate_graph: DependencyGraph,
    bindings_baseline: list[SymbolBinding],
    bindings_candidate: list[SymbolBinding],
) -> list[Change]:
    """Diff per-symbol runtime bindings between two resolved environments.

    Bindings are matched across environments by ``(consumer_soname, symbol,
    required_version)`` rather than raw DSO path, since baseline and
    candidate are resolved under different sysroots and the same logical
    consumer/provider can live at a different absolute path in each. Only
    keys present in both environments are compared — a consumer or import
    that only exists on one side is a different, already-covered event
    (DSO added/removed, or a symbol import added/removed).
    """
    base_soname = _soname_map(baseline_graph)
    cand_soname = _soname_map(candidate_graph)

    base_by_key: dict[tuple[str | None, str, str], SymbolBinding] = {}
    for b in bindings_baseline:
        key = (_identity(base_soname, b.consumer), b.symbol, b.version)
        base_by_key[key] = b

    changes: list[Change] = []
    for b_new in bindings_candidate:
        key = (_identity(cand_soname, b_new.consumer), b_new.symbol, b_new.version)
        b_old = base_by_key.get(key)
        if b_old is None:
            continue
        consumer_soname, symbol, _version = key

        old_provider = _identity(base_soname, b_old.provider)
        new_provider = _identity(cand_soname, b_new.provider)

        if (
            b_old.status in _RESOLVED_STATUSES
            and b_new.status in _RESOLVED_STATUSES
            and old_provider is not None
            and new_provider is not None
            and old_provider != new_provider
        ):
            changes.append(
                make_change(
                    ChangeKind.RUNTIME_SYMBOL_PROVIDER_CHANGED,
                    symbol=symbol,
                    name=str(consumer_soname),
                    old=old_provider,
                    new=new_provider,
                    old_value=old_provider,
                    new_value=new_provider,
                )
            )
            continue

        old_weak = b_old.status == BindingStatus.WEAK_UNRESOLVED
        new_weak = b_new.status == BindingStatus.WEAK_UNRESOLVED
        old_resolved = b_old.status in _RESOLVED_STATUSES
        new_resolved = b_new.status in _RESOLVED_STATUSES
        if (old_weak and new_resolved) or (old_resolved and new_weak):
            changes.append(
                make_change(
                    ChangeKind.RUNTIME_WEAK_RESOLUTION_CHANGED,
                    symbol=symbol,
                    name=str(consumer_soname),
                    old=b_old.status.value,
                    new=b_new.status.value,
                    old_value=b_old.status.value,
                    new_value=b_new.status.value,
                )
            )

    return changes

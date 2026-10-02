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

"""Shared machinery for harness H5 (defect family F5, "optimization ≡ reference").

See ``docs/contribute/plans/defect-family-harnesses.md`` § H5. Three parts:

* :func:`scan_optimization_sites` -- the AST inventory of every cache built on
  the central wrapper (:mod:`abicheck.model.execution_cache`) and every
  thread/process-pool construction under ``abicheck/``.
* :class:`ReferenceMode` -- the **production** switch. Design-hardening plan
  Phase 4 reversed this harness's original decision (a test-side bypass per
  site): every cache now goes through one wrapper honouring
  ``ABICHECK_REFERENCE_MODE=1``, and that switch also forces every pool
  inline. Engagement is read from the wrapper's registry -- each cache counts
  the calls it bypassed -- so a cell still proves the reference configuration
  actually ran (AGENTS.md, "A differential test must prove both of its
  configurations actually ran").
* :func:`canonical_report` -- the oracle's normalisation, removing only the
  fields in :data:`VOLATILE_FIELDS`.
"""

from __future__ import annotations

import ast
import copy
import importlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _mutmut_names import canonical_def_name, is_mutmut_artifact

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "abicheck"

# ── inventory ────────────────────────────────────────────────────────────────

_POOL_CALLEES = ("ThreadPoolExecutor", "ProcessPoolExecutor", "BudgetedExecutor")
#: Wrapper decorators -> site kind.
_DECORATOR_KINDS = {
    "memoized": "memoized",
    "memoized_property": "memoized_property",
    "memoize_header_scan": "header_scan",
}
#: Wrapper cache constructors -> site kind.
_CACHE_CTORS = {
    "MemoryCache": "memory_cache",
    "ScopedCache": "scoped_cache",
    "SharedScopedCache": "shared_scoped_cache",
    "InstanceMemo": "instance_memo",
    "DiskCache": "disk_cache",
    "register_cache": "registered",
}


@dataclass(frozen=True, order=True)
class Site:
    """One optimization site. ``key`` is line-number free so the table is stable."""

    module: str  # dotted module name
    kind: str  # one of _DECORATOR_KINDS / _CACHE_CTORS values, or "pool"
    name: str  # qualname of the decorated function / module variable / enclosing function + callee

    @property
    def key(self) -> str:
        return f"{self.module}::{self.kind}::{self.name}"


def _last(dotted: str) -> str:
    return dotted.rsplit(".", 1)[-1]


class _Scanner(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.stack: list[str] = []
        self.sites: list[Site] = []
        self._seen: dict[str, int] = {}

    def _qual(self, name: str) -> str:
        return ".".join([*self.stack, name])

    def _numbered(self, base: str) -> str:
        n = self._seen.get(base, 0)
        self._seen[base] = n + 1
        return base if n == 0 else f"{base}#{n + 1}"

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        canonical = canonical_def_name(node.name)
        if canonical is None:  # a mutmut per-mutant copy, not a source site
            return
        if canonical != node.name:
            node = copy.copy(node)
            node.name = canonical
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            kind = _DECORATOR_KINDS.get(_last(ast.unparse(target)))
            if kind is not None:
                self.sites.append(Site(self.module, kind, self._qual(node.name)))
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Assign(self, node: ast.Assign | ast.AnnAssign) -> None:
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        value = node.value
        if (
            not self.stack
            and isinstance(target, ast.Name)
            and isinstance(value, ast.Call)
            and _last(ast.unparse(value.func)) in _CACHE_CTORS
            and not is_mutmut_artifact(target.id)
        ):
            kind = _CACHE_CTORS[_last(ast.unparse(value.func))]
            self.sites.append(Site(self.module, kind, target.id))
            for arg in value.args:
                self.visit(arg)
            return
        self.generic_visit(node)

    visit_AnnAssign = visit_Assign  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        callee = _last(ast.unparse(node.func))
        if callee in _POOL_CALLEES and not (
            self.stack
            and self.stack[-1] == "BudgetedExecutor"
            and callee == "BudgetedExecutor"
        ):
            base = f"{'.'.join(self.stack) or '<module>'}->{callee}"
            self.sites.append(Site(self.module, "pool", self._numbered(base)))
        elif callee in _CACHE_CTORS and self.stack:
            # A cache built inside a function (a decorator factory's closure):
            # not a module-level object, so it is inventoried by the function.
            base = f"{'.'.join(self.stack)}->{callee}"
            self.sites.append(
                Site(self.module, _CACHE_CTORS[callee], self._numbered(base))
            )
        self.generic_visit(node)


def scan_optimization_sites(root: Path = PKG) -> list[Site]:
    """Every wrapper cache and pool-construction site under *root* (sorted).

    ``abicheck/model/execution_cache.py`` itself is the wrapper, not a site.
    """
    sites: list[Site] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        if module in (
            "abicheck.model.execution_cache",
            "abicheck.model.execution_cache_scoped",
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanner = _Scanner(module)
        scanner.visit(tree)
        sites.extend(scanner.sites)
    return sorted(set(sites))


# ── volatile-field table (the only things the oracle removes) ────────────────

#: key -> reason. Removed wherever they occur in the report tree. Nothing
#: else is normalised: a difference anywhere else is a violation.
VOLATILE_FIELDS: dict[str, str] = {
    "elapsed_s": "wall-clock duration of a build-source pass (buildsource/model.py); timing, not a fact",
    "created_at": "capture timestamp of a build-source manifest/baseline; differs per run by design",
    "source_mtime": "filesystem mtime of an input; differs between two copies of the same input",
    "source_mtime_epoch": "same as source_mtime, epoch form",
    "extractor.duration_seconds": "ADR-033 D6/D9 wall-clock metric of the inline L4 diff (buildsource/evidence_report.py)",
    "est_seconds": "dry-run cost estimate; a wall-clock projection",
}


def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in VOLATILE_FIELDS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def canonical_report(text: str) -> str:
    """The JSON report with only :data:`VOLATILE_FIELDS` removed, key-sorted."""
    return json.dumps(_strip(json.loads(text)), sort_keys=True, indent=1)


def first_difference(a: str, b: str) -> str:
    for i, (la, lb) in enumerate(zip(a.splitlines(), b.splitlines(), strict=False)):
        if la != lb:
            return f"line {i}: {la!r} != {lb!r}"
    return f"length {len(a)} != {len(b)}" if a != b else ""


# ── reference mode (the production switch) ───────────────────────────────


def _resolve(site: Site) -> tuple[Any, list[str]]:
    mod = importlib.import_module(site.module)
    parts = site.name.split(".")
    owner: Any = mod
    for p in parts[:-1]:
        owner = getattr(owner, p)
    return owner, parts


def registry_name(site: Site) -> str:
    """The name *site*'s cache is registered under in the wrapper's registry."""
    if site.kind == "header_scan":
        return f"{site.module}.{site.name}.header_scan"
    if site.kind in ("memoized", "memoized_property"):
        return f"{site.module}.{site.name}"
    owner, parts = _resolve(site)
    obj = getattr(owner, parts[-1])
    return str(getattr(obj, "name"))


def _stats() -> dict[str, dict[str, int]]:
    from abicheck.model.execution_cache import cache_stats

    return cache_stats()


@dataclass
class ReferenceMode:
    """``ABICHECK_REFERENCE_MODE=1`` for the rest of the test, plus the map
    from each inventoried site to the registry name its bypasses count under.

    :attr:`calls` is read live: ``{site.key: bypasses}`` for every site whose
    cache bypassed at least once since :meth:`install`.
    """

    names: dict[str, str] = field(default_factory=dict)
    unpatchable: dict[str, str] = field(default_factory=dict)

    def install(self, mp: pytest.MonkeyPatch, sites: list[Site]) -> None:
        from abicheck.model.execution_cache import (
            REFERENCE_MODE_ENV_VAR,
            reset_cache_stats,
        )

        registered = set(_stats())
        for site in sites:
            if site.kind == "pool" or "->" in site.name:
                # A pool is governed by the thread budget; a cache built
                # inside a factory is inventoried at each decorated use.
                continue
            try:
                name = registry_name(site)
            except Exception as exc:  # noqa: BLE001 - recorded, asserted on by the caller
                self.unpatchable[site.key] = f"{type(exc).__name__}: {exc}"
                continue
            if name not in registered:
                self.unpatchable[site.key] = f"not registered: {name}"
                continue
            self.names[site.key] = name
        mp.setenv(REFERENCE_MODE_ENV_VAR, "1")
        reset_cache_stats()

    @property
    def calls(self) -> dict[str, int]:
        stats = _stats()
        out = {
            key: stats[name]["bypasses"]
            for key, name in self.names.items()
            if stats.get(name, {}).get("bypasses")
        }
        return out


def functools_objects(sites: list[Site]) -> dict[str, Any]:
    """Every in-process cache's registry name, keyed by site (after importing
    every inventoried module, so each cache is registered). The memoized
    *callables* are resolvable through :func:`memoized_callable`."""
    out: dict[str, Any] = {}
    for site in sites:
        if site.kind == "pool" or "->" in site.name:
            continue
        out[site.key] = registry_name(site)
    return out


def memoized_callable(site_key: str) -> Any:
    module, kind, name = site_key.split("::")
    assert kind == "memoized", site_key
    owner, parts = _resolve(Site(module, kind, name))
    return getattr(owner, parts[-1])


def cache_info_totals(objs: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """``(hits, misses)`` per in-process cache site."""
    stats = _stats()
    return {
        k: (stats[n]["hits"], stats[n]["misses"]) for k, n in objs.items() if n in stats
    }


def clear_functools(objs: dict[str, Any]) -> None:
    """Drop every process-lifetime entry and zero every counter."""
    from abicheck.model.execution_cache import clear_all_caches, reset_cache_stats

    clear_all_caches()
    reset_cache_stats()


@dataclass
class GrantSpy:
    """Records every ``BudgetedExecutor`` grant, so a thread cell proves the
    configuration it claims (a >1-thread grant, or none) really happened."""

    grants: list[int] = field(default_factory=list)

    def install(self, mp: pytest.MonkeyPatch) -> None:
        from abicheck import process_resources

        orig = process_resources.BudgetedExecutor.__init__

        def spy(inner_self: Any, *a: Any, **k: Any) -> None:
            orig(inner_self, *a, **k)
            self.grants.append(inner_self.granted_threads)

        mp.setattr(process_resources.BudgetedExecutor, "__init__", spy)


@dataclass
class DiskCacheSpy:
    """Points the whole-snapshot cache *and* the header-AST cache at *root*
    and counts whole-snapshot hits, misses and stores."""

    hits: int = 0
    misses: int = 0
    stores: int = 0

    def install(self, mp: pytest.MonkeyPatch, root: Path) -> None:
        from abicheck import snapshot_cache

        mp.setattr(snapshot_cache, "_CACHE_DIR", root)
        # The header-AST disk cache (dumper_cache._cache_path) derives its
        # root from XDG_CACHE_HOME; give it the same per-configuration root,
        # or a second configuration would be served the first one's AST.
        mp.setenv("XDG_CACHE_HOME", str(root / "xdg"))
        lookup, store = snapshot_cache.lookup_key, snapshot_cache.store_key

        def spy_lookup(key: str, path: Path) -> Any:
            got = lookup(key, path)
            if got is None:
                self.misses += 1
            else:
                self.hits += 1
            return got

        def spy_store(*a: Any, **k: Any) -> None:
            self.stores += 1
            store(*a, **k)

        mp.setattr(snapshot_cache, "lookup_key", spy_lookup)
        mp.setattr(snapshot_cache, "store_key", spy_store)


def iter_memo_sites(sites: list[Site]) -> Iterator[Site]:
    return (s for s in sites if s.kind != "pool")

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

* :func:`scan_optimization_sites` -- the AST inventory of every memo/cache
  and every thread/process-pool construction under ``abicheck/``.
* :class:`ReferenceMode` -- the *test-side* reference mode. The plan
  proposes a central ``ABICHECK_REFERENCE_MODE`` kill switch; this harness
  deliberately does not add one to production. Every inventoried memo can
  be bypassed from the test (a ``functools`` cache by rebinding every
  module-global reference to its ``__wrapped__``, a ``cached_property`` by a
  plain ``property``, a memo dict by a dict that never stores, a scoped
  ``ContextVar`` memo by a variable that is never set, a memo *object* by a
  per-site handler), so a production switch would only have duplicated that
  with a second, untested code path in every cache. Serial execution uses
  the existing ``ABICHECK_MAX_THREADS`` budget knob; the disk cache uses
  ``snapshot_cache._CACHE_DIR``. Each bypass counts its own calls, which is
  how a cell proves the reference configuration actually ran (AGENTS.md,
  "A differential test must prove both of its configurations actually ran").
* :func:`canonical_report` -- the oracle's normalisation, removing only the
  fields in :data:`VOLATILE_FIELDS`.
"""

from __future__ import annotations

import ast
import contextvars
import copy
import functools
import importlib
import json
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _mutmut_names import canonical_def_name, is_mutmut_artifact

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "abicheck"

# ── inventory ────────────────────────────────────────────────────────────────

_MEMO_DECORATORS = ("cache", "lru_cache", "cached_property", "run_scoped_digest_cache")
_POOL_CALLEES = ("ThreadPoolExecutor", "ProcessPoolExecutor", "BudgetedExecutor")
_MEMO_CONTAINERS = (
    "dict",
    "OrderedDict",
    "WeakKeyDictionary",
    "WeakValueDictionary",
    "ContextVar",
    "defaultdict",
)


@dataclass(frozen=True, order=True)
class Site:
    """One optimization site. ``key`` is line-number free so the table is stable."""

    module: str  # dotted module name
    kind: str  # functools | cached_property | scoped_decorator | memo_dict | memo_contextvar | memo_object | pool
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
        self._pool_seen: dict[str, int] = {}

    def _qual(self, name: str) -> str:
        return ".".join([*self.stack, name])

    def _visit_def(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        canonical = canonical_def_name(node.name)
        if canonical is None:  # a mutmut per-mutant copy, not a source site
            return
        if canonical != node.name:
            node = copy.copy(node)
            node.name = canonical
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            callee = _last(ast.unparse(target))
            if callee in ("cache", "lru_cache"):
                self.sites.append(Site(self.module, "functools", self._qual(node.name)))
            elif callee == "cached_property":
                self.sites.append(
                    Site(self.module, "cached_property", self._qual(node.name))
                )
            elif callee == "run_scoped_digest_cache":
                self.sites.append(
                    Site(self.module, "scoped_decorator", self._qual(node.name))
                )
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        callee = _last(ast.unparse(node.func))
        if callee in _POOL_CALLEES and not (
            self.stack
            and self.stack[-1] == "BudgetedExecutor"
            and callee == "BudgetedExecutor"
        ):
            base = f"{'.'.join(self.stack) or '<module>'}->{callee}"
            n = self._pool_seen.get(base, 0)
            self._pool_seen[base] = n + 1
            self.sites.append(
                Site(self.module, "pool", base if n == 0 else f"{base}#{n + 1}")
            )
        self.generic_visit(node)


def _module_memo_sites(module: str, tree: ast.Module) -> list[Site]:
    out: list[Site] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value = node.targets[0].id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            name, value = node.target.id, node.value
        else:
            continue
        if is_mutmut_artifact(name):
            continue
        named = "cache" in name.lower() or "memo" in name.lower()
        callee = _last(ast.unparse(value.func)) if isinstance(value, ast.Call) else ""
        if named and isinstance(value, ast.Dict) and not value.keys:
            out.append(Site(module, "memo_dict", name))
        elif named and callee in _MEMO_CONTAINERS:
            out.append(
                Site(
                    module,
                    "memo_contextvar" if callee == "ContextVar" else "memo_dict",
                    name,
                )
            )
        elif callee and ("Cache" in callee or "Memo" in callee):
            out.append(Site(module, "memo_object", name))
    return out


def scan_optimization_sites(root: Path = PKG) -> list[Site]:
    """Every memo/cache and pool-construction site under *root* (sorted)."""
    sites: list[Site] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        module = ".".join(parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanner = _Scanner(module)
        scanner.visit(tree)
        sites.extend(scanner.sites)
        sites.extend(_module_memo_sites(module, tree))
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


# ── reference mode (test-side) ───────────────────────────────────────────────


class _NoStoreMixin:
    """A memo mapping that never retains anything; lookups are counted."""

    _on_lookup: Callable[[], None] = staticmethod(lambda: None)

    def __setitem__(self, key: Any, value: Any) -> None:
        return None

    def setdefault(self, key: Any, default: Any = None) -> Any:
        return default

    def get(self, key: Any, default: Any = None) -> Any:
        self._on_lookup()
        return default

    def __contains__(self, key: object) -> bool:
        self._on_lookup()
        return False


class _NoStoreDict(_NoStoreMixin, dict):  # type: ignore[type-arg]
    pass


class _NoStoreOrderedDict(_NoStoreMixin, OrderedDict):  # type: ignore[type-arg]
    pass


class _NeverSetVar:
    """A ``ContextVar`` stand-in that always reads its default: a scoped memo
    whose scope is never observed as open, so every lookup recomputes."""

    def __init__(self, default: Any, on_get: Callable[[], None] = lambda: None) -> None:
        self._default = default
        self._on_get = on_get

    def get(self, *default: Any) -> Any:
        self._on_get()
        return self._default

    def set(self, value: Any) -> object:
        return object()

    def reset(self, token: object) -> None:
        return None


class _NoMemoState:
    __slots__ = ("_on_get", "depth", "lock")

    def __init__(self, lock: Any, on_get: Callable[[], None]) -> None:
        self.depth = 0
        self.lock = lock
        self._on_get = on_get

    @property
    def memo(self) -> None:
        self._on_get()
        return None

    @memo.setter
    def memo(self, value: Any) -> None:
        return None


def _var_default(var: contextvars.ContextVar[Any]) -> Any:
    try:
        return contextvars.Context().run(var.get)
    except LookupError:
        return None


def _resolve(site: Site) -> tuple[Any, list[str]]:
    mod = importlib.import_module(site.module)
    parts = site.name.split(".")
    owner: Any = mod
    for p in parts[:-1]:
        owner = getattr(owner, p)
    return owner, parts


@dataclass
class ReferenceMode:
    """Bypass every reachable memo site; count calls through each bypass."""

    calls: dict[str, int] = field(default_factory=dict)
    unpatchable: dict[str, str] = field(default_factory=dict)
    scoped_keys: list[str] = field(default_factory=list)

    def _tick(self, key: str) -> Callable[[], None]:
        def tick() -> None:
            self.calls[key] = self.calls.get(key, 0) + 1

        return tick

    def _count(self, key: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def counted(*a: Any, **k: Any) -> Any:
            self.calls[key] = self.calls.get(key, 0) + 1
            return fn(*a, **k)

        return counted

    @staticmethod
    def _swap_everywhere(mp: pytest.MonkeyPatch, old: object, new: object) -> int:
        n = 0
        for mod in list(sys.modules.values()):
            d = getattr(mod, "__dict__", None)
            if not d or not str(getattr(mod, "__name__", "")).startswith("abicheck"):
                continue
            for attr, val in list(d.items()):
                if val is old:
                    mp.setattr(mod, attr, new)
                    n += 1
        return n

    def install(self, mp: pytest.MonkeyPatch, sites: list[Site]) -> None:
        for site in sites:
            try:
                self._install_one(mp, site)
            except Exception as exc:  # noqa: BLE001 - recorded, asserted on by the caller
                self.unpatchable[site.key] = f"{type(exc).__name__}: {exc}"

    def _install_one(self, mp: pytest.MonkeyPatch, site: Site) -> None:
        owner, parts = _resolve(site)
        leaf = parts[-1]
        key = site.key
        if site.kind == "functools":
            cached = getattr(owner, leaf)
            if isinstance(cached, staticmethod | classmethod):
                cached = cached.__func__
            inner = self._count(key, cached.__wrapped__)
            if isinstance(owner, type):
                mp.setattr(
                    owner,
                    leaf,
                    staticmethod(inner)
                    if isinstance(owner.__dict__[leaf], staticmethod)
                    else inner,
                )
            if not self._swap_everywhere(mp, cached, inner) and not isinstance(
                owner, type
            ):
                raise LookupError("no module-global reference to rebind")
        elif site.kind == "cached_property":
            cp = owner.__dict__[leaf]
            mp.setattr(owner, leaf, property(self._count(key, cp.func)))
        elif site.kind == "scoped_decorator":
            # Every run-scoped digest memo shares one scope variable: bypass
            # it once; each lookup is credited to every decorated site.
            from abicheck.storage import snapshot_digest_cache as sdc

            self.scoped_keys.append(key)
            if not isinstance(sdc._SCOPE, _NeverSetVar):

                def tick_all() -> None:
                    for k in self.scoped_keys:
                        self.calls[k] = self.calls.get(k, 0) + 1

                mp.setattr(sdc, "_SCOPE", _NeverSetVar(None, tick_all))
        elif site.kind == "memo_dict":
            cur = getattr(owner, leaf)
            repl: _NoStoreMixin = (
                _NoStoreOrderedDict()
                if isinstance(cur, OrderedDict)
                else _NoStoreDict()
            )
            repl._on_lookup = self._tick(key)  # type: ignore[method-assign]
            mp.setattr(owner, leaf, repl)
        elif site.kind == "memo_contextvar":
            cur = getattr(owner, leaf)
            mp.setattr(owner, leaf, _NeverSetVar(_var_default(cur), self._tick(key)))
        elif site.kind == "memo_object":
            self._install_object(mp, site, owner, leaf)
        # pools: governed by ABICHECK_MAX_THREADS, not by this bypass.

    def _install_object(
        self, mp: pytest.MonkeyPatch, site: Site, owner: Any, leaf: str
    ) -> None:
        obj = getattr(owner, leaf)
        key = site.key
        cls = type(obj).__name__
        if cls == "DigestMemo":
            mp.setattr(
                obj, "get_or_compute", self._count(key, lambda _k, compute: compute())
            )
        elif cls == "_MatchCache":
            mp.setattr(obj, "get", self._count(key, lambda _key: None))
            mp.setattr(obj, "put", lambda *a, **k: None)
        elif cls == "_VocabularyCache":
            mp.setattr(
                obj,
                "get_or_compile",
                self._count(key, lambda spellings, fn: fn(frozenset(spellings))),
            )
        elif cls == "_NormalizeMemoState":
            state = _NoMemoState(obj.lock, self._tick(key))
            mp.setattr(owner, leaf, state)
        else:
            raise TypeError(f"no reference-mode handler for memo object {cls}")


def functools_objects(sites: list[Site]) -> dict[str, Any]:
    """The real cached callables, resolved *before* a :class:`ReferenceMode`
    rebinds the module globals, so their counters stay readable."""
    out: dict[str, Any] = {}
    for site in sites:
        if site.kind == "functools":
            owner, parts = _resolve(site)
            fn = getattr(owner, parts[-1])
            out[site.key] = getattr(fn, "__func__", fn)
    return out


def cache_info_totals(objs: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """``(hits, misses)`` per functools site."""
    return {k: (fn.cache_info().hits, fn.cache_info().misses) for k, fn in objs.items()}


def clear_functools(objs: dict[str, Any]) -> None:
    for fn in objs.values():
        fn.cache_clear()


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

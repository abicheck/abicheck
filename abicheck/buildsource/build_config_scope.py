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

"""``BuildConfig``'s ``scope:`` block: the ownership keys' parse and schema,
and the block's serialization.

Split out of ``build_config.py``, which sits at its ADR-061 no-growth
baseline: the ownership keys (``dependencies``, ``private_headers``,
``private_namespaces``, from ``docs/contribute/plans/
target-ownership-and-extraction-scope.md``) moved in here together with the
``scope:`` serializer they extend, so the parent shrinks rather than grows.
"""

from __future__ import annotations

from typing import Any

from ..model.ownership_rules import DependencyRoots, OwnershipRules

__all__ = [
    "OWNERSHIP_LIST_KEYS",
    "dependencies_findings",
    "parse_ownership_rules",
    "scope_block",
]

#: ``scope:`` ownership keys taking a string or a list of strings.
OWNERSHIP_LIST_KEYS = frozenset({"private_headers", "private_namespaces"})


def _strs(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(x) for x in value)
    return ()


def dependencies_findings(value: object) -> list[str]:
    """Type findings for ``scope.dependencies``: a list of
    ``{name: str, header_roots: str | [str]}`` mappings, names unique."""
    if not isinstance(value, list):
        return [
            "scope.dependencies must be a list of {name, header_roots} mappings, "
            f"got {type(value).__name__}: {value!r}"
        ]
    findings: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(value):
        where = f"scope.dependencies[{i}]"
        if not isinstance(entry, dict):
            findings.append(f"{where} must be a mapping, got {type(entry).__name__}")
            continue
        unknown = sorted(set(entry) - {"name", "header_roots"})
        if unknown:
            findings.append(f"{where} has unknown key(s): {', '.join(unknown)}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            findings.append(f"{where}.name must be a non-empty string")
        elif name in seen:
            findings.append(f"{where}.name {name!r} is declared twice")
        else:
            seen.add(name)
        roots = entry.get("header_roots")
        if isinstance(roots, str):
            roots = [roots]
        if not isinstance(roots, list) or not roots:
            findings.append(f"{where}.header_roots must be a non-empty list of strings")
        elif any(not isinstance(r, str) or not r.strip() for r in roots):
            findings.append(f"{where}.header_roots must hold only non-empty strings")
    return findings


def parse_ownership_rules(scope: dict[str, object]) -> OwnershipRules:
    """The configured ownership rules. ``target_roots`` stays empty: it is
    ``public_header_dirs`` plus any ``-H`` directory, folded in by the
    caller that knows both."""
    dependencies: list[DependencyRoots] = []
    raw = scope.get("dependencies")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            dependencies.append(
                DependencyRoots(str(entry["name"]), _strs(entry.get("header_roots")))
            )
    return OwnershipRules(
        dependencies=tuple(dependencies),
        private_headers=_strs(scope.get("private_headers")),
        private_namespaces=_strs(scope.get("private_namespaces")),
    )


def scope_block(cfg: Any) -> dict[str, Any]:
    """Non-default ``scope:`` keys of *cfg* (a ``BuildConfig``)."""
    scope: dict[str, Any] = {}
    if cfg.scope_public is not None:
        scope["public"] = cfg.scope_public
    if cfg.collapse_versioned_symbols is not None:
        scope["collapse_versioned_symbols"] = cfg.collapse_versioned_symbols
    if cfg.public_symbols:
        scope["public_symbols"] = list(cfg.public_symbols)
    if cfg.scope_show_redundant is not None:
        scope["show_redundant"] = cfg.scope_show_redundant
    if cfg.public_header_dirs:
        scope["public_header_dirs"] = list(cfg.public_header_dirs)
    if cfg.exclude_headers:
        scope["exclude_headers"] = list(cfg.exclude_headers)
    if cfg.scope_on_incomplete is not None:
        scope["on_incomplete"] = cfg.scope_on_incomplete
    rules: OwnershipRules = cfg.ownership
    if rules.dependencies:
        scope["dependencies"] = [
            {"name": d.name, "header_roots": list(d.header_roots)}
            for d in rules.dependencies
        ]
    if rules.private_headers:
        scope["private_headers"] = list(rules.private_headers)
    if rules.private_namespaces:
        scope["private_namespaces"] = list(rules.private_namespaces)
    return scope

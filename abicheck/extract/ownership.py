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

"""Classify a declaration's owner and contract from its file.

Phase 1 of ``docs/contribute/plans/target-ownership-and-extraction-scope.md``.
:func:`classify` is the one decision every later phase reads; nothing here
drops, keeps or rewrites a declaration.

The precedence the plan states as normative, and where each is decided:

1. An explicit root beats the system-path heuristic -- roots are tried
   first, :func:`~abicheck.provenance.is_system_header` only when none
   matches.
2. The most specific root wins -- the longest matching root, compared on
   resolved path segments.
3. A ``-I`` directory never grants ownership -- this module never sees one.
   Only :class:`~abicheck.model.ownership_rules.OwnershipRules` roots do.
4. ``private_*`` narrows contract for target-owned declarations only.
5. A file no root claims, outside the system heuristic, is ``unresolved``.
6. An implicit compiler builtin (``artificial`` and global,
   ``__atomic_*``/``__builtin_*``/``__sync_*``) is toolchain-owned whatever
   file castxml attributes it to.
7. A namespace that disagrees with the file is a diagnostic, never a
   reclassification.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..model.ownership_rules import (
    CONTRACT_EXTERNAL,
    CONTRACT_PRIVATE,
    CONTRACT_PUBLIC,
    CONTRACT_UNRESOLVED,
    OWNER_TARGET,
    OWNER_TOOLCHAIN,
    OWNER_UNRESOLVED,
    OwnershipRules,
    dependency_owner,
)
from ..provenance import is_system_header

__all__ = [
    "DeclarationSite",
    "OwnershipDecision",
    "OwnershipRuleError",
    "ResolvedOwnershipRules",
    "classify",
    "resolve_ownership_rules",
]

_BUILTIN_PREFIXES = ("__atomic_", "__builtin_", "__sync_")


class OwnershipRuleError(ValueError):
    """The configured rules are contradictory (a root claimed twice)."""


@dataclass(frozen=True)
class _Root:
    segments: tuple[str, ...]
    owner: str
    rule_id: str


@dataclass(frozen=True)
class ResolvedOwnershipRules:
    """:class:`OwnershipRules` with every root made absolute against one
    project root. Build with :func:`resolve_ownership_rules`."""

    project_root: str
    roots: tuple[_Root, ...]
    private_headers: tuple[str, ...]
    private_namespaces: tuple[tuple[str, ...], ...]
    dependency_names: frozenset[str]


@dataclass(frozen=True)
class DeclarationSite:
    """What classification needs of one declaration."""

    path: str | None
    qualified_name: str = ""
    artificial: bool = False


@dataclass(frozen=True)
class OwnershipDecision:
    owner: str
    contract: str
    #: Which rule decided the owner (and the contract, when it narrowed).
    rule_id: str
    diagnostics: tuple[str, ...] = ()


def _segments(path: str, base: str) -> tuple[str, ...]:
    joined = os.path.join(base, path) if not os.path.isabs(path) else path
    return tuple(PurePosixPath(os.path.normpath(joined).replace("\\", "/")).parts)


def resolve_ownership_rules(
    rules: OwnershipRules, project_root: Path | str
) -> ResolvedOwnershipRules:
    """Make every root absolute against *project_root* and reject a root that
    two entries claim -- the one contradiction precedence cannot resolve."""
    base = os.path.abspath(str(project_root))
    roots: list[_Root] = []
    claimed: dict[tuple[str, ...], str] = {}

    def add(spelling: str, owner: str, rule_id: str) -> None:
        segments = _segments(spelling, base)
        previous = claimed.get(segments)
        if previous is not None and previous != owner:
            raise OwnershipRuleError(
                f"header root {spelling!r} is claimed by both {previous} and {owner}"
            )
        claimed[segments] = owner
        roots.append(_Root(segments, owner, rule_id))

    for spelling in rules.target_roots:
        add(spelling, OWNER_TARGET, f"target_root:{spelling}")
    for dep in rules.dependencies:
        for spelling in dep.header_roots:
            add(
                spelling,
                dependency_owner(dep.name),
                f"dependency:{dep.name}:{spelling}",
            )
    return ResolvedOwnershipRules(
        project_root=base,
        roots=tuple(roots),
        private_headers=tuple(rules.private_headers),
        private_namespaces=tuple(
            _name_parts(ns) for ns in rules.private_namespaces if _name_parts(ns)
        ),
        dependency_names=frozenset(d.name for d in rules.dependencies),
    )


def _name_parts(qualified: str) -> tuple[str, ...]:
    """``::a::b<c::d>::e`` -> ``("a", "b<c::d>", "e")``: split on ``::`` only
    outside template arguments."""
    parts: list[str] = []
    depth = 0
    current = ""
    i = 0
    while i < len(qualified):
        ch = qualified[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(depth - 1, 0)
        if depth == 0 and qualified.startswith("::", i):
            parts.append(current)
            current = ""
            i += 2
            continue
        current += ch
        i += 1
    parts.append(current)
    return tuple(p.strip() for p in parts if p.strip())


def _is_builtin(site: DeclarationSite) -> bool:
    parts = _name_parts(site.qualified_name)
    return (
        site.artificial and len(parts) == 1 and parts[0].startswith(_BUILTIN_PREFIXES)
    )


def _matching_root(segments: tuple[str, ...], roots: tuple[_Root, ...]) -> _Root | None:
    best: _Root | None = None
    for root in roots:
        if segments[: len(root.segments)] != root.segments:
            continue
        if best is None or len(root.segments) > len(best.segments):
            best = root
    return best


def _private_rule(
    site: DeclarationSite, segments: tuple[str, ...], rules: ResolvedOwnershipRules
) -> str | None:
    base = PurePosixPath(*_segments(".", rules.project_root))
    full = PurePosixPath(*segments)
    candidates = [str(full)]
    if full.is_relative_to(base):
        candidates.append(str(full.relative_to(base)))
    for pattern in rules.private_headers:
        if any(fnmatch.fnmatchcase(c, pattern) for c in candidates):
            return f"private_header:{pattern}"
    name = _name_parts(site.qualified_name)
    for ns in rules.private_namespaces:
        # A namespace scopes what is declared *inside* it: `a::b` makes
        # `a::b::X` private, never `a::b` itself nor `a::bc::X`.
        if len(name) > len(ns) and name[: len(ns)] == ns:
            return f"private_namespace:{'::'.join(ns)}"
    return None


def classify(site: DeclarationSite, rules: ResolvedOwnershipRules) -> OwnershipDecision:
    """The owner and contract of the declaration at *site* under *rules*."""
    if _is_builtin(site):
        return OwnershipDecision(OWNER_TOOLCHAIN, CONTRACT_EXTERNAL, "builtin")
    if not site.path:
        return OwnershipDecision(OWNER_UNRESOLVED, CONTRACT_UNRESOLVED, "no_file")
    segments = _segments(site.path, rules.project_root)
    root = _matching_root(segments, rules.roots)
    if root is None:
        if is_system_header(site.path):
            return OwnershipDecision(OWNER_TOOLCHAIN, CONTRACT_EXTERNAL, "system_path")
        return OwnershipDecision(OWNER_UNRESOLVED, CONTRACT_UNRESOLVED, "no_root")
    if root.owner != OWNER_TARGET:
        return OwnershipDecision(root.owner, CONTRACT_EXTERNAL, root.rule_id)
    diagnostics: tuple[str, ...] = ()
    name = _name_parts(site.qualified_name)
    if name and name[0] in rules.dependency_names:
        diagnostics = (
            f"{site.qualified_name} is declared in a target file but in "
            f"dependency namespace {name[0]!r}; the file decides",
        )
    private = _private_rule(site, segments, rules)
    if private is not None:
        return OwnershipDecision(OWNER_TARGET, CONTRACT_PRIVATE, private, diagnostics)
    return OwnershipDecision(OWNER_TARGET, CONTRACT_PUBLIC, root.rule_id, diagnostics)

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

"""Stamp every declaration of a freshly extracted snapshot with its owner.

ADR-075 D2's "one evaluation": :func:`stamp_ownership` runs
:func:`~abicheck.extract.ownership.classify` once per declaration, from the
file the frontend recorded, and records both the per-entity decisions and
the rules they were made under (``AbiSnapshot.extraction_scope``). Every
later stage reads that stamp; nothing downstream re-classifies from paths.

Classification only (ADR-075 D6): no declaration is dropped, kept or
rewritten here.
"""

from __future__ import annotations

import os
from itertools import chain
from pathlib import PurePosixPath

from ..demangle import demangle_batch, strip_signature
from ..model.extraction_scope import EntityOwnership, ExtractionScope
from ..model.fact import Fact
from ..model.ownership_rules import DependencyRoots, OwnershipRequest, OwnershipRules
from ..model.snapshot import AbiSnapshot
from .ownership import DeclarationSite, classify, resolve_ownership_rules

__all__ = ["recorded_rules", "stamp_ownership"]


def _record_root(root: str, project_root: str | None) -> str:
    """*root* as stored: relative to *project_root* when under it, else absolute.

    POSIX spelling either way, with no trailing separator, so the same
    directory spelled two ways fingerprints once.
    """
    absolute = os.path.normpath(os.path.abspath(root))
    if project_root is not None:
        base = os.path.normpath(os.path.abspath(project_root))
        try:
            common = os.path.commonpath([absolute, base])
        except ValueError:  # different drives on Windows
            common = ""
        if common == base:
            rel = os.path.relpath(absolute, base)
            return PurePosixPath(*rel.replace("\\", "/").split("/")).as_posix()
    return absolute.replace("\\", "/")


def recorded_rules(request: OwnershipRequest) -> OwnershipRules:
    """The request's rules in their recorded (fingerprinted) spelling."""
    rules, root = request.rules, request.project_root
    return OwnershipRules(
        target_roots=tuple(_record_root(r, root) for r in rules.target_roots),
        dependencies=tuple(
            DependencyRoots(
                d.name, tuple(_record_root(r, root) for r in d.header_roots)
            )
            for d in rules.dependencies
        ),
        private_headers=rules.private_headers,
        private_namespaces=rules.private_namespaces,
        dependency_evidence=rules.dependency_evidence,
    )


def _declaring_file(decl: object) -> str | None:
    header = getattr(decl, "source_header", None)
    if not header:
        from ..provenance import header_from_location

        header = header_from_location(getattr(decl, "source_location", None))
    if not header:
        return None
    # A frontend spells a file as it was reached; a relative spelling is
    # relative to the working directory the parse ran in, not to the
    # project root the rules are anchored at.
    return os.path.abspath(header)


def _qualified_name(decl: object, demangled: dict[str, str]) -> str:
    """The declaration's scope-qualified name, as far as the record shows it.

    A type carries its own ``qualified_name``. A castxml function or
    variable records only its leaf (``hidden`` for ``lib::detail::hidden``),
    so a namespace rule would never see its scope; its Itanium linker name
    does carry the scope, and *demangled* (built once per snapshot) supplies
    it. A C-linkage symbol has no scope to recover and keeps its name.
    """
    qualified = getattr(decl, "qualified_name", None)
    if qualified:
        return str(qualified)
    mangled = str(getattr(decl, "mangled", "") or "")
    if mangled in demangled:
        return strip_signature(demangled[mangled])
    return str(getattr(decl, "name", "") or "")


def _demangled_scopes(snapshot: AbiSnapshot, needed: bool) -> dict[str, str]:
    """One batch demangle of every linker name, only when a namespace rule
    (``private_namespaces``) or a dependency name (rule 7's diagnostic) can
    read the result -- otherwise classification never looks at scopes."""
    if not needed:
        return {}
    names = [
        str(getattr(d, "mangled", "") or "")
        for d in chain(snapshot.functions, snapshot.variables)
    ]
    return demangle_batch([n for n in names if n])


def stamp_ownership(snapshot: AbiSnapshot, request: OwnershipRequest) -> AbiSnapshot:
    """Classify every declaration of *snapshot* under *request*, in place.

    Raises :class:`~abicheck.extract.ownership.OwnershipRuleError` when the
    rules contradict themselves (one root claimed twice) -- a configuration
    error the caller surfaces, never a silently partial stamp.
    """
    # Resolve the *recorded* spelling (relative roots are relative to the
    # project root, the rest absolute): matching is unchanged, and each
    # decision's rule id names the root exactly as the snapshot records it,
    # not as this machine happened to spell it.
    recorded = recorded_rules(request)
    resolved = resolve_ownership_rules(recorded, request.project_root or os.getcwd())
    # Distinct decisions are few; share one Fact per decision.
    interned: dict[tuple[str, str, str], Fact[EntityOwnership]] = {}
    diagnostics: set[str] = set()
    demangled = _demangled_scopes(
        snapshot, bool(resolved.private_namespaces or resolved.dependency_names)
    )
    for decl in chain(
        snapshot.functions, snapshot.variables, snapshot.types, snapshot.enums
    ):
        site = DeclarationSite(
            path=_declaring_file(decl),
            qualified_name=_qualified_name(decl, demangled),
            artificial=getattr(decl, "is_compiler_generated", None) is True,
        )
        decision = classify(site, resolved)
        diagnostics.update(decision.diagnostics)
        key = (decision.owner, decision.contract, decision.rule_id)
        fact = interned.get(key)
        if fact is None:
            fact = interned[key] = Fact.present(EntityOwnership(*key))
        decl.ownership_fact = fact  # type: ignore[union-attr]
    snapshot.extraction_scope = ExtractionScope(
        ownership_rules=recorded,
        dependency_evidence=recorded.dependency_evidence,
        diagnostics=tuple(sorted(diagnostics)),
    )
    return snapshot

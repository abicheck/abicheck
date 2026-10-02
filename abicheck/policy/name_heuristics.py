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

"""The name-heuristic catalogue (design-hardening plan Phase 5, family F4).

``model.name_heuristics`` is the registration API; the detectors register at
import. This module owns the *complete* registry: which modules register
(:data:`HEURISTIC_OWNER_MODULES`), loading all of them so the registry does
not depend on what happened to be imported (:func:`name_heuristic_registry`),
and the well-formedness rules a reviewer relies on
(:func:`registry_problems`). The H4 harness
(``tests/test_family_f4_heuristics.py``) is the oracle: it maps every
spelling site its AST scan finds in ``compare/``/``policy/``/``diff_*`` to a
registered heuristic or to a stated non-heuristic exemption, and fails on a
site that is neither.

Owner modules are imported by name: several are flat-root legacy modules
(``internal_leak``) that ``policy/`` may not import statically.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from types import ModuleType

from ..model.name_heuristics import (
    NameHeuristic,
    SeverityRaisingNameHeuristic,
    registered_name_heuristics,
)

__all__ = [
    "HEURISTIC_OWNER_MODULES",
    "name_heuristic_registry",
    "registry_problems",
    "severity_raising_heuristics",
]

#: Every module that registers a name heuristic. A registration from a module
#: not listed here is a :func:`registry_problems` finding, so a new heuristic
#: cannot hide outside the catalogue.
HEURISTIC_OWNER_MODULES: tuple[str, ...] = (
    "abicheck.compare.enum_sentinel",
    "abicheck.compare.naming_conventions",
    "abicheck.diff_filtering",
    "abicheck.diff_namespaces",
    "abicheck.diff_serialization",
    "abicheck.diff_symbols_anon_fields",
    "abicheck.diff_types_surface",
    "abicheck.internal_leak",
    "abicheck.policy.public_surface_closure",
)

Registered = NameHeuristic | SeverityRaisingNameHeuristic


def name_heuristic_registry() -> Mapping[str, Registered]:
    """Every registered name heuristic, after importing every owner."""
    for module in HEURISTIC_OWNER_MODULES:
        importlib.import_module(module)
    return registered_name_heuristics()


def severity_raising_heuristics() -> Mapping[str, SeverityRaisingNameHeuristic]:
    """The heuristics whose finding can raise severity (each names a fact)."""
    return {
        k: h
        for k, h in name_heuristic_registry().items()
        if isinstance(h, SeverityRaisingNameHeuristic)
    }


def _vocabulary_target(h: Registered, ref: str) -> tuple[str, str]:
    module, _, name = ref.rpartition(":")
    return (module or h.owner), name


def _is_string_collection(value: object) -> bool:
    if not isinstance(value, tuple | frozenset | set | list) or not value:
        return False
    return all(
        isinstance(v, str)
        or (isinstance(v, tuple) and all(isinstance(x, str) for x in v))
        for v in value
    )


def registry_problems(registry: Mapping[str, Registered] | None = None) -> list[str]:
    """Why the registry is not well formed (empty when it is)."""
    reg = name_heuristic_registry() if registry is None else registry
    problems: list[str] = []
    owners = set(HEURISTIC_OWNER_MODULES)
    seen_owners: set[str] = set()
    for hid, h in sorted(reg.items()):
        if h.id != hid:
            problems.append(f"{hid}: registered under a different id {h.id!r}")
        if h.owner not in owners:
            problems.append(f"{hid}: owner {h.owner} is not in HEURISTIC_OWNER_MODULES")
        seen_owners.add(h.owner)
        for ref in h.vocabularies:
            module, name = _vocabulary_target(h, ref)
            mod: ModuleType = importlib.import_module(module)
            if not _is_string_collection(getattr(mod, name, None)):
                problems.append(
                    f"{hid}: vocabulary {module}:{name} is not a string collection"
                )
        if not all(isinstance(p, re.Pattern) for p in h.patterns):
            problems.append(f"{hid}: a pattern is not compiled")
        if isinstance(h, SeverityRaisingNameHeuristic):
            if not callable(h.fact.check):
                problems.append(f"{hid}: severity-raising fact is not callable")
        elif h.effect_name not in {"lower_confidence", "route_to_review"}:
            problems.append(
                f"{hid}: effect {h.effect_name!r} is neither lowering nor review"
            )
    for owner in sorted(owners - seen_owners):
        problems.append(
            f"{owner}: listed in HEURISTIC_OWNER_MODULES but registers nothing"
        )
    return problems

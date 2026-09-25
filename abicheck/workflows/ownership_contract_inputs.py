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

"""The contract's ownership inputs, resolved with ADR-049 D7 provenance.

ADR-075 D7 (evidence-entity-model invariant I5, "the contract is modelled
once"): the inputs that decide what the target promises -- its header roots,
named dependency roots, and the private narrowing -- are explicit, recorded
fields of the resolved ``CompatibilityEvaluationConfig`` (``surface.
ownership``), each with the layer that stated it, instead of being implicit
in whichever paths a run happened to pass.

Target roots are a *union*, not a precedence contest: ``-H`` directories and
``scope.public_header_dirs`` both root the target, so each keeps its own
field and provenance (``surface.ownership.header_dirs`` from the CLI/API,
``surface.ownership.public_header_dirs`` from the project). The remaining
keys are ordinary D7 fields: a typed-API request's rules outrank the
project's ``.abicheck.yml``, and nothing states them by default.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..compatibility_evaluation_config import SelectedByEntry, ValueProvenance
from ..contract_relevance_types import SelectorLayer
from ..model.ownership_rules import DependencyRoots, OwnershipRules

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "OWNERSHIP_FIELDS",
    "header_dir_spellings",
    "project_ownership_inputs",
    "resolve_ownership_inputs",
]

HEADER_DIRS_FIELD = "surface.ownership.header_dirs"
PUBLIC_HEADER_DIRS_FIELD = "surface.ownership.public_header_dirs"
DEPENDENCIES_FIELD = "surface.ownership.dependencies"
PRIVATE_HEADERS_FIELD = "surface.ownership.private_headers"
PRIVATE_NAMESPACES_FIELD = "surface.ownership.private_namespaces"
DEPENDENCY_EVIDENCE_FIELD = "surface.ownership.dependency_evidence"
#: Every provenance key this module writes.
OWNERSHIP_FIELDS = (
    HEADER_DIRS_FIELD,
    PUBLIC_HEADER_DIRS_FIELD,
    DEPENDENCIES_FIELD,
    PRIVATE_HEADERS_FIELD,
    PRIVATE_NAMESPACES_FIELD,
    DEPENDENCY_EVIDENCE_FIELD,
)


def _candidate(
    field_candidate: Any,
    layer: SelectorLayer,
    value: Hashable,
    *,
    option: str,
    path: str | None = None,
    sha256: str | None = None,
) -> Any:
    return field_candidate(
        provenance=ValueProvenance(
            layer=layer,
            path=path,
            sha256=sha256,
            field_location=option,
            selected_by=(
                SelectedByEntry(layer=layer, option=option, path=path, sha256=sha256),
            ),
        ),
        value=value,
    )


def _default(field_candidate: Any, value: Hashable) -> Any:
    return field_candidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT), value=value
    )


def _deps(rules: OwnershipRules) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        sorted((d.name, tuple(sorted(set(d.header_roots)))) for d in rules.dependencies)
    )


def resolve_ownership_inputs(
    explicit: Any,
    project: Any,
    layer: SelectorLayer,
    *,
    resolve_field: Callable[..., tuple[Hashable, ValueProvenance]],
    field_candidate: Callable[..., Any],
) -> tuple[OwnershipRules | None, dict[str, ValueProvenance]]:
    """``surface.ownership`` and its per-field provenance.

    *explicit* is the invocation's ``ExplicitCompatibilityInputs``
    (``header_dirs``; ``ownership`` for a typed request), *project* the
    ``ProjectCompatibilityInputs`` or ``None``; *layer* is the explicit
    tier (``explicit_cli`` or ``api_request``). Read by attribute so this
    module stays independent of the resolver's input classes.

    *resolve_field*/*field_candidate* are ``compatibility_evaluation_
    resolver``'s own, passed in by the one caller that already imports them:
    that module is not yet classified in ``architecture/modules.yaml``, so a
    ``workflows`` module may not import it, and restating D7 precedence here
    would be a second copy of it.
    """
    prov: dict[str, ValueProvenance] = {}
    api_rules: OwnershipRules | None = getattr(explicit, "ownership", None)
    project_rules: OwnershipRules | None = getattr(project, "ownership", None)
    project_path = getattr(project, "path", None)
    project_sha = getattr(project, "sha256", None)

    def project_candidate(value: Hashable, key: str) -> list[Any]:
        return [
            _candidate(
                field_candidate,
                SelectorLayer.PROJECT_CONFIG,
                value,
                option=f"scope.{key}",
                path=project_path,
                sha256=project_sha,
            )
        ]

    header_dirs = tuple(sorted(set(getattr(explicit, "header_dirs", ()) or ())))
    resolved_header_dirs, prov[HEADER_DIRS_FIELD] = resolve_field(
        HEADER_DIRS_FIELD,
        [_candidate(field_candidate, layer, header_dirs, option="-H")]
        if header_dirs
        else [],
        default=_default(field_candidate, ()),
    )
    config_dirs = (
        tuple(sorted(set(project_rules.target_roots))) if project_rules else ()
    )
    resolved_config_dirs, prov[PUBLIC_HEADER_DIRS_FIELD] = resolve_field(
        PUBLIC_HEADER_DIRS_FIELD,
        project_candidate(config_dirs, "public_header_dirs") if config_dirs else [],
        default=_default(field_candidate, ()),
    )

    def keyed(field_name: str, key: str, value_of: Any, empty: Hashable) -> Hashable:
        candidates: list[Any] = []
        if api_rules is not None and value_of(api_rules) != empty:
            candidates.append(
                _candidate(
                    field_candidate,
                    layer,
                    value_of(api_rules),
                    option=f"InputSpec.ownership.{key}",
                )
            )
        if project_rules is not None and value_of(project_rules) != empty:
            candidates += project_candidate(value_of(project_rules), key)
        value, prov[field_name] = resolve_field(
            field_name,
            candidates,
            default=_default(field_candidate, empty),
            # Two layers stating different rules is ordinary precedence, not
            # a legacy-alias disagreement.
            require_legacy_alias_agreement=False,
        )
        return value

    deps = keyed(DEPENDENCIES_FIELD, "dependencies", _deps, ())
    private_headers = keyed(
        PRIVATE_HEADERS_FIELD,
        "private_headers",
        lambda r: tuple(sorted(set(r.private_headers))),
        (),
    )
    private_namespaces = keyed(
        PRIVATE_NAMESPACES_FIELD,
        "private_namespaces",
        lambda r: tuple(sorted(set(r.private_namespaces))),
        (),
    )
    evidence = keyed(
        DEPENDENCY_EVIDENCE_FIELD,
        "dependency_evidence",
        lambda r: r.dependency_evidence,
        "full",
    )
    rules = OwnershipRules(
        target_roots=tuple(
            sorted(set(_strs(resolved_header_dirs)) | set(_strs(resolved_config_dirs)))
        ),
        dependencies=tuple(
            DependencyRoots(name, tuple(roots)) for name, roots in _pairs(deps)
        ),
        private_headers=_strs(private_headers),
        private_namespaces=_strs(private_namespaces),
        dependency_evidence=str(evidence),
    )
    stated = any(p.layer is not SelectorLayer.BUILT_IN_DEFAULT for p in prov.values())
    # Nothing stated anywhere: the field stays unset, so a run without
    # ownership inputs records the receipt it always recorded.
    return (rules if stated else None), prov


def _strs(value: object) -> tuple[str, ...]:
    return tuple(str(v) for v in value) if isinstance(value, tuple) else ()


def _pairs(value: object) -> Sequence[tuple[str, Sequence[str]]]:
    return (
        [p for p in value if isinstance(p, tuple)] if isinstance(value, tuple) else []
    )


def project_ownership_inputs(cfg: Any) -> OwnershipRules | None:
    """The project config's ownership rules with ``scope.public_header_dirs``
    as the target roots, as ``ProjectCompatibilityInputs.ownership`` records
    them (spelled as the config spells them)."""
    rules: OwnershipRules | None = getattr(cfg, "ownership", None)
    if rules is None:
        return None
    return replace(
        rules,
        target_roots=tuple(
            str(d) for d in getattr(cfg, "public_header_dirs", ()) or ()
        ),
    )


def header_dir_spellings(headers: Sequence[object]) -> tuple[str, ...]:
    """The ``-H`` *directories* among *headers*, resolved -- the one spelling
    the CLI and the typed API both record, so equivalent invocations resolve
    equal (``cross_front_end_differences``). A ``-H`` file is not a root."""
    from pathlib import Path

    return tuple(
        sorted({str(Path(str(h)).resolve()) for h in headers if Path(str(h)).is_dir()})
    )

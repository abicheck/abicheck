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

"""ADR-066 D4/S2: the ``versioning.policy`` D7 field wiring.

A sibling of ``compatibility_evaluation_wiring.py`` rather than an addition
to it: that module carries a ``no_growth`` architecture-debt baseline
(`architecture/debt.yaml`), and per this repo's own convention ("the way to
shrink an entry is to move responsibility out to a properly-owned module,
never to trim the file to fit" — root `AGENTS.md`), new wiring logic gets
its own leaf module instead of growing the capped one. Mirrors
``resolve_internal_namespaces``/``internal_namespaces_candidate`` exactly:
``policy_file.py``'s ``versioning``/``versioning_stated`` (populated only
when a real ``--policy-file`` YAML sets the ``versioning:`` key) is the only
real front end this resolves against today.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .compatibility_evaluation_config import (
    SelectedByEntry,
    ValueProvenance,
)
from .compatibility_evaluation_resolver import FieldCandidate, resolve_field
from .contract_relevance_types import SelectorLayer
from .policy.versioning_policy import (
    VersioningPolicy,
    built_in_default_versioning_policy,
)

if TYPE_CHECKING:
    from .policy_file import PolicyFile

VERSIONING_POLICY_FIELD = "versioning.policy"

#: What ``versioning.policy`` resolves to when no ``--policy-file`` states a
#: ``versioning:`` block -- equal to
#: :func:`~abicheck.policy.versioning_policy.built_in_default_versioning_policy`,
#: the same "accepting this namespace does not, by itself, change today's
#: real default behavior" principle every other D7 wiring follows.
_BUILT_IN_DEFAULT_VERSIONING_POLICY = built_in_default_versioning_policy()


def versioning_policy_candidate(
    *,
    policy_file: PolicyFile | None,
    layer: SelectorLayer = SelectorLayer.EXPLICIT_CLI,
    sha256: str | None = None,
    option: str = "--policy",
) -> FieldCandidate | None:
    """Build the ``versioning.policy`` candidate a real ``--policy-file``
    contributes, or ``None`` when it contributes nothing.

    An explicitly-stated ``versioning:`` block that happens to spell out only
    default values still contributes a real candidate (``versioning_stated``
    is ``True``) -- the same "a document that carried the key said
    something" rule ``internal_namespaces_candidate`` already applies.
    """
    if policy_file is None or not getattr(policy_file, "versioning_stated", False):
        return None
    source_path = str(policy_file.source_path) if policy_file.source_path else None
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=layer,
            source_kind="policy_file",
            sha256=sha256,
            path=source_path,
            selected_by=(
                SelectedByEntry(layer=layer, option=option, path=source_path),
            ),
        ),
        value=policy_file.versioning,
    )


def resolve_versioning_policy(
    *, policy_file: PolicyFile | None
) -> tuple[VersioningPolicy, ValueProvenance]:
    """Resolve ``versioning.policy`` (ADR-066 D4) from a real ``--policy-file``.

    D4's "a run recipe may supply them" is not yet backed by a concrete
    run-recipe input mechanism, so only the ``EXPLICIT_CLI``-shaped
    ``--policy-file`` path (:func:`versioning_policy_candidate`) is wired --
    adding a second front end is a matter of building its own candidate and
    passing it alongside this one, not changing this function.
    """
    default = FieldCandidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
        value=_BUILT_IN_DEFAULT_VERSIONING_POLICY,
    )
    candidate = versioning_policy_candidate(policy_file=policy_file)
    candidates: list[FieldCandidate] = [] if candidate is None else [candidate]
    value, provenance = resolve_field(
        VERSIONING_POLICY_FIELD, candidates, default=default
    )
    return cast(VersioningPolicy, value), provenance

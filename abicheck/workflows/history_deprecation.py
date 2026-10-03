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

"""ADR-066 S2: deprecation-window compliance over a longitudinal history.

A pure, read-only projection over the lifecycle events
:func:`abicheck.workflows.history.build_longitudinal_history` derives: each
``removed`` event is checked against the stated versioning policy's
``deprecation_window``. Split out of ``history.py`` so the history builder
and the policy question it answers each have their own module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..policy.versioning_policy import VersioningPolicy


class _Event(Protocol):
    """The ``LifecycleEvent`` fields the evaluation reads."""

    @property
    def event(self) -> str: ...
    @property
    def entity_key(self) -> str: ...
    @property
    def entity_kind(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    @property
    def index(self) -> int: ...
    @property
    def version(self) -> str: ...


class _History(Protocol):
    """The ``LongitudinalHistoryResult`` fields the evaluation reads (a
    protocol, so this module need not import ``history``, which imports it)."""

    @property
    def events(self) -> Sequence[_Event]: ...
    @property
    def deprecation_resets(self) -> Sequence[tuple[str, int]]: ...


__all__ = ["DeprecationComplianceFinding", "evaluate_deprecation_compliance"]


@dataclass(frozen=True)
class DeprecationComplianceFinding:
    """One ``removed`` lifecycle event's deprecation-window conformance.

    ``status`` is one of:

    * ``"conforming"`` -- a ``deprecated`` event was observed at least
      ``deprecation_window.min_releases`` release-steps before the removal
      (or no window is required at all).
    * ``"non_conforming"`` -- a ``deprecated`` event was observed, but too
      close to the removal to satisfy the declared window.
    * ``"unknown"`` -- no ``deprecated`` event was observed for this entity
      at all, yet the policy requires one. Per ADR-066 D2's terminology,
      this is *not* asserted as a violation: an unobserved deprecation is
      ``unknown``, not a proven absence of one (the entity could have been
      deprecated before the history's first supplied snapshot).
    """

    entity_kind: str
    entity_key: str
    display_name: str
    removed_at_version: str
    removed_at_index: int
    deprecated_at_version: str | None
    deprecated_at_index: int | None
    observed_releases: int | None
    status: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_kind": self.entity_kind,
            "entity_key": self.entity_key,
            "display_name": self.display_name,
            "removed_at_version": self.removed_at_version,
            "removed_at_index": self.removed_at_index,
            "deprecated_at_version": self.deprecated_at_version,
            "deprecated_at_index": self.deprecated_at_index,
            "observed_releases": self.observed_releases,
            "status": self.status,
            "detail": self.detail,
        }


#: Lifecycle events that discard a previously-tracked deprecation record for
#: the same entity key -- a fresh presence cycle (introduced/reintroduced/
#: first_observed) means any earlier deprecation belongs to a *prior* cycle
#: and must not be attributed to a later removal.
_RESTART_EVENTS = frozenset({"introduced", "reintroduced", "first_observed"})


def evaluate_deprecation_compliance(
    history: _History, policy: VersioningPolicy
) -> tuple[DeprecationComplianceFinding, ...]:
    """D4's ``deprecation_window`` evaluated over *history*'s lifecycle events.

    Walks ``history.events`` in their already-chronological order (S1's
    :func:`~abicheck.workflows.history.build_longitudinal_history` appends
    strictly in release-chain order), tracking the most recent ``deprecated``
    event per entity key and closing a finding on each ``removed`` event.
    ``history.deprecation_resets`` -- a plain -> deprecated -> plain
    transition, which carries no ``LifecycleEvent`` of its own (see
    ``_events_from_pair``'s comment) -- is also consulted, so a removal that
    follows such a reset with no subsequent re-``deprecated`` event is not
    attributed stale deprecation evidence from before the reset
    (CodeRabbit review).

    This is a pure, read-only projection over already-computed lifecycle
    facts -- it adds a new finding list, never mutates ``history.events``,
    and is never consulted by :func:`abicheck.checker.compare`'s own verdict
    (ADR-066's "orthogonal axis" requirement).
    """
    min_releases = policy.deprecation_window.min_releases
    deprecated_at: dict[str, tuple[int, str]] = {}
    findings: list[DeprecationComplianceFinding] = []

    resets_by_key: dict[str, list[int]] = {}
    for key, index in history.deprecation_resets:
        resets_by_key.setdefault(key, []).append(index)

    for ev in history.events:
        if ev.event == "deprecated":
            deprecated_at[ev.entity_key] = (ev.index, ev.version)
        elif ev.event in _RESTART_EVENTS:
            deprecated_at.pop(ev.entity_key, None)
        elif ev.event == "removed":
            dep = deprecated_at.pop(ev.entity_key, None)
            if dep is not None and any(
                dep[0] < reset_index <= ev.index
                for reset_index in resets_by_key.get(ev.entity_key, ())
            ):
                # A reset strictly between the tracked deprecation and this
                # removal, with no later "deprecated" event overwriting
                # `deprecated_at` in between (dict assignment above already
                # happens in chronological order, so a later re-deprecation
                # would have replaced `dep` before we get here) -- the
                # deprecation in force at removal time is unobserved, not
                # the one recorded before the reset.
                dep = None
            if dep is None:
                if min_releases <= 0:
                    status, observed = "conforming", None
                    detail = (
                        "removed with no observed deprecation event and no "
                        "deprecation window is required (deprecation_window."
                        "min_releases=0)."
                    )
                else:
                    status, observed = "unknown", None
                    detail = (
                        "removed with no deprecation event observed in this "
                        "history, but the policy requires at least "
                        f"{min_releases} release(s) of deprecation -- the "
                        "entity may have been deprecated before this "
                        "history's earliest supplied snapshot ("
                        "an unobserved deprecation is 'unknown', not a "
                        "proven violation)."
                    )
                dep_index: int | None = None
                dep_version: str | None = None
            else:
                dep_index, dep_version = dep
                observed = ev.index - dep_index
                if observed >= min_releases:
                    status = "conforming"
                    detail = (
                        f"deprecated at {dep_version} (release #{dep_index}), "
                        f"removed at {ev.version} (release #{ev.index}): "
                        f"{observed} release(s) of deprecation observed, "
                        f"meeting the required {min_releases}."
                    )
                else:
                    status = "non_conforming"
                    detail = (
                        f"deprecated at {dep_version} (release #{dep_index}), "
                        f"removed at {ev.version} (release #{ev.index}): only "
                        f"{observed} release(s) of deprecation observed, "
                        f"short of the required {min_releases}."
                    )
            findings.append(
                DeprecationComplianceFinding(
                    entity_kind=ev.entity_kind,
                    entity_key=ev.entity_key,
                    display_name=ev.display_name,
                    removed_at_version=ev.version,
                    removed_at_index=ev.index,
                    deprecated_at_version=dep_version,
                    deprecated_at_index=dep_index,
                    observed_releases=observed,
                    status=status,
                    detail=detail,
                )
            )

    return tuple(findings)

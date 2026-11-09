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

"""The path-equivalent ancestor rule (``base-equivalent-paths``).

Plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md`` (§ Optional:
path-equivalent ancestor). A default-branch workflow with a ``paths:`` filter
snapshots only the commits that touched those paths, so a PR's exact base
often has no snapshot (``base_not_snapshotted``). When the project declares
which paths feed its ABI, a first-parent ancestor of the base whose tree is
identical to the base's under those paths has, by the project's own claim,
the same ABI -- and its snapshot may stand in for the base's.

The rule, all of it opt-in and recorded:

- only when the exact base has **no** snapshot (:data:`WALKABLE_OUTCOMES`):
  a pending run is waited for, an untrusted or failed lookup is reported,
  never walked past;
- along the base's **first-parent** history only, nearest first, at most
  :data:`MAX_ANCESTORS` commits;
- the walk stops at the first ancestor whose tree differs from the base's
  under the declared paths (``git diff --name-only <ancestor> <base> --
  <paths>``). A snapshot further back is never used, even where a later
  revert makes the trees equal again: the nearest change is where the
  claim ends;
- the first candidate with a snapshot that passes the same checks as an
  exact one (artifact provenance, or the cache entry's recorded origin for
  the *ancestor's* SHA) is used;
- otherwise the base's own outcome stands, with the walk's named
  :class:`WalkResult` saying why no ancestor qualified -- never a silent
  fall back to a non-equivalent snapshot.

Every function here is pure; ``actions/fetch-main-snapshot/run.sh`` runs
``git`` and the lookups and hands the facts over.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .main_snapshot import DeltaOutcome

__all__ = [
    "AncestorFact",
    "AncestorPlan",
    "EquivalentPathsError",
    "MAX_ANCESTORS",
    "WALKABLE_OUTCOMES",
    "WalkResult",
    "equivalent_pathspecs",
    "plan_ancestor_walk",
    "walk_message",
]

#: How many first-parent ancestors of the base are considered, nearest first.
#: Fixed, not an input: a longer walk only reaches commits whose snapshots
#: have usually expired, and costs two API calls per candidate.
MAX_ANCESTORS = 50

#: The exact base's outcomes the walk may answer: each one means "no usable
#: snapshot exists for this commit". ``base_pending`` is not one (the exact
#: snapshot is on its way; wait for it), nor ``untrusted_source`` (something
#: claims to be the base's snapshot -- that is reported, not stepped around),
#: nor ``lookup_failed`` (nothing is known), nor the outcomes that selected
#: no base commit.
WALKABLE_OUTCOMES = frozenset(
    {
        DeltaOutcome.BASE_NOT_SNAPSHOTTED,
        DeltaOutcome.SNAPSHOT_MISSING,
        DeltaOutcome.BASE_EXPIRED,
        DeltaOutcome.BASE_FAILED,
    }
)


class WalkResult(enum.StrEnum):
    """How the ancestor walk ended (the ``ancestor-walk`` output)."""

    #: An equivalent ancestor's snapshot is used; ``snapshot-sha`` names it.
    FOUND = "found"
    #: A declared path differs between the base and the nearest ancestor not
    #: yet ruled out; no candidate before it had a snapshot.
    PATHS_CHANGED = "paths_changed"
    #: :data:`MAX_ANCESTORS` equivalent ancestors, none with a snapshot.
    LIMIT_REACHED = "limit_reached"
    #: The base's first-parent history ends (a root commit) before a snapshot.
    HISTORY_ENDED = "history_ended"
    #: The history beyond the last candidate is not in this clone and could
    #: not be fetched (a shallow checkout whose deepening failed).
    HISTORY_UNAVAILABLE = "history_unavailable"
    #: A candidate's lookup failed (API error): nothing about it is known, so
    #: the walk does not step past it.
    LOOKUP_FAILED = "lookup_failed"


class EquivalentPathsError(ValueError):
    """The ``base-equivalent-paths`` value is not a usable declaration."""


_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")


def equivalent_pathspecs(text: str) -> tuple[str, ...]:
    """The git pathspecs for a ``base-equivalent-paths`` value.

    One glob per line (``#`` starts a comment line), relative to the
    repository root, matched with git's ``glob`` magic (``*`` stays inside one
    directory, ``**`` crosses them): ``include/**``, ``src/**``,
    ``CMakeLists.txt``. An empty value is an empty tuple (the rule is off).

    Refused: a pathspec magic of the caller's own (a leading ``:``), an
    absolute path, a ``..`` component, a negation (``!``) and control
    characters. A declaration that could exclude paths or reach outside the
    repository is not a claim about what feeds the ABI.
    """
    specs: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        problem = ""
        if _FORBIDDEN.search(entry):
            problem = "contains a control character"
        elif entry.startswith(":"):
            problem = "starts with ':' (pathspec magic is applied by abicheck)"
        elif entry.startswith("!"):
            problem = "is a negation; list the paths that feed the ABI instead"
        elif entry.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", entry):
            problem = "is absolute; paths are relative to the repository root"
        elif ".." in re.split(r"[\\/]", entry):
            problem = "has a '..' component"
        if problem:
            raise EquivalentPathsError(f"line {number} ({entry!r}) {problem}")
        specs.append(f":(glob){entry}")
    return tuple(dict.fromkeys(specs))


@dataclass(frozen=True)
class AncestorFact:
    """One first-parent ancestor of the base, nearest first."""

    sha: str
    #: The declared paths that differ between this ancestor and the base
    #: (``git diff --name-only``); empty when the trees are equal under them.
    changed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class AncestorPlan:
    #: The ancestors whose snapshot may stand in for the base, nearest first.
    candidates: tuple[str, ...]
    #: Why the walk ends after the candidates, if none of them has a snapshot.
    exhausted: WalkResult
    #: The ancestor where a declared path changed (``paths_changed`` only).
    changed_at: str = ""
    changed_paths: tuple[str, ...] = ()


def plan_ancestor_walk(
    ancestors: Sequence[AncestorFact],
    *,
    limit: int = MAX_ANCESTORS,
    history_complete: bool,
) -> AncestorPlan:
    """Which ancestors may be tried, and how the walk ends without one.

    *ancestors* is the base's first-parent history without the base itself,
    nearest first, as far as this clone has it (at most *limit*; extra rows
    are ignored). *history_complete* says the list ends because the history
    does (a root commit), not because the clone is shallow.
    """
    candidates: list[str] = []
    for fact in list(ancestors)[:limit]:
        if fact.changed_paths:
            return AncestorPlan(
                tuple(candidates),
                WalkResult.PATHS_CHANGED,
                changed_at=fact.sha,
                changed_paths=tuple(fact.changed_paths),
            )
        candidates.append(fact.sha)
    if len(candidates) >= limit:
        return AncestorPlan(tuple(candidates), WalkResult.LIMIT_REACHED)
    if history_complete:
        return AncestorPlan(tuple(candidates), WalkResult.HISTORY_ENDED)
    return AncestorPlan(tuple(candidates), WalkResult.HISTORY_UNAVAILABLE)


def walk_message(
    result: WalkResult,
    *,
    base_sha: str,
    branch: str = "main",
    snapshot_sha: str = "",
    changed_at: str = "",
    changed_paths: Sequence[str] = (),
    tried: int = 0,
) -> str:
    """The log/summary sentence for how the walk ended.

    Appended to the base's own outcome line when no ancestor qualified, so
    the reader learns both why the base has no snapshot and why no
    equivalent one was used.
    """
    base7 = base_sha[:7] or "?"
    where = f"`{branch or 'main'}@{base7}`"
    if result is WalkResult.FOUND:
        return (
            f"Used the snapshot of `{branch or 'main'}@{snapshot_sha[:7] or '?'}`, "
            f"identical to {where} under base-equivalent-paths."
        )
    if result is WalkResult.PATHS_CHANGED:
        shown = ", ".join(f"`{p}`" for p in list(changed_paths)[:3])
        more = len(changed_paths) - 3
        if more > 0:
            shown += f" and {more} more"
        lead = (
            f"none of the {tried} equivalent ancestor(s) has a snapshot, and "
            if tried
            else ""
        )
        return (
            f"No path-equivalent ancestor: {lead}a declared path changed at "
            f"`{changed_at[:7] or '?'}` ({shown})."
        )
    if result is WalkResult.LIMIT_REACHED:
        return (
            f"No path-equivalent ancestor: none of the {tried} nearest first-parent "
            f"ancestors of {where} has a snapshot."
        )
    if result is WalkResult.HISTORY_ENDED:
        return (
            f"No path-equivalent ancestor: the history of {where} ends after "
            f"{tried} ancestor(s), none with a snapshot."
        )
    if result is WalkResult.HISTORY_UNAVAILABLE:
        return (
            f"No path-equivalent ancestor: the history of {where} is not in this "
            f"checkout beyond {tried} ancestor(s) and could not be fetched "
            "(use `fetch-depth: 0`)."
        )
    return (
        "No path-equivalent ancestor: a snapshot lookup failed during the walk "
        "(API error)."
    )

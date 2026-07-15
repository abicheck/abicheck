# Copyright 2026 Nikolay Petrov
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

"""Canonical fact-set identity, coverage rollup, and comparison rules (ADR-038 C.8).

Every producer of :class:`~abicheck.buildsource.source_abi.SourceAbiTu` records
(the reference ``clang.py`` extractor, the Clang facts plugin, a future
GCC/MSVC producer) collects the same complete canonical fact family for its
declared ``fact_set`` version — never a user-selectable subset (see
``source_abi.py``'s ``FACT_FAMILIES``/``coverage_state_for_family``). This
module is pure: it rolls per-TU ``fact_set``/``coverage`` up to a per-surface
summary and implements the comparison-compatibility rules a diff/policy layer
consults before trusting an old/new pair's L4 evidence:

1. Old and new authoritative baselines should use the same fact-set version.
2. Old and new baselines should normally use the same producer family
   (``compiler_family``).
3. Opaque body/template hashes are only comparable when producer recipes
   match — approximated here by requiring the same ``producer`` id, since a
   different producer's subtree-hash algorithm is not guaranteed byte-stable
   (ADR-038 C.7's own documented residual).
4. A missing mandatory fact family (``partial``/``failed`` coverage) must not
   be read as "unchanged" — surfaced via :func:`incomplete_families`.

Nothing here parses binaries or runs external tools; wiring these checks into
a verdict lives in ``source_diff.py`` / ``source_link.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_abi import FACT_FAMILIES, INCOMPLETE_COVERAGE_STATES, SourceAbiTu

#: Worst-first precedence for rolling up one family's coverage across TUs: the
#: least-trustworthy state observed anywhere wins, so one bad TU cannot be
#: averaged away by many clean ones (recommendation P0 #6: "a partial or
#: failed collection must be visible").
_STATE_RANK = {
    "failed": 0,
    "partial": 1,
    "unsupported": 2,
    "complete": 3,
    "empty-confirmed": 4,
}


@dataclass(frozen=True)
class FactSetIssue:
    """One comparison-compatibility finding (ADR-038 C.8 rule 1/2/3/4)."""

    severity: str  # "error" | "warning"
    rule: str
    message: str


def rollup_coverage(tus: list[SourceAbiTu]) -> dict[str, str]:
    """Worst-of-across-TUs coverage state per fact family.

    Families absent from every TU's ``coverage`` (an older/hand-edited
    producer) are omitted rather than guessed at.
    """
    out: dict[str, str] = {}
    for family in FACT_FAMILIES:
        seen: list[str] = [
            tu.coverage[family]
            for tu in tus
            if isinstance(tu.coverage, dict) and family in tu.coverage
        ]
        if not seen:
            continue
        out[family] = min(seen, key=lambda s: _STATE_RANK.get(s, -1))
    return out


def rollup_fact_set(tus: list[SourceAbiTu]) -> dict[str, Any]:
    """The common ``fact_set`` across *tus*, or ``{}`` if none/inconsistent.

    A mixed-producer pack (rare — normally one build uses one producer) has no
    single fact_set to report; callers should treat that itself as a
    comparison-compatibility concern rather than silently picking one.
    """
    sets = [dict(tu.fact_set) for tu in tus if tu.fact_set]
    if not sets:
        return {}
    first = sets[0]
    if all(s == first for s in sets[1:]):
        return first
    return {}


def incomplete_families(coverage: dict[str, str]) -> list[str]:
    """Mandatory families whose rolled-up coverage means "do not trust absence"."""
    return sorted(
        f for f, state in coverage.items() if state in INCOMPLETE_COVERAGE_STATES
    )


def check_fact_set_compatibility(
    old_fact_set: dict[str, Any], new_fact_set: dict[str, Any]
) -> list[FactSetIssue]:
    """Compare two rolled-up ``fact_set`` blocks (ADR-038 C.8 rules 1-3).

    Either side may be ``{}`` (a pre-C.8 producer, or a mixed-producer pack);
    that is reported as a warning, not an error — an unknown fact-set never
    forces the comparison to abort (ADR-028's "never abort a hand-edited
    pack" convention), it is only reported so a strict CI gate can act on it.
    """
    issues: list[FactSetIssue] = []
    if not old_fact_set or not new_fact_set:
        issues.append(
            FactSetIssue(
                "warning",
                "fact_set_unknown",
                "one or both sides carry no fact_set identity (pre-C.8 producer, "
                "or a mixed-producer pack); fact-set compatibility could not be "
                "verified for this comparison.",
            )
        )
        return issues

    old_version = old_fact_set.get("version")
    new_version = new_fact_set.get("version")
    if old_version != new_version:
        issues.append(
            FactSetIssue(
                "error",
                "fact_set_version_mismatch",
                f"old baseline used fact_set version {old_version!r}, new baseline "
                f"used {new_version!r} — the declared mandatory-family contract "
                "differs, so a missing family on one side may only mean it was "
                "not yet defined, not that it disappeared.",
            )
        )

    old_family = old_fact_set.get("compiler_family")
    new_family = new_fact_set.get("compiler_family")
    if old_family != new_family:
        issues.append(
            FactSetIssue(
                "warning",
                "compiler_family_mismatch",
                f"old baseline compiler_family={old_family!r}, new "
                f"baseline compiler_family={new_family!r} — structured facts may "
                "still be comparable, but treat this pair's evidence cautiously.",
            )
        )

    old_producer = old_fact_set.get("producer")
    new_producer = new_fact_set.get("producer")
    if old_producer != new_producer:
        issues.append(
            FactSetIssue(
                "warning",
                "producer_mismatch",
                f"old baseline producer={old_producer!r}, new baseline "
                f"producer={new_producer!r} — opaque body/template hashes "
                "(inline_body_changed, template_body_changed) are producer-specific "
                "and are not guaranteed comparable across different producers; "
                "compiler-neutral structured facts remain comparable.",
            )
        )

    return issues

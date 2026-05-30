# SPDX-License-Identifier: Apache-2.0
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

"""Release-recommendation helper: verdict + change set → semver bump + SONAME action.

A library maintainer's first practical question after running a comparison is
*"what version do I release, and do I need to bump the SONAME?"*. abicheck
already has every signal needed to answer that — the policy-aware verdict and
the per-change classification — but historically left the mapping to the user.

This module derives a :class:`ReleaseRecommendation` from a :class:`DiffResult`:

==================  ===========  =====================  ==========================
Verdict             Bump         SONAME                 Why
==================  ===========  =====================  ==========================
NO_CHANGE           none         no_bump_needed         nothing changed
BREAKING            major        bump_required/…        binary ABI break
API_BREAK           major        no_bump_needed         source break, binary OK
COMPATIBLE_WITH_RISK minor/patch no_bump_needed         deployment-floor risk
COMPATIBLE (adds)   minor        no_bump_needed         new public API surface
COMPATIBLE (quality) patch       no_bump_needed         bad-practice only
==================  ===========  =====================  ==========================

The mapping follows the conventional split between the *API* contract (governs
the package's semantic version) and the *ABI* contract (governs the ELF SONAME /
Mach-O compatibility-version). A source-only break (``API_BREAK``) is a MAJOR
semver event but leaves the binary loadable, so the SONAME need not change; a
binary break (``BREAKING``) requires both.

Cross-references:
    abicheck/checker_policy.py   — Verdict, ADDITION_KINDS
    abicheck/diff_versioning.py  — emits SONAME_BUMP_RECOMMENDED / SONAME_CHANGED
    tests/test_semver_recommendation.py
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .checker_policy import ADDITION_KINDS, ChangeKind, Verdict
from .checker_types import DiffResult


class SemverBump(str, Enum):
    """Recommended semantic-version increment for the next release."""

    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    NONE = "none"


class SonameAction(str, Enum):
    """Recommended action for the binary soname / compatibility-version."""

    #: Binary ABI break detected and the soname does not appear to have moved —
    #: the maintainer must bump it.
    BUMP_REQUIRED = "bump_required"
    #: Binary ABI break detected *and* the soname was already changed in this
    #: revision — nothing more to do (informational, the good path).
    BUMP_PERFORMED = "bump_performed"
    #: Binary ABI break detected and abicheck explicitly observed the soname was
    #: left unchanged (SONAME_BUMP_RECOMMENDED fired) — a deployment hazard.
    BUMP_MISSING = "bump_missing"
    #: Binary remained compatible — no soname change is required.
    NO_BUMP_NEEDED = "no_bump_needed"


@dataclass(frozen=True)
class ReleaseRecommendation:
    """A concrete, machine- and human-readable release recommendation."""

    bump: SemverBump
    soname: SonameAction
    rationale: str

    def to_dict(self) -> dict[str, str]:
        """Serialise for JSON reports (additive ``release_recommendation`` key)."""
        return {
            "version_bump": self.bump.value,
            "soname_action": self.soname.value,
            "rationale": self.rationale,
        }

    def headline(self) -> str:
        """One-line summary suitable for ``--stat`` output or a CI log."""
        if self.soname in (SonameAction.BUMP_REQUIRED, SonameAction.BUMP_MISSING):
            return f"Recommended release: {self.bump.value.upper()} + SONAME bump"
        return f"Recommended release: {self.bump.value.upper()}"


def _soname_action_for_break(kinds: set[ChangeKind]) -> tuple[SonameAction, str]:
    """Pick the soname action (and trailing rationale) for a BREAKING verdict."""
    if ChangeKind.SONAME_BUMP_RECOMMENDED in kinds:
        return (
            SonameAction.BUMP_MISSING,
            " The SONAME was not bumped despite the binary break — bump it "
            "(e.g. libfoo.so.1 → libfoo.so.2) so old binaries fail loudly instead "
            "of silently loading an incompatible library.",
        )
    if ChangeKind.SONAME_CHANGED in kinds:
        return (
            SonameAction.BUMP_PERFORMED,
            " The SONAME was already bumped in this revision — good.",
        )
    return (
        SonameAction.BUMP_REQUIRED,
        " Bump the SONAME (major) so old binaries do not silently load an "
        "incompatible library.",
    )


def recommend_release(result: DiffResult) -> ReleaseRecommendation:
    """Derive a :class:`ReleaseRecommendation` from a comparison result.

    The recommendation is driven by the *policy-aware* verdict already computed
    on ``result`` (so ``--policy sdk_vendor`` / ``plugin_abi`` and custom policy
    files are honoured), refined by which change kinds are present (additions vs
    quality-only) and by the soname signals.
    """
    verdict = result.verdict
    kinds = {c.kind for c in result.changes}
    has_additions = bool(kinds & ADDITION_KINDS)

    if verdict == Verdict.NO_CHANGE:
        return ReleaseRecommendation(
            SemverBump.NONE,
            SonameAction.NO_BUMP_NEEDED,
            "No ABI or API changes detected; no version bump required.",
        )

    if verdict == Verdict.BREAKING:
        soname, extra = _soname_action_for_break(kinds)
        return ReleaseRecommendation(
            SemverBump.MAJOR,
            soname,
            "Binary ABI break detected — release a new MAJOR version." + extra,
        )

    if verdict == Verdict.API_BREAK:
        return ReleaseRecommendation(
            SemverBump.MAJOR,
            SonameAction.NO_BUMP_NEEDED,
            "Source-level API break (recompilation required) with no binary-layout "
            "change — release a new MAJOR version. The SONAME need not change "
            "because already-linked binaries remain loadable.",
        )

    if verdict == Verdict.COMPATIBLE_WITH_RISK:
        bump = SemverBump.MINOR if has_additions else SemverBump.PATCH
        return ReleaseRecommendation(
            bump,
            SonameAction.NO_BUMP_NEEDED,
            f"Binary-compatible, but a deployment risk was detected — a "
            f"{bump.value.upper()} release is appropriate; review the risk "
            f"findings (e.g. a raised runtime/toolchain floor) before shipping.",
        )

    # Verdict.COMPATIBLE
    if has_additions:
        return ReleaseRecommendation(
            SemverBump.MINOR,
            SonameAction.NO_BUMP_NEEDED,
            "Backward-compatible additions to the public API — release a new "
            "MINOR version.",
        )
    return ReleaseRecommendation(
        SemverBump.PATCH,
        SonameAction.NO_BUMP_NEEDED,
        "Only quality / bad-practice findings with no API or ABI surface change — "
        "a PATCH release is sufficient.",
    )

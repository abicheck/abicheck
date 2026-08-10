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

"""Extracting and validating the claim envelope out of a final answer (G37 D3).

The envelope exists so the zero-tolerance dimensions never read prose. A
correct answer here routinely names two outcomes in one paragraph — "ABI-
compatible but source-breaking" *is* `API_BREAK`, not hedging — so a text
parser searching for a verdict word can both miss a real false green and reject
a correct answer.

Extraction is deliberately strict in one direction: **absent, doubled, or
malformed is a failure, never a benefit of the doubt.** Two envelopes leave the
grader choosing which claim the agent meant, and choosing is exactly what the
typed envelope exists to avoid.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: `compare`'s own ordinal vocabulary (`abicheck.checker.Verdict`), least to
#: most severe. The order is the grading instrument: dimension 6 asks whether a
#: claim sits *below* the truth, which needs a rank, not a set.
VERDICT_ORDER: tuple[str, ...] = (
    "NO_CHANGE",
    "COMPATIBLE",
    "COMPATIBLE_WITH_RISK",
    "API_BREAK",
    "BREAKING",
)

UNCERTAINTY_REASONS = frozenset(
    {
        "not_comparable",
        "evidence_too_shallow",
        "matrix_target_unrun",
        "contract_coverage_incomplete",
    }
)

_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def rank(verdict: str | None) -> int | None:
    """Where a verdict sits on the severity ordinal, or None for `null`."""
    if verdict is None:
        return None
    return VERDICT_ORDER.index(verdict)


def _candidate_blocks(text: str) -> list[Any]:
    """Every fenced block that parses as a JSON object carrying a verdict."""
    found: list[Any] = []
    for match in _FENCE.finditer(text):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "verdict" in parsed:
            found.append(parsed)
    return found


def _envelopes(text: str) -> tuple[list[Any], list[Any]]:
    """The verdict-bearing blocks, and the subset that are actually envelopes.

    An envelope carries `verdict` *and* `confident`; a `compare` report carries
    `verdict` alone. Distinguishing them matters because a good answer often
    quotes the report it rests on, and treating that quotation as a second
    claim would fail the strictest dimension for citing evidence — the opposite
    of what the ambiguity rule is for. The ambiguity rule still fires on two
    real envelopes, which is the case where the grader would have to choose
    which claim the agent meant.
    """
    candidates = _candidate_blocks(text)
    return candidates, [c for c in candidates if "confident" in c]


def validate(claim: dict) -> str | None:
    """Why this envelope is not a gradeable claim, or None if it is."""
    verdict = claim.get("verdict")
    if verdict is not None and verdict not in VERDICT_ORDER:
        return f"verdict {verdict!r} is outside the vocabulary"
    if "confident" not in claim or not isinstance(claim["confident"], bool):
        return "confident is missing or not a boolean"
    evidence = claim.get("evidence", [])
    if not isinstance(evidence, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in evidence
    ):
        return "evidence is not a list of non-negative call ids"
    if claim["confident"]:
        return None
    uncertainty = claim.get("uncertainty")
    if not isinstance(uncertainty, dict):
        return "confident is false but no uncertainty object was given"
    if uncertainty.get("reason") not in UNCERTAINTY_REASONS:
        return f"uncertainty.reason {uncertainty.get('reason')!r} is outside the vocabulary"
    unresolved = uncertainty.get("unresolved")
    if not isinstance(unresolved, str) or not unresolved.strip():
        return "uncertainty.unresolved does not name anything"
    return None


def extract(final_text: str) -> tuple[dict | None, str]:
    """The claim this answer makes, and why it is ungradeable when it is not.

    Returns `(claim, status)`. `status` is `"ok"` only when exactly one
    well-formed envelope was found; otherwise the claim is `None` and the
    status says which of the failure shapes applies, so the caller can report
    the reason rather than a bare false.
    """
    candidates, blocks = _envelopes(final_text)
    if not blocks:
        if candidates:
            return None, "invalid: a verdict was given outside a claim envelope"
        return None, "absent"
    if len(blocks) > 1:
        return None, "ambiguous"
    problem = validate(blocks[0])
    if problem is not None:
        return None, f"invalid: {problem}"
    return blocks[0], "ok"

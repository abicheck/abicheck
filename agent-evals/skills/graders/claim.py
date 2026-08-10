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
    """Where a verdict sits on the severity ordinal.

    None for `null` *and* for anything outside the vocabulary. A claim's
    verdict reaches here only after `validate`, but a **scenario's** does not:
    `dimension_6` ranks `scenario["expected"]["verdict"]` straight from the
    pack, so a drifted spelling in a `ground_truth.json` entry would raise and
    abort the whole batch instead of failing the one scenario carrying it. The
    severity comparisons that consume this read None as "no ordinal to
    compare", which is the right answer for a verdict nobody can place.
    """
    if verdict is None or verdict not in VERDICT_ORDER:
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


#: `native-release-compatibility`'s five per-cell states, as `claim.schema.json`
#: publishes them. Kept as a literal rather than read off the schema at import
#: time, with `tests/test_skill_eval_graders.py` pinning the two together — the
#: graders stay file-I/O-free at import, and drift fails loudly instead of
#: silently widening what a zero-tolerance rule accepts.
MATRIX_STATES = frozenset({"pass", "risk", "break", "not_comparable", "not_run"})


def _validate_matrix(matrix: object) -> str | None:
    """Why this matrix block is not gradeable, or None if it is.

    Checked against what the schema actually requires, not merely against what
    would crash a grader. A target carrying only `{"state": "not_run"}` is
    schema-invalid — `id` is required — yet it satisfied `_rule_unrun`, so the
    zero-tolerance matrix rule could pass on a claim that reports *some* target
    went unrun without ever saying which platform was skipped. "A cell is
    missing" is not the finding; "the Windows cell is missing" is.
    """
    if not isinstance(matrix, dict) or not isinstance(matrix.get("targets"), list):
        return "matrix does not carry a list of targets"
    targets = matrix["targets"]
    if not targets:
        return "matrix carries no targets"
    for target in targets:
        # An AttributeError here aborted the whole batch instead of failing the
        # one run that produced it, which is why shape is checked before use.
        if not isinstance(target, dict):
            return "a matrix target is not a record"
        if not isinstance(target.get("id"), str) or not target["id"].strip():
            return "a matrix target does not name which target it is"
        if target.get("state") not in MATRIX_STATES:
            return (
                f"matrix target state {target.get('state')!r} is outside the vocabulary"
            )
    return None


def validate(claim: dict) -> str | None:
    """Why this envelope is not a gradeable claim, or None if it is."""
    verdict = claim.get("verdict")
    if verdict is not None and verdict not in VERDICT_ORDER:
        return f"verdict {verdict!r} is outside the vocabulary"
    if "confident" not in claim or not isinstance(claim["confident"], bool):
        return "confident is missing or not a boolean"
    if "evidence" not in claim:
        # Required by the schema, and defaulting it to `[]` turned an omission
        # into a valid empty list: a `null`-verdict claim then stated no
        # evidence, dimension 6 ran no evidence check (nothing was claimed),
        # and a malformed envelope graded clean.
        return "evidence is missing"
    evidence = claim["evidence"]
    if not isinstance(evidence, list) or any(
        not isinstance(item, int) or isinstance(item, bool) or item < 0
        for item in evidence
    ):
        return "evidence is not a list of non-negative call ids"
    matrix = claim.get("matrix")
    if matrix is not None:
        problem = _validate_matrix(matrix)
        if problem is not None:
            return problem
    if verdict is None and claim["confident"]:
        # `null` means "no verdict exists for this pair", which is a statement
        # of uncertainty, not a confident finding — the rubric's own
        # `not_comparable` rule requires the reason be carried. Accepting a
        # confident null let `{"verdict": null, "evidence": [], "confident":
        # true}` skip dimension 6's evidence block entirely (nothing was
        # claimed) *and* read as `not_applicable` in dimension 2 (it was
        # confident), so a run that compared nothing graded clean against a
        # BREAKING scenario. Routing it here sends it to dimension 2, where the
        # refutation check for a falsely-claimed non-comparability lives.
        return "a null verdict is a statement of uncertainty, so confident is not true"
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

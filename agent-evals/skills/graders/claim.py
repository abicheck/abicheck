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

#: The customer-facing outcome vocabulary from `check-abi-compatibility`'s own
#: SKILL.md ("Step 10 — Report the decision..."), reused verbatim here rather
#: than re-typed so the two cannot drift silently.
DECISION_STATES = frozenset(
    {
        "VERIFIED_COMPATIBLE",
        "COMPATIBLE_WITH_DEPLOYMENT_RISK",
        "SOURCE_BREAK",
        "BINARY_BREAK",
        "NOT_VERIFIED",
    }
)

#: `decision` -> the raw `verdict`s it may legitimately rest on, per the
#: skill's own decision table. `NOT_VERIFIED` is deliberately not keyed by
#: `verdict` at all: it is reached through `confident: false` or a `null`
#: verdict, not through a particular raw ordinal (a `NOT_VERIFIED` answer can
#: sit on top of any raw verdict the evidence fell short of confirming).
_DECISION_VERDICTS: dict[str, frozenset[str | None]] = {
    "VERIFIED_COMPATIBLE": frozenset({"NO_CHANGE", "COMPATIBLE"}),
    "COMPATIBLE_WITH_DEPLOYMENT_RISK": frozenset({"COMPATIBLE_WITH_RISK"}),
    "SOURCE_BREAK": frozenset({"API_BREAK"}),
    "BINARY_BREAK": frozenset({"BREAKING"}),
}


def decision_inconsistency(claim: dict) -> str | None:
    """Why `claim["decision"]` disagrees with its own `verdict`/`confident`, if it does.

    Returns None when no `decision` was given (still optional — see the
    schema) or when it is a legitimate reading of the raw verdict. Called
    only after `validate()` has already accepted the envelope, so `verdict`
    and `confident` are known well-formed here.
    """
    decision = claim.get("decision")
    if decision is None:
        return None
    verdict = claim.get("verdict")
    confident = claim.get("confident")
    if decision == "NOT_VERIFIED":
        # The one state that is reached through uncertainty rather than a
        # specific raw ordinal — legitimate whenever the answer isn't a
        # confident, verdict-bearing claim.
        if confident and verdict is not None:
            return (
                "decision NOT_VERIFIED alongside a confident, non-null verdict "
                f"({verdict}) — a confident verdict is a claim, not an "
                "unverified one"
            )
        return None
    if not confident:
        return (
            f"decision {decision} was reported without confidence "
            "(confident: false) — an unconfident answer must be NOT_VERIFIED"
        )
    allowed = _DECISION_VERDICTS.get(decision)
    if allowed is not None and verdict not in allowed:
        return (
            f"decision {decision} does not match the raw verdict ({verdict!r}) "
            f"— {decision} only follows from {sorted(v for v in allowed if v)}"
        )
    return None


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _outside(value: object, vocab: frozenset[str]) -> bool:
    """`value not in vocab`, without crashing when `value` is unhashable.

    `VERDICT_ORDER`/an `expected["verdict"]` are checked against a *tuple*
    (`in` there is a linear `==` scan, safe for any value), but every
    closed-vocabulary check against a `frozenset` here — `MATRIX_STATES`,
    `UNCERTAINTY_REASONS`, `DECISION_STATES` — needs this: a malformed claim
    is exactly the input this module exists to reject, and one whose field
    is a list or dict (legal JSON, illegal here) makes `in` on a `frozenset`
    raise `TypeError: unhashable type` instead of returning the ordinary
    "outside the vocabulary" verdict, aborting the whole batch on one bad
    envelope rather than failing only that claim (Codex review, PR #819).
    """
    if not isinstance(value, str):
        return True
    return value not in vocab


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


#: The five per-cell matrix-qualification states, originally defined for the
#: now-unpublished `native-release-compatibility` skill (ADR-058's 2026-08-20
#: portfolio-reset amendment) and kept as reserved vocabulary for its eventual
#: second-skill successor, as `claim.schema.json` publishes them. Kept as a
#: literal rather than read off the schema at import
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
        if _outside(target.get("state"), MATRIX_STATES):
            return (
                f"matrix target state {target.get('state')!r} is outside the vocabulary"
            )
    return None


def validate(claim: dict) -> str | None:
    """Why this envelope is not a gradeable claim, or None if it is."""
    verdict = claim.get("verdict")
    if verdict is not None and verdict not in VERDICT_ORDER:
        return f"verdict {verdict!r} is outside the vocabulary"
    if "full_verdict" in claim:
        full_verdict = claim["full_verdict"]
        if full_verdict is not None and full_verdict not in VERDICT_ORDER:
            return f"full_verdict {full_verdict!r} is outside the vocabulary"
    if "decision" in claim and _outside(claim["decision"], DECISION_STATES):
        return f"decision {claim['decision']!r} is outside the vocabulary"
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
    if _outside(uncertainty.get("reason"), UNCERTAINTY_REASONS):
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

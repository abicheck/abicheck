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
"""Validation of a ``run-plan.json``'s top-level ``gate`` block.

Split out of :mod:`abicheck.buildsource.run_plan` (an ``architecture/
debt.yaml`` ``no_growth`` module): reading the gate policy back is its own
question, and it shares nothing with the cell resolution that file owns.
"""

from __future__ import annotations

from typing import Any

__all__ = ["parse_run_plan_gate"]


def parse_run_plan_gate(d: dict[str, Any]) -> tuple[str | None, str | None]:
    """Validate a ``run-plan.json`` top-level ``gate`` block.

    The key absent -> ``(None, None)``, same as everywhere else in this
    module. Present but malformed -- not an object, an unknown key, or
    a value outside :class:`~abicheck.workflows.aggregate.expected_targets.OnMissingRequired`/
    :class:`~abicheck.workflows.aggregate.expected_targets.OnUnexpectedTarget` -- is a loud
    :class:`~abicheck.workflows.aggregate.expected_targets.AggregateError`, not a silent
    coercion to "no gate" (Codex review, fresh evidence: an earlier revision
    treated any non-dict/malformed ``gate`` the same as an absent one, so a
    corrupted or hand-authored v2 plan's requested policy could be silently
    discarded and `aggregate` would fall back to the hard-coded defaults --
    potentially reversing the requested CI outcome instead of failing loud).
    Mirrors :func:`abicheck.workflows.aggregate.expected_targets._parse_manifest_gate`'s own
    key/value validation exactly, kept as a separate function here (not a
    shared call) since that function's own version check is shaped for the
    manifest's ``MAJOR.MINOR`` scheme, not this module's ``vN`` one -- the
    two callers already do their own, differently-shaped version checks.

    Takes the whole top-level mapping (not a pre-extracted ``gate`` value)
    so it can distinguish the key being absent from it being explicitly
    present with a JSON ``null`` -- a plain ``.get("gate")`` on the caller's
    side would conflate the two, and an explicit ``"gate": null`` (or a
    sub-key like ``"missing_required": null``) is rejected outright rather
    than silently treated the same as "not specified" (Codex review, fresh
    evidence -- the same conflation the sibling manifest-side fix closes).
    """
    if "gate" not in d:
        return None, None
    gate_raw = d["gate"]
    from ..workflows.aggregate import (
        AggregateError,
        OnMissingRequired,
        OnUnexpectedTarget,
    )

    if gate_raw is None:
        raise AggregateError("run-plan 'gate' must not be null")
    if not isinstance(gate_raw, dict):
        raise AggregateError("run-plan 'gate' must be an object")
    unknown = sorted(set(gate_raw) - {"missing_required", "unexpected_target"})
    if unknown:
        raise AggregateError(f"run-plan 'gate': unknown key(s) {unknown!r}")
    missing_required: str | None = None
    if "missing_required" in gate_raw:
        mr_raw = gate_raw["missing_required"]
        if mr_raw is None:
            raise AggregateError("run-plan 'gate.missing_required' must not be null")
        try:
            missing_required = OnMissingRequired(mr_raw).value
        except ValueError as exc:
            raise AggregateError(
                f"run-plan 'gate.missing_required' {mr_raw!r} must be one of "
                f"{[v.value for v in OnMissingRequired]}"
            ) from exc
    unexpected_target: str | None = None
    if "unexpected_target" in gate_raw:
        ut_raw = gate_raw["unexpected_target"]
        if ut_raw is None:
            raise AggregateError("run-plan 'gate.unexpected_target' must not be null")
        try:
            unexpected_target = OnUnexpectedTarget(ut_raw).value
        except ValueError as exc:
            raise AggregateError(
                f"run-plan 'gate.unexpected_target' {ut_raw!r} must be one "
                f"of {[v.value for v in OnUnexpectedTarget]}"
            ) from exc
    return missing_required, unexpected_target

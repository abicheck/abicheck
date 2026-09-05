# Copyright 2025 abicheck contributors
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
# SPDX-License-Identifier: Apache-2.0
"""``run_outcome.scope`` (ADR-065 D6) is optional on a schema-version 1.0
block only. From 1.1 on, the version that introduced the axis, every
reader -- the published JSON schema, ``RunOutcome.from_dict``, and the
aggregate's schema validator -- rejects a block that omits or corrupts it,
so a warn-accepted incomplete release can never be re-read as complete by
dropping one key (Codex review, eighteenth round)."""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.policy.outcome import (
    RUN_OUTCOME_SCHEMA_VERSION,
    RunOutcome,
    ScopeCompleteness,
    run_outcome_scope_required,
)
from abicheck.schemas import load_compare_report_schema
from abicheck.workflows.aggregate.gate import _is_schema_valid_run_outcome


def _block(version: str, scope: object = "<absent>") -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": version,
        "compatibility": "NO_CHANGE",
        "assurance": None,
        "gate": "none",
        "operational": "none",
        "lifecycle": "existing",
    }
    if scope != "<absent>":
        data["scope"] = scope
    return data


#: (version, scope value, expected parsed scope or None for "rejected").
CASES = [
    ("1.0", "<absent>", ScopeCompleteness.COMPLETE),
    ("1.0", "complete", ScopeCompleteness.COMPLETE),
    ("1.0", "incomplete", ScopeCompleteness.INCOMPLETE),
    ("1.0", "partial", ScopeCompleteness.COMPLETE),
    ("1.1", "<absent>", None),
    ("1.1", "complete", ScopeCompleteness.COMPLETE),
    ("1.1", "incomplete", ScopeCompleteness.INCOMPLETE),
    ("1.1", "partial", None),
    ("1.1", None, None),
    ("1.2", "<absent>", None),
    ("1.2", "incomplete", ScopeCompleteness.INCOMPLETE),
    ("2", "<absent>", None),
    ("1", "<absent>", ScopeCompleteness.COMPLETE),
    ("0.9", "<absent>", ScopeCompleteness.COMPLETE),
    ("1.00", "<absent>", ScopeCompleteness.COMPLETE),
    ("one.one", "<absent>", None),
    ("1.0.0", "<absent>", None),
    ("", "<absent>", None),
]
_IDS = [f"{v}-{s}" for v, s, _ in CASES]


def test_the_current_writer_version_requires_scope() -> None:
    assert run_outcome_scope_required(RUN_OUTCOME_SCHEMA_VERSION)
    assert not run_outcome_scope_required("1.0")
    # An absent stamp is a pre-axis writer; a present unparseable or
    # non-string one is required, exactly as the schema's pattern decides.
    assert not run_outcome_scope_required(None)
    assert run_outcome_scope_required(1.1)
    assert run_outcome_scope_required("one.one")
    assert run_outcome_scope_required("1.0.0")


@pytest.mark.parametrize(("version", "scope", "expected"), CASES, ids=_IDS)
def test_from_dict_requires_scope_from_1_1_on(
    version: str, scope: object, expected: ScopeCompleteness | None
) -> None:
    parsed = RunOutcome.from_dict(_block(version, scope))
    if expected is None:
        assert parsed is None
    else:
        assert parsed is not None and parsed.scope is expected


@pytest.mark.parametrize(("version", "scope", "expected"), CASES, ids=_IDS)
def test_aggregate_validator_agrees_with_from_dict(
    version: str, scope: object, expected: ScopeCompleteness | None
) -> None:
    valid = _is_schema_valid_run_outcome(_block(version, scope))
    # The validator is stricter than the reader on a *malformed* 1.0 value
    # (the reader backfills, the validator rejects a value outside the
    # enum); on presence/absence the two agree exactly.
    if scope == "partial" and not run_outcome_scope_required(version):
        assert valid is False
    else:
        assert valid is (expected is not None)


@pytest.mark.parametrize(("version", "scope", "expected"), CASES, ids=_IDS)
def test_published_schema_agrees(
    version: str, scope: object, expected: ScopeCompleteness | None
) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = load_compare_report_schema()
    validator = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/run_outcome", "$defs": schema["$defs"]}
    )
    errors = list(validator.iter_errors(_block(version, scope)))
    if scope == "partial" and not run_outcome_scope_required(version):
        assert errors
    else:
        assert bool(errors) is (expected is None), errors


def test_a_real_writer_block_round_trips_under_every_reader() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    from dataclasses import replace

    base = RunOutcome.from_dict(_block("1.1", "complete"))
    assert base is not None
    for completeness in ScopeCompleteness:
        block = replace(base, scope=completeness).to_dict()
        assert block["schema_version"] == RUN_OUTCOME_SCHEMA_VERSION
        assert block["scope"] == completeness.value
        assert _is_schema_valid_run_outcome(block)
        parsed = RunOutcome.from_dict(block)
        assert parsed is not None and parsed.scope is completeness
        schema = load_compare_report_schema()
        jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/run_outcome", "$defs": schema["$defs"]}
        ).validate(block)

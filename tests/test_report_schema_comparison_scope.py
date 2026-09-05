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
"""The published compare-report schema defines the ``comparison_scope``
block (schema 2.50, ADR-065 S2) in full -- every shape the producer
emits validates, every acquisition state the model knows is in the enum,
and a malformed block is rejected rather than admitted through the root's
``additionalProperties: true`` (Codex review, seventeenth round)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)
from abicheck.policy.scope_completeness import resolve_scope_decision
from abicheck.report.comparison_scope import comparison_scope_terms
from abicheck.schemas import load_compare_report_schema
from abicheck.workflows.release_scope import (
    ReleaseInventoryEvidence,
    build_release_scope_record,
    build_stored_baseline_scope_record,
)

jsonschema = pytest.importorskip("jsonschema")


def _scope_validator() -> Any:
    schema = load_compare_report_schema()
    assert "comparison_scope" in schema["properties"]
    assert (
        schema["properties"]["comparison_scope"]["$ref"] == "#/$defs/comparison_scope"
    )
    return jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/comparison_scope", "$defs": schema["$defs"]}
    )


def _section(record: ScopeAcquisitionRecord, policy: str) -> dict[str, Any]:
    section = comparison_scope_terms(resolve_scope_decision(record, policy)).section
    assert section is not None
    return json.loads(json.dumps(section))


def _evidence(*, old_proven: bool, new_proven: bool, single: bool = False):
    return ReleaseInventoryEvidence(
        old=SideInventory(
            InventoryCompleteness.PROVEN
            if old_proven
            else InventoryCompleteness.UNPROVEN,
            "t",
        ),
        new=SideInventory(
            InventoryCompleteness.PROVEN
            if new_proven
            else InventoryCompleteness.UNPROVEN,
            "t",
        ),
        new_single_artifact=single,
    )


def _records() -> list[tuple[str, ScopeAcquisitionRecord]]:
    """One record per producer shape: direct pair, all-expected with every
    operational verdict, D9 narrowing with a failed OLD member, a proven
    removal and addition, a stored-baseline record with degraded/unsupported/
    failed members, and an every-state synthetic record."""
    p = Path
    out: list[tuple[str, ScopeAcquisitionRecord]] = []
    out.append(
        (
            "direct_pair",
            build_release_scope_record(
                {"direct_pair": p("libx.so")},
                {"direct_pair": p("libx.so")},
                ["direct_pair"],
                [{"library": "libx.so", "verdict": "NO_CHANGE"}],
                _evidence(old_proven=False, new_proven=False),
            ),
        )
    )
    old = {k: p(k) for k in ("liba.so", "libb.so", "libc.so", "libd.so", "libe.so")}
    new = {k: p(k) for k in ("liba.so", "libb.so", "libc.so", "libd.so", "libf.so")}
    results = [
        {"library": "liba.so", "verdict": "BREAKING"},
        {"library": "libb.so", "verdict": "ERROR", "error": "boom"},
        {"library": "libc.so", "verdict": "unsupported", "reason": "newer schema"},
        {"library": "libd.so", "verdict": "not_comparable", "reason": "scope"},
    ]
    out.append(
        (
            "all_expected",
            build_release_scope_record(
                old,
                new,
                sorted(set(old) & set(new)),
                results,
                _evidence(old_proven=False, new_proven=False),
            ),
        )
    )
    out.append(
        (
            "proven",
            build_release_scope_record(
                old,
                new,
                sorted(set(old) & set(new)),
                results,
                _evidence(old_proven=True, new_proven=True),
                old_failed={"libz.so": "--dso-only could not read it"},
            ),
        )
    )
    out.append(
        (
            "current_artifact",
            build_release_scope_record(
                {"liba.so": p("liba.so"), "libb.so": p("libb.so")},
                {"liba.so": p("liba.so")},
                ["liba.so"],
                [{"library": "liba.so", "verdict": "NO_CHANGE"}],
                _evidence(old_proven=False, new_proven=False, single=True),
                old_failed={"libq.so": "--dso-only could not read it"},
            ),
        )
    )
    out.append(
        (
            "stored_baseline",
            build_stored_baseline_scope_record(
                ["liba.so", "libb.so", "libc.so", "libd.so", "libe.so"],
                ["liba.so", "libb.so", "libc.so", "libd.so", "libf.so"],
                compared=["liba.so"],
                degraded={"libb.so": "ELF-only"},
                old_provenance="stored",
                new_provenance="live",
                unsupported={"libc.so": "newer"},
                failed={"libd.so": "damaged"},
            ),
        )
    )
    members = tuple(
        MemberAcquisition(f"lib{i}.so", state, True, True, "why")
        for i, state in enumerate(AcquisitionState)
    )
    out.append(
        (
            "every_state",
            ScopeAcquisitionRecord(
                members,
                SideInventory(InventoryCompleteness.UNPROVEN, "t"),
                SideInventory(InventoryCompleteness.PROVEN, "t"),
                "all_expected",
            ),
        )
    )
    return out


class TestComparisonScopeSchema:
    @pytest.mark.parametrize("policy", ["warn", "block"])
    @pytest.mark.parametrize(
        "record", [r for _, r in _records()], ids=[n for n, _ in _records()]
    )
    def test_every_producer_shape_validates(
        self, record: ScopeAcquisitionRecord, policy: str
    ) -> None:
        _scope_validator().validate(_section(record, policy))

    def test_state_enum_matches_the_model(self) -> None:
        schema = load_compare_report_schema()["$defs"]["comparison_scope"]
        member_state = schema["properties"]["members"]["items"]["properties"]["state"]
        assert set(member_state["enum"]) == {s.value for s in AcquisitionState}
        assert set(schema["properties"]["counts"]["required"]) == {
            s.value for s in AcquisitionState
        }
        assert set(schema["properties"]["counts"]["properties"]) == {
            s.value for s in AcquisitionState
        }
        inventory = schema["properties"]["old_inventory"]["properties"]["completeness"]
        assert set(inventory["enum"]) == {c.value for c in InventoryCompleteness}

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: s.pop("members"),
            lambda s: s.__setitem__("extra", 1),
            lambda s: s.__setitem__("completeness", "partial"),
            lambda s: s.__setitem__("policy", "ignore"),
            lambda s: s.__setitem__("incomplete_scope_exit_contribution", 2),
            lambda s: s.__setitem__("no_comparison_completed_exit_contribution", -1),
            lambda s: s.__setitem__("selection", "everything"),
            lambda s: s["members"][0].__setitem__("state", "compared"),
            lambda s: s["members"][0].pop("reason"),
            lambda s: s["members"][0].__setitem__("old_present", "yes"),
            lambda s: s["counts"].pop("available"),
            lambda s: s["counts"].__setitem__("bogus", 0),
            lambda s: s["counts"].__setitem__("failed", -1),
            lambda s: s["old_inventory"].__setitem__("completeness", "declared"),
            lambda s: s["old_inventory"].pop("provenance"),
            lambda s: s["unchecked"].append(3),
            lambda s: s.__setitem__("proven_removed", "liba.so"),
        ],
        ids=[
            "missing-members",
            "extra-key",
            "bad-completeness",
            "bad-policy",
            "contribution-2",
            "contribution-negative",
            "bad-selection",
            "bad-state",
            "member-missing-reason",
            "member-bool-as-string",
            "counts-missing-state",
            "counts-extra-state",
            "counts-negative",
            "bad-inventory-completeness",
            "inventory-missing-provenance",
            "unchecked-non-string",
            "proven-removed-not-list",
        ],
    )
    def test_malformed_block_is_rejected(self, mutate) -> None:
        _, record = _records()[1]
        section = _section(record, "block")
        _scope_validator().validate(copy.deepcopy(section))
        mutate(section)
        with pytest.raises(jsonschema.ValidationError):
            _scope_validator().validate(section)

    def test_release_report_root_still_validates_with_the_block(self) -> None:
        """A root-level document carrying the block is checked through the
        top-level property, not admitted by the root's additionalProperties."""
        schema = load_compare_report_schema()
        _, record = _records()[1]
        doc = {
            "report_schema_version": schema["properties"]["report_schema_version"].get(
                "const", "2.50"
            ),
            "verdict": "BREAKING",
            "comparison_scope": _section(record, "warn"),
        }
        # Only the block's own validity is at stake here; strip the
        # conditional full-shape requirement by using a verdict-free root.
        del doc["verdict"]
        validator = jsonschema.Draft202012Validator(schema)
        errors = [
            e
            for e in validator.iter_errors(doc)
            if "comparison_scope" in list(e.absolute_path)
        ]
        assert errors == []
        doc["comparison_scope"]["members"][0]["state"] = "compared"
        errors = [
            e
            for e in validator.iter_errors(doc)
            if "comparison_scope" in list(e.absolute_path)
        ]
        assert errors

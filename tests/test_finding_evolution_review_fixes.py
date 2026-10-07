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

"""The published ``finding_evolution`` schema, and the ``--view show=``
action filter reading the per-finding operation rather than the kind's."""

from __future__ import annotations

import json
import string
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change import Change
from abicheck.model.evidence_status import CrossSourceEvolution
from abicheck.policy.finding_evolution import apply_finding_evolution
from abicheck.report.change_operation import (
    ACTION_TOKEN_OPERATIONS,
    operation_for_change,
)
from abicheck.reporter import to_json
from abicheck.reporter_markdown import ShowOnlyFilter
from abicheck.schemas import load_compare_report_schema
from tests.schema_validation import validate_instance

jsonschema = pytest.importorskip("jsonschema")

_REPO = Path(__file__).resolve().parents[1]
_ALPHABET = list(string.ascii_lowercase[:6])


def _snap(symbols: set[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1",
        functions=[
            Function(
                name=s,
                mangled=f"_Z1{s}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for s in sorted(symbols)
        ],
    )


def _real(old: set[str], new: set[str]) -> DiffResult:
    return compare(_snap(old), _snap(new))


def _result(symbols: set[str]) -> DiffResult:
    return DiffResult(
        old_version="o",
        new_version="n",
        library="lib",
        changes=[
            Change(kind=ChangeKind.FUNC_REMOVED, symbol=s, description=f"rm {s}")
            for s in sorted(symbols)
        ],
    )


class TestFindingEvolutionSchema:
    def test_both_published_schemas_are_identical(self) -> None:
        a = json.loads(
            (_REPO / "abicheck/schemas/compare_report.schema.json").read_text()
        )
        b = json.loads(
            (_REPO / "docs/reference/schemas/v1/compare_report.schema.json").read_text()
        )
        assert a == b
        assert (
            a["properties"]["finding_evolution"] == b["properties"]["finding_evolution"]
        )

    def test_declared_types(self) -> None:
        block = load_compare_report_schema()["properties"]["finding_evolution"]
        props = block["properties"]
        assert block["type"] == "object"
        assert props["basis"] == {"const": "comparison_chain"}
        assert props["chain_evaluated"] == {"type": "boolean"}
        assert props["total"]["type"] == "integer"
        assert props["within_comparison_counterpart"] == {
            "const": "summary.change_inventory"
        }
        assert props["counts"]["type"] == "object"
        assert props["resolved"]["type"] == "array"
        assert set(block["required"]) == {
            "basis",
            "chain_evaluated",
            "total",
            "counts",
            "resolved",
            "within_comparison_counterpart",
        }

    @settings(max_examples=25, deadline=None)
    @given(
        st.sets(st.sampled_from(_ALPHABET), max_size=4),
        st.sets(st.sampled_from(_ALPHABET), max_size=4),
        st.one_of(st.none(), st.sets(st.sampled_from(_ALPHABET), max_size=4)),
    )
    def test_real_reports_validate(
        self, old: set[str], new: set[str], prev: set[str] | None
    ) -> None:
        """A real ``compare()`` report, with or without a chain applied."""
        result = _real(old, new)
        previous = None if prev is None else _real(prev, old)
        apply_finding_evolution(result, previous)
        doc = json.loads(to_json(result))
        validate_instance(doc, load_compare_report_schema())
        assert doc["finding_evolution"]["chain_evaluated"] is (prev is not None)

    @pytest.mark.parametrize(
        ("field", "bad"),
        [
            ("chain_evaluated", "yes"),
            ("total", -1),
            ("basis", "within_comparison"),
            ("counts", []),
            ("resolved", {}),
        ],
    )
    def test_malformed_block_is_rejected(self, field: str, bad: object) -> None:
        doc = json.loads(to_json(_real({"a"}, set())))
        validate_instance(doc, load_compare_report_schema())
        doc["finding_evolution"][field] = bad
        validator = jsonschema.Draft202012Validator(load_compare_report_schema())
        assert list(validator.iter_errors(doc))


_EXPECTED_OPERATION = {
    CrossSourceEvolution.INTRODUCED: "added",
    CrossSourceEvolution.RESOLVED: "removed",
    CrossSourceEvolution.PERSISTENT: "unchanged",
}


class TestShowFilterUsesPerFindingOperation:
    """Oracle: the documented cross-source mapping, restated here, never
    ``operation_for_change`` itself."""

    @given(
        st.sampled_from(
            [
                ChangeKind.EXPORTED_NOT_PUBLIC,
                ChangeKind.FUNC_REMOVED,
                ChangeKind.FUNC_ADDED,
            ]
        ),
        st.one_of(st.none(), st.sampled_from(list(CrossSourceEvolution))),
        st.sets(st.sampled_from(sorted(ACTION_TOKEN_OPERATIONS)), min_size=1),
    )
    def test_filter_matches_serialized_operation(
        self,
        kind: ChangeKind,
        cross: CrossSourceEvolution | None,
        tokens: set[str],
    ) -> None:
        change = Change(kind=kind, symbol="s", description="d")
        change.cross_source_evolution = cross
        from abicheck.report.change_operation import operation_for_kind

        expected = _EXPECTED_OPERATION.get(cross) or operation_for_kind(kind.value)  # type: ignore[arg-type]
        token_for = {
            "added": "added",
            "removed": "removed",
            "modified": "changed",
            "unchanged": "unchanged",
        }
        filt = ShowOnlyFilter.parse(",".join(sorted(tokens)))
        assert filt.matches(change) is (token_for[expected] in tokens)
        # The serialized operation is what the filter selected on.
        assert operation_for_change(change, kind.value) == expected

    def test_unchanged_token_parses(self) -> None:
        assert ShowOnlyFilter.parse("unchanged").actions == frozenset({"unchanged"})

    def test_persistent_hygiene_finding_is_not_changed(self) -> None:
        change = Change(
            kind=ChangeKind.EXPORTED_NOT_PUBLIC, symbol="s", description="d"
        )
        change.cross_source_evolution = CrossSourceEvolution.PERSISTENT
        assert not ShowOnlyFilter.parse("changed").matches(change)
        assert ShowOnlyFilter.parse("unchanged").matches(change)

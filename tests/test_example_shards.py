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

"""Unit tests for the example-catalog CI sharding helper.

The properties that matter are the ones CI silently depends on: shards
must partition the catalog exactly (a dropped case is a validation gap
that still reports green), and a merge must refuse to emit an artifact
the downstream collector would reject.
"""

from __future__ import annotations

import json

import pytest

from tests.example_shards import (
    assert_names_are_unambiguous,
    load_case_names,
    merge_payloads,
    shard_names,
)


def _payload(names, *, toolchain="gcc", summary=None, **overrides):
    payload = {
        "schema_version": "validate_examples.v2",
        "runner": "tests/validate_examples.py",
        "ground_truth_sha256": "deadbeef",
        "ground_truth_cases": 3,
        "toolchain": toolchain,
        "artifact_variants": ["debug-headers"],
        "selected_cases": len(names),
        "summary": summary or {"PASS": len(names)},
        "results": [{"case_id": n, "status": "PASS"} for n in names],
    }
    payload.update(overrides)
    return payload


class TestShardPartitioning:
    def test_shards_partition_the_real_catalog_exactly(self) -> None:
        names = load_case_names()
        for of in (2, 3, 4, 5):
            union = [
                n for shard in range(1, of + 1) for n in shard_names(names, shard, of)
            ]
            assert sorted(union) == names, f"{of} shards lost or duplicated cases"
            assert len(union) == len(set(union))

    def test_shards_are_balanced_within_one_case(self) -> None:
        names = load_case_names()
        sizes = [len(shard_names(names, s, 4)) for s in range(1, 5)]
        assert max(sizes) - min(sizes) <= 1, f"unbalanced shards: {sizes}"

    def test_round_robin_interleaves_rather_than_slicing_contiguously(self) -> None:
        # Contiguous slicing would put the whole expensive tail in one leg;
        # round-robin is what keeps cost spread. Assert the actual stride.
        assert shard_names(["a", "b", "c", "d", "e"], 1, 2) == ["a", "c", "e"]
        assert shard_names(["a", "b", "c", "d", "e"], 2, 2) == ["b", "d"]

    def test_out_of_range_shard_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            shard_names(["a", "b"], 0, 2)
        with pytest.raises(SystemExit):
            shard_names(["a", "b"], 3, 2)


class TestNameAmbiguityGuard:
    def test_real_catalog_has_no_substring_collisions(self) -> None:
        # This is what makes driving the runner's substring filter from a
        # shard list safe; if a future case name breaks it, fail loudly here
        # rather than as a duplicate-case error two CI jobs downstream.
        assert_names_are_unambiguous(load_case_names()) is None

    def test_substring_collision_is_rejected(self) -> None:
        with pytest.raises(SystemExit, match="ambiguous case names"):
            assert_names_are_unambiguous(["case10", "case100"])


class TestMerge:
    def test_merge_reassembles_the_full_catalog(self) -> None:
        merged = merge_payloads(
            [
                _payload(["a", "c"], summary={"PASS": 2}),
                _payload(["b"], summary={"FAIL": 1}),
            ],
            expected=["a", "b", "c"],
        )
        assert [r["case_id"] for r in merged["results"]] == ["a", "b", "c"]
        assert merged["selected_cases"] == 3
        assert merged["summary"] == {"PASS": 2, "FAIL": 1}
        # Run-describing fields are carried through unchanged.
        assert merged["toolchain"] == "gcc"
        assert merged["ground_truth_sha256"] == "deadbeef"
        assert merged["schema_version"] == "validate_examples.v2"

    def test_merged_output_is_json_serializable(self) -> None:
        merged = merge_payloads([_payload(["a"]), _payload(["b"])], expected=["a", "b"])
        assert json.loads(json.dumps(merged))["selected_cases"] == 2

    def test_missing_case_is_rejected(self) -> None:
        with pytest.raises(SystemExit, match="missing case ids"):
            merge_payloads([_payload(["a"])], expected=["a", "b"])

    def test_duplicate_case_across_shards_is_rejected(self) -> None:
        with pytest.raises(SystemExit, match="duplicate case ids"):
            merge_payloads([_payload(["a"]), _payload(["a"])], expected=["a"])

    def test_unexpected_case_is_rejected(self) -> None:
        with pytest.raises(SystemExit, match="unexpected case ids"):
            merge_payloads([_payload(["a", "zz"])], expected=["a"])

    def test_shards_from_different_toolchains_are_rejected(self) -> None:
        with pytest.raises(SystemExit, match="disagree on 'toolchain'"):
            merge_payloads(
                [_payload(["a"], toolchain="gcc"), _payload(["b"], toolchain="clang")],
                expected=["a", "b"],
            )

    def test_shards_from_different_checkouts_are_rejected(self) -> None:
        with pytest.raises(SystemExit, match="disagree on 'ground_truth_sha256'"):
            merge_payloads(
                [_payload(["a"]), _payload(["b"], ground_truth_sha256="cafe")],
                expected=["a", "b"],
            )

    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(SystemExit, match="no shard payloads"):
            merge_payloads([], expected=["a"])

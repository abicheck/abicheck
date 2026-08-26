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

"""Primitive-level tests for `abicheck.storage.json_budget` (G40) -- the
shared JSON container-node/nesting-depth pre-scan `bundle_facts.py`'s
per-blob decode and `bundle_archive.py`'s `read_manifest()` both share,
rather than each keeping an independent copy (AGENTS.md's own guidance on
testing a reusable merge/dedupe/budget primitive directly, not only
through its highest-level caller)."""

from __future__ import annotations

import json

import pytest

from abicheck.storage.json_budget import (
    JsonContainerBudgetExceeded,
    JsonNestingTooDeepError,
    check_json_container_budget,
)


def test_accepts_a_payload_within_both_budgets():
    raw = json.dumps({"a": [1, 2, {"b": []}], "c": {}}).encode()
    check_json_container_budget(raw, max_container_nodes=100, max_nesting_depth=100)


def test_counts_object_nodes():
    raw = ('[' + ",".join(["{}"] * 10) + ']').encode()
    with pytest.raises(JsonContainerBudgetExceeded):
        check_json_container_budget(raw, max_container_nodes=5)


def test_counts_array_nodes_too():
    """The regression this budget exists to close: an object_pairs_hook
    alone never observes an array node at all."""
    raw = ('[' + ",".join(["[]"] * 10) + ']').encode()
    with pytest.raises(JsonContainerBudgetExceeded):
        check_json_container_budget(raw, max_container_nodes=5)


def test_a_bracket_inside_a_string_value_is_not_counted():
    raw = json.dumps({"weird": "a[b]c{d}e" * 50}).encode()
    # Only the one real outer object -- well under a tiny budget.
    check_json_container_budget(raw, max_container_nodes=1)


def test_an_escaped_quote_inside_a_string_does_not_desync_token_boundaries():
    raw = json.dumps({"s": 'a\\"[b]\\"c', "arr": [1, 2, 3]}).encode()
    # One object, one array = 2 containers; a desync would either
    # miscount or run past the string into structural false positives.
    check_json_container_budget(raw, max_container_nodes=2)
    with pytest.raises(JsonContainerBudgetExceeded):
        check_json_container_budget(raw, max_container_nodes=1)


def test_raises_once_the_budget_is_first_exceeded_not_after_a_full_scan():
    # A huge tail after the budget is exceeded must never be walked.
    raw = b"[" + b"{}," * 1_000_000 + b"{}]"
    with pytest.raises(JsonContainerBudgetExceeded) as excinfo:
        check_json_container_budget(raw, max_container_nodes=3)
    assert excinfo.value.args[0] == 4  # stopped at the 4th container, not 1,000,001


def test_nesting_depth_within_budget_is_accepted():
    depth = 50
    raw = (("[" * depth) + ("]" * depth)).encode()
    check_json_container_budget(raw, max_container_nodes=1000, max_nesting_depth=100)


def test_nesting_depth_exceeding_budget_raises_the_depth_error_not_the_count_error():
    depth = 200
    raw = (("[" * depth) + ("]" * depth)).encode()
    with pytest.raises(JsonNestingTooDeepError):
        check_json_container_budget(raw, max_container_nodes=1_000_000, max_nesting_depth=100)


def test_depth_regression_python_314_json_loads_no_longer_raises_recursionerror():
    """The actual CI regression this depth check exists to close: a
    payload nested deep enough that older Python's json.loads() would
    itself raise RecursionError (relied on by both bundle_facts.py's and
    bundle_archive.py's own translation) parses cleanly with no error at
    all on Python 3.14 (confirmed empirically). This pre-scan must reject
    it deterministically, independent of that json.loads() behavior."""
    deeply_nested = (("[" * 10_000) + ("]" * 10_000)).encode()
    with pytest.raises(JsonNestingTooDeepError):
        check_json_container_budget(deeply_nested, max_container_nodes=1_000_000)


def test_default_depth_budget_does_not_reject_a_legitimate_900_level_payload():
    """Mirrors `tests/test_bundle_facts_archive.py`'s own
    ``test_load_translates_a_recursion_error_when_cloning_a_shared_snapshot``
    fixture depth (900) -- chosen there so json.loads() itself still
    succeeds and a later copy.deepcopy() is what raises. The default depth
    budget here must stay comfortably above that, or this pre-scan would
    intercept before json.loads() is ever reached, changing what that
    test actually exercises."""
    depth = 900
    raw = (("[" * depth) + ("]" * depth)).encode()
    check_json_container_budget(raw, max_container_nodes=1_000_000)  # must not raise


def test_close_without_matching_open_does_not_go_negative_or_crash():
    # Malformed JSON (unbalanced) -- not this pre-check's job to reject,
    # only to not crash on.
    raw = b"]]]}}}{{{[[["
    check_json_container_budget(raw, max_container_nodes=1000, max_nesting_depth=1000)

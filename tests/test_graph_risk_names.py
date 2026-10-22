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
"""``example_names``: the wording both source-graph risk detectors share."""

from __future__ import annotations

from abicheck.compare.graph_risk_names import EXAMPLE_LIMIT, example_names


def test_no_targets_says_nothing() -> None:
    assert example_names([], {}, "Added:") == ""


def test_labels_are_used_and_unknown_ids_fall_back_to_themselves() -> None:
    labels = {"decl:a": "demo::a()"}

    assert example_names(["decl:a", "decl:b"], labels, "Added:") == (
        " Added: demo::a(), decl:b."
    )


def test_targets_past_the_limit_are_counted_not_named() -> None:
    targets = [f"decl:f{i}" for i in range(EXAMPLE_LIMIT + 3)]

    text = example_names(targets, {}, "Removed:")

    assert text == " Removed: decl:f0, decl:f1 (+3 more)."

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

"""Short-option clusters in a call's argv, moved out of `test_skill_eval_graders.py`
to keep that file under its architecture no-growth baseline. Same tests, same
assertions."""

from __future__ import annotations

import pytest
from _skill_eval_graders_fixtures import ev


class TestShortOptionClusters:
    """Click packs short options, and both readers of an argv must agree.

    Verified against the real CLI: `compare x.so -vv x.so` runs the comparison,
    and `compare ... -voreport.json` writes `report.json`.
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "x.so", "-vv", "x.so"],
            ["compare", "x.so", "-vvv", "x.so"],
            ["compare", "-oreport.json", "a.so", "a.so"],
            ["compare", "x.so", "x.so", "-voreport.json"],
        ],
    )
    def test_a_cluster_does_not_hide_the_second_side(self, argv):
        assert ev.compares_one_side_against_itself({"argv": argv})

    @pytest.mark.parametrize(
        "argv",
        [
            ["compare", "a.so", "b.so", "-vo", "report.json"],
            ["compare", "a.so", "b.so", "-j4"],
            ["compare", "a.so", "b.so", "-oreport.json"],
        ],
    )
    def test_an_ordinary_invocation_survives_expansion(self, argv):
        assert not ev.compares_one_side_against_itself({"argv": argv})

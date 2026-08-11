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

"""Split out of ``test_internal_leak.py`` (at its own 2000-line hard cap) —
``_build_call_graph_leak_change``'s ``reachability_proof_path`` selection.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.internal_leak import _build_call_graph_leak_change


class TestBuildCallGraphLeakChangePreferredPath:
    """Codex review on PR #717: reachability_proof_path must prefer an exact
    route over an "overapprox: "-prefixed one when both exist among the
    caller's proof_paths -- _diff_call_graph_leaks concatenates every public
    entry's own independently-discovered path in discovery order, not
    exactness order, so an overapprox path from one entry can precede an
    exact path from a different entry reaching the same internal symbol."""

    def test_reachability_proof_path_prefers_an_exact_route(self) -> None:
        overapprox_first = (
            "overapprox: pubA() --[DECL_CALLS_DECL]--> ns::detail::helper()"
        )
        exact_second = "pubB() --[DECL_CALLS_DECL]--> ns::detail::helper()"
        change = _build_call_graph_leak_change(
            "ns::detail::helper",
            [
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="ns::detail::helper",
                    description="removed",
                )
            ],
            [overapprox_first, exact_second],
        )
        assert change.reachability_proof_path == exact_second

    def test_all_overapprox_falls_back_to_the_first(self) -> None:
        first = "overapprox: pubA() --[DECL_CALLS_DECL]--> ns::detail::helper()"
        second = "overapprox: pubB() --[DECL_CALLS_DECL]--> ns::detail::helper()"
        change = _build_call_graph_leak_change(
            "ns::detail::helper",
            [
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="ns::detail::helper",
                    description="removed",
                )
            ],
            [first, second],
        )
        assert change.reachability_proof_path == first

    def test_an_exact_path_beyond_the_displayed_three_is_still_preferred(
        self,
    ) -> None:
        # The description only ever shows the first 3 paths (path_strs), but
        # the exact-preference search must cover the full proof_paths list,
        # not just what gets displayed.
        paths = [
            "overapprox: pubA() --[DECL_CALLS_DECL]--> ns::detail::helper()",
            "overapprox: pubB() --[DECL_CALLS_DECL]--> ns::detail::helper()",
            "overapprox: pubC() --[DECL_CALLS_DECL]--> ns::detail::helper()",
            "pubD() --[DECL_CALLS_DECL]--> ns::detail::helper()",
        ]
        change = _build_call_graph_leak_change(
            "ns::detail::helper",
            [
                Change(
                    kind=ChangeKind.FUNC_REMOVED,
                    symbol="ns::detail::helper",
                    description="removed",
                )
            ],
            paths,
        )
        assert change.reachability_proof_path == paths[3]

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

"""``RunPlanCheck.consumer_compile_active`` coverage.

Split out of ``test_run_plan.py`` (its own architecture-debt no-growth
ceiling, ADR-061) rather than grown in place -- see
``TestConsumerCompileOverlayProjection`` there for the sibling coverage of
``consumer_compile_gcc_path``/``consumer_compile_gcc_options`` this reuses
``_RAW``/``_parsed``/``_bo`` from.
"""

from __future__ import annotations

from test_run_plan import TestConsumerCompileOverlayProjection, _bo, _parsed

from abicheck.buildsource.run_plan import generate_run_plan

_RAW = TestConsumerCompileOverlayProjection._RAW


def test_consumer_compile_active_is_true_only_with_a_real_overlay() -> None:
    config = _parsed(_RAW)
    plan, report = generate_run_plan(
        config,
        {"gcc14-build-clang20-client": _bo("libfoo"), "plain": _bo("libfoo")},
    )
    assert report.ok
    [with_overlay] = [
        c for c in plan.checks if c.profile_id == "gcc14-build-clang20-client"
    ]
    [without_overlay] = [c for c in plan.checks if c.profile_id == "plain"]
    assert with_overlay.consumer_compile_active is True
    assert with_overlay.to_dict()["consumer_compile_active"] is True
    assert without_overlay.consumer_compile_active is False


def test_empty_consumer_compile_overlay_is_not_active() -> None:
    """An empty ``consumer_compile: {}`` is indistinguishable from absent."""
    raw = {
        "targets": _RAW["targets"],
        "profiles": {
            "empty-overlay": {"contract": True, "consumer_compile": {}},
        },
        "baseline": _RAW["baseline"],
    }
    config = _parsed(raw)
    plan, report = generate_run_plan(config, {"empty-overlay": _bo("libfoo")})
    assert report.ok
    [check] = plan.checks
    assert check.consumer_compile_active is False

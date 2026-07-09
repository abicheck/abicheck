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

"""Validate the ``.pyi``-pair example cases against ``ground_truth.json`` (G23).

The compiled-binary example harness (``test_abi_examples.py``, integration) has
no path for a Python-extension ``.pyi`` pair, and the g20 snapshot harness only
runs single-artifact ``run_crosschecks`` audits. This fast-lane test closes that
gap: for every ``ground_truth.json`` case flagged ``stub_pair``, it loads the
committed ``v1.pyi`` / ``v2.pyi`` (compiler-free, via ``surface_from_stub_file``),
runs ``compare``, and asserts the recovered Python-level surface diff matches the
case's expected verdict and kinds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.model import AbiSnapshot
from abicheck.python_api import surface_from_stub_file

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
_GROUND_TRUTH = json.loads((_EXAMPLES / "ground_truth.json").read_text())["verdicts"]

_STUB_PAIR_CASES = sorted(
    name for name, gt in _GROUND_TRUTH.items() if gt.get("stub_pair")
)


def _snap(case_dir: Path, side: str) -> AbiSnapshot:
    snap = AbiSnapshot(library="mymod.abi3.so", version=side)
    snap.python_api = surface_from_stub_file(
        case_dir / f"{side}.pyi", module_name="mymod"
    )
    return snap


def test_at_least_one_stub_pair_case() -> None:
    # Guard: the discovery must not silently match zero cases (which would make
    # the parametrized test vacuously pass).
    assert _STUB_PAIR_CASES, "no stub_pair example cases found in ground_truth.json"


@pytest.mark.parametrize("case_name", _STUB_PAIR_CASES)
def test_stub_pair_case_matches_ground_truth(case_name: str) -> None:
    case_dir = _EXAMPLES / case_name
    assert (case_dir / "v1.pyi").is_file() and (case_dir / "v2.pyi").is_file()

    gt = _GROUND_TRUTH[case_name]
    result = compare(_snap(case_dir, "v1"), _snap(case_dir, "v2"))

    emitted = {c.kind.value for c in result.changes}
    for expected_kind in gt.get("expected_kinds", []):
        assert expected_kind in emitted, (
            f"{case_name}: expected {expected_kind}, got {sorted(emitted)}"
        )
    assert result.verdict == Verdict(gt["expected"])

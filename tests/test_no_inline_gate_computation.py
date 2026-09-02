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

"""Unit-test mirror of the ``no-inline-gate-computation`` AI-readiness check
(``scripts/no_inline_gate_computation.py``, registered by
``scripts/check_ai_readiness.py``) -- ADR-063 Phase 7's acceptance
criterion.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_ai_readiness import Findings  # noqa: E402
from scripts.no_inline_gate_computation import (  # noqa: E402
    ALLOWED_RELATIVE_PATHS,
    check_no_inline_gate_computation,
    scan_file,
)


def test_no_unlisted_violation_in_real_repo() -> None:
    """The real repository has zero WARN-level hits today -- this pins that
    the check is clean against the actual tree, not just a synthetic
    fixture."""
    findings = Findings()
    check_no_inline_gate_computation(findings)
    warnings = [m for c, m in findings.warnings if c == "no-inline-gate-computation"]
    assert warnings == []


def test_flags_a_gate_attribute_compared_against_a_raw_int(tmp_path) -> None:
    path = tmp_path / "bad.py"
    path.write_text(
        textwrap.dedent(
            """
            def f(outcome):
                if outcome.gate == 4:
                    return "blocking"
                return "ok"
            """
        )
    )
    hits = scan_file(path)
    assert len(hits) == 1
    assert hits[0][0] == 3


def test_flags_an_operational_attribute_compared_against_a_raw_int(tmp_path) -> None:
    path = tmp_path / "bad.py"
    path.write_text(
        textwrap.dedent(
            """
            def f(outcome):
                return outcome.operational != 0
            """
        )
    )
    hits = scan_file(path)
    assert len(hits) == 1


def test_flags_a_max_fold_of_gate_against_a_raw_int(tmp_path) -> None:
    path = tmp_path / "bad.py"
    path.write_text(
        textwrap.dedent(
            """
            def f(outcome):
                return max(outcome.gate, 0)
            """
        )
    )
    hits = scan_file(path)
    assert len(hits) == 1


def test_does_not_flag_exit_code_comparisons() -> None:
    """`fold.py`'s own `max(t.gate.exit_code for t in gated ...)` -- an
    aggregation over an already-decoded `GateInfo.exit_code` int, not a
    `.gate`/`.operational` axis read -- must never be flagged (the Phase 7
    plan's own acceptance-criteria text names this exact shape)."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "fold_like.py"
        path.write_text(
            textwrap.dedent(
                """
                def exit_code(gated):
                    return max((t.gate.exit_code for t in gated), default=0)

                def other(t):
                    return t.exit_code == 4
                """
            )
        )
        assert scan_file(path) == []


def test_does_not_flag_two_gate_reads_compared_to_each_other() -> None:
    """Only a raw integer literal is a violation -- comparing two `.gate`
    reads to each other (or to a non-literal expression) is not the shape
    this check targets."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ok.py"
        path.write_text(
            textwrap.dedent(
                """
                def f(a, b):
                    return a.gate == b.gate
                """
            )
        )
        assert scan_file(path) == []


def test_allowed_paths_are_exempt_from_the_repo_wide_scan() -> None:
    """The four boundary encoders are real, existing files -- this check
    would be vacuous if the allowlist named nothing that exists."""
    pkg = _REPO_ROOT / "abicheck"
    for rel in ALLOWED_RELATIVE_PATHS:
        assert (pkg / rel).is_file(), rel

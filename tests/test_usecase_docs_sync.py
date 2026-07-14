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

"""Fast-lane wrapper for the usecase-registry <-> human-docs sync check.

The gate logic lives in ``scripts/check_usecase_docs_sync.py`` so it is
runnable standalone in CI; this mirrors it into the pytest suite so a gap
status drifting away from its human-doc summary (e.g. a gap marked
``complete`` in the registry but still listed as "planned" or in a backlog
table) fails the ordinary unit-test lane too, not just a separate CI step.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_usecase_docs_sync.py"
)
_spec = importlib.util.spec_from_file_location("check_usecase_docs_sync", _GATE_PATH)
assert _spec and _spec.loader
sync_gate = importlib.util.module_from_spec(_spec)
sys.modules["check_usecase_docs_sync"] = sync_gate
_spec.loader.exec_module(sync_gate)


def test_usecase_docs_agree_with_registry() -> None:
    gap_status = sync_gate._load_registry_gap_status()
    findings = []
    findings += sync_gate._check_eval_doc_gaps_table(gap_status)
    findings += sync_gate._check_backlog_table_excludes_done_gaps(
        gap_status, sync_gate.EVAL_DOC, "## Proposed next steps", "\n## "
    )
    findings += sync_gate._check_backlog_table_excludes_done_gaps(
        gap_status,
        sync_gate.PLANS_INDEX,
        "| Gap | Plan | Registry use cases | Effort |",
        "Initiative plans",
    )
    findings += sync_gate._check_completed_table_excludes_open_gaps(
        gap_status, sync_gate.PLANS_INDEX, "Completed or decided plans are retained"
    )
    assert not findings, (
        "usecase docs drifted from usecase-registry.yaml:\n"
        + "\n".join(f"  - {f}" for f in findings)
    )

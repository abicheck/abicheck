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

"""Finding-identity helpers a frontend needs for its own report rows.

ADR-061 Phase 4. ``finding_identity`` belongs to the ``compare`` ring, which a
frontend may not import; these three helpers answer "what stable id does this
finding carry" for a report row a CLI is about to print. Re-export only.
"""

from __future__ import annotations

from ..diff_build_config import diff_matrix
from ..finding_identity import (
    missing_contract_finding,
    report_canonical_finding_id,
    report_finding_id,
)
from ..probe_harness import load_matrix_snapshot

__all__ = [
    "diff_matrix",
    "load_matrix_snapshot",
    "missing_contract_finding",
    "report_canonical_finding_id",
    "report_finding_id",
]

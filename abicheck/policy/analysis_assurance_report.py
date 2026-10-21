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

"""JSON narrowing of ``DiffResult.analysis_assurance`` for the report writers.

Split out of :mod:`abicheck.analysis_assurance` so that module stays within its
size baseline; the rollup itself still lives there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..analysis_assurance import AnalysisAssurance

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = ["analysis_assurance_report_dict"]


def analysis_assurance_report_dict(result: DiffResult) -> dict[str, Any] | None:
    """``result.analysis_assurance.to_dict()``, or ``None`` when absent/wrong
    type -- the one narrowing implementation ``reporter._add_analysis_assurance``
    calls from all four JSON paths (full/leaf/root-cause/stat), so the
    ``DiffResult.analysis_assurance: object`` circular-import workaround (see
    that field's own comment in ``checker_types.py``) has exactly one place
    that unwraps it.
    """
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance):
        return None
    return aa.to_dict()

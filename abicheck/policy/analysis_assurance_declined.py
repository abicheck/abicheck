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

"""Analysis-assurance notes for per-entity comparisons a detector declined
(ADR-063 T9, ``compare/declined_comparisons.py``).

A declined comparison is a detector refusing to judge an entity because its
evidence was not established on one or both sides. Its ``DetectorResult.
declined`` record reached the JSON report but nothing a reader of the
run's assurance would see, so "no finding" and "not judged" still read the
same at the level of the whole run. One note per declining detector,
naming how many entities it declined, closes that: weaker evidence narrows
the conclusion visibly instead of reading as clean (AGENTS.md, "Weaker
evidence narrows conclusions"). Notes only -- no finding, verdict or exit
code changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

__all__ = ["declined_comparison_notes"]


def declined_comparison_notes(detector_results: Iterable[Any]) -> list[str]:
    """One note per detector that declined at least one comparison, in
    detector order."""
    notes = []
    for det in detector_results:
        declined = getattr(det, "declined", ())
        if declined:
            noun = "comparison" if len(declined) == 1 else "comparisons"
            notes.append(
                f"detector {det.name!r} declined {len(declined)} {noun}: the "
                "evidence it needed was not established on both sides"
            )
    return notes

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

"""Deterministic graders for the skill evaluation (G37 D4, dimensions 1/2/3/6).

Every grader here is a pure function of one recorded run: the claim envelope,
the recorded calls, the artifacts those calls produced, and the scenario's
expected outcome. **No grader in this package calls a model** — that is what
makes them runnable in `pr` (G37 D2), re-runnable by an auditor without
credentials, and stable enough for a zero-tolerance gate to rest on.

Dimensions 4 and 5 are judged and deliberately absent: their first evaluation
is not reproducible from the bundle, so what a bundle carries for them is a
recorded verdict, not a re-derivation.

Pure stdlib, for the same reason `scripts/check_skill_eval_freshness.py` is:
the check has to run wherever the pack is checked.
"""

from __future__ import annotations

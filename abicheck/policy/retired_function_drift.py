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

"""Demote findings on a function an extractor generation retired (ADR-062 D2
fallback; bug class ``storage.cross_generation_spelling_drift``).

A ``SPELLING_MIGRATIONS`` row's ``retires_function_prefixes`` names functions
an older generation recorded only as an extractor artifact -- castxml's
compiler-declared ``__builtin_*`` before generation 57 -- and the newer one no
longer records. No load-time rewrite can drop the entity soundly (a re-saved
baseline could not repeat the load notice), so between operands that
straddle that generation every finding on such a function is annotated
``COMPATIBLE_WITH_RISK`` with the extractor-drift reason. It is never removed
(ADR-067 record before disposing): the finding and its rule stay in the
report, and the baseline's re-dump makes it disappear.
"""

from __future__ import annotations

from ..model.change import Change
from ..model.change_catalog.registry import Verdict
from .generation_drift_demotion import GENERATION_DRIFT_REASON, GENERATION_DRIFT_RULE

__all__ = ["demote_retired_function_drift"]


def demote_retired_function_drift(
    changes: list[Change],
    retired_prefixes: tuple[str, ...],
    old_generation: int,
    new_generation: int,
) -> list[Change]:
    """Annotate each finding on a function whose name starts with one of
    *retired_prefixes* -- the prefixes a generation between the operands'
    retired -- as ``COMPATIBLE_WITH_RISK``. Mutates and returns *changes*; a
    no-op without prefixes or when the generations match."""
    if not retired_prefixes or old_generation == new_generation:
        return changes
    for change in changes:
        if change.effective_verdict is not None or not change.symbol.startswith(
            retired_prefixes
        ):
            continue
        change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        change.modulation_rule = GENERATION_DRIFT_RULE
        change.modulation_reason = (
            f"{GENERATION_DRIFT_REASON} (old operand extractor generation "
            f"{old_generation or 'unstated'}, new {new_generation or 'unstated'}; "
            "a generation in between stopped recording this extractor "
            "artifact)"
        )
    return changes

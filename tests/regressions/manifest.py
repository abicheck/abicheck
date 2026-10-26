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

"""The bug-class regression registry (Phase 1 of
``docs/contribute/plans/bug-class-regression-testing.md``).

This is the durable, queryable index that plan document's Phase 0 process
change (AGENTS.md "A bug fix's regression test targets the bug class, not
the one reported input") points a `fix:` PR's "Bug class" answer at: check
here first for a matching `BugClass.id` before restating an invariant that
already has a home, and add a new entry here — not just prose in a PR body
or an AGENTS.md "Known gaps" paragraph — when a fix closes a genuinely new
class.

This module records *relationships*, not test logic: which files carry the
generalized test(s) for a class, which issues/PRs it traces back to, which
public surfaces and axes it has been verified across, and which known
residual gaps are tracked rather than silently open. It does not replace the
bug-fix test contract's per-PR gate (`scripts/check_bugfix_test_contract.py`),
enforced at PR time; this registry is what a PR's declared answer should
reference, and what the *next* PR should search before writing a fifth narrow
reproducer for a mechanism a class already covers. Entries live here and in
the `manifest_*.py` siblings, concatenated into `BUG_CLASSES` below.

``tests/test_regressions_manifest.py`` enforces this registry's own integrity
mechanically — every named `seed_tests` path exists and is a real,
pytest-collected `test_*.py` file, every `known_gaps` entry names a non-empty
reference, and an entry's optional `canary_test` (most leave it `None`, which
is honest — not every gap has one) resolves the same way. Same "a registry
entry is checked, not just written" discipline `check_ai_readiness.py`'s
`changekind-*` checks apply to `ChangeKind`.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap
from .manifest_analysis_1 import ANALYSIS_1_BUG_CLASSES
from .manifest_analysis_2 import ANALYSIS_2_BUG_CLASSES
from .manifest_caching import CACHING_BUG_CLASSES
from .manifest_classification import CLASSIFICATION_BUG_CLASSES
from .manifest_concurrency import CONCURRENCY_BUG_CLASSES
from .manifest_config import CONFIG_BUG_CLASSES
from .manifest_evidence import EVIDENCE_BUG_CLASSES
from .manifest_extraction import EXTRACTION_BUG_CLASSES
from .manifest_guards import GUARD_BUG_CLASSES
from .manifest_integration import INTEGRATION_BUG_CLASSES
from .manifest_measurement import MEASUREMENT_BUG_CLASSES
from .manifest_performance import PERFORMANCE_BUG_CLASSES
from .manifest_report import REPORT_BUG_CLASSES
from .manifest_serialization import SERIALIZATION_BUG_CLASSES
from .manifest_test_harness import TEST_HARNESS_BUG_CLASSES
from .manifest_tool_surface import TOOL_SURFACE_BUG_CLASSES

__all__ = ["BUG_CLASSES", "BugClass", "KnownGap", "all_ids", "get"]


#: The whole registry: this module's analysis classes plus each per-axis
#: sibling's own list. An assembly point, the shape `abicheck/
#: model/change_catalog/assembled.py` already uses -- adding a class to a sibling needs no
#: edit here.
BUG_CLASSES: tuple[BugClass, ...] = (
    ANALYSIS_1_BUG_CLASSES
    + ANALYSIS_2_BUG_CLASSES
    + CACHING_BUG_CLASSES
    + CLASSIFICATION_BUG_CLASSES
    + CONFIG_BUG_CLASSES
    + EXTRACTION_BUG_CLASSES
    + EVIDENCE_BUG_CLASSES
    + PERFORMANCE_BUG_CLASSES
    + CONCURRENCY_BUG_CLASSES
    + SERIALIZATION_BUG_CLASSES
    + GUARD_BUG_CLASSES
    + INTEGRATION_BUG_CLASSES
    + REPORT_BUG_CLASSES
    + TEST_HARNESS_BUG_CLASSES
    + TOOL_SURFACE_BUG_CLASSES
    + MEASUREMENT_BUG_CLASSES
)

_BY_ID: dict[str, BugClass] = {bc.id: bc for bc in BUG_CLASSES}


def get(bug_class_id: str) -> BugClass:
    """Look up a registered `BugClass` by id.

    Raises `KeyError` (with the full set of valid ids in the message,
    via the dict's own `__getitem__`) rather than returning `None` — a
    lookup miss during a PR review is a "this class isn't registered
    yet, add it" signal, not a value to silently propagate.
    """
    return _BY_ID[bug_class_id]


def all_ids() -> tuple[str, ...]:
    """Every registered `BugClass.id`, in registry order."""
    return tuple(bc.id for bc in BUG_CLASSES)

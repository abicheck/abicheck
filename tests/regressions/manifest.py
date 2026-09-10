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
residual gaps are tracked rather than silently open. It does not replace
the bug-fix test contract's per-PR gate (`scripts/check_bugfix_test_contract.py`)
— that gate is enforced at PR time; this registry is what a PR's declared
answer should reference, and what the *next* PR should search before writing
a fifth narrow reproducer for a mechanism a class already covers.

``tests/test_regressions_manifest.py`` enforces this registry's own
integrity mechanically — every named `seed_tests` path exists and is a
real, pytest-collected `test_*.py` file, every `known_gaps` entry names a
non-empty reference, and, when a `known_gaps` entry sets the optional
`canary_test` (most current entries leave it `None` — a tracked-but-
unmonitored residual is honest, not every gap has one), that path
resolves the same way — the same "a registry entry is checked, not just
written" discipline `scripts/check_ai_readiness.py`'s `changekind-*` checks
already apply to `ChangeKind`.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap
from .manifest_entries_1 import ENTRIES as _ENTRIES_1
from .manifest_entries_2 import ENTRIES as _ENTRIES_2

__all__ = ["BUG_CLASSES", "BugClass", "KnownGap", "all_ids", "get"]


#: The bug classes named in
#: `docs/contribute/plans/bug-class-regression-testing.md`'s Phases 2-9,
#: seeded with the generalized/property test(s) that already exist for
#: each. A class entry does not claim its phase is *complete* — see that
#: plan document for what each phase still has open; this registry only
#: records what already has a home so a future PR can find it.
BUG_CLASSES: tuple[BugClass, ...] = _ENTRIES_1 + _ENTRIES_2


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

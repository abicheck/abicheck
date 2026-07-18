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

"""Hidden test for agent-evals/tasks/add-change-kind-small.

Not visible to the agent under evaluation — the runner copies this file in
after the agent's attempt is complete (see agent-evals/README.md). Modeled
directly on tests/test_diff_types_deep.py's TestFieldDeprecated, which
covers the already-implemented analogous case (a struct *field* gaining/
losing [[deprecated]]) — this exercises the *type-level* [[nodiscard]] case
the task asks the agent to add.

Constructs snapshots directly (no compiler needed), matching this
repository's established pattern for detector-level unit tests.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from abicheck.model import AbiSnapshot, RecordType


def _snap(
    version: str, *, name: str = "Cfg", nodiscard: bool | None = None
) -> AbiSnapshot:
    t = RecordType(name=name, kind="struct", size_bits=32, nodiscard=nodiscard)
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        types=[t],
        from_headers=True,
        ast_producer="castxml",
    )


def _kinds(result):
    return {c.kind for c in result.changes}


class TestTypeNodiscardAdded:
    def test_struct_gains_nodiscard(self) -> None:
        old = _snap("1.0", nodiscard=None)
        new = _snap("2.0", nodiscard=True)
        r = compare(old, new)
        changed = [c for c in r.changes if c.kind == ChangeKind.TYPE_NODISCARD_ADDED]
        assert len(changed) == 1, (
            "expected exactly one TYPE_NODISCARD_ADDED change for a struct "
            f"gaining [[nodiscard]]; got changes: {sorted(k.value for k in _kinds(r))}"
        )
        assert changed[0].symbol == "Cfg" or changed[0].name == "Cfg"

    def test_gaining_nodiscard_is_compatible(self) -> None:
        """[[nodiscard]] is a compile-time-only, source-level advisory — it
        must never be classified as an ABI or API break."""
        old = _snap("1.0", nodiscard=None)
        new = _snap("2.0", nodiscard=True)
        r = compare(old, new)
        assert r.verdict == Verdict.COMPATIBLE
        assert ChangeKind.TYPE_NODISCARD_ADDED not in (BREAKING_KINDS | API_BREAK_KINDS)

    def test_struct_loses_nodiscard(self) -> None:
        old = _snap("1.0", nodiscard=True)
        new = _snap("2.0", nodiscard=None)
        r = compare(old, new)
        assert ChangeKind.TYPE_NODISCARD_REMOVED in _kinds(r)

    def test_unrelated_struct_without_nodiscard_change_is_not_flagged(self) -> None:
        """A same-shaped struct that never had [[nodiscard]] on either side
        must not spuriously get a nodiscard change (or any change at all)."""
        old = _snap("1.0", name="Other", nodiscard=None)
        new = _snap("2.0", name="Other", nodiscard=None)
        r = compare(old, new)
        assert ChangeKind.TYPE_NODISCARD_ADDED not in _kinds(r)
        assert ChangeKind.TYPE_NODISCARD_REMOVED not in _kinds(r)

    def test_nodiscard_change_alongside_unrelated_type_is_isolated(self) -> None:
        """Two types in the same snapshot pair: only the one that actually
        changed nodiscard status is flagged."""
        t_changed_old = RecordType(
            name="Flagged", kind="struct", size_bits=32, nodiscard=None
        )
        t_changed_new = RecordType(
            name="Flagged", kind="struct", size_bits=32, nodiscard=True
        )
        t_stable = RecordType(
            name="Stable", kind="struct", size_bits=64, nodiscard=None
        )
        old = AbiSnapshot(
            library="libtest.so.1",
            version="1.0",
            types=[t_changed_old, t_stable],
            from_headers=True,
            ast_producer="castxml",
        )
        new = AbiSnapshot(
            library="libtest.so.1",
            version="2.0",
            types=[t_changed_new, t_stable],
            from_headers=True,
            ast_producer="castxml",
        )
        r = compare(old, new)
        nodiscard_changes = [
            c for c in r.changes if c.kind == ChangeKind.TYPE_NODISCARD_ADDED
        ]
        assert len(nodiscard_changes) == 1
        assert (
            nodiscard_changes[0].symbol == "Flagged"
            or nodiscard_changes[0].name == "Flagged"
        )

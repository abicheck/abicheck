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

"""The comparison-lifetime memo (``model/comparison_memo.py``).

Contract, each stated independently of the implementation:

* outside a scope nothing is cached -- a snapshot still being built can
  never be served a value derived from an earlier state of itself;
* inside one, a value is computed once per ``(name, snapshot)``, and the
  scope releases every snapshot it pinned when it closes;
* a nested scope resolutions the outer one;
* and, the property that matters: ``compare_snapshots`` renders the same
  JSON report with the memo on as with it off, over every known detector
  mutation -- with a vacuity guard proving the memo really served hits.
"""

from __future__ import annotations

import contextlib
import gc
import weakref

import pytest
from _detector_mutations import MUTATIONS, build_snapshot

from abicheck.compare import surface_reconcile
from abicheck.model import AbiSnapshot, Function, Visibility, snapshot_identity_table
from abicheck.model.comparison_memo import (
    comparison_memo_active,
    comparison_memo_scope,
    comparison_memoized,
)
from abicheck.reporter import to_json
from abicheck.workflows import compare_policy


class _Snap:
    """Any object: the memo keys on identity, not type."""


def test_outside_a_scope_nothing_is_cached() -> None:
    calls = []
    snap = _Snap()
    for _ in range(3):
        comparison_memoized("k", snap, lambda: calls.append(1) or len(calls))
    assert calls == [1, 1, 1]
    assert not comparison_memo_active()


def test_inside_a_scope_each_name_and_snapshot_computes_once() -> None:
    calls: list[tuple[str, int]] = []
    a, b = _Snap(), _Snap()

    def value(name: str, snap: object) -> object:
        return comparison_memoized(
            name, snap, lambda: calls.append((name, id(snap))) or object()
        )

    with comparison_memo_scope():
        assert comparison_memo_active()
        first = {(n, id(s)): value(n, s) for n in ("x", "y") for s in (a, b)}
        for _ in range(3):
            for n in ("x", "y"):
                for s in (a, b):
                    assert value(n, s) is first[(n, id(s))]
    assert sorted(calls) == sorted(first)  # one computation per key
    assert not comparison_memo_active()


def test_nested_scope_resolutions_the_outer_one() -> None:
    calls = []
    snap = _Snap()
    with comparison_memo_scope():
        comparison_memoized("k", snap, lambda: calls.append(1))
        with comparison_memo_scope():
            comparison_memoized("k", snap, lambda: calls.append(2))
        # Still open after the inner scope closed.
        comparison_memoized("k", snap, lambda: calls.append(3))
    assert calls == [1]


def test_closing_the_scope_releases_pinned_snapshots() -> None:
    snap = _Snap()
    ref = weakref.ref(snap)
    with comparison_memo_scope():
        comparison_memoized("k", snap, object)
        del snap
        gc.collect()
        assert ref() is not None  # pinned while open: its id cannot be reused
    gc.collect()
    assert ref() is None


def _context() -> dict:
    return {
        "functions": [
            Function(
                name="ctx_f0",
                mangled="_Zctx_f0v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        "types": [],
    }


def _report(old: AbiSnapshot, new: AbiSnapshot) -> str:
    return to_json(
        compare_policy.compare_snapshots(old, new), include_exit_decision=True
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("tag", [0, 7])
def test_compare_report_is_identical_with_and_without_the_memo(
    mutation, tag: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_extra, new_extra, *_ = mutation(tag)

    def pair() -> tuple[AbiSnapshot, AbiSnapshot]:
        # Fresh objects per run: the two runs must share no cached state.
        return (
            build_snapshot("1.0", _context(), old_extra),
            build_snapshot("2.0", _context(), new_extra),
        )

    resolutions = []
    real_identities = snapshot_identity_table.snapshot_identities

    def counting_identities(*args, **kwargs):
        resolutions.append(1)
        return real_identities(*args, **kwargs)

    monkeypatch.setattr(
        snapshot_identity_table, "snapshot_identities", counting_identities
    )
    with_memo = _report(*pair())
    resolutions_with = len(resolutions)

    resolutions.clear()
    for module in (compare_policy, surface_reconcile):
        monkeypatch.setattr(module, "comparison_memo_scope", contextlib.nullcontext)
    without_memo = _report(*pair())
    resolutions_without = len(resolutions)

    assert with_memo == without_memo
    # Vacuity guard: the memo run really shared work the plain run repeated.
    assert resolutions_with < resolutions_without


def test_export_join_with_a_foreign_identity_table_is_never_shared() -> None:
    """Only a join over the snapshot's own shared identity table may be
    memoized; one built from a caller's separately-constructed table (the
    AST-names form, say) must be computed for that caller, in and out of a
    scope, and equal the plain join whenever the tables agree."""
    from abicheck.compare.export_join import join_exports
    from abicheck.model.graph_entity_identity import snapshot_identities
    from abicheck.model.snapshot_identity_table import identities_for_snapshot

    snap = build_snapshot("1.0", _context(), {})
    foreign = snapshot_identities(snap, export_names=frozenset())
    outside = join_exports(snap, foreign)
    with comparison_memo_scope():
        shared = join_exports(snap)
        assert join_exports(snap, identities_for_snapshot(snap)) is shared
        first = join_exports(snap, foreign)
        second = join_exports(snap, foreign)
    assert first is not shared and first is not second
    assert first == second == outside

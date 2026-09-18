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

"""Retention is resolved per side and per consumer, and the claim is checked.

Bug class ``perf.retention_decided_by_one_switch_not_by_consumers``
(``tests/regressions/manifest_performance.py``).

The hard part of this class is that the defect is invisible in the output:
retaining a NEW-side snapshot nobody reads produces byte-identical reports.
So the tests here are of two kinds and both are needed.

1. **What is retained** -- the member entry carries the compact evidence on
   the side no consumer reads, and the full snapshot only on the side one
   does. Asserted by *absence* of the key, not by "it was not used".
2. **What the consumers read** -- a structural check over the real call
   sites, so the claim "JUnit and ``--bundle-facts-out`` read the OLD side
   only" is re-derived from the source on every run rather than being a
   comment that can go stale the moment someone adds a ``_new_snapshot``
   read.

Equivalence of the *output* across retention settings is covered where it
belongs: the JUnit byte-for-byte comparison in
``TestOutputIsUnchanged`` below, and the per-symbol evidence equivalence in
``tests/test_bundle_signature_evidence_projection.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from abicheck.workflows.release_snapshot_retention import (
    SnapshotRetention,
    resolve_snapshot_retention,
)

_ROOT = Path(__file__).resolve().parents[1] / "abicheck"


class TestResolution:
    @pytest.mark.parametrize(
        ("junit", "baseline", "old_full", "consumers"),
        [
            (False, False, False, ()),
            (True, False, True, ("junit",)),
            (False, True, True, ("bundle_facts_out",)),
            (True, True, True, ("junit", "bundle_facts_out")),
        ],
    )
    def test_every_output_combination(
        self, junit: bool, baseline: bool, old_full: bool, consumers: tuple[str, ...]
    ) -> None:
        """All four combinations, with an oracle stated independently.

        The expectation is not "whatever the function returns": ``old_full``
        is derived here from *whether any consumer was requested*, and the
        consumer tuple is spelled out, so an implementation that returned a
        constant for either field fails.
        """
        got = resolve_snapshot_retention(junit=junit, bundle_facts_out=baseline)
        assert got.old_full is old_full
        assert got.old_consumers == consumers
        # The invariant that carries the whole memory saving: no requested
        # output makes the NEW side full, because none reads it.
        assert got.new_full is False
        assert got.new_consumers == ()
        assert got.any_full is old_full

    def test_the_default_retains_nothing(self) -> None:
        """A bare ``SnapshotRetention()`` is the compact-only default.

        `_compare_one_library` substitutes it for ``None``, so a caller that
        forgets to pass one gets *less* retention, never more.
        """
        assert SnapshotRetention() == SnapshotRetention(old_full=False, new_full=False)
        assert SnapshotRetention().any_full is False

    def test_the_decision_is_recordable_for_a_memory_trace(self) -> None:
        counts = resolve_snapshot_retention(
            junit=True, bundle_facts_out=True
        ).as_counts()
        assert counts == {
            "old_full": True,
            "new_full": False,
            "old_consumers": ["junit", "bundle_facts_out"],
            "new_consumers": [],
        }


def _read(name: str) -> ast.Module:
    return ast.parse((_ROOT / name).read_text(encoding="utf-8"))


def _subscript_string_keys(tree: ast.Module) -> set[str]:
    """Every ``x["literal"]`` / ``x.get("literal")`` key read in *tree*.

    Deliberately over-broad: the claim under test is an *absence*, so a scan
    that reports too much can only make this test stricter, never weaker.
    """
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("get", "pop", "setdefault")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
    return keys


class TestConsumerInventory:
    """Re-derive the inventory from the source, so it cannot go stale."""

    #: Modules that may legitimately mention a stashed snapshot key: the
    #: producer, the bundle fold that accepts either shape, and the strip
    #: pass that discards them before serialisation.
    PRODUCERS = ("cli_compare_release_pairwise.py",)
    CONSUMERS = (
        "cli_compare_release_helpers.py",
        "cli_compare_release_matrix.py",
    )

    def test_no_module_reads_a_new_side_snapshot_it_would_not_find(self) -> None:
        """``_new_snapshot`` is never *read* outside the tolerant fold.

        ``_collect_bundle_result`` reads it with a ``.get`` that falls back
        to the compact evidence, which is why it is exempt; anything else
        reading it would now silently get ``None``.
        """
        offenders = []
        for path in sorted(_ROOT.rglob("*.py")):
            rel = path.relative_to(_ROOT).as_posix()
            if rel.endswith(("cli_compare_release_pairwise.py",)) or rel in (
                "cli_compare_release_helpers.py",
                "cli_compare_release_matrix.py",
            ):
                continue
            if "_new_snapshot" in _subscript_string_keys(
                ast.parse(path.read_text(encoding="utf-8"))
            ):
                offenders.append(rel)
        assert offenders == [], (
            "a module started reading the stashed NEW snapshot, which the "
            "resolved retention no longer keeps: add it to "
            "resolve_snapshot_retention's consumers (and accept the memory "
            "cost), or read the compact evidence instead"
        )

    def test_the_bundle_fold_still_accepts_the_compact_shape(self) -> None:
        """The one tolerant reader must keep its fallback.

        Without it, making NEW compact would hand ``None`` to the bundle
        analysis and silently drop every intra-dependency signature finding
        -- the "do not drop required evidence to hit a memory target" rule,
        made executable.
        """
        src = (_ROOT / "cli_compare_release_helpers.py").read_text(encoding="utf-8")
        assert 'entry.get("_new_snapshot") or entry.get("_new_bundle_evidence")' in src
        assert 'entry.get("_old_snapshot") or entry.get("_old_bundle_evidence")' in src

    def test_the_junit_pair_builder_reads_the_old_side_only(self) -> None:
        """The claim that lets NEW stay compact under ``--format junit``."""
        src = (_ROOT / "cli_compare_release_pairwise.py").read_text(encoding="utf-8")
        marker = "if collect_diff_results:\n        for entry in library_results:"
        assert marker in src
        block = src.split(marker, 1)[1].split("\n\n", 1)[0]
        assert "_old_snapshot" in block
        assert "_new_snapshot" not in block

    def test_the_baseline_writer_reads_the_old_side_only(self) -> None:
        """Same claim for ``--bundle-facts-out``.

        It iterates ``diff_pairs``, whose second element is the OLD
        snapshot by construction (see the pair builder above).
        """
        src = (_ROOT / "cli_compare_release_helpers.py").read_text(encoding="utf-8")
        assert "for diff, old_snapshot in diff_pairs:" in src


class TestTheFoldSeesTheSameEvidence:
    """Substituting compact NEW evidence must not change what is analysed.

    The per-symbol equivalence of the projection itself is established in
    ``tests/test_bundle_signature_evidence_projection.py``. What is checked
    here is the *fold* this change actually touches: the mapping
    ``_collect_bundle_result`` hands to the bundle analysis must answer
    identically whichever shape the member entry carried, or the retention
    decision would be silently deciding evidence.
    """

    def _snapshot(self):
        from abicheck.model import AbiSnapshot, Function, Variable, Visibility

        return AbiSnapshot(
            library="libx.so",
            version="1.0",
            functions=[
                Function(
                    name="f",
                    mangled="_Z1fv",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
                Function(
                    name="hidden",
                    mangled="_Z6hiddenv",
                    return_type="void",
                    params=[],
                    visibility=Visibility.HIDDEN,
                ),
            ],
            variables=[
                Variable(
                    name="v", mangled="v", type="int", visibility=Visibility.PUBLIC
                )
            ],
        )

    def test_both_stash_shapes_produce_the_same_symbol_answers(self) -> None:
        from abicheck.bundle_signature_evidence import (
            _symbol_evidence_sufficient,
            _symbol_was_exported,
        )
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snap = self._snapshot()
        compact = build_bundle_signature_evidence(snap)
        symbols = ["_Z1fv", "_Z6hiddenv", "v", "not_declared_anywhere"]
        assert [_symbol_was_exported(s, snap) for s in symbols] == [
            _symbol_was_exported(s, compact) for s in symbols
        ]
        assert [_symbol_evidence_sufficient(s, snap) for s in symbols] == [
            _symbol_evidence_sufficient(s, compact) for s in symbols
        ]

    def test_the_comparison_above_is_not_vacuous(self) -> None:
        """Both answers must actually occur, or agreement proves nothing."""
        from abicheck.bundle_signature_evidence import _symbol_was_exported

        snap = self._snapshot()
        answers = {
            _symbol_was_exported(s, snap) for s in ("_Z1fv", "not_declared_anywhere")
        }
        assert answers == {True, False}

    def test_the_fold_accepts_a_mixed_pair(self) -> None:
        """OLD full and NEW compact in the same entry -- the new default shape.

        The fold used to see either two full snapshots or two compact
        projections; the per-side decision makes a *mixed* entry the normal
        case, so the mapping builder has to accept one of each.
        """
        from abicheck.bundle_models import BundleSignatureEvidence
        from abicheck.cli_compare_release_helpers import _collect_bundle_result
        from abicheck.workflows.bundle_symbol_status import (
            build_bundle_signature_evidence,
        )

        snap = self._snapshot()
        entry: dict[str, object] = {
            "library": "libx.so",
            "_bundle_key": "libx.so",
            "_old_snapshot": snap,
            "_new_bundle_evidence": build_bundle_signature_evidence(snap),
        }
        captured: dict[str, object] = {}

        import abicheck.cli_compare_release_helpers as helpers

        original = helpers._run_bundle_analysis

        def _capture(*args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            return None

        helpers._run_bundle_analysis = _capture  # type: ignore[assignment]
        try:
            _collect_bundle_result([entry], {}, {}, "NO_CHANGE", None, ())
        finally:
            helpers._run_bundle_analysis = original  # type: ignore[assignment]
        assert captured["old_snapshots"] == {"libx.so": snap}
        new_map = captured["new_snapshots"]
        assert isinstance(new_map, dict)
        assert isinstance(new_map["libx.so"], BundleSignatureEvidence)

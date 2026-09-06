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

"""ADR-066 S1: offline longitudinal compatibility history
(``abicheck.workflows.history``).

Covers the ADR's own "Mandatory tests (contract)" list for this slice: a
three-release add/deprecate/remove sequence, a missing intermediate release,
a symbol removed and reintroduced, an unknown/first-observed distinction for
the earliest snapshot, and non-SemVer labels reporting no gap verdict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot, Function, RecordType, Variable
from abicheck.model.identity import entity_id_for_function
from abicheck.serialization import save_snapshot
from abicheck.workflows.history import (
    HistoryError,
    build_longitudinal_history,
    run_history_request,
)


def _fn(name: str, *, deprecated: str | None = None) -> Function:
    kwargs: dict[str, object] = {}
    if deprecated is not None:
        kwargs["deprecated"] = deprecated
    return Function(name=name, mangled=name, return_type="int", **kwargs)


def _save(
    tmp_path: Path,
    version: str,
    functions: list[Function],
    *,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    library: str = "libmath.so",
    from_headers: bool = True,
    ast_producer: str = "castxml",
) -> str:
    snap = AbiSnapshot(
        library=library,
        version=version,
        functions=functions,
        variables=variables or [],
        types=types or [],
        from_headers=from_headers,
        ast_producer=ast_producer,
    )
    out = tmp_path / f"{version.replace('/', '_')}.json"
    save_snapshot(snap, out)
    return str(out)


class TestBasicLifecycle:
    def test_add_deprecate_remove_across_three_releases(self, tmp_path: Path) -> None:
        add = _fn("add")
        mul = _fn("multiply")
        mul_deprecated = _fn("multiply", deprecated="use scale() instead")

        p1 = _save(tmp_path, "1.0.0", [add, mul])
        p2 = _save(tmp_path, "1.1.0", [add, mul_deprecated])
        p3 = _save(tmp_path, "1.2.0", [add])

        result = run_history_request([p1, p2, p3])

        by_key: dict[str, list[str]] = {}
        for ev in result.events:
            by_key.setdefault(ev.entity_key, []).append(ev.event)

        add_key = next(k for k in by_key if k.endswith(":add"))
        mul_key = next(k for k in by_key if k.endswith(":multiply"))

        assert by_key[add_key] == ["first_observed"]
        assert by_key[mul_key] == ["first_observed", "deprecated", "removed"]

    def test_first_observed_versus_introduced(self, tmp_path: Path) -> None:
        """An API present at the very first supplied snapshot is
        `first_observed`, never a claimed `introduced` -- ADR-066 D2:
        its true introduction point may predate this history."""
        existing = _fn("legacy_call")
        new_api = _fn("brand_new_call")

        p1 = _save(tmp_path, "1.0.0", [existing])
        p2 = _save(tmp_path, "2.0.0", [existing, new_api])

        result = run_history_request([p1, p2])
        events_by_name = {e.display_name: e.event for e in result.events}

        assert events_by_name["legacy_call"] == "first_observed"
        assert events_by_name["brand_new_call"] == "introduced"

    def test_removed_and_reintroduced(self, tmp_path: Path) -> None:
        keep = _fn("keep")
        flappy = _fn("flappy")

        p1 = _save(tmp_path, "1.0.0", [keep, flappy])
        p2 = _save(tmp_path, "1.1.0", [keep])  # flappy removed
        p3 = _save(tmp_path, "1.2.0", [keep, flappy])  # flappy back

        result = run_history_request([p1, p2, p3])
        flappy_events = [
            (e.version, e.event) for e in result.events if e.display_name == "flappy"
        ]
        assert flappy_events == [
            ("1.0.0", "first_observed"),
            ("1.1.0", "removed"),
            ("1.2.0", "reintroduced"),
        ]

    def test_entity_id_backed_correspondence(self, tmp_path: Path) -> None:
        """When the pairwise compare engine attaches a resolved ``EntityId``
        to a finding (ADR-063 Phase 2 header-AST identity), the
        correspondence key uses it directly rather than falling back to
        ``(entity_kind, symbol)`` -- see the module's own "Correspondence
        key" bullet."""
        eid = entity_id_for_function((), "widget_maker", mangled_name="widget_maker")
        made = Function(
            name="widget_maker",
            mangled="widget_maker",
            return_type="int",
            entity_id=eid,
        )

        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "2.0.0", [_fn("add"), made])

        result = run_history_request([p1, p2])
        event = next(e for e in result.events if e.display_name == "widget_maker")
        assert event.event == "introduced"
        assert event.entity_key == f"entity:function:{eid.key}"

    def test_un_deprecation_clears_the_flag_for_a_later_re_deprecation(
        self, tmp_path: Path
    ) -> None:
        """D2 has no "un-deprecated" event word -- clearing the running flag
        is silent -- but the flag really must clear, or a later
        re-deprecation of the same entity would be silently dropped."""
        plain = _fn("scale")
        deprecated = _fn("scale", deprecated="use resize() instead")

        p1 = _save(tmp_path, "1.0.0", [plain])
        p2 = _save(tmp_path, "1.1.0", [deprecated])
        p3 = _save(tmp_path, "1.2.0", [plain])  # un-deprecated, still present
        p4 = _save(tmp_path, "1.3.0", [deprecated])  # re-deprecated

        result = run_history_request([p1, p2, p3, p4])
        scale_events = [
            (e.version, e.event) for e in result.events if e.display_name == "scale"
        ]
        # No "un-deprecated" event at 1.2.0 (not in D2's vocabulary), but the
        # flag clearing is proven by 1.3.0 reporting "deprecated" again
        # rather than being silently suppressed as already-deprecated.
        assert scale_events == [
            ("1.0.0", "first_observed"),
            ("1.1.0", "deprecated"),
            ("1.3.0", "deprecated"),
        ]

    def test_variables_and_types_are_tracked_too(self, tmp_path: Path) -> None:
        # RecordType must be reachable from a public function/variable to be
        # considered part of the ABI surface at all (diff_types's own
        # directly-referenced filter) -- an orphan type is correctly never
        # compared, so give Widget a real consumer on both sides.
        var_old = Variable(name="g_counter", mangled="g_counter", type="int")
        rec_old = RecordType(name="Widget", kind="struct")
        uses_widget_v1 = Function(
            name="make_widget", mangled="make_widget", return_type="Widget"
        )
        uses_widget_v2 = Function(
            name="make_widget", mangled="make_widget", return_type="int"
        )

        p1 = _save(
            tmp_path,
            "1.0.0",
            [_fn("add"), uses_widget_v1],
            variables=[var_old],
            types=[rec_old],
        )
        p2 = _save(
            tmp_path,
            "2.0.0",
            [_fn("add"), uses_widget_v2],
            variables=[],
            types=[],
        )

        result = run_history_request([p1, p2])
        kinds_by_name = {e.display_name: e.entity_kind for e in result.events}
        assert kinds_by_name["g_counter"] == "variable"
        assert kinds_by_name["Widget"] == "type"
        removed_names = {e.display_name for e in result.events if e.event == "removed"}
        assert removed_names == {"g_counter", "Widget"}


class TestCoverageGaps:
    def test_missing_intermediate_semver_release_is_flagged(
        self, tmp_path: Path
    ) -> None:
        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "1.3.0", [_fn("add")])  # skips 1.1.0/1.2.0

        result = run_history_request([p1, p2])
        assert len(result.gaps) == 1
        gap = result.gaps[0]
        assert gap.from_version == "1.0.0"
        assert gap.to_version == "1.3.0"
        assert gap.kind == "unknown_interval"

    def test_consecutive_semver_releases_report_no_gap(self, tmp_path: Path) -> None:
        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "1.1.0", [_fn("add")])
        p3 = _save(tmp_path, "1.1.1", [_fn("add")])
        p4 = _save(tmp_path, "2.0.0", [_fn("add")])

        result = run_history_request([p1, p2, p3, p4])
        assert result.gaps == ()

    def test_non_semver_labels_report_no_gap_verdict(self, tmp_path: Path) -> None:
        """No project-declared version scheme yet (D4/S2) -- an opaque label
        pair must never be silently read as either "contiguous" or "gap"."""
        p1 = _save(tmp_path, "codename-alpha", [_fn("add")])
        p2 = _save(tmp_path, "codename-zeta", [_fn("add")])

        result = run_history_request([p1, p2])
        assert result.gaps == ()

    def test_identical_semver_labels_report_no_gap(self, tmp_path: Path) -> None:
        """Two entries sharing one SemVer label (a re-tagged/re-dumped
        snapshot of the same release) is neither a "gap" nor an ordering
        disagreement -- ``a == b`` is its own no-verdict case, distinct from
        both the successor check and the descending-order check below."""
        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "1.0.0", [_fn("add"), _fn("multiply")])

        result = run_history_request([p1, p2])
        assert result.gaps == ()

    def test_descending_semver_order_is_flagged_as_a_gap(self, tmp_path: Path) -> None:
        """D1: history order is never inferred from labels -- if the
        caller's supplied order and the labels' own SemVer order disagree,
        that is reported as a gap (not silently accepted, and not an
        error abicheck second-guesses the caller's stated order over)."""
        p1 = _save(tmp_path, "2.0.0", [_fn("add")])
        p2 = _save(tmp_path, "1.0.0", [_fn("add")])

        result = run_history_request([p1, p2])
        assert len(result.gaps) == 1
        gap = result.gaps[0]
        assert gap.from_version == "2.0.0"
        assert gap.to_version == "1.0.0"
        assert "does not sort after" in gap.detail


class TestUsageErrors:
    def test_empty_snapshot_list_is_a_history_error(self) -> None:
        with pytest.raises(HistoryError):
            run_history_request([])

    def test_mismatched_version_count_is_a_history_error(self, tmp_path: Path) -> None:
        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "2.0.0", [_fn("add")])
        with pytest.raises(HistoryError):
            run_history_request([p1, p2], versions=["only-one"])

    def test_build_longitudinal_history_rejects_empty_entries(self) -> None:
        with pytest.raises(HistoryError):
            build_longitudinal_history([])


class TestExplicitVersionLabels:
    def test_explicit_versions_override_snapshot_version_field(
        self, tmp_path: Path
    ) -> None:
        # Snapshot's own .version differs from the caller-supplied label --
        # the explicit label wins (e.g. re-labeling a dev snapshot with its
        # real release tag).
        p1 = _save(tmp_path, "dev-snapshot-1", [_fn("add")])
        p2 = _save(tmp_path, "dev-snapshot-2", [_fn("add"), _fn("multiply")])

        result = run_history_request([p1, p2], versions=["1.0.0", "1.1.0"])
        assert [e.version for e in result.entries] == ["1.0.0", "1.1.0"]
        assert result.pairwise[0].from_version == "1.0.0"
        assert result.pairwise[0].to_version == "1.1.0"


class TestPairwiseTransparency:
    def test_pairwise_summaries_reflect_underlying_compare_calls(
        self, tmp_path: Path
    ) -> None:
        p1 = _save(tmp_path, "1.0.0", [_fn("add"), _fn("subtract")])
        p2 = _save(tmp_path, "2.0.0", [_fn("add")])

        result = run_history_request([p1, p2])
        assert len(result.pairwise) == 1
        summary = result.pairwise[0]
        assert summary.verdict == "BREAKING"
        assert summary.change_count == 1

    def test_to_dict_round_trips_json_serializable(self, tmp_path: Path) -> None:
        import json

        p1 = _save(tmp_path, "1.0.0", [_fn("add")])
        p2 = _save(tmp_path, "2.0.0", [_fn("add"), _fn("multiply")])

        result = run_history_request([p1, p2])
        doc = json.loads(json.dumps(result.to_dict()))
        assert doc["schema"] == "abicheck.longitudinal-history/v1"
        assert doc["library"] == "libmath.so"
        assert len(doc["entries"]) == 2

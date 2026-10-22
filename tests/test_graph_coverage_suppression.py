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

"""``GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION`` (impact-analysis-layer P0,
the ``unreachable-only`` decision in ``docs/contribute/plans/g29-impact-analysis-layer.md``).

The default ``unreachable-only`` gate reads only ``Change.public_reachable``,
so a change graph coverage never analysed (``reachability_state`` UNKNOWN) and
one it proved unreachable both pass it. The decision keeps that suppression
semantics unchanged, so existing policy files keep working. What changes is
visibility: when the rule hid a change whose reachability was never
established, the disposition records a diagnostic naming the rule. A
proven-unreachable suppression records nothing new.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compare.finding_identity import report_canonical_finding_id
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.evidence_status import ReachabilityState
from abicheck.policy.finding_disposition import dispose_by_suppression
from abicheck.policy.suppression.rules import Suppression, SuppressionList
from abicheck.serialization import snapshot_to_json

_GRAPH = ChangeKind.GRAPH_COVERAGE_INSUFFICIENT_FOR_SUPPRESSION
_UNKNOWN_SIBLING = ChangeKind.SUPPRESSION_REACHABILITY_UNKNOWN


def _removed(
    state: ReachabilityState,
    *,
    public: bool = False,
    symbol: str = "ns::detail::helper",
) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description="removed",
        public_reachable=public,
        reachability_state=state,
    )


def _dispose(changes: list[Change], rules: list[Suppression]):
    return dispose_by_suppression(
        changes,
        SuppressionList(rules),
        ledger=None,
        application_point="test",
    )


def _graph_diagnostics(outcome) -> list[Change]:
    return [c for c in outcome.diagnostics if c.kind == _GRAPH]


class TestUnreachableOnlyDiagnostic:
    def test_unknown_reachability_is_suppressed_and_diagnosed(self) -> None:
        change = _removed(ReachabilityState.UNKNOWN)
        rule = Suppression(namespace="ns::detail::*", reason="internal")

        outcome = _dispose([change], [rule])

        # The suppression itself is unchanged: the change is hidden, and the
        # disposition still records it as suppressed.
        assert outcome.suppressed == [change]
        assert outcome.kept == []
        assert change.suppression_rule == "internal"
        [record] = _graph_diagnostics(outcome)
        assert record.symbol == "ns::detail::*"
        assert record.description.startswith(
            "1 suppressed by rule 'ns::detail::*' without graph coverage"
        )
        assert report_canonical_finding_id(change) in record.description
        assert record in outcome.visible

    def test_proven_unreachable_is_suppressed_with_no_new_diagnostic(self) -> None:
        change = _removed(ReachabilityState.PROVEN_UNREACHABLE)
        rule = Suppression(namespace="ns::detail::*", reason="internal")

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == [change]
        assert outcome.diagnostics == []

    def test_reachable_change_is_not_suppressed(self) -> None:
        change = _removed(ReachabilityState.PROVEN_REACHABLE, public=True)
        rule = Suppression(namespace="ns::detail::*", reason="internal")

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == []
        assert change in outcome.kept
        assert _graph_diagnostics(outcome) == []

    @pytest.mark.parametrize(
        ("state", "public", "suppressed", "diagnosed"),
        [
            (ReachabilityState.UNKNOWN, False, True, True),
            (ReachabilityState.PROVEN_UNREACHABLE, False, True, False),
            (ReachabilityState.PROVEN_REACHABLE, True, False, False),
            # Public-reachable with an UNKNOWN state never reaches the gate
            # as suppressible: the boolean the default gate reads is True.
            (ReachabilityState.UNKNOWN, True, False, False),
        ],
    )
    def test_suppression_and_diagnostic_follow_state_and_visibility(
        self,
        state: ReachabilityState,
        public: bool,
        suppressed: bool,
        diagnosed: bool,
    ) -> None:
        change = _removed(state, public=public)
        rule = Suppression(namespace="ns::detail::*", reason="internal")

        outcome = _dispose([change], [rule])

        assert (change in outcome.suppressed) is suppressed
        assert bool(_graph_diagnostics(outcome)) is diagnosed
        # Conservation: the change lands in exactly one of suppressed/kept.
        assert (change in outcome.suppressed) != (change in outcome.kept)

    def test_one_record_per_rule_carries_count_and_change_ids(self) -> None:
        changes = [
            _removed(ReachabilityState.UNKNOWN, symbol=f"ns::detail::f{i}")
            for i in range(3)
        ]
        rule = Suppression(namespace="ns::detail::*", reason="internal")

        outcome = _dispose(changes, [rule])

        assert len(outcome.suppressed) == 3
        [record] = _graph_diagnostics(outcome)
        assert record.description.startswith("3 suppressed by rule 'ns::detail::*'")
        for change in changes:
            assert report_canonical_finding_id(change) in record.description

    def test_two_rules_each_get_their_own_record(self) -> None:
        changes = [
            _removed(ReachabilityState.UNKNOWN, symbol="ns::detail::a"),
            _removed(ReachabilityState.UNKNOWN, symbol="ns::other::b"),
        ]
        rules = [
            Suppression(namespace="ns::detail::*", reason="detail"),
            Suppression(namespace="ns::other::*", reason="other"),
        ]

        outcome = _dispose(changes, rules)

        records = _graph_diagnostics(outcome)
        assert sorted(r.symbol for r in records) == ["ns::detail::*", "ns::other::*"]
        assert all(r.description.startswith("1 suppressed by rule") for r in records)

    def test_selector_label_names_finding_id_rule_not_bare_fallback(self) -> None:
        from abicheck.compare.finding_identity import report_canonical_finding_id

        change = _removed(ReachabilityState.UNKNOWN)
        finding_id = report_canonical_finding_id(change)
        rule = Suppression(
            finding_id=finding_id,
            reachability="unreachable-only",
            reason="pinned",
        )

        outcome = _dispose([change], [rule])

        [diag] = _graph_diagnostics(outcome)
        assert finding_id in diag.description
        assert "'?'" not in diag.description

    def test_any_gate_never_asks_for_coverage(self) -> None:
        change = _removed(ReachabilityState.UNKNOWN)
        rule = Suppression(
            namespace="ns::detail::*", reachability="any", reason="no gate"
        )

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == [change]
        assert _graph_diagnostics(outcome) == []


class TestProvenUnreachableOnlyModeUnchanged:
    """The ``proven-unreachable-only`` gate keeps its own diagnostic and
    semantics: the new kind must not double-report what it already covers."""

    def test_unknown_is_withheld_with_the_existing_diagnostic_only(self) -> None:
        change = _removed(ReachabilityState.UNKNOWN)
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            reason="wants proof",
        )

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == []
        assert change in outcome.kept
        kinds = [d.kind for d in outcome.diagnostics]
        assert kinds == [_UNKNOWN_SIBLING]
        assert _graph_diagnostics(outcome) == []

    def test_proven_unreachable_is_suppressed_with_no_diagnostic(self) -> None:
        change = _removed(ReachabilityState.PROVEN_UNREACHABLE)
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            reason="wants proof",
        )

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == [change]
        assert outcome.diagnostics == []

    def test_allow_unknown_reachability_suppresses_without_diagnostic(self) -> None:
        change = _removed(ReachabilityState.UNKNOWN)
        rule = Suppression(
            namespace="ns::detail::*",
            reachability="proven-unreachable-only",
            allow_unknown_reachability=True,
            reason="reviewed",
        )

        outcome = _dispose([change], [rule])

        assert outcome.suppressed == [change]
        assert outcome.diagnostics == []


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write_pair(tmp_path: Path, internal_count: int = 1) -> tuple[Path, Path]:
    """A public API that survives and an internal helper that is removed.

    With no call graph the removal's reachability is never established, so it
    is UNKNOWN: the scenario the ``unreachable-only`` diagnostic exists for.
    """
    internal = [
        _fn(f"ns::detail::helper{i}", f"_ZN2ns6detail7helper{i}Ev")
        for i in range(internal_count)
    ]
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), *internal],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av")],
        from_headers=True,
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _compare(
    tmp_path: Path, suppress_yaml: str | None, *extra: str, internal_count: int = 1
):
    old_p, new_p = _write_pair(tmp_path, internal_count)
    args = ["compare", str(old_p), str(new_p), "-o", "json=-", *extra]
    if suppress_yaml is not None:
        suppress = tmp_path / "suppress.yml"
        suppress.write_text(suppress_yaml, encoding="utf-8")
        args[3:3] = ["--suppress", str(suppress)]
    return CliRunner().invoke(main, args)


_DEFAULT_GATE = (
    "version: 1\n"
    "suppressions:\n"
    "  - namespace: 'ns::detail::*'\n"
    "    reason: internal helper\n"
)

_PROVEN_WITH_ALLOW = (
    "version: 1\n"
    "suppressions:\n"
    "  - namespace: 'ns::detail::*'\n"
    "    reachability: proven-unreachable-only\n"
    "    allow_unknown_reachability: true\n"
    "    reason: reviewed\n"
)


class TestEndToEndCli:
    def test_default_gate_suppresses_visibly_and_exit_code_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        # Same suppression, two spellings: the default gate records the new
        # diagnostic, the explicitly-acknowledged gate records nothing extra.
        # Only the diagnostic may differ, so the exit code must match.
        default = _compare(tmp_path / "a", _DEFAULT_GATE)
        acknowledged = _compare(tmp_path / "b", _PROVEN_WITH_ALLOW)

        assert default.exit_code == acknowledged.exit_code == 0
        default_kinds = {c["kind"] for c in json.loads(default.stdout)["changes"]}
        acked_kinds = {c["kind"] for c in json.loads(acknowledged.stdout)["changes"]}
        assert "graph_coverage_insufficient_for_suppression" in default_kinds
        assert "graph_coverage_insufficient_for_suppression" not in acked_kinds

    def test_informational_record_leaves_verdict_and_exit_unchanged(
        self, tmp_path: Path
    ) -> None:
        # The record is informational (COMPATIBLE): a binary-only run with a
        # default unreachable-only rule keeps its verdict and its exit code.
        result = _compare(tmp_path, _DEFAULT_GATE)

        payload = json.loads(result.stdout)
        assert payload["verdict"] == "COMPATIBLE"
        assert result.exit_code == 0

    def test_fifty_suppressions_render_as_one_record_with_count_fifty(
        self, tmp_path: Path
    ) -> None:
        result = _compare(tmp_path, _DEFAULT_GATE, internal_count=50)

        payload = json.loads(result.stdout)
        records = [
            c
            for c in payload["changes"]
            if c["kind"] == "graph_coverage_insufficient_for_suppression"
        ]
        assert len(records) == 1
        assert records[0]["description"].startswith(
            "50 suppressed by rule 'ns::detail::*' without graph coverage"
        )
        assert payload["verdict"] == "COMPATIBLE"
        assert result.exit_code == 0

    def test_without_a_suppression_rule_no_diagnostic_and_breaking_exit(
        self, tmp_path: Path
    ) -> None:
        result = _compare(tmp_path, None)

        assert result.exit_code == 4
        kinds = {c["kind"] for c in json.loads(result.stdout)["changes"]}
        assert "graph_coverage_insufficient_for_suppression" not in kinds

    @pytest.mark.parametrize("fmt", ["markdown", "html", "sarif"])
    def test_diagnostic_renders_in_each_format_the_sibling_renders_in(
        self, tmp_path: Path, fmt: str
    ) -> None:
        out = tmp_path / f"report.{fmt}"
        result = _compare(tmp_path, _DEFAULT_GATE, "-o", f"{fmt}={out}")

        assert result.exit_code == 0, result.output
        assert "graph_coverage_insufficient_for_suppression" in out.read_text(
            encoding="utf-8"
        )

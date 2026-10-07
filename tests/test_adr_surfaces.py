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

"""ADR-076 traceability gate (``scripts/check_adr_surfaces.py``).

Each class exercises one rule against synthetic registries, so a rule is
proven to *fail* on the shape it guards against, not only to pass on the
committed tree; ``TestCommittedTree`` then runs the real gate once.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_adr_surfaces", _ROOT / "scripts" / "check_adr_surfaces.py"
)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules["check_adr_surfaces"] = gate
_spec.loader.exec_module(gate)


@pytest.fixture(scope="module")
def cli() -> Any:
    return gate.CliTree.from_click()


def _model(
    adrs: list[dict[str, Any]],
    *,
    use_cases: list[dict[str, Any]] | None = None,
    scenarios: list[dict[str, Any]] | None = None,
    statuses: dict[str, str] | None = None,
) -> Any:
    files = [a["file"] for a in adrs]
    return gate.Model(
        adrs=adrs,
        use_cases=use_cases
        if use_cases is not None
        else [{"id": "UC-WF-x", "user_task": ["pr_review"], "adrs": []}],
        scenarios=scenarios or [],
        statuses=statuses
        if statuses is not None
        else {f: "Proposed — not implemented" for f in files},
        files=files,
    )


def _adr(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "ADR-900",
        "file": "900-synthetic.md",
        "disposition": "surfaced",
        "use_cases": [],
        "surfaces": [{"cli": "compare"}],
    }
    base.update(kw)
    return base


class TestStatusClass:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("Accepted — implemented", "implemented"),
            ("Accepted — implemented. Adds `-D`.", "implemented"),
            ("Accepted — S1, S2 and S3 implemented (x); S4 not implemented", "open"),
            ("Accepted — partially implemented", "open"),
            ("Accepted — substantially implemented", "open"),
            ("Accepted — not yet implemented", "open"),
            ("Proposed", "open"),
            ("Deprecated — Retired: removed", "retired"),
            ("Superseded by ADR-043 D4", "retired"),
        ],
    )
    def test_classification(self, status: str, expected: str) -> None:
        assert gate.status_class(status) == expected


class TestRegistryRules:
    def test_valid_entry_passes(self, cli: Any) -> None:
        assert gate.check_registry(_model([_adr()]), gate.Resolver(cli)) == []

    @pytest.mark.parametrize(
        ("override", "needle"),
        [
            ({"disposition": "partial"}, "needs `missing:`"),
            ({"disposition": "gap", "surfaces": [], "missing": ""}, "needs `missing:`"),
            ({"disposition": "internal", "surfaces": []}, "needs `reason:`"),
            ({"disposition": "internal", "reason": "x"}, "must not list surfaces"),
            ({"disposition": "surfaced", "surfaces": []}, "needs at least one surface"),
            ({"disposition": "bogus"}, "not one of"),
            ({"use_cases": ["UC-NOPE-x"]}, "not in usecase-registry"),
        ],
    )
    def test_entry_shape_violations(
        self, cli: Any, override: dict[str, Any], needle: str
    ) -> None:
        errs = gate.check_registry(_model([_adr(**override)]), gate.Resolver(cli))
        assert any(needle in e for e in errs), errs

    def test_missing_adr_entry_fails(self) -> None:
        model = _model([_adr()])
        model.files.append("901-unregistered.md")
        assert any(
            "901-unregistered.md has no entry" in e
            for e in gate.check_registry(model, None)
        )

    def test_implemented_adr_cannot_be_gap(self) -> None:
        entry = _adr(disposition="gap", surfaces=[], missing="nothing")
        model = _model([entry], statuses={entry["file"]: "Accepted — implemented"})
        assert any("reads implemented" in e for e in gate.check_registry(model, None))

    def test_retired_adr_must_be_internal(self) -> None:
        entry = _adr()
        model = _model([entry], statuses={entry["file"]: "Deprecated — removed"})
        assert any("must be internal" in e for e in gate.check_registry(model, None))


class TestSurfaceResolution:
    @pytest.mark.parametrize(
        "surface",
        [
            {"cli": "compare --used-by"},
            {"cli": "compare -o"},
            {"cli": "deps tree"},
            {"cli": "project history --policy"},
            {"api": "abicheck.checker.compare"},
            {"api": "abicheck.storage"},
            {"action": "pr-comment"},
            {"action": "actions/report"},
            {"report": "compare_report.verdict"},
            {"report": "format:sarif"},
        ],
    )
    def test_real_surfaces_resolve(self, cli: Any, surface: dict[str, str]) -> None:
        assert gate.Resolver(cli).check(surface) == []

    @pytest.mark.parametrize(
        "surface",
        [
            {"cli": "nosuchcommand"},
            {"cli": "compare --no-such-flag"},
            {"cli": "deps nosuchsub"},
            {"api": "abicheck.checker.no_such_symbol"},
            {"api": "abicheck_nonexistent_pkg.x"},
            {"action": "no-such-input"},
            {"action": "actions/no-such-action"},
            {"action": "actions/report:no-such-input"},
            {"report": "compare_report.no_such_field"},
            {"report": "no_such_schema.verdict"},
            {"report": "format:pdf"},
            {"weird": "x"},
            {"cli": ""},
        ],
    )
    def test_fabricated_surfaces_fail(self, cli: Any, surface: dict[str, str]) -> None:
        assert gate.Resolver(cli).check(surface) != []


class TestUseCaseMirror:
    def test_mirror_must_match(self) -> None:
        entry = _adr(use_cases=["UC-WF-x"])
        ok = _model(
            [entry],
            use_cases=[{"id": "UC-WF-x", "user_task": ["audit"], "adrs": ["ADR-900"]}],
        )
        assert gate.check_use_case_mirror(ok) == []
        stale = _model(
            [entry], use_cases=[{"id": "UC-WF-x", "user_task": ["audit"], "adrs": []}]
        )
        assert any("adrs [] !=" in e for e in gate.check_use_case_mirror(stale))

    @pytest.mark.parametrize("tasks", [None, [], ["ship_it"], "audit"])
    def test_user_task_vocabulary(self, tasks: Any) -> None:
        model = _model(
            [_adr()], use_cases=[{"id": "UC-WF-x", "user_task": tasks, "adrs": []}]
        )
        assert any("user_task" in e for e in gate.check_use_case_mirror(model))


class TestScenarioFlowVerification:
    @pytest.mark.parametrize(
        ("flow", "surface", "ok"),
        [
            ("abicheck compare a b --suppress s.yaml", "compare --suppress", True),
            ("abicheck compare a b -o sarif=x", "compare --output", True),
            ("abicheck compare a b --output=json=-", "compare -o", True),
            (
                "abicheck compare a b   # comment --suppress",
                "compare --suppress",
                False,
            ),
            ("abicheck compare a b", "compare --suppress", False),
            ("abicheck dump lib.so --suppress x", "compare --suppress", False),
            ("abicheck deps compare a --old-root o --new-root n", "deps compare", True),
            ("abicheck deps tree ./app", "deps compare", False),
        ],
    )
    def test_cli_claim_must_be_in_flow(
        self, cli: Any, flow: str, surface: str, ok: bool
    ) -> None:
        model = _model(
            [_adr()],
            scenarios=[{"id": "SC-X", "flow": [flow], "surfaces": [{"cli": surface}]}],
        )
        assert (gate.check_scenarios(model, cli) == []) is ok


class TestTraceAndRatchet:
    def _snap(self, model: Any, cli: Any) -> dict[str, Any]:
        return gate.snapshot(model, gate.compute_trace(model, cli))

    def test_scenario_superset_traces_surface(self, cli: Any) -> None:
        model = _model(
            [_adr(surfaces=[{"cli": "compare --suppress"}])],
            scenarios=[
                {"id": "SC-X", "surfaces": [{"cli": "compare --suppress --policy"}]}
            ],
        )
        assert gate.compute_trace(model, cli)["untraced_surfaces"] == []

    def test_multichannel_use_case_needs_family(self, cli: Any) -> None:
        entry = _adr(
            use_cases=["UC-WF-x"],
            surfaces=[
                {"cli": "compare"},
                {"api": "abicheck.checker.compare"},
                {"action": "policy"},
            ],
        )
        sc = {"id": "SC-X", "validates": "UC-WF-x"}
        assert gate.compute_trace(_model([entry], scenarios=[sc]), cli)[
            "multichannel_without_family"
        ] == ["UC-WF-x"]
        sc["family"] = "gate"
        assert (
            gate.compute_trace(_model([entry], scenarios=[sc]), cli)[
                "multichannel_without_family"
            ]
            == []
        )

    def test_unchanged_state_passes(self, cli: Any) -> None:
        snap = self._snap(_model([_adr()]), cli)
        assert gate.check_ratchet(snap, snap) == []

    @pytest.mark.parametrize(
        ("old", "new", "fails"),
        [
            ("gap", "partial", False),
            ("partial", "surfaced", False),
            ("gap", "surfaced", False),
            ("surfaced", "partial", True),
            ("partial", "gap", True),
            ("surfaced", "internal", True),
            ("internal", "surfaced", True),
        ],
    )
    def test_disposition_ladder(self, old: str, new: str, fails: bool) -> None:
        def snap(d: str) -> dict[str, Any]:
            return {
                "adrs": {"ADR-900": {"disposition": d, "surfaces": [], "use_cases": []}}
            }

        assert bool(gate.check_ratchet(snap(new), snap(old))) is fails

    def test_lost_surface_use_case_and_entry_fail(self, cli: Any) -> None:
        base = self._snap(
            _model(
                [
                    _adr(
                        use_cases=["UC-WF-x"],
                        surfaces=[{"cli": "compare"}, {"cli": "dump"}],
                    )
                ]
            ),
            cli,
        )
        cur = copy.deepcopy(base)
        cur["adrs"]["ADR-900"]["surfaces"] = ["cli: compare"]
        cur["adrs"]["ADR-900"]["use_cases"] = []
        errs = gate.check_ratchet(cur, base)
        assert any("lost surface `cli: dump`" in e for e in errs)
        assert any("lost use case UC-WF-x" in e for e in errs)
        cur["adrs"] = {}
        assert any("disappeared" in e for e in gate.check_ratchet(cur, base))

    def test_new_untraced_surface_fails_but_traced_shrink_passes(
        self, cli: Any
    ) -> None:
        base = {
            "adrs": {},
            "untraced_surfaces": ["ADR-900 cli: compare"],
            "multichannel_without_family": [],
        }
        grown = dict(
            base, untraced_surfaces=["ADR-900 cli: compare", "ADR-900 cli: dump"]
        )
        assert any("cli: dump" in e for e in gate.check_ratchet(grown, base))
        shrunk = dict(base, untraced_surfaces=[])
        assert gate.check_ratchet(shrunk, base) == []


class TestReport:
    def test_report_lists_every_adr_and_is_marked_generated(self, cli: Any) -> None:
        model = _model(
            [
                _adr(),
                _adr(
                    id="ADR-901",
                    file="901-b.md",
                    disposition="internal",
                    surfaces=[],
                    reason="r",
                ),
            ]
        )
        text = gate.render_report(model, gate.compute_trace(model, cli))
        assert "generated by scripts/check_adr_surfaces.py" in text
        assert "[ADR-900]" in text and "[ADR-901]" in text
        assert "| internal | 1 |" in text and "| surfaced | 1 |" in text


class TestCommittedTree:
    def test_gate_passes_on_committed_tree(self) -> None:
        assert gate.run() == []

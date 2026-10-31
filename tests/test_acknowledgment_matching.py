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

"""ADR-067 D5 matching: an acknowledgment accepts exactly the finding it names.

G2: a ``symbol`` record without a ``change_kind`` accepted every change on that
symbol. G3: a record keyed by a finding id must match one finding per run. An
unscoped record matches in every release, and an expired one must not keep
accepting -- both are reported, not silent.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import checker
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.cli import main
from abicheck.compare.finding_identity import report_canonical_finding_id
from abicheck.errors import PolicyError
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function, Param
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.serialization import save_snapshot
from abicheck.workflows.acknowledgment_inputs import resolve_compare_acknowledgments


def _snap(version: str, funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo",
        functions=funcs,
        variables=[],
        types=[],
        enums=[],
    )


def _bar_return_and_params_change() -> tuple[AbiSnapshot, AbiSnapshot]:
    """One symbol, two distinct findings: a return change and a parameter change."""
    old = _snap(
        "1.0.0",
        [
            Function(
                name="bar",
                mangled="bar",
                return_type="int",
                params=[Param(name="a", type="int")],
            )
        ],
    )
    new = _snap(
        "2.0.0",
        [
            Function(
                name="bar",
                mangled="bar",
                return_type="long",
                params=[Param(name="a", type="int"), Param(name="b", type="int")],
            )
        ],
    )
    return old, new


def test_symbol_record_without_change_kind_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "acks.yml"
    p.write_text("version: 1\nacknowledgments:\n  - symbol: bar\n    reason: x\n")
    with pytest.raises(PolicyError, match="change_kind"):
        AcknowledgmentList.load(p)


def test_finding_id_record_accepts_exactly_its_finding(tmp_path: Path) -> None:
    """G2: the return change is accepted; the parameter change on the same symbol is not."""
    old, new = _bar_return_and_params_change()
    probe = checker.compare(old, new)
    return_change = next(
        c for c in probe.changes if c.kind == ChangeKind.FUNC_RETURN_CHANGED
    )
    params_change = next(
        c for c in probe.changes if c.kind == ChangeKind.FUNC_PARAMS_CHANGED
    )
    fid = report_canonical_finding_id(return_change)
    assert fid != report_canonical_finding_id(params_change)

    p = tmp_path / "acks.yml"
    p.write_text(
        f"version: 1\nacknowledgments:\n  - finding_id: '{fid}'\n    reason: x\n"
    )
    result = checker.compare(old, new, acknowledgments=AcknowledgmentList.load(p))
    by_kind = {r.kind: r for r in result.disposition_ledger.records}
    assert by_kind[ChangeKind.FUNC_RETURN_CHANGED.value].acknowledged_by is not None
    assert by_kind[ChangeKind.FUNC_PARAMS_CHANGED.value].acknowledged_by is None


def test_symbol_and_kind_record_is_as_precise_as_a_finding_id(tmp_path: Path) -> None:
    old, new = _bar_return_and_params_change()
    p = tmp_path / "acks.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n  - symbol: bar\n"
        "    change_kind: func_return_changed\n    reason: x\n"
    )
    result = checker.compare(old, new, acknowledgments=AcknowledgmentList.load(p))
    by_kind = {r.kind: r for r in result.disposition_ledger.records}
    assert by_kind[ChangeKind.FUNC_RETURN_CHANGED.value].acknowledged_by is not None
    assert by_kind[ChangeKind.FUNC_PARAMS_CHANGED.value].acknowledged_by is None


def test_unscoped_record_warns_and_scoped_record_does_not(tmp_path: Path) -> None:
    p = tmp_path / "acks.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n"
        "  - symbol: bar\n    change_kind: func_added\n    reason: x\n"
        "  - symbol: baz\n    change_kind: func_added\n    candidate: '2.0.0'\n    reason: y\n"
    )
    warnings = AcknowledgmentList.load(p).scope_warnings(date(2026, 10, 20))
    assert len(warnings) == 1
    assert "bar[func_added]" in warnings[0]
    assert "names no component, baseline or candidate" in warnings[0]


def test_resolver_surfaces_the_scope_warning(tmp_path: Path) -> None:
    p = tmp_path / "acks.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n  - symbol: bar\n    change_kind: func_added\n    reason: x\n"
    )
    resolved = resolve_compare_acknowledgments(
        p, policy_file=None, configured_action=None
    )
    assert len(resolved.warnings) == 1
    assert "names no component, baseline or candidate" in resolved.warnings[0]


def test_expired_record_resurfaces_the_break_and_says_so(tmp_path: Path) -> None:
    """An expired record never matches: the break gates, and the lapse is reported."""
    old, new = _bar_return_and_params_change()
    p = tmp_path / "acks.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n  - symbol: bar\n"
        "    change_kind: func_return_changed\n    reason: x\n"
        "    expires: 2000-01-01\n"
    )
    acks = AcknowledgmentList.load(p)
    result = checker.compare(old, new, acknowledgments=acks)
    assert result.verdict == Verdict.BREAKING
    records = {r.kind: r for r in result.disposition_ledger.records}
    assert records[ChangeKind.FUNC_RETURN_CHANGED.value].acknowledged_by is None
    warnings = acks.scope_warnings(date(2026, 10, 20))
    assert any("expired on 2000-01-01" in w for w in warnings)


def test_expired_record_gates_and_warns_through_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old_snap = _snap(
        "1.0.0", [Function(name="gone", mangled="gone", return_type="void", params=[])]
    )
    new_snap = _snap("2.0.0", [])
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    save_snapshot(old_snap, old)
    save_snapshot(new_snap, new)
    (tmp_path / "acks.yml").write_text(
        "version: 1\nacknowledgments:\n  - symbol: gone\n"
        "    change_kind: func_removed\n    reason: x\n    expires: 2000-01-01\n"
    )
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text("acknowledgments:\n  file: acks.yml\n")
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg)]
    )
    assert res.exit_code == 4, res.output
    assert "Warning: acknowledgment" in res.output
    assert "expired on 2000-01-01" in res.output


def test_cli_warns_on_an_unscoped_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old_snap = _snap("1.0.0", [])
    new_snap = _snap(
        "2.0.0",
        [Function(name="added", mangled="added", return_type="void", params=[])],
    )
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    save_snapshot(old_snap, old)
    save_snapshot(new_snap, new)
    (tmp_path / "acks.yml").write_text(
        "version: 1\nacknowledgments:\n  - symbol: added\n"
        "    change_kind: func_added\n    reason: x\n"
    )
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text("acknowledgments:\n  file: acks.yml\n")
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg)]
    )
    assert "matches that finding in every release" in res.output


def test_record_ids_distinguish_change_kinds_on_one_symbol() -> None:
    """Two symbol records differing only by kind get two record ids (G2 bookkeeping)."""
    from abicheck.policy.acknowledgment import Acknowledgment

    a = Acknowledgment(symbol="bar", change_kind="func_added", reason="x")
    b = Acknowledgment(symbol="bar", change_kind="func_removed", reason="x")
    assert a.record_id() != b.record_id()
    assert a.record_id() == "*::*::*::bar[func_added]"

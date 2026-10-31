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

"""ADR-067 D5 ``acknowledgment.breaks``: an acknowledged break's exit contribution.

``gate`` (default) is what every run did before the setting existed: an
acknowledged break still gates. ``accept`` drops the acknowledged break -- and
only that break -- from the compatibility exit. The verdict, the report and the
release recommendation still read it as a break.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck import checker
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.frontends.cli.runtime import _exit_with_severity_or_verdict
from abicheck.model import AbiSnapshot
from abicheck.model.acknowledgment_policy import AcknowledgmentPolicy
from abicheck.model.declarations import Function
from abicheck.policy.acknowledgment import AcknowledgmentList
from abicheck.policy.acknowledgment_breaks import ACCEPTED_BREAK_APPLICATION_POINT
from abicheck.policy.disposition_ledger import Disposition
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.semver import SemverBump, recommend_release
from abicheck.serialization import save_snapshot
from abicheck.workflows.acknowledgment_inputs import resolve_compare_acknowledgments


def _snapshot(version: str, names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo",
        functions=[
            Function(name=n, mangled=n, return_type="void", params=[]) for n in names
        ],
        variables=[],
        types=[],
        enums=[],
    )


def _records(tmp: Path, *symbols: str) -> AcknowledgmentList:
    body = "".join(
        f"  - symbol: {s}\n    change_kind: func_removed\n    reason: planned\n"
        for s in symbols
    )
    path = tmp / "acks.yml"
    path.write_text("version: 1\nacknowledgments:\n" + body)
    return AcknowledgmentList.load(path)


def _exit_code(result: DiffResult) -> int:
    """The process exit the CLI would give (0 when the process would not exit)."""
    try:
        _exit_with_severity_or_verdict(result, EffectiveGate.from_severity(None))
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _three_removals() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snapshot("1.0.0", ["keep", "gone_a", "gone_b"])
    new = _snapshot("2.0.0", ["keep"])
    return old, new


def test_gate_default_keeps_an_acknowledged_break_gating(tmp_path: Path) -> None:
    old, new = _three_removals()
    acks = _records(tmp_path, "gone_a", "gone_b")
    result = checker.compare(old, new, acknowledgments=acks)
    assert result.verdict == Verdict.BREAKING
    assert _exit_code(result) == 4


def test_accept_drops_only_the_acknowledged_break(tmp_path: Path) -> None:
    """The sibling break on the same run stays gating: exit 4, not 0."""
    old, new = _three_removals()
    acks = _records(tmp_path, "gone_a")
    result = checker.compare(
        old, new, acknowledgments=acks, acknowledgment_breaks="accept"
    )
    assert result.verdict == Verdict.BREAKING
    assert _exit_code(result) == 4
    by_symbol = {r.symbol: r for r in result.disposition_ledger.records}
    assert by_symbol["gone_a"].disposition is Disposition.NON_GATING
    assert by_symbol["gone_a"].application_point == ACCEPTED_BREAK_APPLICATION_POINT
    assert by_symbol["gone_a"].acknowledged_by is not None
    assert by_symbol["gone_b"].disposition is Disposition.GATING


def test_accept_exits_zero_once_every_break_is_accepted(tmp_path: Path) -> None:
    old, new = _three_removals()
    acks = _records(tmp_path, "gone_a", "gone_b")
    result = checker.compare(
        old, new, acknowledgments=acks, acknowledgment_breaks="accept"
    )
    assert _exit_code(result) == 0


def test_accept_leaves_the_verdict_and_release_advice_a_break(tmp_path: Path) -> None:
    """D5: a known, accepted break is still a MAJOR-class break in the report."""
    old, new = _three_removals()
    acks = _records(tmp_path, "gone_a", "gone_b")
    result = checker.compare(
        old, new, acknowledgments=acks, acknowledgment_breaks="accept"
    )
    assert result.verdict == Verdict.BREAKING
    assert {c.symbol for c in result.changes} == {"gone_a", "gone_b"}
    assert recommend_release(result).bump == SemverBump.MAJOR


def test_accept_never_lowers_a_compatible_addition(tmp_path: Path) -> None:
    """``accept`` is for breaks only: an acknowledged addition changes nothing."""
    old = _snapshot("1.0.0", ["keep"])
    new = _snapshot("2.0.0", ["keep", "added"])
    acks = AcknowledgmentList.load(_write(tmp_path, "added"))
    result = checker.compare(
        old, new, acknowledgments=acks, acknowledgment_breaks="accept"
    )
    assert result.verdict == Verdict.COMPATIBLE
    assert _exit_code(result) == 0
    record = next(r for r in result.disposition_ledger.records if r.symbol == "added")
    assert record.application_point != ACCEPTED_BREAK_APPLICATION_POINT


def _write(tmp: Path, symbol: str) -> Path:
    path = tmp / "addition.yml"
    path.write_text(
        f"version: 1\nacknowledgments:\n  - symbol: {symbol}\n"
        "    change_kind: func_added\n    reason: planned\n"
    )
    return path


def test_breaks_accept_must_be_a_known_value() -> None:
    with pytest.raises(ValueError, match="breaks"):
        AcknowledgmentPolicy(breaks="ignore")  # type: ignore[arg-type]


def test_resolver_carries_breaks_from_the_typed_request_surface(tmp_path: Path) -> None:
    """``CompareRequest.acknowledgment_breaks`` reaches the one resolver."""
    resolved = resolve_compare_acknowledgments(
        None,
        policy_file=None,
        configured_action=None,
        configured_breaks="accept",
    )
    assert resolved.policy is not None
    assert resolved.policy.breaks == "accept"
    assert resolved.policy.unacknowledged_additions == "allow"


def test_resolver_default_is_gate_and_unset_policy_stays_none() -> None:
    assert (
        resolve_compare_acknowledgments(
            None, policy_file=None, configured_action=None
        ).policy
        is None
    )


def test_policy_file_block_outranks_configured_breaks(tmp_path: Path) -> None:
    """ADR-049 D7: a ``--policy`` document's own block wins as one whole block."""
    from abicheck.policy_file import PolicyFile

    policy_path = tmp_path / "policy.yml"
    policy_path.write_text("base_policy: strict_abi\nacknowledgments:\n  breaks: gate\n")
    pf = PolicyFile.load(policy_path)
    resolved = resolve_compare_acknowledgments(
        None,
        policy_file=pf,
        configured_action=None,
        configured_breaks="accept",
    )
    assert resolved.policy is not None
    assert resolved.policy.breaks == "gate"


def test_typed_compare_breaks_kw_overrides_the_policy(tmp_path: Path) -> None:
    old, new = _three_removals()
    acks = _records(tmp_path, "gone_a", "gone_b")
    result = checker.compare(
        old,
        new,
        acknowledgments=acks,
        acknowledgment_policy=AcknowledgmentPolicy(breaks="gate"),
        acknowledgment_breaks="accept",
    )
    assert _exit_code(result) == 0


# --- CLI: the trust boundary on `breaks` --------------------------------------


def _cli_pair(tmp: Path) -> tuple[Path, Path]:
    old = _snapshot("1.0.0", ["keep", "gone_a"])
    new = _snapshot("2.0.0", ["keep"])
    paths = (tmp / "old.json", tmp / "new.json")
    save_snapshot(old, paths[0])
    save_snapshot(new, paths[1])
    return paths


def _cli_exit(args: list[str], cwd: Path) -> tuple[int, dict]:
    out = cwd / "report.json"
    res = CliRunner().invoke(main, [*args, "-o", f"json={out}"])
    report = json.loads(out.read_text()) if out.exists() else {}
    return res.exit_code, report


def test_cli_explicit_config_applies_breaks_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    old, new = _cli_pair(tmp_path)
    (tmp_path / "acks.yml").write_text(
        "version: 1\nacknowledgments:\n  - symbol: gone_a\n"
        "    change_kind: func_removed\n    reason: planned\n"
    )
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text("acknowledgments:\n  file: acks.yml\n  breaks: accept\n")
    code, report = _cli_exit(
        ["compare", str(old), str(new), "--config", str(cfg)], tmp_path
    )
    assert code == 0
    assert report["verdict"] == "BREAKING"


def test_cli_discovered_config_cannot_loosen_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR-edited, auto-discovered ``.abicheck.yml`` cannot set ``accept``."""
    monkeypatch.chdir(tmp_path)
    old, new = _cli_pair(tmp_path)
    (tmp_path / "acks.yml").write_text(
        "version: 1\nacknowledgments:\n  - symbol: gone_a\n"
        "    change_kind: func_removed\n    reason: planned\n"
    )
    (tmp_path / ".abicheck.yml").write_text(
        "acknowledgments:\n  file: acks.yml\n  breaks: accept\n"
    )
    code, report = _cli_exit(["compare", str(old), str(new)], tmp_path)
    assert code == 4
    assert report["verdict"] == "BREAKING"


def test_cli_policy_breaks_accept_is_applied_when_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``--policy`` document may state ``breaks: accept`` itself (operator input)."""
    monkeypatch.chdir(tmp_path)
    old, new = _cli_pair(tmp_path)
    (tmp_path / "acks.yml").write_text(
        "version: 1\nacknowledgments:\n  - symbol: gone_a\n"
        "    change_kind: func_removed\n    reason: planned\n"
    )
    (tmp_path / "policy.yml").write_text(
        "base_policy: strict_abi\nacknowledgments:\n  breaks: accept\n"
    )
    code, _report = _cli_exit(
        [
            "compare",
            str(old),
            str(new),
            "--policy",
            str(tmp_path / "policy.yml"),
            "--acknowledgments",
            str(tmp_path / "acks.yml"),
        ],
        tmp_path,
    )
    assert code == 0


def test_no_records_leaves_the_gate_exactly_as_it_was() -> None:
    """No records at all: the typed API's pre-existing exit 4 is unchanged."""
    old, new = _three_removals()
    result = checker.compare(old, new)
    assert result.verdict == Verdict.BREAKING
    assert _exit_code(result) == 4
    assert ChangeKind.FUNC_REMOVED in {c.kind for c in result.changes}

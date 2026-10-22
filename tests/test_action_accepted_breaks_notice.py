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

"""ADR-067 D5 ``acknowledgment.breaks: accept`` surfaced by the Action.

Accepted breaks leave the exit code (exit 0) while the verdict stays
BREAKING, so ``action/run.sh`` emits one ``::notice::`` naming them. The
reports are produced by the real ``compare`` CLI and replayed through the
whole of ``run.sh``; the oracle is the removed-symbol set each case builds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
    _stub_abicheck,
)
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

pytestmark = REQUIRES_POSIX_SHELL

PREFIX = "::notice::abicheck accepted "


def _real_report(tmp_path: Path, removed: int, breaks: str) -> tuple[int, dict]:
    """Compare where *removed* functions vanish, all acknowledged."""
    keep = Function(name="keep", mangled="keep", return_type="void")
    gone = [
        Function(name=f"gone_{i}", mangled=f"gone_{i}", return_type="void")
        for i in range(removed)
    ]
    added = Function(name="added", mangled="added", return_type="void")
    old, new = tmp_path / "o.json", tmp_path / "n.json"
    save_snapshot(
        AbiSnapshot(library="libx.so.1", version="1", functions=[keep, *gone]), old
    )
    save_snapshot(
        AbiSnapshot(library="libx.so.1", version="2", functions=[keep, added]), new
    )
    records = "".join(
        f"  - symbol: gone_{i}\n    change_kind: func_removed\n    reason: planned\n"
        for i in range(removed)
    )
    body = "acknowledgments:\n" + records if records else "acknowledgments: []\n"
    (tmp_path / "acks.yml").write_text("version: 1\n" + body)
    cfg = tmp_path / "cfg.abicheck.yml"
    cfg.write_text(
        f"acknowledgment:\n  file: {tmp_path / 'acks.yml'}\n  breaks: {breaks}\n"
    )
    out = tmp_path / "real.json"
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "--config", str(cfg), "-o", f"json={out}"]
    )
    assert out.exists(), res.output
    return res.exit_code, json.loads(out.read_text())


def _notices(tmp_path: Path, code: int, report: dict) -> list[str]:
    bindir = _stub_abicheck(
        tmp_path, exit_code=code, payload=json.dumps(report).encode()
    )
    outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
    assert outputs["_exit"] == 0, outputs["_stdout"]
    return [ln for ln in outputs["_stdout"].splitlines() if ln.startswith(PREFIX)]


def test_report_lists_each_accepted_break(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 3, "accept")
    assert code == 0
    assert report["verdict"] == "BREAKING"
    rows = report["disposition_audit"]["accepted_breaks"]
    assert sorted(r["symbol"] for r in rows) == ["gone_0", "gone_1", "gone_2"]
    assert {r["kind"] for r in rows} == {"func_removed"}


def test_report_lists_none_under_breaks_gate(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 2, "gate")
    assert code == 4
    assert report["disposition_audit"]["accepted_breaks"] == []


def test_no_breaks_no_notice(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 0, "accept")
    assert code == 0
    assert report["disposition_audit"]["accepted_breaks"] == []
    assert _notices(tmp_path, code, report) == []


def test_two_accepted_breaks_notice_names_count_and_symbols(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 2, "accept")
    (line,) = _notices(tmp_path, code, report)
    assert line.startswith(f"{PREFIX}2 acknowledged break(s)")
    assert "the verdict is still BREAKING" in line
    assert "disposition_audit" in line
    assert "gone_0" in line and "gone_1" in line
    assert "more" not in line


def test_seven_accepted_breaks_list_is_capped(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 7, "accept")
    (line,) = _notices(tmp_path, code, report)
    assert line.startswith(f"{PREFIX}7 acknowledged break(s)")
    assert line.count("gone_") == 5
    assert line.endswith(" and 2 more")


def test_breaks_gate_emits_no_notice(tmp_path: Path) -> None:
    code, report = _real_report(tmp_path, 2, "gate")
    bindir = _stub_abicheck(
        tmp_path, exit_code=code, payload=json.dumps(report).encode()
    )
    outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
    assert outputs["_exit"] != 0
    assert PREFIX not in outputs["_stdout"]


@pytest.mark.parametrize(
    ("symbol", "encoded"),
    [
        ("evil\n::error::forged", "evil%0A::error::forged"),
        ("pct%0Aliteral", "pct%250Aliteral"),
        ("cr\rx", "cr%0Dx"),
    ],
)
def test_symbol_is_command_escaped(tmp_path: Path, symbol: str, encoded: str) -> None:
    code, report = _real_report(tmp_path, 1, "accept")
    report["disposition_audit"]["accepted_breaks"][0]["symbol"] = symbol
    (line,) = _notices(tmp_path, code, report)
    assert line.endswith(encoded)
    bindir = _stub_abicheck(
        tmp_path, exit_code=code, payload=json.dumps(report).encode()
    )
    stdout = _run_action(tmp_path, _compare_env(tmp_path), bindir)["_stdout"]
    assert not [ln for ln in stdout.splitlines() if ln.startswith("::error::forged")]

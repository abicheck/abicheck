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

"""The `action-cli-mirror` gate resolves real citations and catches rot.

ADR-070 D4 / plan Phase 4. The audit behind that ADR found four comments in
`action/run.sh` justifying themselves by naming `abicheck/cli_scan.py` and
`scan_engine` — modules ADR-068 deleted. A reader checking one against its
cited source found nothing, and could not tell whether the code or the comment
was wrong.

These tests cover the three ways such a citation rots (deleted file, renamed
symbol, symbol that appears only in prose) plus the gate's own vacuity guard,
because a gate that cannot fail is worse than none.

**They deliberately do not assert that the gate proves behaviour.** It does
not: symbol existence is much weaker than symbol behaviour, and both
release-operand drifts the audit found live inside functions that still exist
and merely reject less than they used to. One test below pins that limitation
explicitly so nobody later mistakes this gate for a behavioural check and
stops looking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from action_cli_mirror import (  # noqa: E402
    ANNOTATED_FILES,
    MIRROR_RE,
    check_action_cli_mirror,
)


class _Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, check: str, msg: str) -> None:
        self.errors.append(msg)


def _check(root: Path) -> list[str]:
    findings = _Findings()
    check_action_cli_mirror(findings, root=root)
    return findings.errors


def _stage(
    tmp_path: Path, *, annotation: str, target: str | None, body: str = ""
) -> Path:
    """Build a minimal tree: one annotated shell file, optionally one target."""
    (tmp_path / "action").mkdir(parents=True, exist_ok=True)
    (tmp_path / "action" / "run.sh").write_text(
        f"#!/usr/bin/env bash\n# {annotation}\necho hi\n", encoding="utf-8"
    )
    if target is not None:
        path = tmp_path / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


class TestTheRealRepositoryPasses:
    def test_every_committed_annotation_resolves(self) -> None:
        assert _check(REPO_ROOT) == []

    def test_at_least_one_annotation_exists(self) -> None:
        """The gate's vacuity guard, asserted from the outside too: with no
        annotations anywhere it reports an error rather than passing."""
        found = 0
        for rel in ANNOTATED_FILES:
            path = REPO_ROOT / rel
            if path.is_file():
                found += sum(
                    1
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if MIRROR_RE.search(line)
                )
        assert found > 0


class TestCitationRot:
    def test_a_deleted_file_is_caught(self, tmp_path: Path) -> None:
        """The exact shape the audit found: `cli_scan.py` no longer exists."""
        root = _stage(
            tmp_path,
            annotation="cli-mirror: abicheck/cli_scan.py::run_scan",
            target=None,
        )
        errors = _check(root)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_a_renamed_symbol_is_caught(self, tmp_path: Path) -> None:
        root = _stage(
            tmp_path,
            annotation="cli-mirror: abicheck/engine.py::gone_away",
            target="abicheck/engine.py",
            body="def still_here() -> None:\n    pass\n",
        )
        errors = _check(root)
        assert len(errors) == 1
        assert "defines no such symbol" in errors[0]

    def test_a_symbol_only_mentioned_in_prose_is_caught(self, tmp_path: Path) -> None:
        """The subtle one: grepping for the name would pass, because the target
        file talks *about* the symbol. Only a definition counts."""
        root = _stage(
            tmp_path,
            annotation="cli-mirror: abicheck/engine.py::EXIT_BUDGET",
            target="abicheck/engine.py",
            body='"""Historically EXIT_BUDGET lived here; see the changelog."""\n',
        )
        errors = _check(root)
        assert len(errors) == 1
        assert "defines no such symbol" in errors[0]

    @pytest.mark.parametrize(
        "definition",
        [
            "def target() -> None:\n    pass\n",
            "async def target() -> None:\n    pass\n",
            "class target:\n    pass\n",
            "target = 7\n",
            "target: int = 7\n",
        ],
    )
    def test_every_definition_shape_counts(
        self, tmp_path: Path, definition: str
    ) -> None:
        """A constant, a dataclass and a function are all legitimate referents,
        so the gate must accept each — otherwise the convention quietly pushes
        authors toward citing only functions."""
        root = _stage(
            tmp_path,
            annotation="cli-mirror: abicheck/engine.py::target",
            target="abicheck/engine.py",
            body=definition,
        )
        assert _check(root) == []

    def test_no_annotations_at_all_is_an_error_not_a_pass(self, tmp_path: Path) -> None:
        (tmp_path / "action").mkdir(parents=True)
        (tmp_path / "action" / "run.sh").write_text("echo hi\n", encoding="utf-8")
        errors = _check(tmp_path)
        assert len(errors) == 1
        assert "no cli-mirror annotations found" in errors[0]


class TestTheGatesDocumentedLimit:
    def test_it_does_not_detect_a_behaviour_change_in_a_surviving_symbol(
        self, tmp_path: Path
    ) -> None:
        """**This is the limitation, pinned as a test on purpose.**

        Both drifts the audit actually cared about —
        `cli_resolve._reject_compile_context_for_set_inputs` and
        `cli_compare_options._resolve_depth_for_set_inputs` — live in functions
        that still exist and merely reject less than the Action believes. A
        citation to either resolves perfectly, before and after.

        So this gate is not a substitute for the behavioural checks (plan
        Phases 2 and 5). Asserting that here means a future reader who assumes
        otherwise meets a test that says so, rather than a comment they may not
        read.
        """
        root = _stage(
            tmp_path,
            annotation="cli-mirror: abicheck/resolve.py::_reject_everything",
            target="abicheck/resolve.py",
            body="def _reject_everything(ctx) -> None:\n    return None  # rejects nothing now\n",
        )
        assert _check(root) == [], (
            "the gate flagged a behaviour change it cannot see; if it really "
            "gained that power, this test and ADR-070's alternatives section "
            "both need rewriting"
        )

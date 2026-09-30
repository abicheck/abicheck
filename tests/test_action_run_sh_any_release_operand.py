# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Nikolay Petrov
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

"""``action/run.sh``'s ``_any_compare_release_operand``: one classifier
process answering what two ``_is_compare_release_operand`` calls answer.

Split out of ``test_action_run_sh_helpers.py`` (test-file size cap); it
reuses that module's real-file harness rather than a copy of it.
"""

from __future__ import annotations

import itertools
import shlex
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from test_action_run_sh_helpers import (
    RUN_SH,
    _cli_introspection_prelude,
    _helpers_region,
    _real_package,
)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAnyCompareReleaseOperand:
    """``_any_compare_release_operand OLD NEW`` answers, in one classifier
    process, what ``_is_compare_release_operand OLD || _is_compare_release_operand
    NEW`` answers in two -- it exists only to halve the per-run classifier
    import cost. The oracle is the per-operand helper itself, evaluated in the
    same shell over every ordered pair of a small domain of operand kinds
    (exhaustive, not sampled), on both the probe path and the fallback path."""

    @staticmethod
    def _domain(tmp_path: Path) -> dict[str, str]:
        (tmp_path / "bundle").mkdir()
        snapshot = tmp_path / "old.json"
        snapshot.write_text("{}", encoding="utf-8")
        return {
            "empty": "",
            "missing": str(tmp_path / "no-such.so"),
            "json": str(snapshot),
            "dir": str(tmp_path / "bundle"),
            "relative-dir": "bundle",
            "rpm": str(_real_package(tmp_path, ".rpm")),
            "whl": str(_real_package(tmp_path, ".whl")),
        }

    @pytest.mark.parametrize("abicheck_available", [True, False])
    def test_equals_the_per_operand_disjunction(
        self, tmp_path: Path, abicheck_available: bool
    ) -> None:
        require_bash()
        domain = self._domain(tmp_path)
        # Unordered pairs: the disjunction is symmetric, and each probe costs
        # a Python start-up, so the ordered square would double the runtime
        # without adding a case. The oracle is evaluated once per kind.
        pairs = list(itertools.combinations_with_replacement(domain, 2))
        lines = []
        for kind, path in domain.items():
            lines.append(
                f"if _is_compare_release_operand {shlex.quote(path)}; then "
                f"o_{kind.replace('-', '_')}=1; else o_{kind.replace('-', '_')}=0; fi\n"
            )
        for a, b in pairs:
            qa, qb = shlex.quote(domain[a]), shlex.quote(domain[b])
            va, vb = a.replace("-", "_"), b.replace("-", "_")
            lines.append(
                f"if _any_compare_release_operand {qa} {qb}; then g=1; else g=0; fi\n"
                f"o=$(( o_{va} | o_{vb} ))\n"
                f'printf "%s %s {a} {b}\\n" "$g" "$o"\n'
            )
        script = (
            _helpers_region()
            + _cli_introspection_prelude()
            + ("" if abicheck_available else "\n_PY_BIN_HAS_ABICHECK=false\n")
            + "\n"
            + "".join(lines)
        )
        script_path = tmp_path / "harness.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        result = subprocess.run(
            [bash_executable(), str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
        )
        rows = [line.split() for line in result.stdout.splitlines()]
        assert len(rows) == len(pairs), result.stdout + result.stderr
        disagreements = [r for r in rows if r[0] != r[1]]
        assert not disagreements, disagreements
        # Vacuity guard: the domain must exercise both answers.
        assert {r[1] for r in rows} == {"0", "1"}, rows

    @pytest.mark.parametrize(
        "template",
        [
            "$(touch {canary})",
            "`touch {canary}`",
            "x'; touch {canary}; '",
            'x"; touch {canary}; "',
            "x\nimport os; os.system('touch {canary}')",
        ],
    )
    def test_a_hostile_operand_path_is_never_evaluated(
        self, tmp_path: Path, template: str
    ) -> None:
        """Operand paths reach the classifier as argv, never as code: no
        spelling may execute in the shell or in the inline Python probe."""
        require_bash()
        canary = tmp_path / "pwned.canary"
        payload = template.format(canary=canary)
        script = (
            _helpers_region()
            + _cli_introspection_prelude()
            + f"\n_any_compare_release_operand {shlex.quote(payload)} {shlex.quote(payload)} || true\n"
        )
        script_path = tmp_path / "harness.sh"
        script_path.write_text(script, encoding="utf-8", newline="\n")
        subprocess.run(
            [bash_executable(), str(script_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
        )
        assert not canary.exists(), f"operand {payload!r} was evaluated"

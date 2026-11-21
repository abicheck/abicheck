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

"""A caller's own non-JSON ``extra-args`` export keeps the Action's floors.

Closes the former known gap "the composite Action's single write slot can
leave its unconditional coverage/assurance/severity floors without a
structured report": ``-o`` is repeatable, so ``run.sh`` injects its internal
``-o json=$PR_JSON`` sidecar beside every caller export that names no JSON
destination, and the floors read that sidecar.

Driven through the real ``run.sh`` with a stub ``abicheck`` that honours
every ``-o`` export. The oracle is the published ``verdict``: without the
sidecar the exit-1 run has no structured report and maps to ``ERROR``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _lib, _run_action

pytestmark = REQUIRES_POSIX_SHELL

_COVERAGE_FAILURE = {
    "provider": "export_table",
    "side": "old",
    "record_id": "old/export_table",
    "reason": "search_incomplete",
    "status": "unavailable",
    "completeness": "none",
    "mode": "exports",
    "suppressible": False,
}


def _report(*, coverage: int, severity_exit: int) -> dict:
    return {
        "report_schema_version": "2.26",
        "verdict": "COMPATIBLE",
        "contract_coverage_exit_contribution": coverage,
        "contract_coverage_failures": [_COVERAGE_FAILURE] if coverage else [],
        "severity": {
            "config": {},
            "categories": {},
            "exit_code": severity_exit,
            "blocking": severity_exit != 0,
            "blocking_categories": ["addition"] if severity_exit else [],
        },
    }


def _stub(tmp_path: Path, report: dict, exit_code: int) -> Path:
    """Writes *report* to every JSON export, a text body to every other one."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(report), encoding="utf-8")
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" && "$arg" == *=* && "$arg" != *=- ]]; then\n'
        '    if [[ "$arg" == json=* ]]; then\n'
        f'      cp "{payload}" "${{arg#*=}}"\n'
        "    else\n"
        "      echo 'rendered non-json report' > \"${arg#*=}\"\n"
        "    fi\n"
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


@pytest.mark.parametrize("primary", ["markdown", "json"])
@pytest.mark.parametrize("caller_format", ["text", "markdown", "sarif", "junit"])
@pytest.mark.parametrize(
    ("coverage", "severity_exit", "expected"),
    [(1, 0, "COVERAGE_INCOMPLETE"), (1, 1, "SEVERITY_ERROR"), (0, 1, "SEVERITY_ERROR")],
)
def test_floors_read_the_sidecar_beside_a_non_json_caller_export(
    tmp_path: Path,
    primary: str,
    caller_format: str,
    coverage: int,
    severity_exit: int,
    expected: str,
) -> None:
    bindir = _stub(tmp_path, _report(coverage=coverage, severity_exit=severity_exit), 1)
    caller_dest = tmp_path / f"mine.{caller_format}"
    outputs = _run_action(
        tmp_path,
        {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            "INPUT_FORMAT": primary,
            # The caller's export claims stdout as well, so the Action's own
            # primary export is dropped and the sidecar is the only JSON.
            "INPUT_EXTRA_ARGS": f"-o {caller_format}=- -o {caller_format}={caller_dest}",
        },
        bindir,
    )
    assert outputs["verdict"] == expected, outputs
    assert outputs["exit-code"] == "1", outputs
    # The caller's own export was honoured, not replaced.
    assert caller_dest.read_text(encoding="utf-8").strip() == "rendered non-json report"

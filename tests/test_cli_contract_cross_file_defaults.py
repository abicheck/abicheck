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

"""D10.4's one-default-per-flag gate, cross-file half (ADR-037 D3 / G22 Phase 2).

Split out of ``test_cli_contract.py`` (already at its own `no_growth` line-
count pin) rather than grown there: these two tests cover
``_check_one_default_per_flag``'s scan across ``cli_options.py`` *and* its
``frontends/cli/options/`` siblings (``contract.py``/``profiles.py``/
``secondary_output.py``/``release.py``), the gap CodeRabbit found on PR #973
— a hardcoded single-file scan stopped seeing a sibling's half of a
conflicting-default comparison the moment the first such split landed
(``frontends/cli/options/contract.py``, well before this PR). See
``test_cli_contract.py``'s own single-file ``test_gate_flags_conflicting_
default``/``test_conflicting_defaults_always_flagged`` for the same gate's
single-file coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the gate from scripts/ — the AI-readiness module is pure stdlib.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_gate_flags_conflicting_default_across_options_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D10.4 must also catch a flag split between ``cli_options.py`` and a
    sibling under ``frontends/cli/options/`` (e.g. ``contract.py``/
    ``profiles.py``/``release.py``) -- a single-file scan stopped seeing the
    sibling's half of the comparison the moment the first such split landed
    (CodeRabbit review, PR #973)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    options_dir = pkg / "frontends" / "cli" / "options"
    options_dir.mkdir(parents=True)
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--depth", default="l0")(func)\n'
    )
    (options_dir / "release.py").write_text(
        "import click\n"
        "def b(func):\n"
        '    return click.option("--depth", default="l2")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(msgs) == 1 and "--depth" in msgs[0], msgs


def test_gate_allows_matching_default_across_options_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cross-file scan must not false-positive when the same flag is
    legitimately redeclared with an identical default in a sibling module
    (the ``--policy``/``--profile`` shape already live in
    ``frontends/cli/options/contract.py``/``profiles.py``)."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    options_dir = pkg / "frontends" / "cli" / "options"
    options_dir.mkdir(parents=True)
    (pkg / "cli_options.py").write_text(
        "import click\n"
        "def a(func):\n"
        '    return click.option("--policy", default="strict_abi")(func)\n'
    )
    (options_dir / "contract.py").write_text(
        "import click\n"
        "def b(func):\n"
        '    return click.option("--policy", default="strict_abi")(func)\n'
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate._check_one_default_per_flag(findings)
    msgs = [m for c, m in findings.errors if c == "cli-contract"]
    assert msgs == []

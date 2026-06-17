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

"""CLI interface-contract gate mirror + chokepoint parity (ADR-037 / G22 Phase 1).

This is the unit-test mirror of the ``cli-contract`` AI-readiness check
(``scripts/check_ai_readiness.py``), so the contract is enforced both as a fast
CI gate and in the regular test suite. It also pins the *behavioural* payoff of
the single chokepoint: ``compare-release`` and ``service.run_compare`` classify
a given pair identically (no ``scope_public`` default drift).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import the gate from scripts/ — the AI-readiness module is pure stdlib.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.serialization import save_snapshot  # noqa: E402
from scripts.check_ai_readiness import Findings, check_cli_contract  # noqa: E402

# ── D10.1: no front-end skips the Tier-2 service ─────────────────────────────


def test_no_tier_skip():
    """No ``abicheck/cli*.py`` module calls Tier-1 ``checker.compare`` directly.

    Front-ends must route through ``service.run_compare`` /
    ``service.compare_snapshots`` (ADR-037 D1/D10.1).
    """
    findings = Findings()
    check_cli_contract(findings)
    contract_errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert contract_errors == [], "Tier-1 call sites in front-ends:\n" + "\n".join(
        contract_errors
    )


def test_gate_flags_a_planted_violation(tmp_path, monkeypatch):
    """The gate is not a no-op: a planted direct ``compare`` call is caught."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_bad.py").write_text(
        "from .checker import compare\ndef go(a, b):\n    return compare(a, b)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    errors = [m for c, m in findings.errors if c == "cli-contract"]
    assert len(errors) == 1
    assert "cli_bad.py" in errors[0]


def test_gate_allows_aliased_import_call(tmp_path, monkeypatch):
    """An aliased lazy import (``compare as _compare``) is still caught."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_alias.py").write_text(
        "def go(a, b):\n"
        "    from .checker import compare as _compare\n"
        "    return _compare(a, b)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert any(c == "cli-contract" for c, _ in findings.errors)


def test_service_compare_call_is_not_flagged(tmp_path, monkeypatch):
    """Routing through ``service.compare_snapshots`` must NOT be flagged."""
    import scripts.check_ai_readiness as gate

    pkg = tmp_path / "abicheck"
    pkg.mkdir()
    (pkg / "cli_ok.py").write_text(
        "from .service import compare_snapshots\n"
        "def go(a, b):\n"
        "    return compare_snapshots(a, b)\n"
    )
    monkeypatch.setattr(gate, "PKG", pkg)
    monkeypatch.setattr(gate, "ROOT", tmp_path)

    findings = gate.Findings()
    gate.check_cli_contract(findings)
    assert not any(c == "cli-contract" for c, _ in findings.errors)


# ── Chokepoint parity: one classifier, no scope_public drift ─────────────────


def _make_snap_file(tmp_path: Path, name: str, version: str, funcs) -> Path:
    snap = AbiSnapshot(library=name, version=version, functions=funcs)
    p = tmp_path / f"{name}_{version}.json"
    save_snapshot(snap, p)
    return p


def _func(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
    )


def test_compare_release_matches_service_run_compare(tmp_path):
    """``compare-release``'s per-pair runner classifies identically to
    ``service.run_compare`` — they share the one chokepoint (ADR-037 D1)."""
    from abicheck import service
    from abicheck.cli_compare_release import _run_compare_pair

    old_p = _make_snap_file(tmp_path, "libfoo", "1.0", [_func("foo"), _func("bar")])
    new_p = _make_snap_file(tmp_path, "libfoo", "2.0", [_func("foo")])

    svc_result, _, _ = service.run_compare(old_p, new_p, scope_to_public_surface=True)
    rel_result, _, _ = _run_compare_pair(
        old_p,
        new_p,
        old_headers=[],
        new_headers=[],
        old_includes=[],
        new_includes=[],
        old_version="",
        new_version="",
        lang="c++",
        suppress=None,
        policy="strict_abi",
        policy_file_path=None,
        old_pdb_path=None,
        new_pdb_path=None,
        scope_to_public_surface=True,
    )

    assert svc_result.verdict == rel_result.verdict
    assert sorted(c.kind for c in svc_result.breaking) == sorted(
        c.kind for c in rel_result.breaking
    )
    assert sorted(c.kind for c in svc_result.source_breaks) == sorted(
        c.kind for c in rel_result.source_breaks
    )
    assert sorted(c.kind for c in svc_result.compatible) == sorted(
        c.kind for c in rel_result.compatible
    )


def test_run_compare_request_equivalent_to_kwargs_shim(tmp_path):
    """The kwargs ``run_compare`` shim and a hand-built ``CompareRequest`` agree."""
    from abicheck.api_types import CompareRequest, InputSpec
    from abicheck.service import run_compare, run_compare_request

    old_p = _make_snap_file(tmp_path, "libbar", "1.0", [_func("a"), _func("b")])
    new_p = _make_snap_file(tmp_path, "libbar", "2.0", [_func("a")])

    shim_result, _, _ = run_compare(old_p, new_p)
    req = CompareRequest(old=InputSpec.of(old_p), new=InputSpec.of(new_p))
    req_result, _, _ = run_compare_request(req)

    assert shim_result.verdict == req_result.verdict
    assert sorted(c.kind for c in shim_result.breaking) == sorted(
        c.kind for c in req_result.breaking
    )

"""The compare gate is resolved from one ``EffectiveGate``
(duplication-and-convergence-assessment P0).

Bug class: a severity map and an exit-code scheme passed as two separate
arguments, so a caller can hand over a pair that disagrees. Before this
change the process exit, the report's ``exit`` block, the scoped gate and
the effective-config digest each took ``(sev_config, scheme)`` and each
re-paired them; a test was even passing ``True`` as the scheme, which the
resolver silently read as "legacy". The scheme is now derived from
``EffectiveGate.severity`` alone (``None`` exactly under the legacy scheme).

Two invariants:

* structural -- no function under ``abicheck/`` takes both a scheme
  parameter and a severity-map parameter, except the named exceptions below;
* behavioral -- over every subset of one finding per verdict category and
  every severity setting, the process exit and the report's ``exit`` block
  agree, the digest records the scheme the gate used, and the legacy scheme
  matches an independently written verdict-to-exit table.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from abicheck.checker import DiffResult, Verdict
from abicheck.checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    BREAKING_KINDS,
    RISK_KINDS,
    compute_verdict,
)
from abicheck.model.change import Change
from abicheck.policy.effective_gate import EffectiveGate
from abicheck.policy.severity import SEVERITY_PRESETS

ROOT = Path(__file__).resolve().parents[1] / "abicheck"

#: Functions allowed to take a scheme and a severity map side by side, with
#: the reason. Each entry must still exist (checked below), so the list can
#: only shrink.
PAIR_EXCEPTIONS = {
    # The persisted evaluation-context receipt records concrete severity
    # levels even under the legacy scheme (an audit record of the levels in
    # force, not a gate input), so its severity cannot carry the scheme.
    ("contract_context.py", "with_resolved_gate"),
}


def _scheme_and_severity_map_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]
    has_scheme = any("scheme" in a for a in args)
    # `severity_scheme_active` (the release resolver's bool) names a scheme,
    # not a severity map.
    has_map = any(
        a.startswith(("sev_", "severity")) and "scheme" not in a for a in args
    )
    return has_scheme and has_map


def _pair_taking_functions() -> set[tuple[str, str]]:
    found = set()
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
                _scheme_and_severity_map_params(node)
            ):
                found.add((path.name, node.name))
    return found


def test_no_function_takes_a_scheme_beside_a_severity_map() -> None:
    assert _pair_taking_functions() - PAIR_EXCEPTIONS == set()


def test_every_exception_is_still_live() -> None:
    assert PAIR_EXCEPTIONS <= _pair_taking_functions()


def test_the_detector_sees_the_retired_shape() -> None:
    # Vacuity guard: the pre-change signature must be flagged.
    fn = ast.parse(
        "def resolve(result, sev_config, scheme, *, require_complete_analysis=False): ..."
    ).body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert _scheme_and_severity_map_params(fn)


# One finding per verdict category; every subset is a run.
_SAMPLE_KINDS = (
    sorted(BREAKING_KINDS)[0],
    sorted(API_BREAK_KINDS)[0],
    sorted(RISK_KINDS)[0],
    sorted(ADDITION_KINDS)[0],
)
_RUNS = [
    combo
    for r in range(len(_SAMPLE_KINDS) + 1)
    for combo in itertools.combinations(_SAMPLE_KINDS, r)
]
_SEVERITIES = [None, *(SEVERITY_PRESETS[p] for p in ("default", "strict", "info-only"))]

# Written from the documented legacy exit table (AGENTS.md "Exit codes"),
# not from `legacy_exit_code`.
_LEGACY_TABLE = {
    Verdict.NO_CHANGE: 0,
    Verdict.COMPATIBLE: 0,
    Verdict.COMPATIBLE_WITH_RISK: 0,
    Verdict.API_BREAK: 2,
    Verdict.BREAKING: 4,
}


def _result(kinds: tuple) -> DiffResult:
    changes = [
        Change(kind=k, symbol=f"sym_{k.value}", description=k.value) for k in kinds
    ]
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes,
        verdict=compute_verdict(changes),
    )


def _process_exit(result: DiffResult, gate: EffectiveGate) -> int:
    from abicheck.frontends.cli.runtime import _exit_with_severity_or_verdict

    try:
        _exit_with_severity_or_verdict(result, gate)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_every_consumer_reads_the_same_gate() -> None:
    from abicheck.reporter_contract_blocks import add_contract_context

    disagreements = []
    legacy_seen: set[int] = set()
    for kinds, severity in itertools.product(_RUNS, _SEVERITIES):
        result = _result(kinds)
        gate = EffectiveGate.from_severity(severity)
        report: dict = {}
        add_contract_context(report, result, severity_config=severity)
        process = _process_exit(result, gate)
        scheme = report["effective_config_fields"]["gate.exit_code_scheme"]
        expected_scheme = "legacy" if severity is None else "severity"
        problems = []
        if report["exit"]["code"] != process:
            problems.append(f"report {report['exit']['code']} != process {process}")
        if scheme != expected_scheme or gate.exit_code_scheme != expected_scheme:
            problems.append(f"scheme {scheme}/{gate.exit_code_scheme}")
        if severity is None:
            legacy_seen.add(process)
            if process != _LEGACY_TABLE[result.verdict]:
                problems.append(
                    f"legacy {process} != table {_LEGACY_TABLE[result.verdict]}"
                )
        if problems:
            disagreements.append((tuple(k.value for k in kinds), severity, problems))
    assert disagreements == []
    # The oracle is not a constant: the sample reaches all three legacy codes.
    assert legacy_seen == {0, 2, 4}


@pytest.mark.parametrize(
    "severity", _SEVERITIES[1:], ids=["default", "strict", "info-only"]
)
def test_a_severity_map_alone_selects_the_severity_scheme(severity) -> None:
    # With no scheme argument left, the info-only preset cannot be read as
    # legacy: a BREAKING finding gates through the severity levels.
    result = _result((sorted(BREAKING_KINDS)[0],))
    legacy = _process_exit(result, EffectiveGate.from_severity(None))
    gated = _process_exit(result, EffectiveGate.from_severity(severity))
    assert legacy == 4
    if severity is SEVERITY_PRESETS["info-only"]:
        assert gated == 0
    else:
        assert gated == 4

# SPDX-License-Identifier: Apache-2.0
"""Regression corpus for two capabilities from ADR-068 §1's list that used
to be scan-only, **now closed**: the ``abi3`` stable-ABI audit and
changed-path localization (``--since``/``--changed-path``).

Neither is a cross-source check, so neither goes through
``run_crosschecks`` -- but both were, just as concretely, capabilities a
``compare`` user could not reach at all. Phase 2c/2d moved them: ``compare``
now accepts ``--since``, ``--changed-path`` and ``--abi3``, and the two
``EXPECTED_GAPS`` entries are gone from ``gaps.py``. The tests below used to
also prove parity against a live ``scan`` invocation directly
(``test_abi3_audit_fires_under_scan``, ``test_scan_supports_changed_path_
localization``, and half of ``test_abi3_audit_fires_under_compare_as_
candidate_side_enrichment``'s own count derivation) -- deleted with the
``scan`` command itself (ADR-068 Phase 6); what remains pins ``compare``'s
own behavior directly (candidate-side, advisory, in the same result
document, exactly as ADR-068 D3 requires).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .gaps import EXPECTED_GAPS
from .runner import compare_json, invoke_cli, write_snapshot


def _abi3_snapshot():
    from abicheck.model import AbiSnapshot
    from abicheck.python_ext import PythonExtMetadata

    return AbiSnapshot(
        library="foo.abi3.so",
        version="1.0",
        python_ext=PythonExtMetadata(
            module_name="foo",
            init_symbol="PyInit_foo",
            # PyType_GetName only entered the stable ABI in 3.11 -- a
            # violation under a 3.9 floor.
            cpython_imports=["PyList_New", "PyType_GetName"],
        ),
    )


def _plain_snapshot():
    from abicheck.model import AbiSnapshot

    return AbiSnapshot(library="libfoo.so", version="1.0")


def test_the_two_gaps_are_closed_and_deregistered() -> None:
    """`gaps.py` is the migration's definition of done: a capability
    `compare` has reached must not still be listed as scan-only."""
    assert "abi3_audit" not in EXPECTED_GAPS
    assert "changed_path_localization" not in EXPECTED_GAPS


def test_abi3_audit_fires_under_compare_as_candidate_side_enrichment(
    tmp_path: Path,
) -> None:
    """The stable-ABI audit fires in `compare`'s own single result document
    -- one finding for `_abi3_snapshot`'s one violation
    (`PyType_GetName`, which only entered the stable ABI in 3.11, under a
    3.9 floor) -- marked as candidate-side (ADR-068 D3), not split off into
    a second result."""
    path = write_snapshot(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
    report = compare_json(path, path, "--abi3", "3.9")
    findings = [
        c for c in report["changes"] if c["kind"] == "python_stable_abi_violation"
    ]
    assert len(findings) == 1
    assert all(c.get("candidate_side_enrichment") is True for c in findings)


def test_compare_abi3_stays_advisory(tmp_path: Path) -> None:
    """Migrating the audit must not promote it: with no severity policy in
    effect the run still exits 0, and the finding stays a RISK kind."""
    path = write_snapshot(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
    result = invoke_cli(
        "compare", str(path), str(path), "--abi3", "3.9", "--format", "json"
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["exit"]["code"] == 0
    severities = {
        c["severity"]
        for c in report["changes"]
        if c["kind"] == "python_stable_abi_violation"
    }
    assert severities == {"risk"}, severities


def test_compare_abi3_on_a_non_extension_is_an_evidence_contract_error(
    tmp_path: Path,
) -> None:
    """Same precondition `scan --abi3` enforces, reported through the exit
    axis `compare` already has (ADR-064): exit 7, not a fabricated finding."""
    path = write_snapshot(_plain_snapshot(), tmp_path / "libfoo.abi.json")
    result = invoke_cli(
        "compare", str(path), str(path), "--abi3", "3.9", "--format", "json"
    )
    assert result.exit_code == 7, result.output
    report = json.loads(result.stdout)
    assert report["exit"]["evidence_contract_error_contribution"] == 7
    assert "evidence_contract_error" in report["exit"]["reasons"]
    assert not [
        c for c in report["changes"] if c["kind"] == "python_stable_abi_violation"
    ]


def test_changed_path_promotes_public_to_internal_dependency_confidence() -> None:
    """This proves changed-path localization reaches a real cross-source
    finding (case181's public_to_internal_dependency) through
    `CrosscheckConfig`, not merely a report-summary bookkeeping field. When
    the internal declaration's own file is a changed path, the finding is
    promoted from MEDIUM to HIGH confidence and gains that file as
    `source_location` (ADR-035 D4's "L5 reachability <-> PR changed files"
    note in `crosscheck.py`)."""
    from abicheck.buildsource.cross_source_checks import (
        CrosscheckConfig,
        run_crosschecks,
    )
    from abicheck.checker_policy import Confidence

    from .test_cross_source_checks_parity import _g20_snapshot

    snapshot = _g20_snapshot("case181_xcheck_public_to_internal_dependency")
    internal_file = "src/json_internal.cc"

    baseline = run_crosschecks(snapshot)
    unscoped = next(
        c for c in baseline.findings if c.kind.value == "public_to_internal_dependency"
    )
    assert unscoped.confidence == Confidence.MEDIUM
    assert unscoped.source_location is None

    scoped = run_crosschecks(
        snapshot, CrosscheckConfig(changed_paths=frozenset({internal_file}))
    )
    promoted = next(
        c for c in scoped.findings if c.kind.value == "public_to_internal_dependency"
    )
    assert promoted.confidence == Confidence.HIGH
    assert promoted.source_location == internal_file


@pytest.mark.parametrize("flag", ["--since", "--changed-path"])
def test_compare_accepts_the_changed_path_options(flag: str, tmp_path: Path) -> None:
    """Both spellings exist on `compare` and are accepted (not "No such
    option"), and neither manufactures a finding of its own: a snapshot
    compared against itself under a changed-path seed is still NO_CHANGE."""
    path = write_snapshot(_plain_snapshot(), tmp_path / "snap.abi.json")
    value = "origin/main" if flag == "--since" else "src/foo.h"
    result = invoke_cli(
        "compare", str(path), str(path), flag, value, "--format", "json"
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["changes"] == []
    assert report["verdict"] in ("NO_CHANGE", "COMPATIBLE")

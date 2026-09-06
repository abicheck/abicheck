# SPDX-License-Identifier: Apache-2.0
"""Parity for two more scan-only capabilities from ADR-068 §1's list:
the ``abi3`` single-artifact stable-ABI audit and changed-path
localization (``--since``/``--changed-path``).

Neither is a cross-source check, so neither goes through
``run_crosschecks`` -- but both are, just as concretely, capabilities a
``compare`` user cannot reach at all: `compare` has no ``--abi3``,
``--since``, or ``--changed-path`` option (not hidden, not aliased --
absent), while ``scan`` has all three.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .gaps import EXPECTED_GAPS
from .runner import invoke_cli, scan_json, write_snapshot


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


def test_abi3_audit_fires_under_scan(tmp_path: Path) -> None:
    assert "abi3_audit" in EXPECTED_GAPS

    path = write_snapshot(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
    report = scan_json(path, "--abi3", "3.9")
    assert report["mode"] == "audit"
    assert (
        report["crosscheck"]["counts_by_check"].get("python_stable_abi_violation") == 1
    ), report["crosscheck"]


def test_compare_has_no_abi3_option(tmp_path: Path) -> None:
    path = write_snapshot(_abi3_snapshot(), tmp_path / "foo.abi3.so.abi.json")
    result = invoke_cli("compare", str(path), str(path), "--abi3", "3.9")
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output and "--abi3" in result.output


def test_scan_supports_changed_path_localization(tmp_path: Path) -> None:
    assert "changed_path_localization" in EXPECTED_GAPS

    from abicheck.model import AbiSnapshot

    path = write_snapshot(
        AbiSnapshot(library="libfoo.so", version="1.0"), tmp_path / "snap.abi.json"
    )
    report = scan_json(path, "--changed-path", "src/foo.h")
    assert report["changed_paths"]["count"] == 1


def test_changed_path_promotes_public_to_internal_dependency_confidence() -> None:
    """The bookkeeping check above only proves scan *counted* the changed
    path; this proves it actually reaches a real cross-source finding
    (case181's public_to_internal_dependency) through `CrosscheckConfig`,
    not merely a report-summary field. When the internal declaration's own
    file is a changed path, the finding is promoted from MEDIUM to HIGH
    confidence and gains that file as `source_location` (ADR-035 D4's
    "L5 reachability <-> PR changed files" note in `crosscheck.py`)."""
    from abicheck.buildsource.crosscheck import CrosscheckConfig, run_crosschecks
    from abicheck.checker_policy import Confidence

    from .test_crosscheck_parity import _g20_snapshot

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
def test_compare_has_no_changed_path_option(flag: str, tmp_path: Path) -> None:
    from abicheck.model import AbiSnapshot

    path = write_snapshot(
        AbiSnapshot(library="libfoo.so", version="1.0"), tmp_path / "snap.abi.json"
    )
    value = "origin/main" if flag == "--since" else "src/foo.h"
    result = invoke_cli("compare", str(path), str(path), flag, value)
    assert result.exit_code == 64, result.output
    assert "No such option" in result.output and flag in result.output

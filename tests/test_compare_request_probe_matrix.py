# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``CompareRequest.old_probe_matrix``/``new_probe_matrix``.

The typed API now takes the build-configuration matrices the CLI accepts as
``--build-info old=<matrix>``/``new=<matrix>``; both routes diff them through
``workflows.pair_evidence.load_probe_matrix_changes``. Parity of the findings
is the F2 ``probe_matrix`` axis; this module pins the one-sided rejection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _family_f2_routes import _invoke, _probe_matrices, write_operands

from abicheck.errors import ValidationError
from abicheck.service import CompareRequest, InputSpec
from abicheck.service_compare_pipeline import run_compare_request

ONE_SIDED = "a build-configuration matrix needs both sides"


@pytest.mark.parametrize("side", ["old", "new"])
def test_one_sided_matrix_is_rejected_on_both_front_ends(
    side: str, tmp_path: Path
) -> None:
    ops = write_operands(tmp_path, "identical")
    old_m, new_m = _probe_matrices(tmp_path)
    matrix = old_m if side == "old" else new_m
    with pytest.raises(ValidationError, match=ONE_SIDED):
        run_compare_request(
            CompareRequest(
                old=InputSpec(path=ops.old),
                new=InputSpec(path=ops.new),
                **{f"{side}_probe_matrix": matrix},
            )
        )
    cli = _invoke(
        ["compare", str(ops.old), str(ops.new), "--build-info", f"{side}={matrix}"]
    )
    assert cli.exit_code == 64
    assert ONE_SIDED in cli.output


def test_no_matrix_adds_nothing(tmp_path: Path) -> None:
    ops = write_operands(tmp_path, "identical")
    res = run_compare_request(
        CompareRequest(old=InputSpec(path=ops.old), new=InputSpec(path=ops.new))
    )
    assert not [
        c for c in res.diff.changes if c.kind.value == "cxx_standard_floor_raised"
    ]


@pytest.mark.parametrize("side", ["old", "new"])
def test_one_sided_matrix_fails_validation_before_any_input_is_resolved(
    side: str, tmp_path: Path
) -> None:
    """Operands that do not exist prove the rejection precedes extraction."""
    old_m, new_m = _probe_matrices(tmp_path)
    request = CompareRequest(
        old=InputSpec(path=tmp_path / "missing-old.so"),
        new=InputSpec(path=tmp_path / "missing-new.so"),
        **{f"{side}_probe_matrix": old_m if side == "old" else new_m},
    )
    assert any(ONE_SIDED in e for e in request.validation_errors())
    with pytest.raises(ValidationError, match=ONE_SIDED):
        run_compare_request(request)

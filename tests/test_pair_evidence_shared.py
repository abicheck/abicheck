# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Every compare route folds a pair's evidence through one function.

The native ``compare`` CLI, the typed ``run_compare_request`` pipeline and a
directory/package member must all run the build-source diff and the abi3
audit through ``workflows.pair_evidence.fold_pair_evidence`` -- one place, one
order -- so the findings that reach classification cannot drift by route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _family_f2_routes import (
    AXES_BY_NAME,
    run_api,
    run_cli,
    run_release_member,
    write_operands,
)

import abicheck.workflows.pair_evidence as pair_evidence


@pytest.mark.parametrize(
    "route", [run_cli, run_api, run_release_member], ids=["cli", "api", "release"]
)
def test_route_folds_evidence_through_the_shared_fold(
    route: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = pair_evidence.fold_pair_evidence

    def counting(old: Any, new: Any, **kwargs: Any) -> Any:
        calls.append(new.version)
        return real(old, new, **kwargs)

    monkeypatch.setattr(pair_evidence, "fold_pair_evidence", counting)
    ops = write_operands(tmp_path, "removal_and_addition")
    outcome = route(ops, AXES_BY_NAME["default"], tmp_path)
    assert outcome.verdict == "BREAKING"
    assert calls == ["2.0"]

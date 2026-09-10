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

"""``depth_evidence_contract_satisfied``'s liveness rule, exhaustively.

The primitive both halves of ADR-064's depth axis call. Its whole point is
the distinction the hard floor it replaced did not make: a side that falls
short of the pinned rung is only a contract failure when *this run actually
extracted it*, since a pre-serialized snapshot was never asked to reach any
depth (`policy/depth_evidence_contract.py`'s "Live extraction only" note).

Lives in its own module rather than in ``test_service_unit.py``, where the
test it replaced sat: it exercises a `policy` primitive directly, not the
service layer, and that file is at its `architecture/debt.yaml` baseline.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


@pytest.mark.parametrize("old_live", (True, False))
@pytest.mark.parametrize("new_live", (True, False))
@pytest.mark.parametrize("failing_side", ("old", "new"))
def test_only_a_live_short_side_trips_the_contract(
    old_live: bool, new_live: bool, failing_side: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which side is short, crossed with which sides this run extracted.

    Replaces a test that asserted the *message* named the failing side
    ("new side"), which only meant something while the shortfall raised.
    The contract is now a boolean on the result, so the invariant worth
    stating is the one the old hard fail had wrong: a short side is only
    a contract failure when this run actually extracted it. Exercised on
    the shared primitive both halves of the axis call
    (`depth_evidence_contract_satisfied`), across all eight
    combinations, rather than through one hand-picked case.
    """
    from abicheck.model import AbiSnapshot
    from abicheck.policy.depth_evidence_contract import (
        depth_evidence_contract_satisfied,
    )

    short = AbiSnapshot(library="libtest", version="short")
    deep = AbiSnapshot(library="libtest", version="deep")
    old_snap = short if failing_side == "old" else deep
    new_snap = short if failing_side == "new" else deep

    def _by_version(build_source, snap):
        return "binary" if snap.version == "short" else "source"

    monkeypatch.setattr(
        "abicheck.policy.depth_evidence_contract.gated_source_label", _by_version
    )
    satisfied = depth_evidence_contract_satisfied(
        "source", old_snap, new_snap, old_is_live=old_live, new_is_live=new_live
    )

    short_side_is_live = old_live if failing_side == "old" else new_live
    assert satisfied is not short_side_is_live


@pytest.mark.integration
@pytest.mark.parametrize("operand", ("binary", "linker_script"))
def test_a_linker_script_operand_is_live_on_the_typed_api(
    tmp_path: Path, operand: str
) -> None:
    """A GNU ld linker script is an operand this run *does* extract.

    ``detect_binary_format`` answers ``None`` for one (it is a text file),
    so the typed pipeline's first cut asked ``pair.old_fmt is not None`` and
    read a script operand as a stored snapshot -- skipping the depth floor
    for a side it then followed to a live DSO and parsed. It went unnoticed
    while ``enforce_requested_depth`` still fired unconditionally, and
    surfaced the moment that was removed (Codex review, P1 on `ba00389`).

    Parametrized over the script and the DSO it resolves to, because the
    invariant is that they answer the *same*: whether the caller names the
    artifact directly or through a script it points at is not a fact about
    how much evidence this run collected. Asserting only the script case
    would pass against a build that had stopped gating both.
    """
    if not __import__("shutil").which("gcc"):
        pytest.skip("gcc is required to build the live fixture")
    src = tmp_path / "l.c"
    src.write_text("int l(void){return 1;}\n", encoding="utf-8")
    made = []
    for side in ("old", "new"):
        so = tmp_path / f"libreal_{side}.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        script = tmp_path / f"libscript_{side}.so"
        script.write_text(f"INPUT({so.name})\n", encoding="utf-8")
        made.append(so if operand == "binary" else script)

    from abicheck.api_types import CompareRequest, InputSpec
    from abicheck.service_compare_pipeline import run_compare_request

    request = CompareRequest(
        old=InputSpec.of(made[0]), new=InputSpec.of(made[1]), depth="build"
    )
    noise = io.StringIO()
    with redirect_stderr(noise), redirect_stdout(noise):
        result = run_compare_request(request).diff
    # Neither side carries build evidence, and both are extracted by this
    # run, so the pinned rung is unmet however the operand was spelled.
    assert result.evidence_contract_error is True, result

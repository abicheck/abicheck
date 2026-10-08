"""``compare --no-baseline`` names each cross-source finding's providers.

Public-surface test for ``findings[].providers`` (audit report schema): every
catalog audit case that declares ``provider_assertions`` in
``catalog/ground_truth.json`` -- the hand-curated oracle, not anything the
implementation derives -- must produce, through the real CLI, a finding of
each asserted check whose ``providers`` list is exactly the asserted set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import example_catalog  # noqa: E402

_CASES = sorted(
    (name, entry["provider_assertions"])
    for name, entry in example_catalog.load_ground_truth()["verdicts"].items()
    if entry.get("provider_assertions")
)


def test_some_case_declares_provider_assertions() -> None:
    assert len(_CASES) >= 4


@pytest.mark.parametrize(("case", "assertions"), _CASES, ids=[c for c, _ in _CASES])
def test_findings_name_their_providers(case: str, assertions: dict) -> None:
    snapshot = example_catalog.case_dir(case) / "snapshot.abi.json"
    result = CliRunner().invoke(
        main, ["compare", "--no-baseline", str(snapshot), "-o", "json=-"]
    )
    payload = json.loads(result.output[result.output.index("{") :])
    findings = payload.get("findings") or []
    for check, providers in assertions.items():
        seen = [
            sorted(f.get("providers") or []) for f in findings if f.get("kind") == check
        ]
        assert sorted(providers) in seen, (check, seen)

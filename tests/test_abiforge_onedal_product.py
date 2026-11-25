# SPDX-License-Identifier: Apache-2.0
"""Check the oneDAL witness joins, with an opt-in real SDK replay."""

from __future__ import annotations

import csv
import gzip
import json
import os
from pathlib import Path

import pytest

from abiforge.run import onedal_arguments, onedal_status_return, onedal_table_ownership


def test_product_receipts_cover_provider_matched_signatures():
    product = onedal_table_ownership.PRODUCT
    with gzip.open(product / "ledger.csv.gz", "rt") as handle:
        ledger = [r for r in csv.DictReader(handle) if r["providers"]]
    arguments = {r["symbol"] for r in ledger if r["kind"] == "by_value_arg0"}
    evidence = json.loads((product / "argument-abi.json").read_text())
    assert evidence["checker"]["mutant"]["arguments"] == {
        symbol: ["p0:nontrivial", "p0:trivial"] for symbol in arguments
    }
    assert evidence["client_exits"] == {
        symbol: {"old": 0, "control": 0, "mutant": 41} for symbol in arguments
    }
    status = json.loads((product / "status-return-abi.json").read_text())
    assert status["client_exits"] == {
        name: {"release": 0, "old": 0, "control": 0, "mutant": 42}
        for name in ("compute", "table")
    }
    assert status["checker"]["control"]["verdict"] == "NO_CHANGE"
    assert status["checker"]["mutant"]["status_return"] == [
        ["memory (sret)", "regs(INTEGER)"]
    ]
    tables = {
        r["symbol"]
        for r in ledger
        if r["kind"] == "by_value_return"
        and r["detail"] == "data_management::NumericTablePtr"
    }
    with (product / "table-ownership.csv").open() as handle:
        runs = list(csv.DictReader(handle))
    assert {(r["symbol"], r["package"]) for r in runs} == {
        (s, v) for s in tables for v in ("dal-2025.9.0", "dal-2025.11.0")
    }
    assert all(
        {k: int(r[k]) for k in onedal_table_ownership.MODES}
        == onedal_table_ownership.MODES
        for r in runs
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("ABIFORGE_ONEDAL_OLD") or not os.getenv("ABIFORGE_ONEDAL_NEW"),
    reason="requires the pinned oneDAL CPU development packages",
)
def test_installed_cpu_sdk(tmp_path):
    old = Path(os.environ["ABIFORGE_ONEDAL_OLD"]).resolve()
    new = Path(os.environ["ABIFORGE_ONEDAL_NEW"]).resolve()
    assert onedal_status_return.run(old, tmp_path / "status")
    assert onedal_arguments.run(old, tmp_path / "arguments", check=True)
    assert onedal_table_ownership.run(old, new, tmp_path / "tables")


def test_release_campaigns_reject_ambiguous_inputs(tmp_path):
    from abiforge.run import onedal_loadability

    old = tmp_path / "old/install"
    new = tmp_path / "new/install"
    with pytest.raises(ValueError, match="distinct package names"):
        onedal_table_ownership.run(old, new, tmp_path / "ownership")
    (tmp_path / "stale").touch()
    with pytest.raises(ValueError, match="empty output"):
        onedal_loadability.run(old, tmp_path)


def test_export_identity_includes_compatibility_versions(monkeypatch):
    from abiforge.run import onedal_exports

    monkeypatch.setattr(
        onedal_exports.subprocess,
        "check_output",
        lambda *a, **kw: "foo@VER_1 T 0\nbar@@VER_2 T 1\nbaz T 2\n",
    )
    assert onedal_exports.symbols("provider.so") == {"foo", "bar", "baz"}

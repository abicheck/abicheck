"""Trace the recorded product edits to executable, product-independent mutations."""

import csv
import subprocess
import sys
from pathlib import Path

from abiforge.run import l2_representatives, lifetime_campaign, value_state


def test_product_edit_links_resolve_to_generic_mutations(tmp_path):
    root = Path(__file__).resolve().parents[1]
    audit = root / "research/onedal-coverage/check_scenario_basis.py"
    matrix = tmp_path / "rows.csv"
    subprocess.run([sys.executable, audit, "--rows", matrix], check=True)
    available = (
        {f"value_state/{name}" for name in value_state.EDITS}
        | {f"lifetime_campaign/{name}" for name in lifetime_campaign.CASES}
        | {
            f"l2_representatives/{family}/{variant}"
            for family, variants in l2_representatives.FAMILIES.items()
            for variant in variants
        }
    )
    with matrix.open() as stream:
        rows = list(csv.DictReader(stream))
    linked = [row for row in rows if row["status"] == "linked edit"]
    assert linked
    for row in linked:
        assert row["witness"]
        assert set(row["generic_mutations"].split("|")) <= available
        assert len(row["generic_mutations"].split("|")) == len(
            row["package_edits"].split("|")
        )
    assert all(
        not row["generic_mutations"]
        for row in rows
        if row["status"] == "candidate only"
    )
    spmd = [r for r in rows if r["witness"] == "spmd_consumer.cpp"]
    assert sum(r["scenario_contracts"].startswith("erased_buffer") for r in spmd) == 8
    for row in spmd:
        if row["status"] == "package: virtual-dispatch-pass":
            assert row["scenario_contracts"] == "l2_representatives/virtual/swap"
        elif row["key"].endswith(("::get_queue", "::make_communicator")):
            assert row["scenario_contracts"] == ""

    from abiforge.operators import OP_BY_NAME

    withdrawals = [row for row in rows if row["ledger"] == "checker-withdrawals.tsv"]
    assert len(withdrawals) == 59
    assert all(
        set(row["generic_mutations"].split("|")) <= OP_BY_NAME.keys()
        for row in withdrawals
    )

    observed = [r for r in rows if r["status"] == "paired package observation"]
    assert {r["package_edits"] for r in observed} == {
        "borrow",
        "extra_owner",
        "reject_success",
        "swallow_error",
    }
    assert all(r["generic_mutations"] in available for r in observed)
    content = [r for r in rows if r["package_edits"] == "drop_sum"]
    assert {r["key"] for r in content} == {"dal-2025.9.0", "dal-2025.11.0"}
    assert all(r["status"] == "implementation-only result content" for r in content)

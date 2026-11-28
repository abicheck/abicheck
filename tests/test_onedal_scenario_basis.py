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

    observed = [
        r
        for r in rows
        if r["ledger"] in {"table-ownership.csv", "return-witnesses.csv"}
        and r["status"] == "paired package observation"
    ]
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
    arguments = [r for r in rows if r["ledger"] == "argument-abi.json"]
    assert len(arguments) == 6
    assert (
        {r["generic_mutations"] for r in arguments}
        == {"l2_representatives/large_argument/stack_value"}
        <= available
    )

    lowering = [r for r in rows if r["ledger"] == "return-lowering.csv"]
    options = [r for r in rows if r["ledger"] == "option-returns.csv"]
    assert len(lowering) == 225
    assert len(options) == 38
    assert all(not r["generic_mutations"] for r in lowering + options)

    members = [r for r in rows if r["scenario_contracts"] == "member return"]
    assert sum(r["status"] == "paired package observation" for r in members) == 3
    assert sum(r["status"] == "compiler observation only" for r in members) == 38
    assert (
        {r["generic_mutations"] for r in members}
        == {"l2_representatives/member_return/register"}
        <= available
    )

    slots = [r for r in rows if r["ledger"] == "serialization-slots.json"]
    assert {r["package_edits"] for r in slots} == {"insert", "remove", "swap"}
    assert all(r["generic_mutations"] in available | OP_BY_NAME.keys() for r in slots)

    pilots = [r for r in rows if r["status"] == "paired pilot observation"]
    assert {r["generic_mutations"] for r in pilots} == {
        "l2_representatives/layout/swap",
        "l2_representatives/virtual/swap",
    }
    slots_dwarf = next(
        r for r in pilots if r["key"] == "slots" and r["detail"] == "dwarf"
    )
    assert slots_dwarf["witness"] == "client exit 1, checker NO_CHANGE"

    enums = [r for r in rows if r["scenario_contracts"] == "scoped enum"]
    assert len(enums) == 44
    assert (
        {r["generic_mutations"] for r in enums if r["generic_mutations"]}
        == {
            "enum_class_append",
            "enum_class_enumerator_values_swapped",
            "enum_class_underlying_narrow",
        }
        <= OP_BY_NAME.keys()
    )

    exceptions = [r for r in rows if r["ledger"] == "exception-abi.json"]
    withdrawals = [r for r in exceptions if r["generic_mutations"]]
    assert len(exceptions) == 24 and len(withdrawals) == 22
    assert (
        {r["generic_mutations"] for r in withdrawals}
        == {"object_removed"}
        <= OP_BY_NAME.keys()
    )

    imports = [r for r in rows if r["ledger"] == "import-withdrawals.json"]
    assert sum(r["status"] == "loader failure" for r in imports) == 44
    assert (
        sum(
            r["status"] == "unresolved import with successful workload" for r in imports
        )
        == 19
    )
    assert (
        {r["generic_mutations"] for r in imports}
        == {"func_remove", "object_removed"}
        <= OP_BY_NAME.keys()
    )

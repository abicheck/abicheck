# SPDX-License-Identifier: Apache-2.0
"""Join executable and compiler receipts back to the SDK's export obligations."""

import csv
import gzip
import json

import pytest

from abiforge.run.onedal_release_matrix import PRODUCT, obligations


def test_three_release_presence_and_complete_return_receipts():
    with gzip.open(PRODUCT / "release-matrix.csv.gz", "rt") as f:
        matrix = list(csv.DictReader(f))
    keys = [
        (r["kind"], r["declaration"], r["symbol"], r["baseline_provider"])
        for r in matrix
    ]
    assert len(set(keys)) == len(keys)
    assert set(keys) == {
        (r["kind"], r["key"], r["symbol"], provider)
        for r in obligations()
        for provider in r["providers"].split(",")
    }
    assert all(r["dal-2025.9.0"] == r["dal-2025.11.0"] == "1" for r in matrix)
    assert {r["dal-2026.2.0"] for r in matrix} == {"0", "1"}
    returns = [r for r in matrix if r["kind"] == "by_value_return"]
    status = {r["symbol"] for r in returns if r["detail"] == "services::Status"}
    receipt = json.loads((PRODUCT / "status-exports.json").read_text())
    for mode in ("old-mutant", "mutant-old"):
        assert receipt[mode] == {"verdict": "BREAKING", "affected_exports": len(status)}
    assert receipt["old-old"] == {"verdict": "NO_CHANGE", "affected_exports": 0}
    with (PRODUCT / "return-lowering.csv").open() as f:
        lowering = {r["symbol"]: r for r in csv.DictReader(f)}
    assert set(lowering) == {
        r["symbol"]
        for r in returns
        if r["detail"] not in ("services::Status", "data_management::NumericTablePtr")
    }
    with (PRODUCT / "option-returns.csv").open() as f:
        options = list(csv.DictReader(f))
    assert {r["symbol"] for r in options} == {
        r["symbol"]
        for r in returns
        if r["detail"] in ("result_option_id", "optional_result_id")
    }
    assert all(
        lowering[r["symbol"]]["llvm_return"] == "i64"
        and r["dal-2025.9.0"] == r["dal-2025.11.0"]
        for r in options
    )


def test_serialization_dispatch_and_variant_loss_receipts():
    slots = json.loads((PRODUCT / "serialization-slots.json").read_text())
    assert slots["old"]["method_exits"] == slots["control"]["method_exits"] == [21, 22]
    assert slots["swap"]["method_exits"] == [22, 21]
    assert slots["remove"]["method_exits"] == [22]
    assert slots["insert"]["method_exits"] == [23, 21]
    for key in ("swap", "remove", "insert"):
        assert slots[key]["verdict"] == "BREAKING"
        assert "type_vtable_changed" in slots[key]["kinds"]
    variants = json.loads((PRODUCT / "variant-loss.json").read_text())
    assert set(variants) == {"control", "C1", "C2", "D0", "D1", "D2", "data"}
    for absent, r in variants.items():
        assert len(r["exits"]) == 6
        assert {tag for tag, code in r["exits"].items() if code == 127} == (
            {absent} if absent != "control" else set()
        )
        assert set(r["exits"].values()) <= {0, 127}


def test_virtual_offsets_and_status_impact_keep_their_evidence_sources():
    rows = list(obligations())
    virtuals = {r["symbol"] for r in rows if r["kind"] == "virtual_slot"}
    with gzip.open(PRODUCT / "virtual-slots.csv.gz", "rt") as f:
        cells = list(csv.DictReader(f))
    with (PRODUCT / "header-slots.csv").open() as f:
        headers = list(csv.DictReader(f))
    for release in ("dal-2025.9.0", "dal-2025.11.0"):
        observed = {
            r["symbol"] for r in cells if r["release"] == release and r["vtable"]
        }
        header_only = {r["symbol"] for r in headers if r["release"] == release}
        assert virtuals - observed == header_only
        assert all(int(r["byte_offset"]) % 8 == 0 for r in cells if r["vtable"])
    with gzip.open(PRODUCT / "status-impact.csv.gz", "rt") as f:
        impact = list(csv.DictReader(f))
    assert {r["symbol"] for r in impact} == {
        r["symbol"]
        for r in rows
        if r["kind"] == "by_value_return" and r["detail"] == "services::Status"
    }
    for r in impact:
        assert (r["sdk_declaration"] == "virtual") == (r["symbol"] in virtuals)
        assert (r["forward"], r["reverse"], r["control"]) == ("1", "1", "0")


def test_shared_return_types_have_independent_ownership_controls():
    from collections import Counter

    with (PRODUCT / "return-lowering.csv").open() as f:
        counts = Counter(
            r["sdk_type"]
            for r in csv.DictReader(f)
            if r["sdk_type"].startswith("daal::services::interface1::SharedPtr<")
        )
    with (PRODUCT / "shared-return-types.csv").open() as f:
        runs = list(csv.DictReader(f))
    for release in ("dal-2025.9.0", "dal-2025.11.0"):
        assert {
            r["sdk_type"]: int(r["return_symbols"])
            for r in runs
            if r["release"] == release
        } == counts
    assert all(
        (r["control"], r["borrow"], r["extra_owner"]) == ("0", "11", "12") for r in runs
    )


def test_all_source_argument_positions_are_accounted_for():
    from abiforge.run.onedal_arguments import argument_inventory

    positions, symbols = argument_inventory()
    receipt = json.loads((PRODUCT / "argument-abi.json").read_text())
    assert receipt["positions"] == positions
    assert set(receipt["calls"]) == symbols


def test_minor_release_added_virtual_cells():
    with gzip.open(PRODUCT / "virtual-slots.csv.gz", "rt") as stream:
        releases = {}
        for row in csv.DictReader(stream):
            if row["vtable"]:
                releases.setdefault(row["release"], set()).add(
                    (row["provider"], row["vtable"], row["byte_offset"], row["symbol"])
                )
    old, new = (releases["dal-" + version] for version in ("2025.9.0", "2025.11.0"))
    assert not old - new
    assert new - old == {
        (
            "libonedal_core.so.3",
            table,
            offset,
            "_ZN4daal10algorithms10interface113AlgorithmImplILNS_11ComputeModeE1EE"
            + target,
        )
        for table in (
            "_ZTVN4daal10algorithms19optimization_solver10interface110BatchIfaceE",
            "_ZTVN4daal10algorithms6kmeans4init10interface29BatchBaseE",
        )
        for offset, target in (
            ("32", "18checkComputeParamsEv"),
            ("40", "11checkResultEv"),
        )
    }


def test_original_sdk_status_impact_matches_ledger_on_both_frontends():
    expected = {
        row["symbol"]
        for row in obligations()
        if row["kind"] == "by_value_return" and row["detail"] == "services::Status"
    }
    receipts = json.loads((PRODUCT / "status-original-headers.json").read_text())
    assert set(receipts) == {"clang", "castxml"}
    assert receipts["clang"] == receipts["castxml"]
    assert len(receipts["clang"]["old-mutant"]["symbols_sha256"]) == 64
    for runs in receipts.values():
        assert runs["old-mutant"] == runs["mutant-old"]
        assert runs["old-mutant"]["verdict"] == "BREAKING"
        assert runs["old-mutant"]["ledger_exports"] == len(expected)
        assert runs["old-mutant"]["affected_exports"] >= len(expected)
        assert runs["old-old"] == {
            "verdict": "NO_CHANGE",
            "affected_exports": 0,
            "ledger_exports": 0,
        }


def assert_table_clients(receipt, variants):
    assert receipt["selector"] == "opencl:cpu"
    assert sorted(
        (r["mode"], r["package"], r["provider"], r["exit"]) for r in receipt["clients"]
    ) == sorted(
        (mode, package, provider, code)
        for mode in ("host", "sycl")
        for package in ("dal-2025.9.0", "dal-2025.11.0")
        for provider, code in variants
    )


def test_frozen_host_and_sycl_table_layout_receipt():
    receipt = json.loads((PRODUCT / "table-layout.json").read_text())
    assert_table_clients(receipt, (("old", 0), ("extend", 0), ("swap", 42)))
    assert receipt["checker"] == {
        "extend": {"members": [], "verdict": "COMPATIBLE", "exit": 0},
        "old": {"members": [], "verdict": "NO_CHANGE", "exit": 0},
        "swap": {
            "members": ["data_layout::column_major", "data_layout::row_major"],
            "verdict": "BREAKING",
            "exit": 4,
        },
    }


def test_frozen_cross_queue_client_observes_producer_write():
    rows = json.loads((PRODUCT / "device-release-matrix.json").read_text())
    deps = [r for r in rows if r["client"] == "dependency"]
    assert sorted((r["package"], r["exit"], r.get("stdout")) for r in deps) == [
        ("dal-2025.11.0", 0, "42"),
        ("dal-2025.9.0", 0, "42"),
        ("dal-2026.2.0", 127, None),
    ]
    assert all(r["matches"] for r in deps)


def test_two_rank_sycl_communicator_release_receipt():
    matrix = json.loads((PRODUCT / "device-release-matrix.json").read_text())
    rows = {row["package"]: row for row in matrix if row["client"] == "spmd"}
    assert set(rows) == {"dal-2025.9.0", "dal-2025.11.0", "dal-2026.2.0"}
    for package in ("dal-2025.9.0", "dal-2025.11.0"):
        assert rows[package]["exit"] == 0
        assert "libonedal_dpc.so.3" in rows[package]["providers"]
    assert rows["dal-2026.2.0"]["exit"] == 127
    assert rows["dal-2026.2.0"]["missing_soname"] == "libonedal_dpc.so.3"


@pytest.mark.parametrize("frontend", ["clang", "castxml"])
def test_frozen_sparse_indexing_value_and_storage_changes(frontend):
    receipt = json.loads((PRODUCT / f"sparse-indexing-{frontend}.json").read_text())
    assert receipt["frontend"] == frontend
    assert_table_clients(
        receipt, (("old", 0), ("extend", 0), ("swap", 42), ("narrow", 42))
    )
    assert receipt["checker"] == {
        "old": {"members": [], "exit": 0, "verdict": "NO_CHANGE"},
        "extend": {"members": [], "exit": 0, "verdict": "COMPATIBLE"},
        "swap": {
            "members": ["sparse_indexing::one_based", "sparse_indexing::zero_based"],
            "exit": 4,
            "verdict": "BREAKING",
        },
        "narrow": {
            "members": [],
            "exit": 4,
            "verdict": "BREAKING",
            "layout_changes": {
                "enum_underlying_size_changed": ["4", "1"],
                "type_size_changed": ["64", "16"],
                "type_alignment_changed": ["32", "8"],
                "type_field_offset_changed": ["32", "8"],
            },
        },
    }


def test_export_surface_joins_use_provider_and_preserve_shared_names():
    surface = json.loads((PRODUCT / "export-surface.json").read_text())
    providers = surface["providers"]
    assert len(providers) == 6
    witnesses = surface["witnessed_lower_bound"]
    assert (
        witnesses["status_l2"]
        == json.loads((PRODUCT / "status-original-headers.json").read_text())["clang"][
            "old-mutant"
        ]["affected_exports"]
    )
    assert (
        witnesses["status_l2"]
        <= witnesses["distinct_names"]
        <= witnesses["provider_pairs"]
    )
    assert surface["unique_names"] < sum(p["exports"] for p in providers.values())
    assert surface["unique_sdk_matches"] <= sum(p["sdk"] for p in providers.values())
    for row in providers.values():
        assert (
            row["ledger_edit"]
            <= row["sdk"]
            <= row["sdk_name_any_provider"]
            <= row["exports"]
        )
        assert len(set(row["imports_outside_sdk"])) <= row["client_import"]
    dpc = providers["libonedal_dpc.so.3"]
    assert (
        dpc["client_import"] and len(dpc["imports_outside_sdk"]) == dpc["client_import"]
    )
    scan = surface["sdk_inventory"]
    assert scan["provider_pairs"]["libonedal_dpc.so.3"] > dpc["sdk"]
    assert (
        0
        < len(scan["client_imports_outside_sdk"]["libonedal_dpc.so.3"])
        < dpc["client_import"]
    )
    major = surface["releases"]["dal-2026.2.0"]
    assert 0 < major["sdk_inventory"]["unique_names"] <= major["unique_names"]
    minor = surface["releases"]["dal-2025.11.0"]["provider_changes"]
    withdrawn = json.loads((PRODUCT / "minor-dpc-withdrawals.json").read_text())
    assert minor["libonedal_dpc.so.3"]["removed"] == len(withdrawn) == 4
    assert len({r["symbol"] for r in withdrawn}) == len(withdrawn)
    for row in withdrawn:
        assert row["detected_removal"] and "set_csr_data" in row["symbol"]
        assert row["exits"] == {"dal-2025.9.0": 0, "dal-2025.11.0": 127}


def test_status_member_specializations_have_independent_call_witnesses():
    receipt = json.loads((PRODUCT / "status-member-templates.json").read_text())
    assert receipt["old"] == "sret" and receipt["mutant"] == "register"
    names = set(receipt["symbols"])
    assert len(names) == 38
    assert sum("HomogenNumericTable" in name for name in names) == 36
    assert sum("8allocateIfE" in name for name in names) == 2


def test_frozen_import_coverage_tracks_real_consumers_and_sdk_locations():
    surface = json.loads((PRODUCT / "export-surface.json").read_text())
    locations = json.loads((PRODUCT / "import-declarations.json").read_text())
    withdrawals = json.loads((PRODUCT / "import-withdrawals.json").read_text())
    expected = {
        (provider, name)
        for provider, names in surface["sdk_inventory"][
            "client_imports_outside_sdk"
        ].items()
        for name in names
    }
    assert {(p, s) for p, names in locations.items() for s in names} == expected
    assert {(row["provider"], row["symbol"]) for row in withdrawals} == expected
    assert len(withdrawals) == len(expected)
    scenarios = json.loads((PRODUCT / "withdrawal-scenarios.json").read_text())
    assert sum(s["count"] for s in scenarios) == len(withdrawals)
    assert len(
        {(s["entity"], tuple(s["relocations"]), s["withdrawn"]) for s in scenarios}
    ) == len(scenarios)
    outcomes = {
        (r["provider"], r["symbol"], r["client"], r["withdrawn"]) for r in withdrawals
    }
    assert all(
        (s["provider"], s["symbol"], s["client"], s["withdrawn"]) in outcomes
        for s in scenarios
    )
    for row in withdrawals:
        provider, symbol = row["provider"], row["symbol"]
        loc = locations[provider][symbol]
        assert loc["line"] > 0 and loc["header"] and loc["declaration"] and loc["type"]
        assert (
            row["client"]
            in surface["providers"][provider]["imports_outside_sdk"][symbol]
        )
        assert row["baseline_exit"] == row["minor_exit"] == 0
        assert row["unresolved_relocation"] and row["withdrawn"] in {0, 127}


def test_major_dispatcher_and_sycl_inventory():
    folder = PRODUCT / "2026.2"
    roots = set((folder / "roots.txt").read_text().splitlines())
    with (folder / "unparsed.csv").open() as stream:
        assert list(csv.DictReader(stream)) == []
    with gzip.open(folder / "obligations.csv.gz", "rt") as stream:
        rows = list(csv.DictReader(stream))
    params = {r for r in roots if "/parameters/cpu/" in r}
    assert len(roots) == 432 and len(params) == 6
    assert params <= {r["header"] for r in rows}
    assert len({r["symbol"] for r in rows if r["symbol"] and r["providers"]}) == 2189
    original = PRODUCT.parents[1] / "onedal-sycl-usm/inventory.tsv"
    before = set(original.read_text().splitlines()[1:])
    after = set((folder / "sycl.tsv").read_text().splitlines()[1:])
    assert before < after and len(after) == 61

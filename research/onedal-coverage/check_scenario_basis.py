"""Check the oneDAL-to-generic-scenario crosswalk against the tracked evidence."""

import argparse
import csv
import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = Path(__file__).resolve().parent / "product"

# A package replay may exercise multiple independent ABI contracts.
RUNNERS = {
    "calling convention": "argument_checker argument_client arguments option_returns return_lowering status_exports status_member_templates status_return",
    "ownership and calling convention": "shared_return_types table_ownership",
    "symbol binding": "export_imports export_lookup import_withdrawals minor_withdrawals variant_loss withdrawal_scenarios",
    "provider resolution": "loadability",
    "PE export identity": "pe_exports",
    "virtual dispatch and representation": "tree virtual_slots serialization_slots",
    "runtime type identity and binding": "exception_abi",
    "record representation and backend boundary": "table_layout",
    "backend boundary and provider resolution": "sycl device_release",
    "ownership and symbol binding": "release_clients",
    "inventory and attribution": "elf_extraction export_surface exports import_declarations release_matrix",
}
FIXTURES = {
    "ownership": "collection_ownership shared_return_types",
    "runtime type identity": "exception_client exception_provider",
    "symbol binding": "export_lookup release_client",
    "virtual dispatch": "header_slots serialization_slots",
    "calling convention": "modern_returns status_member_templates status_return value_arguments",
}
KINDS = {
    "symbol binding": "constructor_abi_variant destructor_abi_variant exported_variable",
    "representation": "field_decl enum_decl",
    "calling convention": "by_value_return by_value_arg0 by_value_arg1 by_value_arg2 by_value_arg3 by_value_arg4 by_value_arg5 by_value_arg6 by_value_arg7",
    "dispatch and type identity": "virtual_slot cxx_base_specifier",
    "declaration inventory; concrete operation required": "cxx_method function_decl constructor destructor class_template function_template class_decl struct_decl",
}
EDITS = {
    "ownership": "borrow extra_owner",
    "record representation": "struct_swap_fields",
    "calling convention": "record_call_triviality",
    "virtual dispatch": "virtual_reorder",
    "returned state": "reject_success swallow_error discard_copy discard_assignment no_op_clear",
}


# Case IDs refer to the generated generic mutations, not the product names.
REPRESENTATIVES = {
    "borrow": "lifetime_campaign/early_release",
    "extra_owner": "lifetime_campaign/extra_owner",
    "struct_swap_fields": "l2_representatives/layout/swap",
    "record_call_triviality": "l2_representatives/calls/nontrivial",
    "virtual_reorder": "l2_representatives/virtual/swap",
    "reject_success": "value_state/reject_success",
    "swallow_error": "value_state/swallow_error",
    "discard_copy": "value_state/discard_copy",
    "discard_assignment": "value_state/discard_assignment",
    "no_op_clear": "value_state/ignore_reset",
}


def check_mapping(group, actual, mapping):
    listed = [name for names in mapping.values() for name in names.split()]
    if len(listed) != len(set(listed)) or set(listed) != actual:
        raise ValueError(
            f"{group}: unmapped={sorted(actual - set(listed))}, stale={sorted(set(listed) - actual)}, duplicate={len(listed) != len(set(listed))}"
        )
    print(f"{group}: {len(actual)} mapped")


def main():
    check_mapping(
        "package runners",
        {
            p.stem.removeprefix("onedal_")
            for p in (ROOT / "abiforge/run").glob("onedal_*.py")
        },
        RUNNERS,
    )
    check_mapping(
        "package fixtures",
        {p.stem for p in (ROOT / "abiforge/fixtures/onedal").glob("*.cpp")},
        FIXTURES,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rows", type=Path, help="write one disposition per obligation to CSV"
    )
    output = parser.parse_args().rows
    kinds, edits = set(), set()
    kind_map = {
        name: family for family, names in KINDS.items() for name in names.split()
    }
    edit_map = {
        name: family for family, names in EDITS.items() for name in names.split()
    }
    dispositions = []
    for file in (
        "ledger.csv.gz",
        "extended-obligations.csv.gz",
        "2026.2/obligations.csv.gz",
    ):
        linked = 0
        with gzip.open(PRODUCT / file, "rt") as stream:
            for row in csv.DictReader(stream):
                kind = row["kind"]
                kinds.add(kind)
                labels = tuple(filter(None, row.get("edit_family", "").split("|")))
                edits.update(labels)
                witness = row.get("independent_edit_witness", "")
                linked += bool(witness and labels)
                families = sorted({edit_map.get(label, "unmapped") for label in labels})
                dispositions.append(
                    (
                        file,
                        row["key"],
                        kind,
                        row["detail"],
                        row["symbol"],
                        kind_map.get(kind, "unmapped"),
                        "|".join(labels),
                        "|".join(families),
                        "|".join(REPRESENTATIVES[label] for label in labels),
                        witness,
                        "linked edit" if witness and labels else "candidate only",
                    )
                )
        print(f"{file}: {linked} rows with an explicit package edit-to-scenario link")
    check_mapping("declaration kinds", kinds, KINDS)
    check_mapping("observed mutation labels", edits, EDITS)
    device = ROOT / "research/onedal-sycl-usm"
    with (device / "signature-witnesses.tsv").open() as stream:
        signatures = list(csv.DictReader(stream, delimiter="\t"))

    def signature_key(row):
        return row["header"], row["qualified_name"], row["signature"]

    with (device / "inventory.tsv").open() as stream:
        older = {signature_key(row) for row in csv.DictReader(stream, delimiter="\t")}
    with (PRODUCT / "2026.2/sycl.tsv").open() as stream:
        newer = {signature_key(row) for row in csv.DictReader(stream, delimiter="\t")}
    if {signature_key(row) for row in signatures} != older:
        raise ValueError("SYCL signature receipts do not match the inventory")
    print(
        f"SYCL: {len(older)} older signatures, {len(newer)} newer signatures, {len(newer - older)} newer-only"
    )
    # This receipt records a header-projection edit independently of runtime success.
    # It identifies a declaration by qualified name and full written signature.
    with (device / "checker-withdrawals.tsv").open() as stream:
        withdrawals = list(csv.DictReader(stream, delimiter="\t"))
    expected_keys = {(name, signature) for _, name, signature in older}
    actual_keys = {(r["qualified_name"], r["signature"]) for r in withdrawals}
    if (
        actual_keys != expected_keys
        or len(withdrawals) != len(older)
        or len(actual_keys) != len(withdrawals)
    ):
        raise ValueError(
            "SYCL withdrawal receipts must match each signature exactly once"
        )
    for row in withdrawals:
        if tuple(
            row[key]
            for key in ("isolated_removal", "bulk_removal", "unchanged_removal")
        ) != ("True", "True", "False"):
            raise ValueError(
                f"withdrawal or unchanged control failed for {row['qualified_name']} {row['signature']}"
            )
        dispositions.append(
            (
                "checker-withdrawals.tsv",
                row["qualified_name"],
                "SYCL signature",
                row["signature"],
                "",
                "declaration availability",
                "withdraw declaration",
                "source surface",
                "header_inline_removed|src_public_function_removed",
                "checker-withdrawals.tsv",
                "projected withdrawal; inline/exported kind unresolved",
            )
        )
    for row in signatures:
        witness = row["witness"]
        scenario = {
            "dependency_consumer.cpp": "dependency_forwarding",
            "ownership_consumer.cpp": "lifetime_campaign",
            "dispatch_consumer.cpp": "template_wrapper_consumers",
            "abiforge.run.onedal_sycl": "template_wrapper_consumers",
        }.get(witness, "")
        if witness == "spmd_consumer.cpp":
            operation = row["qualified_name"].rsplit("::", 1)[-1]
            if row["result"] == "virtual-dispatch-pass":
                scenario = "l2_representatives/virtual/swap"
            elif operation in {"allgatherv", "allreduce", "bcast", "sendrecv_replace"}:
                scenario = "erased_buffer (typed and byte forms only)"
        dispositions.append(
            (
                "signature-witnesses.tsv",
                row["qualified_name"],
                "SYCL signature",
                row["signature"],
                "",
                "backend boundary",
                "",
                scenario,
                "",
                witness,
                "package: " + row["result"],
            )
        )
    for header, name, sig in sorted(newer):
        dispositions.append(
            (
                "2026.2/sycl.tsv",
                name,
                "SYCL signature",
                sig,
                "",
                "backend boundary",
                "",
                "",
                "",
                "",
                "candidate only",
            )
        )
    if output is not None:
        with output.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "ledger",
                    "key",
                    "kind",
                    "detail",
                    "symbol",
                    "candidate_contract",
                    "package_edits",
                    "scenario_contracts",
                    "generic_mutations",
                    "witness",
                    "status",
                )
            )
            writer.writerows(dispositions)


if __name__ == "__main__":
    main()

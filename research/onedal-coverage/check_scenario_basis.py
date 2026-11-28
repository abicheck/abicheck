"""Check the oneDAL-to-generic-scenario crosswalk against the tracked evidence."""

import argparse
import csv
import gzip
import json
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


def return_receipts():
    """Keep controls paired with mutations of the same packaged entry point."""
    groups = {}
    for filename in ("return-witnesses.csv", "table-ownership.csv"):
        with (PRODUCT / filename).open() as stream:
            for row in csv.DictReader(stream):
                key = (
                    filename,
                    row.get("environment") or row["package"],
                    row.get("family", "table ownership"),
                    row["symbol"],
                )
                variants = groups.setdefault(key, {})
                observations = (
                    {row["variant"]: row["client_exit"]}
                    if "variant" in row
                    else {
                        v: row[v]
                        for v in ("baseline", "forward", "borrow", "extra_owner")
                    }
                )
                if variants.keys() & observations.keys():
                    raise ValueError(f"duplicate return receipt: {key}")
                variants.update(observations)
    for (filename, release, family, symbol), observations in groups.items():
        expected = {"baseline": "0", "forward": "0"}
        mutations = {
            "status": {"swallow_error": "20", "reject_success": "23"},
            "statistics": {"reject_success": "30", "drop_sum": "31"},
        }.get(family, {"borrow": "11", "extra_owner": "12"})
        if observations != expected | mutations:
            raise ValueError(
                f"incomplete or unexpected return receipt: {release} {symbol}"
            )
        for edit in mutations:
            behavior = edit == "drop_sum"
            scenario = REPRESENTATIVES.get(edit, "")
            yield (
                filename,
                release,
                family,
                "baseline=0 forward=0",
                symbol,
                "returned payload"
                if behavior
                else (
                    "ownership"
                    if edit in ("borrow", "extra_owner")
                    else "returned state"
                ),
                edit,
                scenario.partition("/")[0],
                scenario,
                f"{family}: exit {observations[edit]}",
                "implementation-only result content"
                if behavior
                else "paired package observation",
            )


def argument_receipts():
    filename = "argument-abi.json"
    receipt = json.loads((PRODUCT / filename).read_text())
    calls = receipt["calls"]
    clients = receipt["client_exits"]
    checker = receipt["checker"]
    if (
        not calls
        or calls.keys() != clients.keys()
        or calls.keys() != checker["mutant"]["arguments"].keys()
    ):
        raise ValueError("argument witnesses disagree on the exercised calls")
    if checker["control"] != {"verdict": "NO_CHANGE", "exit": 0, "arguments": {}} or (
        checker["mutant"]["verdict"],
        checker["mutant"]["exit"],
    ) != ("BREAKING", 4):
        raise ValueError("unexpected argument checker result")
    for symbol, lowering in calls.items():
        if (
            lowering
            != {"old": "indirect", "control": "indirect", "mutant": "stack-by-value"}
            or clients[symbol] != {"old": 0, "control": 0, "mutant": 41}
            or checker["mutant"]["arguments"][symbol] != ["p0:nontrivial", "p0:trivial"]
        ):
            raise ValueError(f"unexpected argument witness for {symbol}")
        yield (
            filename,
            symbol,
            "by-value argument",
            "p0",
            symbol,
            "calling convention",
            "indirect to stack-by-value",
            "large aggregate argument",
            "l2_representatives/large_argument/stack_value",
            "LLVM lowering, frozen client and checker",
            "paired package observation",
        )


def return_lowering_receipts():
    """Distinguish observed calling sequences from mutation evidence."""
    direct = set()
    seen = set()
    with (PRODUCT / "return-lowering.csv").open() as stream:
        for row in csv.DictReader(stream):
            symbol = row["symbol"]
            shape = row["llvm_return"], row["hidden_result_pointer"]
            if symbol in seen or shape not in {("void", "1"), ("i64", "0")}:
                raise ValueError(f"unexpected or repeated return lowering: {symbol}")
            seen.add(symbol)
            if shape == ("i64", "0"):
                direct.add(symbol)
            yield (
                "return-lowering.csv",
                symbol,
                "by-value return",
                row["sdk_type"],
                symbol,
                "calling convention",
                "",
                "integer register" if symbol in direct else "hidden result pointer",
                "",
                "LLVM IR of SDK type probe",
                "baseline lowering only",
            )
    observed = set()
    with (PRODUCT / "option-returns.csv").open() as stream:
        for row in csv.DictReader(stream):
            symbol = row["symbol"]
            old, minor = (
                int(row[release]) for release in ("dal-2025.9.0", "dal-2025.11.0")
            )
            if symbol in observed or old != minor or old <= 0 or old & (old - 1):
                raise ValueError(f"unexpected or repeated option mask: {symbol}")
            observed.add(symbol)
            yield (
                "option-returns.csv",
                symbol,
                "option getter",
                str(old),
                symbol,
                "returned bit mask",
                "",
                "",
                "",
                "frozen client on two releases",
                "implementation-level value control",
            )
    if observed != direct:
        raise ValueError(
            "direct-return type probes and frozen option getter observations disagree"
        )


def member_return_receipts():
    mutation = "l2_representatives/member_return/register"
    filename = "status-return-abi.json"
    receipt = json.loads((PRODUCT / filename).read_text())
    clients = receipt["client_exits"]
    if set(clients) != {"compute", "table", "virtual"} or receipt["checker"] != {
        "control": {"exit": 0, "verdict": "NO_CHANGE", "status_return": []},
        "mutant": {
            "exit": 4,
            "verdict": "BREAKING",
            "status_return": [["memory (sret)", "regs(INTEGER)"]],
        },
    }:
        raise ValueError("unexpected status return checker or client set")
    for name, exits in clients.items():
        if exits != {"release": 0, "old": 0, "control": 0, "mutant": 42}:
            raise ValueError(f"unexpected status receiver observation: {name}")
        yield (
            filename,
            name,
            "member return",
            "",
            "",
            "calling convention",
            "sret to register",
            "member return",
            mutation,
            "receiver address in frozen client",
            "paired package observation",
        )
    filename = "status-member-templates.json"
    receipt = json.loads((PRODUCT / filename).read_text())
    symbols = receipt["symbols"]
    if (
        (receipt["old"], receipt["mutant"]) != ("sret", "register")
        or set(receipt["providers"]) != {"dal-2025.9.0", "dal-2025.11.0"}
        or len(symbols) != len(set(symbols))
    ):
        raise ValueError("unexpected specialized member return evidence")
    for symbol in symbols:
        yield (
            filename,
            symbol,
            "member specialization",
            "",
            symbol,
            "calling convention",
            "sret to register",
            "member return",
            mutation,
            "call-site LLVM IR and provider export",
            "compiler observation only",
        )


def root_gap_receipts():
    """Check the installed-header failures against separately recorded compile probes."""
    with (PRODUCT / "root-context.csv").open() as stream:
        contexts = {}
        for row in csv.DictReader(stream):
            probes = contexts.setdefault(row["root"], {})
            context = row["context"]
            if context in probes:
                raise ValueError(f"duplicate root probe: {row}")
            probes[context] = (row["exit"], row["first_error"])
    for prefix in ("", "2026.2/"):
        filename = prefix + "unparsed.csv"
        roots = set((PRODUCT / (prefix + "roots.txt")).read_text().splitlines())
        with (PRODUCT / filename).open() as stream:
            failures = list(csv.DictReader(stream))
        rejected = {r["root"] for r in failures}
        if len(rejected) != len(failures) or rejected - roots:
            raise ValueError(f"invalid rejected root in {filename}")
        if not prefix and rejected != contexts.keys():
            raise ValueError(
                "installed-header failures disagree with independent probes"
            )
        for row in failures:
            root = row["root"]
            detail = row["diagnostic"]
            if not prefix:
                probes = contexts[root]
                if (
                    probes
                    != {
                        "package": (
                            "1",
                            "oneapi/dal/backend/dispatcher.hpp: No such file or directory",
                        ),
                        "source": (
                            "1",
                            "oneapi/dal/_dal_cpu_dispatcher_gen.hpp: No such file or directory",
                        ),
                        "source-ide": ("0", ""),
                    }
                    or "oneapi/dal/backend/dispatcher.hpp" not in detail
                ):
                    raise ValueError(f"unexpected parse context for {root}")
            yield (
                filename,
                root,
                "installed header",
                detail,
                "",
                "unknown",
                "",
                "",
                "",
                "root-context.csv" if not prefix else filename,
                "uninventoried root",
            )
        print(f"{filename}: {len(rejected)} rejected of {len(roots)} selected roots")


def serialization_slot_receipts():
    filename = "serialization-slots.json"
    variants = json.loads((PRODUCT / filename).read_text())
    expected = {
        "old": [21, 22],
        "control": [21, 22],
        "swap": [22, 21],
        "remove": [22],
        "insert": [23, 21],
    }
    if {k: v["method_exits"] for k, v in variants.items()} != expected:
        raise ValueError(
            "serialization-slot dispatch observations disagree with controls or mutations"
        )
    if variants["control"]["verdict"] != "NO_CHANGE":
        raise ValueError("serialization-slot control changed")
    for edit, mutation in {
        "swap": "l2_representatives/virtual/swap",
        "insert": "l2_representatives/virtual/insert",
        "remove": "virtual_remove",
    }.items():
        result = variants[edit]
        if (
            result["verdict"] != "BREAKING"
            or "type_vtable_changed" not in result["kinds"]
        ):
            raise ValueError(f"unexpected virtual dispatch verdict for {edit}")
        exits = ", ".join(map(str, result["method_exits"]))
        yield (
            filename,
            edit,
            "virtual slot",
            "control exits 21, 22",
            "",
            "virtual dispatch",
            edit,
            "virtual slot order and presence",
            mutation,
            f"frozen client exits {exits}",
            "paired package observation",
        )


def visitor_receipts():
    field_swaps = {
        "base_level_impurity",
        "base_level_nNodeSampleCount",
        "base_impurity_nNodeSampleCount",
        "split_featureIndex_featureValue",
        "split_featureIndex_coverValue",
        "split_featureValue_coverValue",
        "leaf_response_coverValue",
    }
    visitor_cases = {
        "base_level_impurity",
        "slots",
        "field_alias",
        "nonvirtual",
        "identity",
    }
    for path, cases, lanes in (
        (PRODUCT / "visitor.csv", visitor_cases, {"stripped+headers"}),
        (
            PRODUCT.parent / "pilot/results.csv",
            visitor_cases | field_swaps,
            {"symbols", "dwarf", "headers"},
        ),
        (
            PRODUCT.parent / "pilot/range_results.csv",
            {"old", "alias", "swap"},
            {"symbols", "dwarf", "headers"},
        ),
    ):
        seen = set()
        with path.open() as stream:
            for row in csv.DictReader(stream):
                case = row["case"]
                lane = row.get("lane", "stripped+headers")
                mutation = ""
                if case in field_swaps or case == "swap":
                    mutation = "l2_representatives/layout/swap"
                elif case == "slots":
                    mutation = "l2_representatives/virtual/swap"
                verdict = row.get("checker") or row["verdict"]
                if (
                    (case, lane) in seen
                    or row["client_exit"] != str(int(bool(mutation)))
                    or (
                        mutation
                        and lane in {"headers", "stripped+headers"}
                        and verdict != "BREAKING"
                    )
                ):
                    raise ValueError(
                        f"unexpected pilot observation: {path.name} {case} {lane}"
                    )
                seen.add((case, lane))
                filename = path.name if path.parent == PRODUCT else f"pilot/{path.name}"
                yield (
                    filename,
                    case,
                    "frozen client",
                    lane,
                    "",
                    "object representation"
                    if case in field_swaps or case == "swap"
                    else "virtual dispatch"
                    if case == "slots"
                    else "compatibility control",
                    case,
                    mutation.rpartition("/")[0],
                    mutation,
                    f"client exit {row['client_exit']}, checker {verdict}",
                    "paired pilot observation"
                    if mutation
                    else "compatible client control",
                )
        if seen != {(case, lane) for case in cases for lane in lanes}:
            raise ValueError(f"incomplete pilot observations: {path}")


def enum_receipts():
    changes = {
        "old": ("", "NO_CHANGE", 0),
        "extend": ("enum_class_append", "COMPATIBLE", 0),
        "swap": ("enum_class_enumerator_values_swapped", "BREAKING", 42),
        "narrow": ("enum_class_underlying_narrow", "BREAKING", 42),
    }
    for filename in (
        "table-layout.json",
        "sparse-indexing-clang.json",
        "sparse-indexing-castxml.json",
    ):
        receipt = json.loads((PRODUCT / filename).read_text())
        edits = set(changes)
        if filename == "table-layout.json":
            edits.remove("narrow")
        expected = {
            (mode, release, edit)
            for mode in ("host", "sycl")
            for release in ("dal-2025.9.0", "dal-2025.11.0")
            for edit in edits
        }
        rows = receipt["clients"]
        observed = {(r["mode"], r["package"], r["provider"]) for r in rows}
        if (
            observed != expected
            or len(rows) != len(observed)
            or receipt["checker"].keys() != edits
        ):
            raise ValueError(f"incomplete enum experiment: {filename}")
        for edit in edits:
            check = receipt["checker"][edit]
            if check["verdict"] != changes[edit][1] or check["exit"] != (
                4 if changes[edit][2] else 0
            ):
                raise ValueError(f"unexpected checker result: {filename}/{edit}")
            if edit == "narrow" and check["layout_changes"][
                "enum_underlying_size_changed"
            ] != ["4", "1"]:
                raise ValueError(f"unexpected enum width: {filename}")
        for row in rows:
            edit = row["provider"]
            mutation, verdict, exit_code = changes[edit]
            if row["exit"] != exit_code:
                raise ValueError(
                    f"unexpected enum client observation: {filename} {row}"
                )
            yield (
                filename,
                row["package"],
                row["mode"],
                receipt["selector"] if row["mode"] == "sycl" else "",
                "",
                "enum width and containing record"
                if edit == "narrow"
                else "enum numeric value",
                edit,
                "scoped enum",
                mutation,
                f"frozen client exit {exit_code}, checker {verdict}",
                "paired package observation"
                if exit_code
                else "compatible client control",
            )


def provider_receipts():
    for filename in (
        "provider-binding.csv",
        "provider-move.csv",
        "provider-move-bundle.csv",
    ):
        with (PRODUCT / filename).open() as stream:
            rows = list(csv.DictReader(stream))
        binding = filename == "provider-binding.csv"
        expected = (
            {"reachable", "missing", "wrong_version", "unreachable"}
            if binding
            else {"moved", "missing"}
        )
        if {r["case"] for r in rows} != expected or len(rows) != len(expected):
            raise ValueError(f"incomplete provider observations: {filename}")
        for row in rows:
            case = row["case"]
            target = "reachable" if case == "moved" else case
            loads = target == "reachable"
            if row["client_exit"] != ("0" if loads else "127"):
                raise ValueError(f"unexpected loader exit: {filename}/{case}")
            if binding:
                if row["loadability"] != ("pass" if loads else "fail"):
                    raise ValueError(f"unexpected loadability result: {case}")
                detail = row["loadability"]
            else:
                detail = row.get("single_provider_verdict") or row["release_verdict"]
                if "bundle_verdict" in row:
                    detail += f", bundle {row['bundle_verdict']} ({row['bundle_findings']} findings)"
            yield (
                filename,
                case,
                "ELF dependency",
                detail,
                "",
                "provider resolution",
                case,
                "versioned dependency lookup",
                f"provider_campaign/{target}",
                f"frozen client exit {row['client_exit']}",
                "loader observation",
            )

    filename = "duplicate-provider.json"
    receipt = json.loads((PRODUCT / filename).read_text())
    cases = receipt["cases"]
    for variant in ("control", "withdrawal"):
        case = cases[variant]
        for library in ("libonedal_dpc.so.3", "libonedal.so.3"):
            missing = variant == "withdrawal" and library == "libonedal_dpc.so.3"
            result = case[library]
            if result != {
                "exit": 127 if missing else 0,
                "binding": None if missing else library,
            }:
                raise ValueError(
                    f"unexpected duplicate-provider binding: {variant}/{library}"
                )
            yield (
                filename,
                library,
                "ELF dependency",
                case["package_verdict"],
                receipt["symbol"],
                "provider resolution",
                variant,
                "duplicate provider",
                f"duplicate_provider/{variant}",
                f"client exit {result['exit']}, binding {result['binding']}",
                "loader observation",
            )


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
    parser.add_argument(
        "--sources",
        type=Path,
        help="index all structured product receipts and their direct crosswalk rows",
    )
    args = parser.parse_args()
    output = args.rows
    kinds, edits = set(), set()
    kind_map = {
        name: family for family, names in KINDS.items() for name in names.split()
    }
    edit_map = {
        name: family for family, names in EDITS.items() for name in names.split()
    }
    dispositions = [
        *return_receipts(),
        *argument_receipts(),
        *return_lowering_receipts(),
        *member_return_receipts(),
        *root_gap_receipts(),
        *serialization_slot_receipts(),
        *visitor_receipts(),
        *enum_receipts(),
        *provider_receipts(),
    ]
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
    if args.sources:
        from collections import Counter

        counts = Counter(row[0] for row in dispositions)
        with args.sources.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("product_file", "direct_crosswalk_rows"))
            for path in sorted(PRODUCT.rglob("*")):
                if path.name.endswith((".csv", ".csv.gz", ".json", ".tsv", ".txt")):
                    name = path.relative_to(PRODUCT).as_posix()
                    writer.writerow((name, counts[name]))
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

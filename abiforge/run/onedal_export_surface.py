# SPDX-License-Identifier: Apache-2.0
"""Measure published ELF names, SDK ledger matches and frozen-consumer imports."""

import argparse
import csv
import gzip
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from .onedal_release_matrix import PRODUCT


def symbol_names(path, *, imports=False):
    flag = "--undefined-only" if imports else "--defined-only"
    output = subprocess.check_output(["nm", "-D", "-P", flag, path], text=True)
    return {line.split()[0] for line in output.splitlines() if line}


def sdk_evidence():
    evidence = defaultdict(set)
    for ledger in ("ledger.csv.gz", "extended-obligations.csv.gz"):
        with gzip.open(PRODUCT / ledger, "rt") as stream:
            for row in csv.DictReader(stream):
                for provider in filter(None, row["providers"].split(",")):
                    key = provider, row["symbol"]
                    evidence[key].add("sdk")
                    if row["client_imports"]:
                        evidence[key].add("ledger_import")
                    if row.get("independent_edit_witness"):
                        evidence[key].add("ledger_edit")
    return evidence


def surface(prefix):
    paths = sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")})
    images = {}
    for path in paths:
        dynamic = subprocess.check_output(["readelf", "-d", path], text=True)
        match = re.search(r"\(SONAME\).*?\[(.*?)\]", dynamic)
        if match is None or match[1] in images:
            raise ValueError(f"missing or duplicate SONAME: {path}")
        images[match[1]] = symbol_names(path)
    if not images:
        raise ValueError(f"missing oneDAL ELF images in {prefix}")
    return images


def sdk_inventory_coverage(directory, images, observed=None, ledger=None):
    matched = defaultdict(set)
    with (directory / "obligations.csv").open() as stream:
        for row in csv.DictReader(stream):
            for provider in filter(None, row["providers"].split(",")):
                if row["symbol"] not in images[provider]:
                    raise ValueError(f"stale SDK match: {provider} {row['symbol']}")
                matched[provider].add(row["symbol"])
    with (directory / "unparsed.csv").open() as stream:
        unavailable = sorted({row["root"] for row in csv.DictReader(stream)})
    result = {
        "roots": len((directory / "roots.txt").read_text().splitlines()),
        "unparsed": unavailable,
        "unique_names": len(set().union(*matched.values())),
        "provider_pairs": {name: len(matched[name]) for name in images},
    }
    if ledger is not None:
        combined = {
            provider: matched[provider]
            | {name for name in names if (provider, name.partition("@")[0]) in ledger}
            for provider, names in images.items()
        }
        result["combined_unique_names"] = len(set().union(*combined.values()))
        result["combined_provider_pairs"] = {
            p: len(names) for p, names in combined.items()
        }
    if observed is not None:
        result["client_imports_outside_sdk"] = {
            provider: sorted(names - matched[provider])
            for provider, names in observed.items()
        }
    return result


def client_observations(clients):
    observations = defaultdict(set)
    client_labels = {c: f"{c.parent.name}/{c.name}" for c in clients}
    for client in clients:
        dynamic = subprocess.check_output(["readelf", "-d", client], text=True)
        needed = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
        for symbol in symbol_names(client, imports=True):
            for provider in needed:
                observations[provider, symbol.replace("@@", "@")].add(
                    client_labels[client]
                )
    return observations, client_labels


def measure(
    prefix,
    candidates,
    clients,
    inventories=None,
    baseline_inventory=None,
    status_report=None,
):
    baseline = surface(prefix)
    evidence = sdk_evidence()
    # The current SDK ledgers identify the providers by their baseline SONAME.
    if not {provider for provider, _ in evidence} <= baseline.keys():
        raise ValueError("baseline image does not supply the SDK ledger providers")
    observations, client_labels = client_observations(clients)
    sdk_names = {name for _, name in evidence}
    providers = {}
    observed = {}
    distinct = defaultdict(set)
    for soname, names in baseline.items():
        groups = defaultdict(set)
        consumers = {}
        for name in names:
            plain = name.partition("@")[0]
            if plain in sdk_names:
                groups["sdk_name_any_provider"].add(name)
            for tag in evidence.get((soname, plain), ()):
                groups[tag].add(name)
            # An unversioned import can bind a default version.
            imports = observations.get((soname, name.replace("@@", "@")), set())
            if "@@" in name:
                imports = imports | observations.get((soname, plain), set())
            if imports:
                groups["client_import"].add(name)
                consumers[name] = sorted(imports)
            distinct[name].add(soname)
        observed[soname] = groups["client_import"]
        missing = sorted(groups["client_import"] - groups["sdk"])
        providers[soname] = {
            "exports": len(names),
            **{
                key: len(groups[key])
                for key in (
                    "sdk",
                    "sdk_name_any_provider",
                    "ledger_import",
                    "ledger_edit",
                    "client_import",
                )
            },
            "imports_outside_sdk": {name: consumers[name] for name in missing},
        }
    releases = {}
    for candidate in candidates:
        images = surface(candidate)
        by_stem = {name.split(".so.")[0]: names for name, names in images.items()}
        releases[candidate.name] = {
            "exports": sum(map(len, images.values())),
            "unique_names": len(set.union(*images.values())),
            "baseline_sdk_names_present": len(
                {
                    name
                    for names in images.values()
                    for name in names
                    if name.partition("@")[0] in sdk_names
                }
            ),
            "provider_changes": {
                name: {
                    "removed": len(names - by_stem.get(name.split(".so.")[0], set())),
                    "added": len(by_stem.get(name.split(".so.")[0], set()) - names),
                }
                for name, names in baseline.items()
            },
        }
        if inventories and candidate in inventories:
            releases[candidate.name]["sdk_inventory"] = sdk_inventory_coverage(
                inventories[candidate], images
            )
    known = {
        name
        for name in distinct
        if any((p, name.partition("@")[0]) in evidence for p in distinct[name])
    }
    result = {
        "baseline": prefix.name,
        "clients": sorted(client_labels.values()),
        "unique_names": len(distinct),
        "unique_sdk_matches": len(known),
        "providers": providers,
        "releases": releases,
    }
    if baseline_inventory:
        result["sdk_inventory"] = sdk_inventory_coverage(
            baseline_inventory, baseline, observed, evidence
        )
    if status_report:
        report = json.loads(status_report.read_bytes())
        changes = [
            c
            for c in report["changes"]
            if c["symbol"] == "Status"
            and c["kind"] == "struct_return_convention_changed"
        ]
        if report["verdict"] != "BREAKING" or len(changes) != 1:
            raise ValueError("expected a single Status return-convention change")
        affected = set(changes[0]["affected_symbols"])
        core = "libonedal_core.so.3"
        if not affected <= baseline[core]:
            raise ValueError("Status evidence includes a missing baseline export")
        witnessed = {key for key, tags in evidence.items() if "ledger_edit" in tags}
        witnessed.update((core, name) for name in affected)
        for row in json.loads((PRODUCT / "minor-dpc-withdrawals.json").read_text()):
            if (
                row["detected_removal"]
                and row["exits"].get(prefix.name) == 0
                and 127 in row["exits"].values()
            ):
                witnessed.add(("libonedal_dpc.so.3", row["symbol"]))
        result["witnessed_lower_bound"] = {
            "status_l2": len(affected),
            "provider_pairs": len(witnessed),
            "distinct_names": len({name for _, name in witnessed}),
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--candidate", type=Path, action="append", default=[])
    parser.add_argument("--client", type=Path, action="append", default=[])
    parser.add_argument(
        "--candidate-inventory",
        type=Path,
        nargs=2,
        action="append",
        metavar=("PREFIX", "INVENTORY"),
        default=[],
    )
    parser.add_argument(
        "--status-report", type=Path, help="forward SDK Status mutation compare JSON"
    )
    parser.add_argument("--baseline-inventory", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = measure(
        args.baseline,
        args.candidate,
        args.client,
        dict(args.candidate_inventory),
        args.baseline_inventory,
        args.status_report,
    )
    args.out.write_text(json.dumps(result, indent=2) + "\n")

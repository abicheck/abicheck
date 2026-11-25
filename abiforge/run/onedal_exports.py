"""Score product comparison reports against oneDAL's ledger and the actual ELF exports.

Pass the baseline and candidate lib directories, the ledger CSV.gz, and one or
more abicheck compare JSON reports. Inputs are bound to reports by SHA-256.
"""

import argparse
import csv
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

REMOVALS = {
    "func_removed",
    "func_removed_elf_only",
    "var_removed",
    "var_removed_elf_only",
    "instantiation_missing_from_binary",
    "func_likely_renamed",
}


def symbols(path):
    output = subprocess.check_output(
        ["nm", "-D", "--defined-only", "-P", str(path)], text=True
    )
    return {line.split()[0].split("@@")[0] for line in output.splitlines() if line}


def binary(directory, evidence):
    path = directory / Path(evidence["path"]).name
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != evidence["sha256"]:
        raise ValueError(f"report digest differs for {path}")
    return path


def score(old_dir, new_dir, reports, ledger, additional_ledgers=()):
    rows = []
    for path in (ledger, *additional_ledgers):
        with gzip.open(path, "rt") as handle:
            rows.extend(csv.DictReader(handle))
    expected = {}
    for row in rows:
        if row["symbol"]:
            for provider in filter(None, row["providers"].split(",")):
                expected.setdefault(provider, set()).add(row["symbol"])
    results = {}
    for file in reports:
        report = json.loads(file.read_text())
        old = binary(old_dir, report["old_file"])
        new = binary(new_dir, report["new_file"])
        if old.name in results:
            raise ValueError(f"duplicate provider {old.name}")
        wanted = expected.get(old.name, set())
        missing_baseline = wanted - symbols(old)
        lost = wanted - symbols(new)
        flagged = {
            c["symbol"] for c in report["changes"] if c["kind"] in REMOVALS
        } & wanted
        results[old.name] = dict(
            candidate=new.name,
            identities=len(wanted),
            absent=len(lost),
            missing_baseline=sorted(missing_baseline),
            missed=sorted(lost - flagged),
            false_positives=sorted(flagged - lost),
        )
    if not expected or expected.keys() - results.keys():
        raise ValueError("missing ledger providers or empty ledger")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("old", "new", "ledger"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--additional-ledger", action="append", default=[], type=Path)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    results = score(
        args.old, args.new, args.reports, args.ledger, args.additional_ledger
    )
    print(json.dumps(results, indent=2))
    return int(
        any(
            r["missed"] or r["false_positives"] or r["missing_baseline"]
            for r in results.values()
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

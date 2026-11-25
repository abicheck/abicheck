# SPDX-License-Identifier: Apache-2.0
"""Reconcile the legacy and extended oneDAL ABI ledgers against three ELF releases."""

import argparse
import csv
import gzip
from pathlib import Path

from .onedal_exports import symbols

PRODUCT = Path(__file__).resolve().parents[2] / "research/onedal-coverage/product"
KINDS = {
    "virtual_slot",
    "by_value_return",
    "constructor_abi_variant",
    "destructor_abi_variant",
    "exported_variable",
}


def obligations():
    rows = {}
    for name in ("ledger.csv.gz", "extended-obligations.csv.gz"):
        with gzip.open(PRODUCT / name, "rt") as handle:
            for row in csv.DictReader(handle):
                if row["kind"] in KINDS and row["providers"]:
                    rows[row["key"], row["kind"], row["symbol"]] = row
    return rows.values()


def release_matrix(old, minor, major, output):
    exports = {}
    rows = []
    for row in obligations():
        for provider in row["providers"].split(","):
            presence = []
            for prefix, soname in (
                (old, provider),
                (minor, provider),
                (major, provider.removesuffix(".3") + ".4"),
            ):
                binary = prefix / "lib" / soname
                if binary not in exports:
                    exports[binary] = symbols(binary)
                presence.append(int(row["symbol"] in exports[binary]))
            if not presence[0]:
                raise ValueError(f"baseline export missing: {provider} {row['symbol']}")
            rows.append(
                (
                    row["kind"],
                    row["detail"],
                    provider,
                    row["symbol"],
                    row["key"],
                    *presence,
                )
            )
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "kind",
                "detail",
                "baseline_provider",
                "symbol",
                "declaration",
                old.name,
                minor.name,
                major.name,
            )
        )
        writer.writerows(sorted(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "major", "output"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    release_matrix(args.old, args.minor, args.major, args.output)

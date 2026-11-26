# SPDX-License-Identifier: Apache-2.0
"""Choose one frozen witness per observed ELF withdrawal mechanism and outcome."""

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path


def scenarios(rows, clients):
    relocations = {}
    groups = defaultdict(list)
    for row in rows:
        client = clients / row["client"]
        if client not in relocations:
            table = defaultdict(set)
            listing = subprocess.check_output(["readelf", "-Wr", client], text=True)
            for line in listing.splitlines():
                fields = line.split()
                if len(fields) >= 5 and fields[2].startswith("R_"):
                    table[fields[4]].add(fields[2])
            relocations[client] = table
        symbol = row["symbol"]
        kinds = tuple(sorted(relocations[client][symbol]))
        if not kinds:
            raise ValueError(f"{client}: no dynamic relocation for {symbol}")
        entity = "function"
        if symbol.startswith("_ZTI"):
            entity = "typeinfo"
        elif symbol.startswith("_ZTV"):
            entity = "vtable"
        groups[entity, kinds, row["withdrawn"]].append(row)
    result = []
    for (entity, kinds, code), members in sorted(groups.items()):
        witness = min(
            members, key=lambda r: (len(r["symbol"]), r["provider"], r["symbol"])
        )
        result.append(
            {
                "entity": entity,
                "relocations": kinds,
                "withdrawn": code,
                "count": len(members),
                "provider": witness["provider"],
                "symbol": witness["symbol"],
                "client": witness["client"],
            }
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("receipt", "clients", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    groups = scenarios(json.loads(args.receipt.read_text()), args.clients)
    args.out.write_text(json.dumps(groups, indent=2) + "\n")

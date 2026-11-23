"""Score observed edits per family/lane and select uncovered return types from the ledger."""

import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def quality():
    groups = defaultdict(Counter)
    for file in ("contracts.csv", "visitor.csv", "lifetime.csv", "return-scans.csv"):
        with (ROOT / file).open() as stream:
            for row in csv.DictReader(stream):
                family = row.get("family", Path(file).stem)
                counts = groups[family + "/" + row.get("lane", "headers")]
                if file == "lifetime.csv":
                    broken = (
                        int(row["surviving_value"]),
                        int(row["deleter_calls"]),
                    ) != (17, 1)
                else:
                    broken = int(row["client_exit"]) != 0
                verdict = row.get("checker") or row["verdict"]
                counts["broken" if broken else "controls"] += 1
                if verdict == "UNCONFIRMED":
                    counts["insufficient_evidence"] += 1
                elif verdict == "BREAKING":
                    counts["detected" if broken else "false_break"] += 1
                elif broken:
                    counts["missed"] += 1
                elif verdict == "COMPATIBLE_WITH_RISK":
                    counts["control_risk_to_review"] += 1

    targets = defaultdict(set)
    imported = defaultdict(set)
    with gzip.open(ROOT / "ledger.csv.gz", "rt") as stream:
        for row in csv.DictReader(stream):
            if (
                row["kind"] == "by_value_return"
                and row["providers"]
                and not row["independent_edit_witness"]
            ):
                targets[row["detail"]].add(row["symbol"])
                if row["client_imports"]:
                    imported[row["detail"]].add(row["symbol"])
    priorities = [
        {
            "return_type": name,
            "unwitnessed_symbols": len(targets[name]),
            "already_imported": len(imported[name]),
            "example": min(imported[name] or targets[name]),
        }
        for name in sorted(
            targets, key=lambda key: (-bool(imported[key]), -len(targets[key]), key)
        )
    ]
    return {"families": groups, "return_targets": priorities}


if __name__ == "__main__":
    print(json.dumps(quality(), indent=2))

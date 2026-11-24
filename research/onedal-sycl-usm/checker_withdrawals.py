"""Check exact oneDAL declaration withdrawals using L2 functions from real dumps."""

import argparse
import csv
from pathlib import Path

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot
from abicheck.model.export_entity_name import entity_name_components
from abicheck.model.type_label import qualified_spelling
from abicheck.serialization import load_snapshot


def signature(fn):
    name = qualified_spelling(fn.entity_id)
    if fn.entity_id and not fn.entity_id.scope:
        parsed = entity_name_components(fn.mangled)
        if parsed:
            name = "::".join(parsed.components)
    params = ", ".join(p.type for p in fn.params)
    spelling = f"{fn.return_type} ({params})" + (" const" if fn.is_const else "")
    return name, "".join(spelling.split())


def withdrawal(report, fn):
    return any(
        c.entity_id == fn.entity_id
        and c.kind
        in {
            ChangeKind.INLINE_FUNCTION_REMOVED,
            ChangeKind.FUNC_REMOVED,
        }
        for c in report.changes
    )


def snapshot(functions):
    return AbiSnapshot(
        library="oneDAL header projection", version="fixture", functions=functions
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parallel_dump")
    parser.add_argument("host_dump")
    args = parser.parse_args()
    baseline, host = (load_snapshot(p) for p in (args.parallel_dump, args.host_dump))
    grouped = compare(baseline, host)
    functions = baseline.declarations.functions
    inventory = Path(__file__).with_name("inventory.tsv")
    with inventory.open() as source:
        rows = list(csv.DictReader(source, delimiter="\t"))
    print(
        "qualified_name",
        "signature",
        "isolated_removal",
        "bulk_removal",
        "unchanged_removal",
        sep="\t",
    )
    failures = 0
    for row in rows:
        key = row["qualified_name"], "".join(row["signature"].split())
        cohort = [
            f
            for f in functions
            if (f.source_header or "").endswith("/oneapi/dal/" + row["header"])
        ]
        candidates = [f for f in cohort if signature(f) == key]
        if not candidates:
            raise ValueError(f"declaration missing from L2 dump: {row}")
        # Prefer the pattern when an explicit instantiation also has the same
        # written type. Retain siblings and the other declarations in its header.
        fn = next((f for f in candidates if f.mangled == f.name), candidates[0])
        old = snapshot(cohort)
        new = snapshot([f for f in cohort if f.entity_id != fn.entity_id])
        single = withdrawal(compare(old, new), fn)
        control = withdrawal(compare(old, snapshot(list(cohort))), fn)
        bulk = withdrawal(grouped, fn)
        print(row["qualified_name"], row["signature"], single, bulk, control, sep="\t")
        failures += not (single and bulk and not control)
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())

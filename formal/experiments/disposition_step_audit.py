#!/usr/bin/env python3
"""E7 (implementation side): check the TLA+ precondition on the real pipeline.

DispositionPipeline.tla shows a *sufficient* condition for the count trigger in
`policy/finding_disposition/pipeline.py::_record_dropped_duplicates` to be
sound: no step drops a finding *and* introduces a new object (into `changes`
or a bucket) in the same call, unless it records its own drops. It is not
necessary: a mixed call that drops more than it adds still trips the trigger. This script wraps
that function, runs real comparisons, and reports per step:

  * calls where the step dropped findings and also introduced new objects;
  * findings that left `changes`, reached no bucket and are not in the ledger
    (a real vanish).

Usage: disposition_step_audit.py SNAPSHOT_PAIR_GLOB...   (old,new pairs)
       disposition_step_audit.py --pairs old1.json new1.json old2.json new2.json ...
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

from abicheck.checker import compare
from abicheck.policy.finding_disposition import pipeline as pl
from abicheck.serialization import load_snapshot

BUCKETS = ("suppressed", "redundant", "opaque_filtered", "out_of_surface")
mixed: Counter[str] = Counter()
vanished: dict[str, list[str]] = defaultdict(list)
calls: Counter[str] = Counter()
_orig = pl._record_dropped_duplicates


def _wrapped(before, after, bucket_total_before, ctx, step_name, disposition):  # type: ignore[no-untyped-def]
    calls[step_name] += 1
    in_buckets = {id(c) for b in BUCKETS for c in getattr(ctx, b)}
    before_ids = {id(c) for c in before}
    dropped = before_ids - {id(c) for c in after} - in_buckets
    new_after = {id(c) for c in after} - before_ids
    grew = (pl._bucket_total(ctx) - bucket_total_before) - len(before_ids & in_buckets)
    _orig(before, after, bucket_total_before, ctx, step_name, disposition)
    if dropped and (new_after or grew > 0):
        mixed[step_name] += 1
    ledger = ctx.disposition_ledger
    if ledger is not None and disposition is not None:
        seen = ledger._seen_ids
        for c in before:
            if id(c) in dropped and id(c) not in seen:
                vanished[step_name].append(f"{c.kind.value}:{c.symbol}")


pl._record_dropped_duplicates = _wrapped


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv]
    pairs = list(zip(paths[0::2], paths[1::2]))
    total = 0
    skipped = 0
    compared = 0
    for old_p, new_p in pairs:
        try:
            old, new = load_snapshot(old_p), load_snapshot(new_p)
        except Exception as exc:  # noqa: BLE001 - report unreadable inputs, keep going
            skipped += 1
            print(f"skip {old_p}: {type(exc).__name__}: {exc}")
            continue
        r = compare(old, new)
        compared += 1
        total += len(r.changes)
    print(f"compared={compared} skipped={skipped}")
    if compared == 0:
        print("no snapshot pair was compared: nothing was audited")
        return 2
    print(f"pairs={len(pairs)} kept_changes={total} step_calls={sum(calls.values())}")
    print(
        "steps that dropped AND introduced objects in one call:", dict(mixed) or "none"
    )
    print(
        "vanished (dropped, unbucketed, unrecorded):",
        {k: len(v) for k, v in vanished.items()} or "none",
    )
    for k, v in vanished.items():
        print(" ", k, v[:5])
    return 1 if vanished else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

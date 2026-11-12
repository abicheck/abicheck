#!/usr/bin/env python3
"""E9: "unknown evidence never creates a finding", swept over the layout corpus.

Takes a real old/new snapshot pair, erases one family of HEADER-layer layout
facts on ONE side (TypeField.offset_bits, RecordType.size_bits /
alignment_bits -> None), recompares, and checks every header-channel claim
(kind, subject, old value, new value) against the full-evidence truth set,
which E5 validated against the Lean model. Oracle: Lean's
FieldOffset.unknown_old_not_fires, generalised. A claim must not appear, and
must not change value, once its only evidence is erased.

Scope limit: the DWARF StructLayout channel is NOT erased, because
`FieldInfo.byte_offset: int` has no "unknown" value to erase to (E9 finding).
DWARF-channel kinds (`struct_*`) are reported separately as an unerased
independent channel, never counted as passing.

Usage: unknown_evidence_sweep.py OLD.json NEW.json
"""

from __future__ import annotations

import copy
import dataclasses
import sys
from collections import Counter
from pathlib import Path

from abicheck.checker import compare
from abicheck.model import Fact
from abicheck.serialization import load_snapshot

#: header-channel kinds each erased fact feeds (the ones this sweep can test)
FED_BY = {
    "offset_bits": {"type_field_offset_changed"},
    "size_bits": {"type_size_changed"},
    "alignment_bits": {"type_alignment_changed"},
}
#: DWARF-channel counterparts: not erasable, reported only
DWARF_KINDS = {
    "struct_field_offset_changed",
    "struct_size_changed",
    "struct_alignment_changed",
}


def erase(snap, fact: str):  # type: ignore[no-untyped-def]
    s = copy.deepcopy(snap)
    for t in s.declarations.types:
        if fact == "offset_bits":
            for f in t.fields:
                object.__setattr__(f, "offset_bits", None)
        elif hasattr(t, fact):
            object.__setattr__(t, fact, None)
    # The record-layout detectors read size/alignment from the snapshot's
    # semantic IR, which holds its own copy of the facts: erase them there too
    # (as NOT_COLLECTED), or the erasure never reaches the detector.
    if fact in ("size_bits", "alignment_bits") and s.semantic_ir is not None:
        ir = s.semantic_ir
        occ = {
            k: dataclasses.replace(e, **{fact: Fact.not_collected()})
            for k, e in ir.occurrences.items()
        }
        s.semantic_ir = dataclasses.replace(ir, occurrences=occ)
    return s


def kinds(result) -> Counter[str]:  # type: ignore[no-untyped-def]
    return Counter(c.kind.value for c in result.changes)


def facts(result) -> set[tuple[str, str, str | None, str | None]]:  # type: ignore[no-untyped-def]
    """Header-channel claims with their values: (family, subject, old, new).
    Keeping the values catches a claim that survives erasure with a wrong
    offset or size, not only a claim about a new subject."""
    out = set()
    for c in result.changes:
        k = c.kind.value
        for fam, fed in FED_BY.items():
            if k in fed:
                subj = c.symbol
                if fam == "offset_bits" and "::" not in subj:
                    subj = f"{subj}::{getattr(c, 'field_name', None) or c.detail}"
                out.add((fam, subj, c.old_value, c.new_value))
    return out


def main(old_p: str, new_p: str) -> int:
    old, new = load_snapshot(Path(old_p)), load_snapshot(Path(new_p))
    base_r = compare(old, new)
    base = kinds(base_r)
    truth = facts(
        base_r
    )  # full-evidence claims, validated against the Lean model in E5
    bad = 0
    for fact, fed in FED_BY.items():
        for side in ("old", "new"):
            o = erase(old, fact) if side == "old" else old
            n = erase(new, fact) if side == "new" else new
            r = compare(o, n)
            got = kinds(r)
            claims = facts(r)
            manufactured = claims - truth
            lost = {c for c in truth if c[0] == fact} - claims
            dwarf = {k: v for k, v in got.items() if k in DWARF_KINDS}
            print(
                f"erase {fact:14s} on {side}: header claims not in truth={len(manufactured)}; "
                f"header claims still made={len({c for c in truth if c[0] == fact}) - len(lost)}; "
                f"unerased DWARF-channel findings (not tested)={dwarf or 0}"
            )
            for m in sorted(manufactured)[:3]:
                print("    manufactured:", m)
            bad += len(manufactured)
    print(
        "baseline header-channel findings:",
        {k: v for k, v in base.items() if any(k in f for f in FED_BY.values())},
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))

#!/usr/bin/env python3
"""E9: "unknown evidence never creates a finding", swept over the layout corpus.

Takes a real old/new snapshot pair, erases one family of layout facts on ONE
side (offset_bits -> None, size_bits -> None, alignment -> None), recompares,
and reports every finding of a kind that the erased fact feeds. The oracle is
the Lean result FieldOffset.unknown_old_not_fires generalised: a finding whose
only evidence is the erased fact must not appear; a calm "unverifiable" RISK
finding is allowed (it states the gap, it does not claim a change).

Usage: unknown_evidence_sweep.py OLD.json NEW.json
"""

from __future__ import annotations

import copy
import sys
from collections import Counter
from pathlib import Path

from abicheck.checker import compare
from abicheck.serialization import load_snapshot

FED_BY = {
    "offset_bits": {"type_field_offset_changed", "struct_field_offset_changed"},
    "size_bits": {"type_size_changed", "struct_size_changed"},
    "alignment_bits": {"type_alignment_changed", "struct_alignment_changed"},
}


def erase(snap, fact: str):  # type: ignore[no-untyped-def]
    s = copy.deepcopy(snap)
    for t in s.declarations.types:
        if fact == "offset_bits":
            for f in t.fields:
                object.__setattr__(f, "offset_bits", None)
        elif hasattr(t, fact):
            object.__setattr__(t, fact, None)
    return s


def kinds(result) -> Counter[str]:  # type: ignore[no-untyped-def]
    return Counter(c.kind.value for c in result.changes)


def facts(result) -> set[tuple[str, str]]:  # type: ignore[no-untyped-def]
    """(family, subject) claims, normalising the header and DWARF spellings of
    the same claim: offset -> "rec::field", size/alignment -> "rec"."""
    out = set()
    for c in result.changes:
        k = c.kind.value
        for fam, fed in FED_BY.items():
            if k in fed:
                subj = c.symbol
                if fam == "offset_bits" and "::" not in subj:
                    subj = f"{subj}::{getattr(c, 'field_name', None) or c.detail}"
                out.add((fam, subj))
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
            extra = {
                k: v - base.get(k, 0) for k, v in got.items() if v > base.get(k, 0)
            }
            print(
                f"erase {fact:14s} on {side}: claims not in truth={len(manufactured)}; "
                f"true claims still made={len({c for c in truth if c[0] == fact}) - len(lost)}; "
                f"kinds that grew={extra or 0}"
            )
            for m in sorted(manufactured)[:3]:
                print("    manufactured:", m)
            bad += len(manufactured)
    print(
        "baseline fed-kind findings:",
        {k: v for k, v in base.items() if any(k in f for f in FED_BY.values())},
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))

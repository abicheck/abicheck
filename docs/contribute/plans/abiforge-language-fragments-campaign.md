# Union, bit-field and language-mode campaign

This batch expands the tested fragment rather than claiming a percentage of all C++.
The checker baseline is `b0b8793989c150a5b05f48e4e999a7df4219365a` on GCC 14.2, x86_64 Linux.

| Campaign | Compiler configurations | Observations | Public checker comparisons |
| --- | ---: | --- | ---: |
| Union | 192 | 1,344 layout values | 28 |
| Bit-fields | 108 | 540 storage/DWARF checks | 59 |
| C11/C17/C23 shared scalar fragment | 12 | 4,080 record layout vectors | 0 |

These are 312 probe configurations and 87 selected checker comparisons, not 312 research iterations. Provider builds and optimization controls are separate costs. Observations have different units and must not be summed into a coverage percentage.

## Consequences for the reduction model

A product estimate must specify a contract projection, configuration domain, admissible changes and independent evidence. Union member identity, bit ranges, alignment, and embedding can matter even when total size does not change. Reduction classes depend on these observations: the union matrix has 122 union-layout delta signatures but 790 layout-plus-member signatures among the same 2,592 pairs. Neither number bounds the unrestricted language.

Separate finite evidence from proof: these campaigns establish measured agreement on their enumerated domains. They do not establish an eventual depth cutoff, all consumer equivalence, or a completeness theorem for arbitrary C++ programs. A proof needs a compositional abstraction and preservation under every admitted context. Calling conventions require their own projection beyond storage layout.

Implementation priorities are (1) parameterized union member changes and boundary extents; (2) bit-field widths and zero-width barriers with packing-dependent expectations; (3) exact entity/member/old/new scoring, including unchanged controls; (4) explicit unknown alignment evidence; (5) reduction certificates scoped to a named projection. Cross-target/compiler and by-value consumer validation remain open.

The alignment observations below are deliberately outside the passing field/size projections. They are not counted as full ABI successes. Header-backed completion should be tested on these new fragments before transferring the earlier scalar-record result.


---

# Packed union layout campaign

GCC agreed with the independent model on all **1,344 layout values in 192 compiled configurations**. In 28 selected public DWARF comparisons, abicheck matched all 20 expected size and offset findings and all 24 expected union member findings. The probes measured four alignment deltas in those comparisons. The checker emitted zero alignment findings.

## Matrix and independent model

| Axis | Levels |
| --- | --- |
| Union members | `char[N]` alone, with `uint64_t`, with `double`, or with both scalars |
| Array extent | 7, 8, 9, 15, 16, 17 bytes |
| Union pack cap | 1, 2, 4, 8 bytes |
| Optimization | `-O0`, `-O2` |

Each C11 executable prints seven compiler-computed values. The union contributes size and alignment. An unpacked `Envelope` with a leading `char`, the embedded `Payload` union, and a trailing `uint32_t` contributes size, alignment, and all three member offsets. The fixture statically verifies the target's assumed scalar sizes, alignments, and byte width.

The Python model uses fixed scalar sizes and alignments and the declared array extent. Union alignment is the larger natural member alignment capped by the pack setting. Union size is the largest member size rounded up to that alignment. The envelope places the union at the first aligned offset after the leading byte and the trailing integer at the next four-byte boundary after the complete union. Its final size rounds up to the larger of four and the union alignment. The model consumes neither compiler probe results nor checker facts.

All 96 matched O0/O2 pairs have identical measured layouts. With the mixed union at pack 8, array changes 7→8 and 15→16 leave the union and envelope layout unchanged, while 8→9 and 16→17 increase the union size by eight bytes and move the trailing envelope member. The pack 1→8 comparison at extent 9 moves the embedded union from byte 1 to byte 8 and the trailing integer from byte 12 to byte 24.

## Public DWARF audit

The runner built 46 distinct shared libraries with DWARF 5. An exported envelope variable makes the two types reachable. Selected comparisons use live binaries with `python -m abicheck compare --depth binary` and the public JSON report. All 28 reported `dwarf_aware` evidence and matched the audited findings. There were zero failed, unknown, or mismatched probes or comparisons in the completed run.

| Selected comparison | Pairs | Size and offset rows | Union member rows |
| --- | ---: | ---: | ---: |
| Same-layout scalar retype, `uint64_t` → `double` | 8 | 0 | 8 |
| Same-layout addition of `uint64_t bits` | 8 | 0 | 8 |
| Mixed union array extent at the four boundary steps | 8 | 12 | 8 |
| Mixed union pack 1→8 and envelope placement | 2 | 8 | 0 |
| Char-only pack control | 1 | 0 | 0 |
| Optimization control | 1 | 0 | 0 |
| **Total** | **28** | **20** | **24** |

Size and offset findings are reconciled as multisets of metric, entity, field where applicable, and exact old/new values after conversion from checker units. Twelve rows are record size findings and eight are field offset findings. Union member rows are reconciled by kind, union, member and old/new declared type. There are 16 retype rows and eight addition rows. For additions, the public description supplies the member name because the row has no structured field name. The receipt retains the full change lists, including findings outside these projections.

The two pack comparisons each change `Payload` alignment from 1 to 8 and `Envelope` alignment from 4 to 8. Zero alignment rows appeared in the public reports. The receipt records these four probe-backed deltas separately from the matched size, offset and member rows. The two unchanged controls displayed no findings.

## Candidate reduction by observation

The 2,592 candidate pairs consist of 2,208 distinct member configurations at fixed pack and optimization, 288 pack comparisons at fixed members and optimization, and 96 optimization controls. Each unordered combination has one fixed old→new direction from matrix order. A signature records changed keys with **both endpoint values**. One deterministic representative is retained per distinct signature, and the receipt records each class's multiplicity.

| Observation projection | Classes | Candidates beyond one per class | Empty delta pairs |
| --- | ---: | ---: | ---: |
| Union size and alignment | 122 | 2,470 | 510 |
| All seven measured layout values | 127 | 2,465 | 510 |
| Measured layout and declared member name/type changes | 790 | 1,802 | 168 |

All 2,592 pairs contributed to each projection. Of the 510 pairs with unchanged union size and alignment, 342 changed declared members. The union-only projection also merges some nonempty signatures that differ in envelope observations. A representative preserves the indicated delta signature in this matrix. The 28 checker audits were selected for boundary and control coverage, and the reduction counts describe measured layout and declarations across the full candidate set.

## Reproduction and scope

Run `python research/union-campaign/run.py` from the checkout with GCC and the project's Python dependencies installed. The recorded run used Debian GCC 14.2.0-19 targeting `x86_64-linux-gnu`, Python 3.12.14, and abicheck `0.8.0+gb0b879398` at revision `b0b8793989c150a5b05f48e4e999a7df4219365a`. Use `--cc` to select a compiler and `--output` to write a fresh receipt elsewhere. Generated C, executables, ELF providers, and individual CLI reports are temporary. The generated `results.json` receipt contains the model and probe values, projection classes, and reconciled checker output.

The observations apply to this compiler, target, and C fixture. The packing axis applies to the union alone. The probes execute standalone compiler layout expressions. The campaign includes no cross-build consumer, by-value argument or return tests, register classification, or runtime compatibility oracle. The public checker audit covers only the selected pairs.

## Reproduction source: union-campaign

Save as `research/union-campaign/run.py`.

```python
#!/usr/bin/env python3
"""Measure packed union layout and audit selected public DWARF comparisons."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from itertools import combinations, product
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LIMITATIONS = (
    "Native C11 layouts on one GCC Linux x86-64 target.",
    "The fixture packs only Payload. Envelope uses natural placement.",
    "Checker audits use live ELF binaries with DWARF 5 at binary depth.",
    "Alignment findings are inventoried separately. Missing findings do not establish alignment.",
    "Candidate reduction preserves exact observed directional deltas only within its projection.",
    "No cross-build consumer, by-value call or return, or runtime compatibility oracle.",
)
FAMILIES = {
    "chars": {},
    "integer": {"scalar": "uint64_t"},
    "double": {"scalar": "double"},
    "mixed": {"scalar": "double", "bits": "uint64_t"},
}
EXTENTS = (7, 8, 9, 15, 16, 17)
PACKS = (1, 2, 4, 8)
OPTS = ("O0", "O2")
METRICS = {
    "Payload.size": "sizeof(union Payload)",
    "Payload.align": "_Alignof(union Payload)",
    "Envelope.size": "sizeof(struct Envelope)",
    "Envelope.align": "_Alignof(struct Envelope)",
    "Envelope.head": "offsetof(struct Envelope, head)",
    "Envelope.payload": "offsetof(struct Envelope, payload)",
    "Envelope.tail": "offsetof(struct Envelope, tail)",
}


@dataclass(frozen=True)
class Case:
    family: str
    extent: int
    pack: int
    opt: str

    @property
    def key(self) -> str:
        return f"{self.family}-{self.extent}-p{self.pack}-{self.opt}"

    def members(self) -> dict[str, str]:
        return {"bytes": f"char[{self.extent}]", **FAMILIES[self.family]}


def round_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def model(case: Case) -> dict[str, int]:
    scalar_layout = [(8, 8) for _ in FAMILIES[case.family]]
    members = [(case.extent, 1), *scalar_layout]
    union_align = min(case.pack, max(align for _, align in members))
    union_size = round_up(max(size for size, _ in members), union_align)
    embed_align = max(union_align, 4)
    payload = round_up(1, union_align)
    tail = round_up(payload + union_size, 4)
    return dict(zip(METRICS, (union_size, union_align, round_up(tail + 4, embed_align),
                             embed_align, 0, payload, tail), strict=True))


def source(case: Case) -> str:
    scalars = "\n".join(f"    {ctype} {name};" for name, ctype in FAMILIES[case.family].items())
    probe = "\n".join(f'    printf("{key}=%zu\\n", {expr});' for key, expr in METRICS.items())
    return f"""#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <limits.h>

_Static_assert(CHAR_BIT == 8, "byte");
_Static_assert(sizeof(uint64_t) == 8 && _Alignof(uint64_t) == 8, "uint64_t");
_Static_assert(sizeof(double) == 8 && _Alignof(double) == 8, "double");
_Static_assert(sizeof(uint32_t) == 4 && _Alignof(uint32_t) == 4, "uint32_t");
#pragma pack(push, {case.pack})
union Payload {{
    char bytes[{case.extent}];
{scalars}
}};
#pragma pack(pop)
struct Envelope {{
    char head;
    union Payload payload;
    uint32_t tail;
}};
#ifndef LAYOUT_PROBE
struct Envelope exported_envelope;
#else
int main(void) {{
{probe}
    return 0;
}}
#endif
"""


def run(*argv: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(arg) for arg in argv], cwd=ROOT, capture_output=True,
                          text=True, timeout=120, check=False)


def checked(*argv: object) -> str:
    result = run(*argv)
    result.check_returncode()
    return result.stdout.strip()


def measure(cc: str, work: Path, case: Case) -> dict:
    src = work / case.key / "union.c"
    src.parent.mkdir()
    src.write_text(source(case))
    exe = src.with_name("probe")
    row = {**asdict(case), "expected": model(case), "observed": None}
    try:
        checked(cc, "-std=c11", f"-{case.opt}", "-DLAYOUT_PROBE", src, "-o", exe)
        pairs = [line.split("=") for line in checked(exe).splitlines()]
        facts = {key: int(value) for key, value in pairs}
        if len(pairs) != len(facts) or facts.keys() != METRICS.keys():
            raise ValueError(f"unexpected fields: {pairs}")
    except subprocess.CalledProcessError as error:
        return {**row, "status": "failed", "error": str(error), "stderr": error.stderr}
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        return {**row, "status": "unknown", "error": str(error)}
    return {**row, "observed": facts, "status": "matched" if facts == row["expected"] else "mismatch"}


def candidate_pairs(cases: list[Case]):
    for old, new in combinations(cases, 2):
        if old.opt == new.opt and (old.pack == new.pack or
                                   (old.family, old.extent) == (new.family, new.extent)):
            yield old, new
        elif replace(old, opt=new.opt) == new:
            yield old, new


def differences(old: dict, new: dict) -> tuple:
    return tuple((key, old.get(key), new.get(key)) for key in sorted(old.keys() | new.keys())
                 if old.get(key) != new.get(key))


def projections(pairs, rows: dict) -> dict:
    groups = {name: defaultdict(list) for name in ("union", "layout", "layout_and_members")}
    unknown = []
    for old, new in pairs:
        pair = [old.key, new.key]
        a, b = (rows[key]["observed"] for key in pair)
        if a is None or b is None:
            unknown.append(pair)
            continue
        layout = differences(a, b)
        union = tuple(item for item in layout if item[0].startswith("Payload."))
        members = differences(old.members(), new.members())
        groups["union"][union].append(pair)
        groups["layout"][layout].append(pair)
        groups["layout_and_members"][(layout, members)].append(pair)
    return {
        "unknown_pairs": unknown,
        "projections": {
            name: {"classes": len(classes),
                   "reducible_candidates": sum(len(pairs) - 1 for pairs in classes.values()),
                   "groups": [{"signature": signature, "representative": members[0], "count": len(members)}
                              for signature, members in classes.items()]}
            for name, classes in groups.items()
        },
    }


def selected():
    for opt in OPTS:
        for pack in PACKS:
            yield "same-layout scalar retype", Case("integer", 8, pack, opt), Case("double", 8, pack, opt)
            yield "same-layout member addition", Case("double", 16, pack, opt), Case("mixed", 16, pack, opt)
        for lo, hi in ((7, 8), (8, 9), (15, 16), (16, 17)):
            yield "extent boundary", Case("mixed", lo, 8, opt), Case("mixed", hi, 8, opt)
        yield "pack and embedding placement", Case("mixed", 9, 1, opt), Case("mixed", 9, 8, opt)
    yield "pack insensitive chars", Case("chars", 17, 1, "O2"), Case("chars", 17, 8, "O2")
    yield "optimization control", Case("mixed", 17, 8, "O0"), Case("mixed", 17, 8, "O2")


def audit(work: Path, index: int, sample, binaries: dict, rows: dict) -> dict:
    label, old, new = sample
    row = dict(label=label, old=old.key, new=new.key,
               declared_member_delta=differences(old.members(), new.members()))
    path = work / f"audit-{index}.json"
    result = run(sys.executable, "-m", "abicheck", "compare", binaries[old], binaries[new],
                 "--depth", "binary", "-o", f"json={path}")
    row["exit_code"] = result.returncode
    if result.returncode not in (0, 1, 2, 4) or not path.exists():
        return {**row, "status": "failed", "stderr": result.stderr}
    report = json.loads(path.read_text())
    for name in ("verdict", "evidence_tier", "analysis_assurance", "old_evidence_depth",
                 "new_evidence_depth", "changes"):
        row[name] = report[name]
    a, b = (rows[case.key]["observed"] for case in (old, new))
    if a is None or b is None:
        return {**row, "status": "unknown"}
    delta = differences(a, b)
    expected = Counter()
    expected_alignment = Counter()
    for metric, before, after in delta:
        entity, field = metric.split(".")
        if field == "size":
            expected[("size", entity, None, before, after)] += 1
        elif field == "align":
            expected_alignment[("align", entity, None, before, after)] += 1
        else:
            expected[("offset", entity, field, before, after)] += 1
    actual = Counter()
    actual_alignment = Counter()
    expected_members = Counter(
        ("union_field_added" if before is None else "union_field_type_changed",
         "Payload", field, before, after)
        for field, before, after in row["declared_member_delta"]
    )
    actual_members = Counter()
    for change in row["changes"]:
        kind = change["kind"]
        if kind in {"union_field_added", "union_field_removed", "union_field_type_changed"}:
            field = change.get("field_name")
            if field is None and kind in {"union_field_added", "union_field_removed"}:
                # These findings expose the member name in the public description.
                prefix = f"Union field {kind.rsplit('_', 1)[1]}: {change['symbol']}::"
                description = change["description"]
                field = description.removeprefix(prefix) if description.startswith(prefix) else None
            actual_members[(kind, change["symbol"], field,
                            change["old_value"], change["new_value"])] += 1
        rules = {
            "type_size_changed": ("size", 8),
            "struct_size_changed": ("size", 1),
            "type_field_offset_changed": ("offset", 8),
            "struct_field_offset_changed": ("offset", 1),
            "type_alignment_changed": ("align", 8),
            "struct_alignment_changed": ("align", 1),
        }
        if kind in rules:
            metric, scale = rules[kind]
            before, after = (int(change[f"{side}_value"]) / scale for side in ("old", "new"))
            field = change.get("field_name") if metric == "offset" else None
            target = actual_alignment if metric == "align" else actual
            target[(metric, change["symbol"], field, before, after)] += 1
    matched = (actual == expected and actual_members == expected_members
               and not (actual_alignment - expected_alignment)
               and row["evidence_tier"] == "dwarf_aware")
    return dict(row, status="matched" if matched else "mismatch",
                expected_members=list(expected_members.elements()),
                actual_members=list(actual_members.elements()),
                missing_members=list((expected_members - actual_members).elements()),
                unexpected_members=list((actual_members - expected_members).elements()),
                expected=list(expected.elements()), actual=list(actual.elements()),
                missing=list((expected - actual).elements()),
                unexpected=list((actual - expected).elements()),
                alignment_probe_delta=list(expected_alignment.elements()),
                alignment_findings=list(actual_alignment.elements()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    args = parser.parse_args()
    target = checked(args.cc, "-dumpmachine")
    if not (target.startswith("x86_64-") and "linux" in target):
        parser.error(f"expected a Linux x86-64 compiler target, got {target}")
    cases = [Case(*values) for values in product(FAMILIES, EXTENTS, PACKS, OPTS)]
    samples = list(selected())
    pairs = list(candidate_pairs(cases))
    assert all((old, new) in pairs for _, old, new in samples)
    providers = dict.fromkeys(case for _, old, new in samples for case in (old, new))
    with TemporaryDirectory(prefix="abicheck-union-") as directory, ThreadPoolExecutor(8) as pool:
        work = Path(directory)
        rows = dict(zip((case.key for case in cases),
                        pool.map(lambda case: measure(args.cc, work, case), cases), strict=True))
        for case in providers:
            folder = work / case.key
            providers[case] = folder / "libunion.so"
            checked(args.cc, "-std=c11", f"-{case.opt}", "-gdwarf-5", "-fPIC", "-shared",
                    "-Wl,-soname,libunion.so.1", folder / "union.c", "-o", providers[case])
        audits = [audit(work, i, sample, providers, rows) for i, sample in enumerate(samples)]
    reduction = projections(pairs, rows)
    counts = {
        "probe_status": dict(Counter(row["status"] for row in rows.values())),
        "measured_layout_values": sum(len(row["observed"] or {}) for row in rows.values()),
        "dwarf_providers": len(providers),
        "audit_status": dict(Counter(row["status"] for row in audits)),
        "expected_size_offset_rows": sum(len(row.get("expected", ())) for row in audits),
        "actual_size_offset_rows": sum(len(row.get("actual", ())) for row in audits),
        "expected_alignment_deltas": sum(len(row.get("alignment_probe_delta", ())) for row in audits),
        "actual_alignment_rows": sum(len(row.get("alignment_findings", ())) for row in audits),
        "expected_member_rows": sum(len(row.get("expected_members", ())) for row in audits),
        "actual_member_rows": sum(len(row.get("actual_members", ())) for row in audits),
        "candidate_pairs": len(pairs),
        "optimization_controls": sum(old.opt != new.opt for old, new in pairs),
        "same_union_size_align_with_member_delta": sum(
            group["count"]
            for group in reduction["projections"]["layout_and_members"]["groups"]
            if group["signature"][1] and not any(key.startswith("Payload.")
                                                 for key, *_ in group["signature"][0])
        ),
    }
    receipt = {
        "compiler": checked(args.cc, "--version").splitlines()[0], "target": target,
        "python": sys.version.split()[0],
        "checker": checked(sys.executable, "-m", "abicheck", "--version"),
        "revision": checked("git", "rev-parse", "HEAD"),
        "limitations": LIMITATIONS,
        "counts": counts, "configurations": rows, "reduction": reduction, "audits": audits,
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(counts, indent=2))
    return int(any(row["status"] != "matched" for row in (*rows.values(), *audits)))


if __name__ == "__main__":
    sys.exit(main())

```


---

# GCC 14 C++20 bit-field campaign

All 108 configurations produced usable byte-level storage probes. The probes
agreed with raw DWARF on all 540 comparisons (108 record sizes, 324 named
bit-field offset/width pairs, and 108 ordinary-member offsets). All 54
O0/O2 storage signatures matched. All 59 selected public abicheck comparisons
matched the exact expected field findings. There were zero unknowns or
mismatches in those checks.

| Public checker audit | Pairs |
|---|---:|
| Adjacent widths in the tested set, no barrier | 16 |
| Insert barrier before `b` | 12 |
| Insert barrier after `b` | 12 |
| Default packing → pack 1 | 9 |
| O0 → O2 controls | 6 |
| Width 16 → 17 with a barrier | 4 |
| Total | 59 |

The public reports contained 105 exact field findings. These comprised 20
width changes for `a`, 24 offsets for `b`, 34 offsets for `c`, and 27
offsets for the ordinary `tail` member. All reports had the `dwarf_aware`
evidence tier. Their overall verdicts were 43 `BREAKING` and 16 `NO_CHANGE`.
The exact audit covers the field findings, with other finding kinds and
verdicts retained in the JSON for context.

## Method and reproduction

Run `python research/bitfield-campaign/run.py` from the checkout with the
abicheck dependencies, `g++-14`, and GNU `readelf` installed. The recorded run
used Debian GCC 14.2.0-19 for little-endian `x86_64-linux-gnu`, GNU binutils
2.44, Python 3.12.14, and abicheck `0.8.0+gb0b879398`. The runner recreates
its sources, executables, shared libraries, and CLI reports in a temporary
directory. The generated `results.json` receipt records every configuration, raw
sample, selected DIE attribute, optimization comparison, and field audit.

The matrix crosses `unsigned a` widths 1, 7, 8, 9, 15, 16, 17, 31, and 32
with three barrier placements (absent, unnamed `unsigned : 0` before `b`,
and after `b`), default packing and scoped `#pragma pack(push, 1)`, and
`-O0` and `-O2`. The other members are `unsigned b : 16`,
`unsigned c : 3`, and `unsigned char tail`. Both the probe and provider
use C++20. Packing is scoped around the fixture type.

Each probe zeroes the entire trivially copyable object, copies the baseline
with `memcpy`, assigns a single named field through a runtime setter, then
copies the resulting object bytes with `memcpy`. It tests zero, all ones,
and every one-hot input for each field. The decoder accepts a bit range only
when all samples support contiguous ascending bits, using the target's
little-endian byte representation. `sizeof` and `alignof` come directly
from compiled expressions. The probes use no member addresses or
`offsetof`.

The independently compiled shared library exposes the record through an
exported function and carries DWARF 5. GNU `readelf` supplies the named
members' `DW_AT_data_bit_offset` and `DW_AT_bit_size`, the ordinary
member's constant `DW_AT_data_member_location`, and the record's
`DW_AT_byte_size`. The runner converts only the ordinary location from
bytes to bits. It compares these facts with probe data, then derives
expected old/new field deltas from DWARF and checks the public
`abicheck compare --depth binary` JSON as an exact multiset of
kind, record, field, old value, and new value. Offset rows carry a
structured `field_name`. Width rows identify the field through the
anchored public description `Bitfield layout changed: CampaignBits::<field>`
and carry `bits=N` values. Missing or unsupported layout evidence and
unparseable checker findings are recorded as unknown with the reason.

The unnamed barriers had no member DIE in any provider. Their positions
are known from the generated source, and their effects are observed through
the named fields and record size. Alignment comes from the storage probe.
GCC layout observations apply to this fixture, compiler, target, and packing
mechanism. Generator flag `-fpack-struct=1`, legacy DWARF encodings, other
targets, signed or mixed-width base types, and calling conventions are
outside the run.

## Observations for abiforge

Offsets below are in bits and sizes are in bytes. `a` starts at bit 0 in
every configuration.

| Old → new at fixed optimization | `b` offset | `c` offset | `tail` offset | Size |
|---|---:|---:|---:|---:|
| Width 7 → 8, no barrier, pack 1 | 7 → 8 | 23 → 24 | 32 → 32 | 5 → 5 |
| Width 16 → 17, no barrier, default pack | 16 → 32 | 32 → 48 | 40 → 56 | 8 → 8 |
| Width 16 → 17, no barrier, pack 1 | 16 → 17 | 32 → 33 | 40 → 40 | 6 → 6 |
| Width 16 → 17, barrier before `b`, default pack | 32 → 32 | 48 → 48 | 56 → 56 | 8 → 8 |
| Insert barrier before `b`, width 17, default pack | 32 → 32 | 48 → 48 | 56 → 56 | 8 → 8 |
| Insert barrier after `b`, width 17, default pack | 32 → 32 | 48 → 64 | 56 → 72 | 8 → 12 |

1. Extend the two fixed-width cases in `abiforge/operators/layout.py`
   with parameterized width pairs and a following bit-field. Keep distinct
   expected rows for width-only changes, intra-byte movement, and allocation
   unit spill. The 16 → 17 rows show that spill can move later fields while
   `sizeof` stays fixed. Condition expected field deltas on packing and
   verify equivalent fixtures with abiforge's actual packing flag.
2. Add zero-width barrier insertion and removal operators before and after
   a named field. Seed an absorbed case and a moving case from the table.
   Derive expectations from downstream field offsets and size. The absence
   of a barrier DIE provides no basis for a named-member add/remove finding.
3. Strengthen the current `any_of` expectations for the bit-field width
   operators with exact entity, member, and old/new bit values for observable
   deltas. A width-only expected row catches a spurious offset. A combined
   row catches dropped or byte-rounded offsets even when the width finding
   still appears. Carry an explicit unknown state for missing bit evidence.
4. Keep alignment in the generator's probe signature. Four audited pack
   pairs had an observed alignment change from 4 to 1 with identical sizes
   and named-member ranges and returned `NO_CHANGE`. Those pairs matched
   the exact field audit. They provide alignment-only fixtures for a
   separate evidence audit.

## Reproduction source: bitfield-campaign

Save as `research/bitfield-campaign/run.py`.

```python
#!/usr/bin/env python3
"""Run the finite GCC 14 C++20 bit-field storage and public checker campaign."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from itertools import product
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = (1, 7, 8, 9, 15, 16, 17, 31, 32)
BARRIERS = ("none", "before-b", "after-b")
PACKS = ("default", "1")
OPTS = ("O0", "O2")
RECORD = "CampaignBits"
FIELDS = ("a", "b", "c", "tail")
BITFIELDS = FIELDS[:3]
OFFSET = "type_field_offset_changed"
WIDTH = "field_bitfield_changed"


@dataclass(frozen=True)
class Config:
    width: int
    barrier: str
    pack: str
    opt: str

    @property
    def key(self) -> str:
        return f"w{self.width}-{self.barrier}-pack{self.pack}-{self.opt}"

    @property
    def widths(self) -> dict[str, int]:
        return dict(a=self.width, b=16, c=3, tail=8)

    def header(self) -> str:
        lines = ["struct CampaignBits {", f"    unsigned a : {self.width};"]
        if self.barrier == "before-b":
            lines.append("    unsigned : 0;")
        lines.append("    unsigned b : 16;")
        if self.barrier == "after-b":
            lines.append("    unsigned : 0;")
        lines.extend(["    unsigned c : 3;", "    unsigned char tail;", "};"])
        if self.pack == "1":
            lines = ["#pragma pack(push, 1)", *lines, "#pragma pack(pop)"]
        return "\n".join(lines) + "\n"


PROVIDER = """#include "fixture.h"
extern "C" unsigned campaign_read(const CampaignBits* p) {
    return p->a + p->b + p->c + p->tail;
}
"""

PROBE = """#include "fixture.h"
#include <bit>
#include <climits>
#include <cstdio>
#include <cstring>
#include <limits>
#include <type_traits>

static_assert(std::endian::native == std::endian::little);
static_assert(CHAR_BIT == 8 && std::numeric_limits<unsigned>::digits == 32);
static_assert(std::is_trivially_copyable_v<CampaignBits>);

template<class Setter>
void measure(const char* field, unsigned width, Setter set) {
    for (int sample = -2; sample < static_cast<int>(width); ++sample) {
        const unsigned input = sample == -2 ? 0u :
            sample == -1 ? (~0u >> (32 - width)) : (1u << sample);
        CampaignBits value;
        std::memset(&value, 0, sizeof value);
        unsigned char baseline[sizeof value];
        std::memcpy(baseline, &value, sizeof value);
        set(&value, input);
        unsigned char bytes[sizeof value];
        std::memcpy(bytes, &value, sizeof value);
        std::printf("%s %d %08x ", field, sample, input);
        for (unsigned char byte : baseline) std::printf("%02x", unsigned(byte));
        std::printf(" ");
        for (unsigned char byte : bytes) std::printf("%02x", unsigned(byte));
        std::puts("");
    }
}
"""

DIE = re.compile(r"^\s*<(\d+)><[0-9a-f]+>: Abbrev Number: \d+(?: \((DW_TAG_\w+)\))?$")
ATTR = re.compile(r"^\s*<[0-9a-f]+>\s+DW_AT_(\w+)\s*:\s*(.*)$")
NAME = re.compile(r"(?:\([^)]*\):\s*)?(\w+)")


def run(*args, cwd=ROOT):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=45,
        env={**os.environ, "LC_ALL": "C"},
    )


def checked(*args, cwd=ROOT):
    result = run(*args, cwd=cwd)
    if result.returncode:
        raise ValueError(f"{args[0]} exit {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def unknown(stage, error):
    return dict(status="unknown", stage=stage, reason=str(error))


def make_probe(config):
    setters = "\n".join(
        f"[[gnu::noinline, gnu::noipa]] void set_{field}(CampaignBits* p, unsigned input) "
        f"{{ p->{field} = input; }}" for field in FIELDS
    )
    calls = "\n".join(
        f'    measure("{field}", {width}, set_{field});'
        for field, width in config.widths.items()
    )
    return PROBE + setters + '\nint main() {\n' + (
        '    std::printf("%zu %zu\\n", sizeof(CampaignBits), alignof(CampaignBits));\n'
        + calls + "\n}\n"
    )


def storage(stdout, config):
    layout, *samples = stdout.splitlines()
    size, alignment = map(int, layout.split())
    raw = {field: {} for field in FIELDS}
    for sample in samples:
        field, index, supplied, baseline, after = sample.split()
        index = int(index)
        if index in raw[field]:
            raise ValueError(f"duplicate sample {field} {index}")
        raw[field][index] = dict(input_hex=supplied, zeroed_hex=baseline, stored_hex=after)
    fields = {}
    for field, width in config.widths.items():
        rows = raw[field]
        try:
            if set(rows) != set(range(-2, width)):
                raise ValueError("missing or extra samples")
            masks = {}
            for index, sample in rows.items():
                before = bytes.fromhex(sample["zeroed_hex"])
                after = bytes.fromhex(sample["stored_hex"])
                value = 0 if index == -2 else (1 << width) - 1 if index == -1 else 1 << index
                if len(before) != size or len(after) != size or any(before):
                    raise ValueError("invalid object bytes or nonzero baseline")
                if int(sample["input_hex"], 16) != value:
                    raise ValueError("unexpected input value")
                masks[index] = int.from_bytes(after, "little")
            start = masks[0].bit_length() - 1
            if start < 0 or masks[-2] or masks[-1] != ((1 << width) - 1) << start:
                raise ValueError("zero or all-ones sample does not support a contiguous range")
            if any(masks[index] != 1 << (start + index) for index in range(width)):
                raise ValueError("one-hot samples do not support ascending contiguous bits")
            fields[field] = dict(status="observed", offset_bits=start, width_bits=width)
        except ValueError as error:
            fields[field] = unknown("decode_storage", error)
        fields[field]["samples"] = rows
    return dict(size_bytes=size, alignment_bytes=alignment, fields=fields)


def debug_dies(stdout):
    """Read just the record/member DIE attributes from GNU readelf debug-info text."""
    entries = []
    for line in stdout.splitlines():
        if match := DIE.fullmatch(line):
            entries.append((int(match[1]), match[2], {}))
        elif entries and (match := ATTR.fullmatch(line)):
            entries[-1][2][match[1]] = match[2]
    return entries


def name_of(attrs):
    name = NAME.fullmatch(attrs.get("name", ""))
    return name[1] if name else None


def number(attrs, attribute):
    value = attrs.get(attribute, "")
    if not re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", value):
        raise ValueError(f"missing or unsupported DW_AT_{attribute}: {value!r}")
    return int(value, 16 if value.startswith("0x") else 10)


def debug_layout(stdout):
    dies = debug_dies(stdout)
    records = [
        i for i, (_, tag, attrs) in enumerate(dies)
        if tag == "DW_TAG_structure_type" and name_of(attrs) == RECORD
    ]
    if len(records) != 1:
        raise ValueError(f"expected one {RECORD} DIE, saw {len(records)}")
    index = records[0]
    depth, _, attrs = dies[index]
    size = number(attrs, "byte_size")
    members = []
    for child_depth, tag, attrs in dies[index + 1:]:
        if child_depth <= depth:
            break
        if child_depth == depth + 1 and tag == "DW_TAG_member":
            members.append(attrs)
    fields = {}
    for field in FIELDS:
        matches = [attrs for attrs in members if name_of(attrs) == field]
        try:
            if len(matches) != 1:
                raise ValueError(f"expected one member DIE for {field}, saw {len(matches)}")
            attrs = matches[0]
            if field in BITFIELDS:
                offset = number(attrs, "data_bit_offset")
                width = number(attrs, "bit_size")
            else:
                offset = 8 * number(attrs, "data_member_location")
                width = None
            fields[field] = dict(status="observed", offset_bits=offset, width_bits=width)
        except ValueError as error:
            fields[field] = unknown("decode_member_die", error)
        fields[field]["raw_attributes"] = matches
    return dict(size_bytes=size, fields=fields, unnamed_members=[
        attrs for attrs in members if name_of(attrs) is None
    ])


def reconcile_storage(probe, debug):
    result = {"sizeof": "matched" if probe["size_bytes"] == debug["size_bytes"] else "mismatch"}
    for field in FIELDS:
        measured, recorded = probe["fields"][field], debug["fields"][field]
        if any(value["status"] != "observed" for value in (measured, recorded)):
            result[field] = "unknown"
            continue
        metrics = ("offset_bits", "width_bits") if field in BITFIELDS else ("offset_bits",)
        result[field] = "matched" if all(measured[key] == recorded[key] for key in metrics) else "mismatch"
    return result


def collect(work, cxx, config):
    folder = work / config.key
    folder.mkdir()
    header = config.header()
    (folder / "fixture.h").write_text(header)
    (folder / "provider.cc").write_text(PROVIDER)
    (folder / "probe.cc").write_text(make_probe(config))
    row = dict(config=asdict(config), fixture=header)
    flags = ("-std=c++20", f"-{config.opt}")
    try:
        checked(cxx, *flags, "probe.cc", "-o", "probe", cwd=folder)
        row["storage"] = storage(checked("./probe", cwd=folder), config)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
        row["storage"] = unknown("storage_probe", error)
    try:
        checked(cxx, *flags, "-g", "-gdwarf-5", "-fPIC", "-shared",
                "-Wl,-soname,libcampaignbits.so", "provider.cc", "-o", "libcampaignbits.so", cwd=folder)
        row["provider"] = {"status": "built"}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        row["provider"] = unknown("provider_build", error)
    if row["provider"]["status"] == "built":
        try:
            row["dwarf"] = debug_layout(checked("readelf", "--debug-dump=info", "libcampaignbits.so", cwd=folder))
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            row["dwarf"] = unknown("readelf", error)
    else:
        row["dwarf"] = unknown("provider_dependency", row["provider"]["reason"])
    if "fields" in row["storage"] and "fields" in row["dwarf"]:
        row["storage_vs_dwarf"] = reconcile_storage(row["storage"], row["dwarf"])
    else:
        row["storage_vs_dwarf"] = dict.fromkeys(("sizeof", *FIELDS), "unknown")
    return row


def selected_pairs():
    for pack in PACKS:
        for width in (1, 7, 8, 9, 15, 16, 17, 31):
            next_width = WIDTHS[WIDTHS.index(width) + 1]
            old = Config(width, "none", pack, "O0")
            yield "width", old, replace(old, width=next_width)
        for opt in OPTS:
            for width in (7, 17, 32):
                old = Config(width, "none", pack, opt)
                yield "barrier-before", old, replace(old, barrier="before-b")
                yield "barrier-after", old, replace(old, barrier="after-b")
    for barrier, width in product(BARRIERS, (9, 17, 32)):
        old = Config(width, barrier, "default", "O2")
        yield "pack", old, replace(old, pack="1")
    for pack, barrier in product(PACKS, BARRIERS):
        old = Config(17, barrier, pack, "O0")
        yield "optimization-control", old, replace(old, opt="O2")
    for barrier, pack in product(BARRIERS[1:], PACKS):
        old = Config(16, barrier, pack, "O2")
        yield "width-with-barrier", old, replace(old, width=17)


def expected_findings(old, new):
    expected = Counter()
    unknown_fields = []
    for field in FIELDS:
        facts = [row.get("dwarf", {}).get("fields", {}).get(field, {}) for row in (old, new)]
        if any(fact.get("status") != "observed" for fact in facts):
            unknown_fields.append(field)
            continue
        left, right = facts
        for kind, metric in ((OFFSET, "offset_bits"), (WIDTH, "width_bits")):
            before, after = left[metric], right[metric]
            if before != after:
                expected[(kind, RECORD, field, before, after)] += 1
    return expected, unknown_fields


def observed_findings(changes):
    observed = Counter()
    undecodable = []
    for change in changes:
        kind = change["kind"]
        if kind not in (WIDTH, OFFSET):
            continue
        symbol = change["symbol"]
        field = change.get("field_name")
        if kind == WIDTH and not field:
            match = re.fullmatch(rf"Bitfield layout changed: {RECORD}::(\w+)", change["description"])
            field = match[1] if match else None
        try:
            pattern = r"bits=(\d+)" if kind == WIDTH else r"(\d+)"
            values = [re.fullmatch(pattern, str(change[key])) for key in ("old_value", "new_value")]
            if not field or any(value is None for value in values):
                raise ValueError("field or values unavailable")
            observed[(kind, symbol, field, *(int(value[1]) for value in values))] += 1
        except (KeyError, ValueError):
            undecodable.append(change)
    return observed, undecodable


def audit(work, rows, pair):
    scope, old, new = pair
    row = dict(scope=scope, old=old.key, new=new.key)
    old_row, new_row = rows[old.key], rows[new.key]
    expected, unknown_fields = expected_findings(old_row, new_row)
    row.update(expected=list(expected.elements()), unknown_fields=unknown_fields)
    if any(item["provider"]["status"] != "built" for item in (old_row, new_row)):
        return dict(row, **unknown("provider_dependency", "one or both provider builds failed"))
    report = work / f"{old.key}__{new.key}.json"
    try:
        result = run(sys.executable, "-m", "abicheck", "compare",
                     work / old.key / "libcampaignbits.so", work / new.key / "libcampaignbits.so",
                     "--depth", "binary", "-o", f"json={report}")
        row["exit_code"] = result.returncode
        if result.returncode not in (0, 2, 4):
            raise ValueError(f"exit {result.returncode}: {result.stderr.strip()}")
        document = json.loads(report.read_text())
        changes = document["changes"]
        observed, undecodable = observed_findings(changes)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
        return dict(row, **unknown("checker", error))
    known = Counter({finding: count for finding, count in observed.items()
                     if finding[1] != RECORD or finding[2] not in unknown_fields})
    missing = list((expected - known).elements())
    unexpected = list((known - expected).elements())
    if unknown_fields or undecodable:
        status = "unknown"
    else:
        status = "mismatch" if missing or unexpected else "matched"
    return dict(row, status=status, observed=list(observed.elements()), missing=missing,
                unexpected=unexpected, undecodable=undecodable, verdict=document.get("verdict"),
                evidence_tier=document.get("evidence_tier"),
                analysis_assurance=document.get("analysis_assurance"),
                change_kinds=dict(Counter(change["kind"] for change in changes)),
                field_findings=[change for change in changes if change["kind"] in (WIDTH, OFFSET)])


def signature(row):
    storage_row = row["storage"]
    if "fields" not in storage_row or any(
        fact["status"] != "observed" for fact in storage_row["fields"].values()
    ):
        return None
    return [
        storage_row["size_bytes"], storage_row["alignment_bytes"],
        *[[fact["offset_bits"], fact["width_bits"]] for fact in storage_row["fields"].values()],
    ]


def optimization_checks(rows):
    checks = []
    for width, barrier, pack in product(WIDTHS, BARRIERS, PACKS):
        old = Config(width, barrier, pack, "O0")
        new = replace(old, opt="O2")
        left, right = (signature(rows[config.key]) for config in (old, new))
        if left is None or right is None:
            status = "unknown"
        else:
            status = "matched" if left == right else "mismatch"
        checks.append(dict(old=old.key, new=new.key, status=status))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++-14")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("results.json"))
    args = parser.parse_args()
    version = checked(args.cxx, "-dumpfullversion").strip()
    if version.split(".")[0] != "14":
        parser.error(f"GCC 14 required, got {version}")
    metadata = dict(
        compiler=checked(args.cxx, "--version").splitlines()[0],
        target=checked(args.cxx, "-dumpmachine").strip(),
        readelf=checked("readelf", "--version").splitlines()[0],
        checker=checked(sys.executable, "-m", "abicheck", "--version").strip(),
        revision=checked("git", "rev-parse", "HEAD").strip(),
        python=sys.version.split()[0],
        provider_flags=["-std=c++20", "-O0 or -O2", "-g", "-gdwarf-5", "-fPIC", "-shared"],
        probe_flags=["-std=c++20", "-O0 or -O2"],
    )
    configs = [Config(*items) for items in product(WIDTHS, BARRIERS, PACKS, OPTS)]
    with TemporaryDirectory(prefix="abicheck-bitfield-") as directory, ThreadPoolExecutor(4) as pool:
        work = Path(directory)
        rows = dict(zip((config.key for config in configs),
                        pool.map(lambda config: collect(work, args.cxx, config), configs), strict=True))
        audits = list(pool.map(lambda pair: audit(work, rows, pair), selected_pairs()))
    optimization = optimization_checks(rows)
    summary = dict(
        configurations=len(rows),
        observed_storage=sum(signature(row) is not None for row in rows.values()),
        storage_vs_dwarf=dict(Counter(status for row in rows.values()
                                      for status in row["storage_vs_dwarf"].values())),
        optimization_checks=dict(Counter(row["status"] for row in optimization)),
        checker_audits=dict(Counter(row["status"] for row in audits)),
        expected_findings=dict(Counter(finding[0] for row in audits for finding in row["expected"])),
        observed_findings=dict(Counter(finding[0] for row in audits for finding in row.get("observed", []))),
    )
    args.output.write_text(json.dumps(dict(metadata=metadata, summary=summary, configurations=rows,
                                           optimization_checks=optimization, audits=audits), indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

```


---

# C standard cross-check

Twelve GCC 14.2 compiler configurations (C11/C17/C23 × pack caps 1/2/4/8, O2) each measured 340 ordered scalar records: **4,080 layout vectors**. All matched the existing C++20 O2 vectors, which had separately matched the independent round-up model. A vector contains size, alignment and every field offset; this is not 4,080 builds.

The shared fragment has one to four unsigned scalar fields of natural sizes/alignments 1, 2, 4 and 8 on this x86_64 target. Builds used `-pedantic-errors`, with the GCC packing extension explicitly enabled. The initial C conversion used `alignof` without its pre-C23 header and failed for C11/C17; the harness was corrected to `_Alignof` and all twelve configurations rerun. That harness error is not language ABI incompatibility.

This supports sharing a layout model across these standard modes for this fragment. It does not cover C++ inheritance, templates, overload resolution, enum differences, calling convention, source compatibility or other targets/compilers. No checker comparisons were run in this cross-check.

Reproduction: first run the packing campaign from the configuration campaign document to produce `research/packing-campaign/probe.cpp` and `results.json`, then run the script below. Unsupported compiler modes remain failures, never silently excluded from the denominator.

## Reproduction source: c-standard-campaign

Save as `research/c-standard-campaign/run.py`.

```python
from pathlib import Path
import re,json,subprocess as sp
P=Path(__file__).resolve().parent;B=P.parent/'packing-campaign'
s=(B/'probe.cpp').read_text().replace('<cstdio>','<stdio.h>').replace('<cstddef>','<stddef.h>').replace('std::printf','printf').replace('alignof(', '_Alignof(')
s=re.sub(r'(?<!struct )\bT\d+\b',lambda m:'struct '+m[0],s)
(P/'probe.c').write_text(s)
obs=json.loads((B/'results.json').read_text());rows=[]
for standard in ['c11','c17','c23']:
 for cap in [1,2,4,8]:
  exe=P/f'{standard}-pack{cap}';cmd=['gcc','-std='+standard,'-pedantic-errors','-O2',f'-fpack-struct={cap}',str(P/'probe.c'),'-o',str(exe)]
  p=sp.run(cmd,capture_output=True,text=True,timeout=60)
  if p.returncode:rows.append(dict(standard=standard,cap=cap,status='compile_failed',stderr=p.stderr));continue
  output=sp.check_output([str(exe)],text=True,timeout=10)
  got=[list(map(int,l.split())) for l in output.splitlines()];expected=[x['layout'] for x in obs if x['opt']=='O2' and x['cap']==cap]
  rows.append(dict(standard=standard,cap=cap,status='matched' if got==expected else 'mismatch',types=len(got),argv=cmd,observations=got))
(P/'results.json').write_text(json.dumps(rows,indent=2));print([(r['standard'],r['cap'],r['status']) for r in rows])

raise SystemExit(0 if all(r["status"] == "matched" for r in rows) else 1)

```

---

# Follow-up: bit-field reduction certificate and language-revision edits

## Bit-field reduction certificate (priority 5)

`abiforge/model/reduction.py` checks whether the generator's bit-field edits
stand for every edit of the same shape, under a named projection, by exhaustive
enumeration over the independent layout model.

- **Domain.** The `bitfield_record` shape (`a : w`, `b : 16`, `c : 3`, an
  ordinary `tail`, an optional `unsigned : 0` barrier before or after `b`),
  `w` in 1..32. Every width change at a fixed barrier and every barrier
  insertion or removal at a fixed width: 3,104 transitions, each under gcc and
  clang with default packing and `-fpack-struct=1` (12,416 transition cells).
- **Projection.** The *shape* of the exact rows an assertion judges: the edit
  kind and the set of (row kind, member) pairs (offset of `b`, `c`, `tail`;
  width of `a`; size; alignment). Values are excluded. A class under this
  projection says nothing about values, calling convention, consumers or any
  other record shape.

| Configuration | Classes | Reached by the original 15 edits | Reached after adding 7 |
| --- | ---: | ---: | ---: |
| gcc, default packing | 10 | 8 | 10 |
| clang, default packing | 10 | 8 | 10 |
| gcc, `-fpack-struct=1` | 9 | 5 | 9 |
| clang, `-fpack-struct=1` | 9 | 5 | 9 |

The original edits missed barrier removals that change the size, width changes
on either side of a barrier, and the packed-only absorbed cases. For example,
gcc's byte-rounded barrier after `b` absorbs an edit when `a + b` already ends
on a byte. Seven operators close every class: barrier removals at widths 1, 6,
14, 16 and 30, plus `a : 1 → 2` with a barrier before and after `b`. Removing
any one of the seven reopens a class (`tests/test_abiforge_reduction.py`).
Under this projection, 13 of the original 15 edits are redundant. They are kept
anyway: the exact assertions judge values, and the standard-space fault study
(`research/standard-space/followup/reduction`) shows that selected
representatives can miss a fault that the dense run catches. The seven
additions were chosen after the classes were computed, a greedy cover of a
known finite domain. They are not a sample whose detection rate generalizes.

**Fault retention.** Four rule mutants of the model each count the
(transition, configuration) cells whose exact rows differ from the true model:
barrier ignored, gcc/clang barrier rule swapped (the `#pragma pack` versus
`-fpack-struct` transfer error), packing ignored and packing forced.

| Mutant | Dense detecting cells | Selected (22 edits × 4 configs) |
| --- | ---: | ---: |
| barrier ignored | 7,356 | 42 |
| barrier rule swapped | 3,264 | 16 |
| packing ignored | 4,448 | 32 |
| packing forced | 4,448 | 32 |

Every mutant is detected by both suites. A suite of unpacked width-only edits
without barriers detects neither barrier mutant, and the test pins that
negative control. Four mutants say nothing about faults outside them. The
certificate is the model's own arithmetic. The compiler side is
`tests/test_abiforge_layout_model.py` and the checker side is
`python -m abiforge.run.fragment_campaign`.

## Language-revision edits (standard-space follow-up)

`research/standard-space/cpp.csv` found two breaks from identical source when
only the language revision of the provider changes. The gated `cxx_dialect`
seed (`abiforge/operators/dialect.py`) makes them generator inputs, each with
the untouched functions as forbidden-attribution controls:

| Operator | Baseline | Rebuild | Old name lost |
| --- | --- | --- | --- |
| `dialect_cxx17_noexcept_in_function_type` | C++11/14 | `-std=c++17` (P0012R1) | `_Z11dl_registerPFvvE` |
| `dialect_cxx20_char8_t_parameter` | C++17 | `-std=c++20` (P0482R6) | `_Z7dl_takeRKc` |

On gcc 13 and clang 18, all eight lanes (binary, DWARF, headers, stripped
headers) return BREAKING with the claim on the old name, and the execution
oracle observes the consumer break. Binary and DWARF lanes pair the two names
(`FUNC_PARAMS_CHANGED`, plus `CHAR8T_MIGRATION` for the second edit). Header
lanes parse the unchanged header at the baseline standard, so they still see
the old declaration and report its export lost (`FUNC_VISIBILITY_CHANGED`).
That is a true reading of the evidence they were given, but it does not name
the rename. Naming it needs the new build's `-std=`, which is L3 build data.
Reverse directions, mixed compilers and other triggers listed in the research
remain open.

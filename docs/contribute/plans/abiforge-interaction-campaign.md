# GCC packing × short-enum interaction campaign

GCC 14.2.0 matched the arithmetic layout model in all 64 builds.
Packing changed the effect of short enums in 30 of 48 four-corner
comparisons. The complete measured layouts reduce to 12 classes for
this fixture, while the public checker sample shows why an ABI
comparison also needs to preserve enum values and flag provenance.

## Design and replay

Run `python research/interaction-campaign/run.py` with the checkout's
Python dependencies and `g++-14`. Override the compiler with
`INTERACTION_CAMPAIGN_CXX`. This run used Debian GCC 14.2.0-19,
x86_64-linux-gnu, Python 3.12.14 and abicheck `0.8.0+gb0b879398`.

The full factorial crosses pack caps 1, 2, 4 and 8, explicit
`-fno-short-enums` and `-fshort-enums`, four unscoped enum ranges
starting at zero, and `-O0` and `-O2`. The ranges straddle both
unsigned thresholds established in the enum campaign.

| Upper enumerator | Default width | Short width |
| ---: | ---: | ---: |
| 255 | 4 | 1 |
| 256 | 4 | 2 |
| 65535 | 4 | 2 |
| 65536 | 4 | 4 |

Every cell generates a standalone C++20 program using compiler-evaluated
`sizeof`, `alignof` and `offsetof`. It measures a direct enum member,
an array of three enums, an eight-byte-anchored inner record, a record
embedding that inner record, and a record embedding three inner records.
All five records are defined under the same pack cap. The outer
records put their payload between byte members. The probe has no shared
provider linkage and makes no cross-build calls.

The Python model takes enum width from the threshold rule above. It
caps each field's natural alignment, rounds the running offset up to
that alignment, and rounds the final extent to the maximum effective
field alignment. Array extent is element extent times count, including
any tail padding in each inner record. This model uses target-specific
assumptions and never reads compiler or checker output to compute its
expectations. The probe statically verifies the eight-byte anchor's
size and alignment.

| Work item | Completed | Failed | Unknown | Mismatched |
| --- | ---: | ---: | ---: | ---: |
| Standalone probe builds and executions | 64 | 0 | 0 | 0 |
| Shared providers for selected audits | 32 | 0 | 0 | — |
| Four-corner layout comparisons | 48 | 0 | 0 | — |
| Public CLI comparisons | 31 | 0 | 0 | — |
| Exact CLI size and offset reconciliation | 31 | 0 | 0 | 0 |

The probes cover 320 record instances and 64 enum instances, with
46 measurements per cell (2,944 total). A command failure is recorded
as failed. Missing dependencies for a comparison or unparseable
evidence produce an unknown result. A valid measurement that disagrees
with its expectation produces a mismatch. `results.json`
contains per-cell facts, all four-corner differences, layout classes,
and the public finding rows. The runner regenerates the sources and
uses temporary paths for binaries and CLI reports.

## Interactions and reduction

For each range and optimization level, all six pairs of pack caps are
checked. The interaction is the short-minus-default change at the
higher cap minus the same change at the lower cap. Each row below
counts cap pairs with any nonzero measurement. O0 and O2 agree on all
32 matched setting pairs.

| Upper enumerator | Interacting cap pairs at each optimization |
| ---: | --- |
| 255 | 1–2, 1–4, 1–8, 2–4, 2–8 |
| 256 | 1–4, 1–8, 2–4, 2–8, 4–8 |
| 65535 | 1–4, 1–8, 2–4, 2–8, 4–8 |
| 65536 | None |

For caps 1 and 8 at the 255 threshold, these are the measured bytes
at both optimization levels.

| Metric | Pack 1 default | Pack 1 short | Pack 8 default | Pack 8 short |
| --- | ---: | ---: | ---: | ---: |
| Direct enum offset | 1 | 1 | 4 | 1 |
| Inner size | 14 | 11 | 24 | 16 |
| Nested size | 16 | 13 | 40 | 32 |
| Record array element stride | 14 | 11 | 24 | 16 |
| Record array tail offset | 43 | 34 | 80 | 56 |

The enum still shrinks from four bytes to one at pack 1, where its
direct member offset is invariant. The record array tail moves nine
bytes at pack 1 and 24 at pack 8. A packing delta plus a short-enum
delta measured from the same baseline therefore gives the wrong joint
prediction. At the 256 and 65535 ranges the pack 4–8 interaction
appears in the anchored and nested contexts even though both direct
contexts have reached their alignment ceiling.

For storage-layout sampling within this exact fixture, the 64 cells
collapse to 12 measured signatures, one for each combination of cap and
observed enum width. One optimization level and one representative per
signature suffice to reproduce the measured vectors. Keep the second
optimization level as a sampled control when replaying the reduction on
another toolchain. Keep 255/256 and 65535/65536 as separate checker
boundary pairs, and retain the width-preserving flag toggle. These carry
enum-value and flag evidence omitted by the storage signature.

Include nested and array-of-record witnesses when reducing comparison
pairs. At pack 8 with short enums, 255 → 256 keeps the sizes of
`Inner`, `Nested` and `RecordArray` at 16, 32 and 64 bytes,
respectively, and leaves the record-array stride at 16. Inside each
element, `Inner::value` moves from 9 to 10 and `Inner::tail` from
10 to 12. Conversely, pack 4 → 8 at the four-byte range leaves
`Direct` and `EnumArray` unchanged while all three anchored or
nested record sizes change. A reduction based only on outer extent or
direct enum members would discard these witnesses.

## Public checker audit

The runner calls `python -m abicheck compare OLD NEW --depth binary -o json=REPORT`
on live shared providers with DWARF 5 and a constant SONAME. The sample
includes single-factor and joint changes, threshold edits, and unchanged
controls. Each provider exposes the enum and all five record types.
All 31 reports were DWARF-aware with complete analysis assurance and
clean DWARF context.

| Audit class | Comparisons | Verdict |
| --- | ---: | --- |
| Width-changing flag toggles | 6 | BREAKING (4) |
| Width-preserving flag toggles | 2 | COMPATIBLE (0) |
| Joint packing and flag changes | 4 | BREAKING (4) |
| Packing-only changes | 4 | BREAKING (4) |
| Threshold edits in short mode | 6 | BREAKING (4) |
| Threshold edits in default mode | 6 | BREAKING (4) |
| Matched optimization and self controls | 3 | NO_CHANGE (0) |

The checker exactly matched the probes on 16 enum-width changes, 90
record extents and 142 member offsets, with no missing or extra rows
of those three kinds. The pack-8 short 255 → 256 pair reported the two
inner offset changes at both optimization levels. The default-mode
boundary pairs changed `Hi` while preserving layout and each produced
an enumerator-value finding and a SONAME recommendation. The two
width-preserving flag pairs produced only `toolchain_flag_drift`.
The three optimization and self controls had empty change lists.

The exact audit covers enum width, record extent and member offsets.
Alignment, member extent and array stride remain direct probe evidence.
The layout equivalence classes and arithmetic model are scoped to
these unsigned enums, shared pack scopes, ordinary aggregate fields,
this GCC build and this target. Calling convention behavior, separate
inner and outer pack scopes, and other C++ layouts remain outside this
campaign.

## Reproducer

Save as `research/interaction-campaign/run.py`.

```python
#!/usr/bin/env python3
"""Replay GCC packing and short-enum layout probes and a public CLI sample."""

import itertools
import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CXX = os.environ.get("INTERACTION_CAMPAIGN_CXX", "g++-14")
LIMITS = (255, 256, 65535, 65536)
CAPS = (1, 2, 4, 8)
OPTS = ("O0", "O2")
SCHEMA = {
    "Direct": (("lead", "byte", 1), ("value", "E", 1), ("tail", "byte", 1)),
    "EnumArray": (("lead", "byte", 1), ("values", "E", 3), ("tail", "byte", 1)),
    "Inner": (("anchor", "word", 1), ("lead", "byte", 1),
              ("value", "E", 1), ("tail", "byte", 1)),
    "Nested": (("lead", "byte", 1), ("item", "Inner", 1), ("tail", "byte", 1)),
    "RecordArray": (("lead", "byte", 1), ("items", "Inner", 3), ("tail", "byte", 1)),
}
AUDIT = (
    ("flag-width-1-pack1", 255, 1, False, 1, True),
    ("flag-width-2-pack2", 256, 2, False, 2, True),
    ("flag-width-2-pack8", 65535, 8, False, 8, True),
    ("flag-width-4-control", 65536, 8, False, 8, True),
    ("joint-to-pack1", 255, 8, False, 1, True),
    ("joint-width-2", 256, 8, False, 2, True),
    ("pack-only", 65536, 8, True, 1, True),
    ("pack4-to-pack8", 65536, 4, True, 8, True),
)


def command(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)


def checked(*args):
    result = command(*args)
    result.check_returncode()
    return result.stdout.strip()


def key(opt, limit, cap, short):
    return f"{opt}/u{limit}/p{cap}/{'short' if short else 'default'}"


def header(limit, cap):
    lines = [
        "using byte = unsigned char;",
        "using word = unsigned long long;",
        "static_assert(sizeof(word) == 8 && alignof(word) == 8);",
        f"enum E {{ Lo = 0, Hi = {limit} }};",
        f"#pragma pack(push, {cap})",
    ]
    for name, fields in SCHEMA.items():
        declarations = " ".join(
            f"{kind} {field}" + (f"[{count}];" if count > 1 else ";")
            for field, kind, count in fields
        )
        lines.append(f"struct {name} {{ {declarations} }};")
    return "\n".join([*lines, "#pragma pack(pop)", ""])


def probe_source():
    lines = ['#include "fixture.h"', "#include <cstddef>", "#include <cstdio>", "int main() {"]
    for name in ("E", *SCHEMA):
        lines.append(f'  std::printf("{name}.size %zu\\n{name}.align %zu\\n", '
                     f'sizeof({name}), alignof({name}));')
        for field, _, count in SCHEMA.get(name, ()):
            lines.append(f'  std::printf("{name}.{field}.offset %zu\\n{name}.{field}.size %zu\\n", '
                         f'offsetof({name}, {field}), sizeof({name}::{field}));')
            if count > 1:
                lines.append(f'  std::printf("{name}.{field}.stride %zu\\n", '
                             f'sizeof({name}::{field}[0]));')
    return "\n".join([*lines, "}", ""])


def provider_source():
    lines = ['#include "fixture.h"', 'extern "C" E enum_echo(E value) { return value; }']
    for name in SCHEMA:
        field = "lead" if name == "Inner" else "tail"
        lines.append(f'extern "C" byte read_{name}(const {name}* p) {{ return p->{field}; }}')
    return "\n".join(lines) + "\n"


def round_up(value, align):
    return ((value + align - 1) // align) * align


def predict(limit, cap, short):
    width = next(size for size in (1, 2, 4) if limit < 256**size) if short else 4
    types = {"E": (width, width), "word": (8, 8), "byte": (1, 1)}
    facts = {"E.size": width, "E.align": width}
    for name, fields in SCHEMA.items():
        end, record_align = 0, 1
        for field, kind, count in fields:
            stride, natural_align = types[kind]
            align = min(cap, natural_align)
            offset = round_up(end, align)
            size = stride * count
            facts[f"{name}.{field}.offset"] = offset
            facts[f"{name}.{field}.size"] = size
            if count > 1:
                facts[f"{name}.{field}.stride"] = stride
            record_align = max(record_align, align)
            end = offset + size
        size = round_up(end, record_align)
        types[name] = (size, record_align)
        facts[f"{name}.size"] = size
        facts[f"{name}.align"] = record_align
    return facts


def delta(before, after):
    return {field: [value, after.get(field)] for field, value in before.items()
            if value != after.get(field)} | {
        field: [None, value] for field, value in after.items() if field not in before
    }


def execute(row, stage, *args):
    try:
        result = command(*args)
    except (OSError, subprocess.TimeoutExpired) as error:
        row.update(status="failed", stage=stage, error=str(error))
        return None
    if stage != "compare" and result.returncode != 0:
        row.update(status="failed", stage=stage, exit_code=result.returncode, error=result.stderr)
        return None
    return result


def probe(work, opt, limit, cap, short):
    case = key(opt, limit, cap, short)
    folder = work / case
    folder.mkdir(parents=True)
    (folder / "fixture.h").write_text(header(limit, cap))
    (folder / "probe.cpp").write_text(probe_source())
    row = {"case": case, "expected": predict(limit, cap, short)}
    result = execute(row, "probe_compile", CXX, "-std=c++20", f"-{opt}",
                     "-fshort-enums" if short else "-fno-short-enums",
                     folder / "probe.cpp", "-o", folder / "probe")
    if result is None:
        return row
    result = execute(row, "probe_execute", folder / "probe")
    if result is None:
        return row
    try:
        pairs = [line.split() for line in result.stdout.splitlines()]
        observed = {field: int(value) for field, value in pairs}
        if len(observed) != len(pairs) or observed.keys() != row["expected"].keys():
            raise ValueError("unexpected probe fields")
    except ValueError as error:
        row.update(status="unknown", stage="probe_parse", error=str(error), output=result.stdout)
    else:
        difference = delta(row["expected"], observed)
        row.update(status="mismatch" if difference else "matched",
                   observed=observed, differences=difference)
    return row


def provider(work, case):
    folder = work / case
    opt, _, _, mode = case.split("/")
    (folder / "provider.cpp").write_text(provider_source())
    row = {"case": case}
    result = execute(row, "provider_compile", CXX, "-std=c++20", f"-{opt}", "-gdwarf-5",
                     "-fshort-enums" if mode == "short" else "-fno-short-enums",
                     "-shared", "-fPIC", "-Wl,-soname,libinteraction.so.1",
                     folder / "provider.cpp", "-o", folder / "libinteraction.so")
    if result is not None:
        row["status"] = "built"
    return row


def audit_pairs():
    for opt in OPTS:
        for label, limit, p0, s0, p1, s1 in AUDIT:
            yield label, key(opt, limit, p0, s0), key(opt, limit, p1, s1)
        for low, high, cap in ((255, 256, 1), (255, 256, 8), (65535, 65536, 8)):
            for short in (False, True):
                label = "boundary-short" if short else "boundary-default"
                yield label, key(opt, low, cap, short), key(opt, high, cap, short)
    for limit, cap, short in ((255, 1, True), (65536, 8, False)):
        yield "optimization-control", key("O0", limit, cap, short), key("O2", limit, cap, short)
    case = key("O0", 255, 1, True)
    yield "self-control", case, case


def compare(work, index, pair, providers, probes):
    label, old, new = pair
    row = dict(label=label, old=old, new=new)
    if any(providers[case]["status"] != "built" for case in (old, new)):
        return dict(row, status="unknown", stage="provider_dependency")
    if all("observed" in probes[case] for case in (old, new)):
        row["layout_delta"] = delta(probes[old]["observed"], probes[new]["observed"])
    else:
        row["layout_delta"] = None
    report_path = work / f"compare-{index}.json"
    result = execute(row, "compare", sys.executable, "-m", "abicheck", "compare",
                     work / old / "libinteraction.so", work / new / "libinteraction.so",
                     "--depth", "binary", "-o", f"json={report_path}")
    if result is None:
        return row
    row["exit_code"] = result.returncode
    if result.returncode not in (0, 2, 4):
        return dict(row, status="failed", stage="compare", error=result.stderr)
    try:
        report = json.loads(report_path.read_text())
        for field in ("verdict", "evidence_tier", "analysis_assurance", "changes",
                      "old_evidence_depth", "new_evidence_depth"):
            row[field] = report[field]
        row["change_kinds"] = dict(Counter(change["kind"] for change in row["changes"]))
    except (OSError, ValueError, KeyError, TypeError) as error:
        return dict(row, status="unknown", stage="report_parse", error=str(error))
    row["layout_finding_check"] = reconcile(row["layout_delta"], row["changes"])
    return dict(row, status="completed")


def reconcile(layout_delta, changes):
    """Check the reported facts with directly comparable metrics and units."""
    if layout_delta is None:
        return {"status": "unknown"}
    kinds = {"enum_underlying_size_changed", "type_size_changed", "type_field_offset_changed"}
    expected = Counter()
    for metric, (old, new) in layout_delta.items():
        symbol, *parts = metric.split(".")
        if metric == "E.size":
            finding = ("enum_underlying_size_changed", symbol, None, old, new)
        elif parts == ["size"]:
            finding = ("type_size_changed", symbol, None, old * 8, new * 8)
        elif parts[-1] == "offset":
            finding = ("type_field_offset_changed", symbol, parts[0], old * 8, new * 8)
        else:
            continue
        expected[finding] += 1
    actual = Counter(
        (change["kind"], change["symbol"], change.get("field_name"),
         int(change["old_value"]), int(change["new_value"]))
        for change in changes if change["kind"] in kinds
    )
    return dict(status="matched" if actual == expected else "mismatch",
                expected=sum(expected.values()), actual=sum(actual.values()),
                missing=list((expected - actual).elements()),
                unexpected=list((actual - expected).elements()))


def interactions(probes):
    rows = []
    for opt, limit, (lo, hi) in itertools.product(OPTS, LIMITS, itertools.combinations(CAPS, 2)):
        corners = [key(opt, limit, cap, short) for cap, short in
                   ((lo, False), (lo, True), (hi, False), (hi, True))]
        row = dict(corners=corners)
        if any("observed" not in probes[case] for case in corners):
            rows.append(dict(row, status="unknown"))
            continue
        a, b, c, d = (probes[case]["observed"] for case in corners)
        mixed = {field: (d[field] - c[field]) - (b[field] - a[field]) for field in a}
        rows.append(dict(row, status="measured", mixed={k: v for k, v in mixed.items() if v}))
    return rows


def reduction(probes):
    signatures = {}
    for case, row in probes.items():
        if "observed" in row:
            signature = tuple(sorted(row["observed"].items()))
            signatures.setdefault(signature, []).append(case)
    optimization = []
    for limit, cap, short in itertools.product(LIMITS, CAPS, (False, True)):
        old, new = (key(opt, limit, cap, short) for opt in OPTS)
        before, after = (probes[case].get("observed") for case in (old, new))
        optimization.append(dict(old=old, new=new, differences=(
            None if before is None or after is None else delta(before, after)
        )))
    return dict(layout_classes=list(signatures.values()), optimization=optimization)


def main():
    target = checked(CXX, "-dumpmachine")
    if "x86_64" not in target or "linux" not in target:
        raise SystemExit(f"Arithmetic model is scoped to x86_64 Linux, got {target}")
    environment = dict(compiler=checked(CXX, "--version").splitlines()[0], compiler_command=CXX,
                       target=target, checker=checked(sys.executable, "-m", "abicheck", "--version"),
                       python=sys.version.split()[0], git_head=checked("git", "rev-parse", "HEAD"))
    matrix = list(itertools.product(OPTS, LIMITS, CAPS, (False, True)))
    pairs = list(audit_pairs())
    needed = sorted({case for _, old, new in pairs for case in (old, new)})
    with TemporaryDirectory(prefix="abicheck-interaction-") as temporary, ThreadPoolExecutor(4) as pool:
        work = Path(temporary)
        probes = {row["case"]: row for row in pool.map(lambda args: probe(work, *args), matrix)}
        providers = {row["case"]: row for row in pool.map(lambda case: provider(work, case), needed)}
        audits = list(pool.map(lambda item: compare(work, *item, providers, probes), enumerate(pairs)))
    mixed = interactions(probes)
    groups = dict(probes=list(probes.values()), providers=list(providers.values()), audits=audits,
                  interactions=mixed,
                  layout_finding_checks=[dict(audit=index, **row.get("layout_finding_check",
                                         {"status": "unknown"}))
                                         for index, row in enumerate(audits)])
    counts = {name: dict(Counter(row["status"] for row in rows)) for name, rows in groups.items()}
    for group in counts.values():
        for status in ("failed", "unknown"):
            group.setdefault(status, 0)
    receipt = dict(environment=environment, counts=counts, reduction=reduction(probes), **groups)
    (HERE / "results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(counts, indent=2))
    return int(any(status in {"failed", "unknown", "mismatch"}
                   for group in counts.values() for status, count in group.items() if count))


if __name__ == "__main__":
    sys.exit(main())

```

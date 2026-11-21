# Explicit alignment inside independently packed outer records

GCC14.2 Linux x86-64 C++20. Six explicit inner alignments1/2/4/8/16/32,
four outer packing caps1/2/4/8 and O0/O2:48 compiled probes,288 scalar measurements.
All matched the independent capped outer placement model. Inner I contains one
char, is defined outside the packing scope, and has explicit alignas(A). Outer O
contains a byte tag, I values[3], and a byte tail under pack(C).

Inner size and alignment stay A. Outer placement alignment is min(A,C), array
stride remains A, and the tail is start+3*A. For A16,C1 the outer layout is
size50,alignment1,array offset1,tail49; for C8 it is64,8,8,56.
Treating outer packing as a recursive rewrite of inner layout predicts the wrong
stride. Do not summarize nested types by the outer packing cap alone.

This campaign measures layout only. It makes no claims about safe dereferencing
of pointers to packed members or by-value calling conventions. No new public
checker audit is included in this48-cell experiment. Arrays have fixed length3;
other lengths, inheritance, bitfields and compiler families remain excluded.

## Reproduce

Save as `research/explicit-align-campaign/run.py`.

```python
from pathlib import Path
import json,subprocess as sp
P=Path(__file__).resolve().parent;rows=[]
def up(x,a):return (x+a-1)//a*a
for opt in ['O0','O2']:
 for a in [1,2,4,8,16,32]:
  for cap in [1,2,4,8]:
   d=P/f'{opt}-{a}-{cap}';d.mkdir(exist_ok=True)
   text=f'''#include <cstdio>
#include <cstddef>
struct alignas({a}) I {{ char value; }};
#pragma pack(push,{cap})
struct O {{ char tag; I values[3]; char tail; }};
#pragma pack(pop)
int main() {{ std::printf("%zu %zu %zu %zu %zu %zu\\n",sizeof(I),alignof(I),sizeof(O),alignof(O),offsetof(O,values),offsetof(O,tail)); }}
'''
   (d/'probe.cpp').write_text(text)
   sp.run(['g++','-std=c++20','-'+opt,str(d/'probe.cpp'),'-o',str(d/'probe')],check=True,capture_output=True,timeout=30)
   actual=list(map(int,sp.check_output([str(d/'probe')],text=True,timeout=5).split()))
   oa=min(a,cap);start=up(1,oa);tail=start+3*a
   expected=[a,a,up(tail+1,oa),oa,start,tail]
   rows.append(dict(opt=opt,inner_alignment=a,outer_cap=cap,actual=actual,expected=expected,match=actual==expected))
(P/'results.json').write_text(json.dumps(rows,indent=2));print('cells',len(rows),'mismatch',sum(not r['match'] for r in rows))

```

## Independent packing scopes

# Independent inner and outer pack caps

GCC matched the independent arithmetic oracle in all 256 configurations
and all 7,424 measured layout facts. Of the 192 off-diagonal configurations,
96 have no equivalent shared-cap layout at the same enum settings and
optimization level. The 25 selected public DWARF comparisons also agreed
with the probes on all 96 comparable finding rows.

## Reproduction and finite scope

Run `python research/independent-pack-campaign/run.py` from the checkout
with the installed abicheck dependencies and `g++-14`. The
`INDEPENDENT_PACK_CAMPAIGN_CXX` environment variable selects another
compiler executable. The recorded run used Debian GCC 14.2.0-19 targeting
`x86_64-linux-gnu`, Python 3.12.14, and abicheck
`0.8.0+gb0b879398`. `results.json` contains every probe
fact, interaction vector, shared-cap comparison, signature class and
checker finding. The runner recreates fixtures, binaries and CLI reports
in temporary directories.

The matrix crosses inner cap and outer cap independently (1, 2, 4, 8
each), explicit short enums on and off, upper enumerators 255, 256,
65535 and 65536, and O0/O2. The enum is unscoped and starts at zero.
The oracle assumes four-byte default enums and short widths of
1, 2, 2 and 4 bytes respectively, with natural alignment equal to width.
The fixture statically checks the eight-byte anchor's size and alignment.

The three ordinary C++20 records are `Inner` (eight-byte anchor, byte,
enum, byte), `Nested` (byte, `Inner`, byte), and `RecordArray` (byte,
three `Inner` elements, byte). Separate push/pop scopes set their caps.
The probes collect size and alignment, member offsets and extents, and
array-element stride. This campaign covers this compiler, target and
fixture, with no explicit over-alignment, bit-fields, inheritance,
or cross-build calling-convention tests.

The Python oracle lays out `Inner` first. It rounds each field offset
up to the minimum of the field's natural alignment and the inner cap,
then rounds the complete size to the record alignment. Each outer record
uses the same arithmetic with its own cap and takes the **complete**
inner size and alignment as inputs. The outer cap can lower the embedded
member's placement alignment. The inner size, internal offsets and
array stride come from the already completed inner type. Three elements
occupy three complete inner sizes, including tail padding in each element.

| Stage | Completed | Failed | Unknown | Mismatched |
| --- | ---: | ---: | ---: | ---: |
| Standalone compiler probes | 256 | 0 | 0 | 0 |
| Shared providers for audits | 30 | 0 | 0 | — |
| Four-corner interaction contrasts | 960 | 0 | 0 | — |
| Public CLI comparisons | 25 | 0 | 0 | — |
| Exact finding reconciliation | 25 | 0 | 0 | 0 |

## Interactions and reduction

An interaction contrast takes a pair of levels on each named axis while
holding the remaining axes fixed. For each metric, it subtracts the
effect of the first axis at the lower second-axis setting from its
effect at the higher setting. A contrast counts once if any metric is
nonzero. The counts below include both optimization levels.

| Axes | 255 | 256 | 65535 | 65536 | Total nonzero / measured |
| --- | ---: | ---: | ---: | ---: | ---: |
| Inner cap × outer cap | 104 | 104 | 104 | 104 | 416 / 576 |
| Inner cap × short-enum flag | 40 | 40 | 40 | 0 | 120 / 192 |
| Outer cap × short-enum flag | 0 | 0 | 0 | 0 | 0 / 192 |

The anchor fixes inner alignment at the inner cap in this fixture.
For fixed inner cap, changing the short-enum flag can change complete
inner size. That size remains divisible by each effective outer
alignment, so its contribution to either outer aggregate is additive
across outer caps. Changing the inner cap can also change effective
outer placement alignment, which accounts for the inner × outer
interaction.

All 128 O0/O2 pairs have identical layout vectors. The complete vector
has 30 measured signatures, indexed exactly by enum width, inner cap
and effective outer alignment `min(inner cap, outer cap)`. There are ten
distinct inner-cap/effective-outer-alignment pairs and three widths.
The projections have 3 enum signatures, 12 inner-record signatures,
17 nested-record signatures and 17 record-array signatures. Signature
equivalence here concerns measured storage layout. The CLI controls
below show that enum values and compiler flag provenance can distinguish
cases in the same storage-layout class.

## Where one shared cap loses information

For each off-diagonal cell, the runner compares the complete vector
against all four diagonal cells at the same range, enum mode and
optimization. All 96 cases where outer cap exceeds inner cap reproduce
the diagonal at the inner cap. All 96 cases where outer cap is lower
than inner cap differ from every diagonal. The complete vector exposes
both the inner alignment and the lower outer alignment in those cases.

These two strict off-diagonal examples and their diagonal endpoints
use short enums with upper enumerator 255. Values are bytes and are the
same at O0 and O2.

| Inner / outer cap | Inner size / align | Nested item offset | Nested size / align | Array stride | Array tail offset | Array size / align |
| --- | --- | ---: | --- | ---: | ---: | --- |
| 8 / 8 | 16 / 8 | 8 | 32 / 8 | 16 | 56 | 64 / 8 |
| 8 / 1 | 16 / 8 | 1 | 18 / 1 | 16 | 49 | 50 / 1 |
| 1 / 1 | 11 / 1 | 1 | 13 / 1 | 11 | 34 | 35 / 1 |
| 4 / 4 | 12 / 4 | 4 | 20 / 4 | 12 | 40 | 44 / 4 |
| 4 / 2 | 12 / 4 | 2 | 16 / 2 | 12 | 38 | 40 / 2 |
| 2 / 2 | 12 / 2 | 2 | 16 / 2 | 12 | 38 | 40 / 2 |

At 8/1 the record array starts at byte 1 and occupies 48 bytes.
At 1/1 it also starts at byte 1, while its three elements occupy 33
bytes. At 4/2 the two outer records have the same measured layouts as
at 2/2, including stride. The inner record's alignment still
distinguishes those complete signatures.

## Public checker sample

Each selected provider was built with the same generated header,
optimization and enum flag as its corresponding probe, using DWARF 5
and a stable SONAME. It exports an enum echo and pointer-based field
readers for the three records. The runner uses the public
`python -m abicheck compare --depth binary` workflow with JSON output.
All 25 reports had `dwarf_aware` evidence and complete analysis
assurance at the requested depth.

The exact reconciliation compares enum underlying size in bytes and
record size and field offsets in bits. It checks finding kind, symbol,
field where applicable, and both old and new values. All 96 expected
rows appeared with no unexpected rows in those three finding kinds.
Alignment, member extent and stride remain probe measures.

| Selected case, run at O0 and O2 | CLI verdict per optimization |
| --- | --- |
| Outer-only 8/8 → 8/1, inner-only 8/1 → 1/1 | BREAKING for each |
| Outer-only 1/1 → 1/8 | NO_CHANGE |
| Enable short enums with width 4 → 1 or 4 → 2 | BREAKING for each |
| Enable short enums with width 4 → 4 | COMPATIBLE, flag drift |
| Short-enum thresholds 255 → 256 and 65535 → 65536 | BREAKING for each |
| Same-width 256 → 65535 with short enums, default 255 → 256 | BREAKING for each, enumerator value change |
| Joint cap and short-enum change | BREAKING |

The two cross-optimization comparisons and the self-comparison returned
NO_CHANGE. Overall there were 18 BREAKING (exit 4), two COMPATIBLE
(exit 0) and five NO_CHANGE (exit 0).

### Independent packing reproducer

Save as `research/independent-pack-campaign/run.py`.

```python
#!/usr/bin/env python3
"""Replay GCC independent packing and short-enum layout probes and a public CLI sample."""

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
CXX = os.environ.get("INDEPENDENT_PACK_CAMPAIGN_CXX", "g++-14")
LIMITS = (255, 256, 65535, 65536)
CAPS = (1, 2, 4, 8)
OPTS = ("O0", "O2")
SCHEMA = {
    "Inner": (("anchor", "word", 1), ("lead", "byte", 1),
              ("value", "E", 1), ("tail", "byte", 1)),
    "Nested": (("lead", "byte", 1), ("item", "Inner", 1), ("tail", "byte", 1)),
    "RecordArray": (("lead", "byte", 1), ("items", "Inner", 3), ("tail", "byte", 1)),
}


def command(*args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)


def checked(*args):
    result = command(*args)
    result.check_returncode()
    return result.stdout.strip()


def key(opt, limit, inner, outer, short):
    return f"{opt}/u{limit}/i{inner}-o{outer}/{'short' if short else 'default'}"


def header(limit, inner, outer):
    lines = [
        "using byte = unsigned char;",
        "using word = unsigned long long;",
        "static_assert(sizeof(word) == 8 && alignof(word) == 8);",
        f"enum E {{ Lo = 0, Hi = {limit} }};",
    ]
    for name, fields in SCHEMA.items():
        if name == "Inner":
            lines.append(f"#pragma pack(push, {inner})")
        elif name == "Nested":
            lines.extend(("#pragma pack(pop)", f"#pragma pack(push, {outer})"))
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


def predict(limit, inner, outer, short):
    """Lay out each complete type once, then cap its alignment at each use."""
    width = next(size for size in (1, 2, 4) if limit < 256**size) if short else 4
    types = {"E": (width, width), "word": (8, 8), "byte": (1, 1)}
    facts = {"E.size": width, "E.align": width}
    for name, fields in SCHEMA.items():
        cap = inner if name == "Inner" else outer
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


def probe(work, opt, limit, inner, outer, short):
    case = key(opt, limit, inner, outer, short)
    folder = work / case
    folder.mkdir(parents=True)
    (folder / "fixture.h").write_text(header(limit, inner, outer))
    (folder / "probe.cpp").write_text(probe_source())
    row = {"case": case, "expected": predict(limit, inner, outer, short)}
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
                     "-shared", "-fPIC", "-Wl,-soname,libindependentpack.so.1",
                     folder / "provider.cpp", "-o", folder / "libindependentpack.so")
    if result is not None:
        row["status"] = "built"
    return row


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
                     work / old / "libindependentpack.so", work / new / "libindependentpack.so",
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


def audit_pairs():
    # Tuples specify upper enumerator, inner cap, outer cap and short mode.
    samples = (
        ("outer-only", (255, 8, 8, True), (255, 8, 1, True)),
        ("inner-only", (255, 8, 1, True), (255, 1, 1, True)),
        ("outer-masked", (255, 1, 1, True), (255, 1, 8, True)),
        ("flag-width-1", (255, 8, 1, False), (255, 8, 1, True)),
        ("flag-width-2", (256, 8, 2, False), (256, 8, 2, True)),
        ("flag-width-4-control", (65536, 8, 2, False), (65536, 8, 2, True)),
        ("short-255-to-256", (255, 8, 2, True), (256, 8, 2, True)),
        ("short-65535-to-65536", (65535, 8, 2, True), (65536, 8, 2, True)),
        ("same-width-enumerator", (256, 8, 2, True), (65535, 8, 2, True)),
        ("default-boundary", (255, 8, 2, False), (256, 8, 2, False)),
        ("joint", (255, 8, 8, False), (255, 4, 2, True)),
    )
    for opt in OPTS:
        for label, old, new in samples:
            yield label, key(opt, *old), key(opt, *new)
    for settings in ((255, 8, 1, True), (65536, 1, 8, False)):
        yield "optimization-control", key("O0", *settings), key("O2", *settings)
    case = key("O0", 255, 8, 1, True)
    yield "self-control", case, case


def interactions(probes, matrix):
    axes = (OPTS, LIMITS, CAPS, CAPS, (False, True))
    pairs = ((2, 3, "inner × outer"), (2, 4, "inner × short"), (3, 4, "outer × short"))
    for first, second, measure in pairs:
        for base in matrix:
            if base[first] != axes[first][0] or base[second] != axes[second][0]:
                continue
            for (lo1, hi1), (lo2, hi2) in itertools.product(
                itertools.combinations(axes[first], 2), itertools.combinations(axes[second], 2)
            ):
                corners = []
                for x, y in ((lo1, lo2), (hi1, lo2), (lo1, hi2), (hi1, hi2)):
                    settings = list(base)
                    settings[first], settings[second] = x, y
                    corners.append(key(*settings))
                vectors = [probes[case].get("observed") for case in corners]
                row = dict(measure=measure, corners=corners)
                if any(vector is None for vector in vectors):
                    yield dict(row, status="unknown")
                    continue
                a, b, c, d = vectors
                mixed = {field: d[field] - c[field] - b[field] + a[field] for field in a}
                yield dict(row, status="measured", mixed={k: v for k, v in mixed.items() if v})


def reduction(probes, matrix):
    classes = {}
    for name in ("complete", "E", *SCHEMA):
        signatures = {}
        for case, row in probes.items():
            if "observed" in row:
                signature = tuple((field, value) for field, value in row["observed"].items()
                                  if name == "complete" or field.startswith(name + "."))
                signatures.setdefault(signature, []).append(case)
        classes[name] = [dict(facts=dict(facts), cases=cases)
                         for facts, cases in signatures.items()]

    optimization = []
    shared_cap = []
    for opt, limit, inner, outer, short in matrix:
        old = key(opt, limit, inner, outer, short)
        observed = probes[old].get("observed")
        if opt == "O0":
            new = key("O2", limit, inner, outer, short)
            other = probes[new].get("observed")
            optimization.append(dict(old=old, new=new, differences=(
                None if observed is None or other is None else delta(observed, other)
            )))
        if inner != outer:
            candidates = {}
            for cap in CAPS:
                diagonal = key(opt, limit, cap, cap, short)
                other = probes[diagonal].get("observed")
                candidates[diagonal] = (
                    None if observed is None or other is None else delta(other, observed)
                )
            shared_cap.append(dict(case=old, diagonal_differences=candidates))
    return dict(signatures=classes, optimization=optimization, shared_cap=shared_cap)


def main():
    target = checked(CXX, "-dumpmachine")
    if target != "x86_64-linux-gnu":
        raise SystemExit(f"Arithmetic model is scoped to x86_64-linux-gnu, got {target}")
    environment = dict(compiler=checked(CXX, "--version").splitlines()[0], compiler_command=CXX,
                       target=target, checker=checked(sys.executable, "-m", "abicheck", "--version"),
                       python=sys.version.split()[0], git_head=checked("git", "rev-parse", "HEAD"))
    matrix = list(itertools.product(OPTS, LIMITS, CAPS, CAPS, (False, True)))
    pairs = list(audit_pairs())
    needed = sorted({case for _, old, new in pairs for case in (old, new)})
    with TemporaryDirectory(prefix="abicheck-independent-pack-") as temporary, ThreadPoolExecutor(4) as pool:
        work = Path(temporary)
        probes = {row["case"]: row for row in pool.map(lambda args: probe(work, *args), matrix)}
        providers = {row["case"]: row for row in pool.map(lambda case: provider(work, case), needed)}
        audits = list(pool.map(lambda item: compare(work, *item, providers, probes), enumerate(pairs)))

    groups = dict(probes=list(probes.values()), providers=list(providers.values()), audits=audits,
                  interactions=list(interactions(probes, matrix)),
                  layout_finding_checks=[dict(audit=index, **row.get("layout_finding_check",
                                         {"status": "unknown"}))
                                         for index, row in enumerate(audits)])
    counts = {name: dict(Counter(row["status"] for row in rows)) for name, rows in groups.items()}
    receipt = dict(environment=environment, counts=counts, reduction=reduction(probes, matrix), **groups)
    (HERE / "results.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(counts, indent=2))
    return int(any(status in {"failed", "unknown", "mismatch"}
                   for group in counts.values() for status, count in group.items() if count))


if __name__ == "__main__":
    sys.exit(main())

```

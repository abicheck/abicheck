# Packing campaign: bounded coverage and configuration reduction

GCC 14.2, Linux x86-64, C++20, pinned checker b0b8793989c150a5b05f48e4e999a7df4219365a.

Exhaustively enumerated 340 nonempty ordered field sequences of length 1–4 over
unsigned scalars of size/alignment 1,2,4,8. Eight compile/run configurations:
packing caps 1,2,4,8 x O0/O2. All 2720 layout observations agree with an independent
round-up model. These are layout observations, not 2720 library replacements.

For this fragment a type-dependent quotient groups caps by the tuple
(size, alignment, all field offsets). Dense directed cap pairs total 5440;
class pairs total4438 (18.42% reduction). Types with1/2/3/4 classes:4/26/90/220.
No arbitrary-depth theorem or compiler correctness proof follows from this grid.
A quotient is property-specific: this tuple does not classify all call protocols.

Four O0 shared libraries contain340 public pointer-taking functions each.
Sixteen public DWARF comparisons include four identity controls. Size/offset
findings were independently audited for5440 size and20032 field obligations:
zero missing, extra or numerically wrong findings in these categories.

There are884 directed type/configuration pairs whose sizes and field offsets agree
but alignments differ. All1360 type records in the four DWARF dumps have unknown
alignment_bits. Thus this evidence lane cannot certify full layout equality.
Flag-drift or inferred packing findings do not replace a measured alignment fact.
This is an acquisition/assurance limitation, not an established detector defect.

## Execution loop and exit conditions

Enumerate explicit fragment -> compute independent oracle -> compile/run -> compare
checker facts -> classify unknowns separately -> minimize discrepancies -> update
manifest and tasks. A campaign completes only when every planned cell has a terminal
status (pass, mismatch, unsupported, timeout); unsupported cells remain open coverage.

## Implementation tasks

1. Generator: attach fragment ID, applicability predicates, expected observables and
positive/negative controls to operators. Acceptance: byte-only packing drift yields
an unchanged-layout control, while padded types yield changed-layout witnesses.
2. Evidence: persist compiler, flags, extraction lane and per-observable availability.
Acceptance: unknown alignment never becomes verified equality; request matching
headers with the same packing configuration or retain an explicit unknown.
3. Reduction: selectors must state preserved observables and map each original cell
to its representative. Acceptance: dense and selected predictions agree on this
bounded universe, including884 alignment-only pairs. Do not substitute pairwise
interaction coverage for a completeness proof.
4. Checker: retain this campaign as a conformance audit. No production fix justified
by size/offset observations: those all pass. Investigate assurance for unavailable
alignment before asserting a new defect; existing functionality may already cover it.
5. Tracking: count distinct rules, fragments, configurations, runtime witnesses and
checker comparisons separately. Link every claim to artifacts and a source revision.

Remaining exclusions: bases, bitfields, unions, explicit alignment, nested records,
arrays, vectors, nontrivial objects, return/argument classification, other compilers.
A future fragment must cover these or list an explicit exclusion; this campaign is
not a percentage of all C++ ABI behavior.

## run.py

```python
from pathlib import Path
import itertools,json,subprocess,time
P=Path(__file__).resolve().parent
sizes=[1,2,4,8];types={1:'unsigned char',2:'unsigned short',4:'unsigned int',8:'unsigned long long'}
seqs=[s for n in range(1,5) for s in itertools.product(sizes,repeat=n)]
def layout(s,cap):
 off=0;offsets=[];align=max(min(x,cap) for x in s)
 for size in s:
  a=min(size,cap);off=(off+a-1)//a*a;offsets.append(off);off+=size
 return [(off+align-1)//align*align,align]+offsets
src=['#include <cstdio>','#include <cstddef>']
for j,s in enumerate(seqs):src.append('struct T%d {'%j+''.join(types[v]+' f%d;'%i for i,v in enumerate(s))+'};')
src.append('int main(){')
for j,s in enumerate(seqs):
 expr=[f'sizeof(T{j})',f'alignof(T{j})']+[f'offsetof(T{j},f{i})' for i in range(len(s))]
 src.append('std::printf("'+' '.join(['%zu']*len(expr))+'\\n",'+','.join(expr)+');')
src.append('}')
(P/'probe.cpp').write_text('\n'.join(src));rows=[];started=time.monotonic()
for opt,cap in itertools.product(['O0','O2'],sizes):
 exe=P/f'{opt}-pack{cap}'
 cmd=['g++','-std=c++20','-'+opt,f'-fpack-struct={cap}',str(P/'probe.cpp'),'-o',str(exe)]
 subprocess.run(cmd,check=True,capture_output=True,timeout=60)
 out=subprocess.check_output([str(exe)],text=True,timeout=30).splitlines()
 assert len(out)==len(seqs)
 for seq,line in zip(seqs,out):
  actual=list(map(int,line.split()));expected=layout(seq,cap)
  assert actual==expected,(seq,cap,actual,expected)
  rows.append(dict(opt=opt,cap=cap,fields=seq,layout=actual))
classes=[len({tuple(layout(s,c)) for c in sizes}) for s in seqs]
summary=dict(types=len(seqs),compiler_configurations=8,layout_observations=len(rows),model_errors=0,dense_directed_pack_pairs=len(seqs)*16,reduced_directed_layout_class_pairs=sum(n*n for n in classes),class_histogram={str(n):classes.count(n) for n in sorted(set(classes))},seconds=time.monotonic()-started)
(P/'results.json').write_text(json.dumps(rows,indent=2));(P/'summary.json').write_text(json.dumps(summary,indent=2));print(summary)

```

## checker.py

```python
from pathlib import Path
import subprocess,json,sys,itertools
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
src=(P/'probe.cpp').read_text().split('int main(){')[0]
src+='\n'.join(f'extern "C" unsigned long long probe{i}(const T{i}* p) {{ return p->f0; }}' for i in range(340))
(P/'provider.cpp').write_text(src)
def run(a):
 r=subprocess.run(list(map(str,a)),cwd=ROOT,capture_output=True,text=True,timeout=120);assert r.returncode==0,r.stderr
for cap in [1,2,4,8]:
 lib=P/f'pack{cap}.so'
 run(['g++','-std=c++20','-O0','-g',f'-fpack-struct={cap}','-shared','-fPIC',P/'provider.cpp','-o',lib])
 run([sys.executable,'-m','abicheck','dump',lib,'-o',P/f'pack{cap}.json'])
rows=[]
for a,b in itertools.product([1,2,4,8],repeat=2):
 out=P/f'compare-{a}-{b}.json'
 r=subprocess.run([sys.executable,'-m','abicheck','compare',str(P/f'pack{a}.json'),str(P/f'pack{b}.json'),'-o','json='+str(out)],cwd=ROOT,capture_output=True,text=True,timeout=120)
 assert r.returncode in [0,1,2,4],r.stderr
 d=json.loads(out.read_text());rows.append(dict(old=a,new=b,verdict=d['verdict'],findings=len(d['changes']),kinds=sorted({c['kind'] for c in d['changes']})))
 if a==b:assert not d['changes']
(P/'checker-summary.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))

```

## audit.py

```python
from pathlib import Path
import json,itertools
P=Path(__file__).resolve().parent
obs=json.loads((P/'results.json').read_text());layout={(r['cap'],tuple(r['fields'])):r['layout'] for r in obs if r['opt']=='O0'}
seqs=[r['fields'] for r in obs if r['opt']=='O0' and r['cap']==1]
errors=[];counts={'size_obligations':0,'offset_obligations':0,'alignment_only_pairs':0}
for a,b in itertools.product([1,2,4,8],repeat=2):
 d=json.loads((P/f'compare-{a}-{b}.json').read_text())
 sizes={c['symbol']:(c['old_value'],c['new_value']) for c in d['changes'] if c['kind']=='type_size_changed'}
 offsets={(c['symbol'],c.get('field_name')):(c['old_value'],c['new_value']) for c in d['changes'] if c['kind']=='type_field_offset_changed'}
 for i,s in enumerate(seqs):
  x,y=layout[a,tuple(s)],layout[b,tuple(s)];sym=f'T{i}'
  expected=(str(x[0]*8),str(y[0]*8)) if x[0]!=y[0] else None
  counts['size_obligations']+=1
  if sizes.get(sym)!=expected:errors.append([a,b,sym,'size',expected,sizes.get(sym)])
  for j,(u,v) in enumerate(zip(x[2:],y[2:])):
   expected=(str(u*8),str(v*8)) if u!=v else None;counts['offset_obligations']+=1
   if offsets.get((sym,f'f{j}'))!=expected:errors.append([a,b,sym,f'f{j}',expected,offsets.get((sym,f'f{j}'))])
  if x[0]==y[0] and x[2:]==y[2:] and x[1]!=y[1]:counts['alignment_only_pairs']+=1
result={'counts':counts,'errors':errors,'scope':'size and offsets only; alignment extraction not certified'}
(P/'audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))

```

## summary.json

```json
{
  "types": 340,
  "compiler_configurations": 8,
  "layout_observations": 2720,
  "model_errors": 0,
  "dense_directed_pack_pairs": 5440,
  "reduced_directed_layout_class_pairs": 4438,
  "class_histogram": {
    "1": 4,
    "2": 26,
    "3": 90,
    "4": 220
  },
  "seconds": 1.1765091230045073
}
```

## audit.json

```json
{
  "counts": {
    "size_obligations": 5440,
    "offset_obligations": 20032,
    "alignment_only_pairs": 884
  },
  "errors": [],
  "scope": "size and offsets only; alignment extraction not certified"
}
```

## alignment-audit.json

```json
{
  "1": {
    "records": 340,
    "alignment_unknown": 340
  },
  "2": {
    "records": 340,
    "alignment_unknown": 340
  },
  "4": {
    "records": 340,
    "alignment_unknown": 340
  },
  "8": {
    "records": 340,
    "alignment_unknown": 340
  }
}
```

## Enum campaign

# GCC 14 short-enum campaign

## Method

Run `python research/enum-campaign/run.py` from this checkout with its Python
dependencies and GCC 14 installed. Set `ENUM_CAMPAIGN_CXX` to override
`g++-14`. The run targets x86_64 Linux and uses C++20 and DWARF 5. It builds
each source case at `-O0` and `-O2`, each with explicit
`-fno-short-enums` and `-fshort-enums`.

Each build produces a shared provider with C-linkage exports for an enum
argument and return and for access through pointers to three embedding
types. `Compact` places the enum between byte members. `Padded` starts
with an eight-byte anchor and retains aggregate size and alignment as its
trailing byte moves. `Array` places two enum elements between byte members.
The generated header declares all of these surfaces. A separately compiled
executable includes that header and prints compiler-evaluated `sizeof`,
`alignof`, `offsetof`, member sizes, enum signedness and enumerator
values. The executable has no provider linkage and performs no cross-build
object access or calls. Reverse flag comparisons exchange checker operands only.

The runner asserts the complete probe output against pinned target-specific
baselines for every build. It independently reads the provider ELF's named
DWARF DIEs with pyelftools and checks enum extent, base-type width and encoding,
enumerator values, record byte sizes and member offsets against the probe.
The public checker receives the two live shared objects via
`python -m abicheck compare OLD NEW --depth binary -o json=REPORT`.
There are no headers or external build manifests passed to the checker.
GCC's DWARF producer string can still supply flag evidence.

`probes.json` records the build matrix, expected and measured
facts, raw DIE attributes, source and ELF hashes, and the toolchain identity.
`checker.json` records the command form, paired probe deltas,
exit codes, verdicts, evidence tier, assurance and complete change rows from the
public reports. The generated sources, executables, providers and full CLI
reports are temporary. The runner reconstructs the sources on replay.

## Layout observations

Both optimization levels produced the same measurements for every case and mode.
All 19 unfixed enums occupied four bytes by default. Both fixed
`unsigned short` controls occupied two bytes in both modes.

| Unfixed range with short enums | Bytes |
| --- | ---: |
| 0 to 127, 254 or 255 | 1 |
| 0 to 256, 257, 65534 or 65535 | 2 |
| 0 to 65536 or 65537 | 4 |
| −127 or −128 to 1 | 1 |
| −129, −32767 or −32768 to 1 | 2 |
| −32769 to 1 | 4 |
| −1 to 127 | 1 |
| −1 to 128 or 32767 | 2 |
| −1 to 32768 | 4 |

Enums with a negative enumerator used signed base types. The other cases
used unsigned base types. `u127` and `s127` therefore differed in signedness
at equal width under both flags. The transition also changes `Lo` from 0 to −1.

| Enum size and alignment | Compact size / align / value offset / tail offset | Padded size / align / value offset / tail offset | Array size / align / values offset / tail offset |
| ---: | --- | --- | --- |
| 1 | 3 / 1 / 1 / 2 | 16 / 8 / 8 / 9 | 4 / 1 / 1 / 3 |
| 2 | 6 / 2 / 2 / 4 | 16 / 8 / 8 / 10 | 8 / 2 / 2 / 6 |
| 4 | 12 / 4 / 4 / 8 | 16 / 8 / 8 / 12 | 16 / 4 / 4 / 12 |

The layouts establish concrete storage and member-offset differences for
these compiled fixtures. In particular, a provider load at its compiled
`Padded::tail` offset would address a different byte in a record created with
the other representation. The campaign measures type facts and checker behavior.
Its evidence does not establish the register convention for enum arguments
or returns, a runtime failure rate, or behavior on other targets and compilers.

## Public checker results

The completed run used GCC 14.2.0 (Debian 14.2.0-19), Python 3.12.14
and abicheck `0.8.0+gb0b879398` from
`b0b8793989c150a5b05f48e4e999a7df4219365a`. There were 21 source cases,
84 shared-provider builds, 84 standalone probe builds and executions,
84 validated provider DWARF readings, and 158 completed comparisons.
All 158 reports had the `dwarf_aware` tier, `complete` analysis assurance,
and a `clean` DWARF context.

| Comparison | Pairs | BREAKING (4) | COMPATIBLE (0) | NO_CHANGE (0) |
| --- | ---: | ---: | ---: | ---: |
| Default → short, identical source | 42 | 30 | 12 | 0 |
| Short → default, identical source | 42 | 30 | 12 | 0 |
| Change an enumerator across a width boundary, default flag | 12 | 12 | 0 | 0 |
| Change an enumerator across a width boundary, short flag | 12 | 12 | 0 | 0 |
| Same-width signedness and enumerator change | 4 | 4 | 0 | 0 |
| O0 → O2, same source and flag | 42 | 0 | 0 | 42 |
| Same artifact | 4 | 0 | 0 | 4 |
| **Total** | **158** | **88** | **24** | **46** |

- All **72** measured width changes had one `enum_underlying_size_changed`
  on `E`, attributed to all four public exports. All 72 had record-size
  findings for `Compact` and `Array` and the exact five measured member
  offsets reported as changes. These included **72** `Padded::tail`
  findings despite invariant `Padded` size, alignment and enum-member offset.
  Checker record extents and offsets are in bits. For example, O0
  default → short at `0..255` reported `E` 4 → 1 byte, `Compact`
  96 → 24 bits, and `Padded::tail` 96 → 72 bits.
- All **84** flag comparisons reported `toolchain_flag_drift`. The **24**
  that preserved the measured representation reported only that finding.
  These were the two fixed controls and four unfixed four-byte cases,
  at both optimization levels and in both directions.
- The **12** default-mode boundary pairs had unchanged measured layouts.
  The checker reported the changed value of an existing enumerator and
  a SONAME recommendation in each. The **four** equal-width signedness
  pairs had those same two finding kinds, with the value finding on
  `E::Lo`. Raw DWARF exposed the base encoding in all four pairs, but none
  produced an independent signedness finding. A breaking verdict alone
  cannot measure detection of that representation change.
- The **42** optimization comparisons and **four** self comparisons
  contained zero findings. Across the entire campaign, each of the **88**
  breaking comparisons also had a SONAME recommendation, with the fixture
  SONAME kept constant.

## Generator and checker follow-up

1. **Parameterize abiforge's enum operators by range and observed width.**
   `abiforge/operators/__init__.py` currently gives
   `enum_append_wide_value` a single added value of 300 and describes
   `rebuild_short_enums` as shrinking every unscoped enum. Add configurations
   on both sides of 255/256 and 65535/65536, signed minimum thresholds
   −128/−129 and −32768/−32769, and signed maximum thresholds 127/128
   and 32767/32768. For append cases keep existing names and values and
   append a fresh enumerator, so the experiment isolates addition from
   the value edits measured here. Keep fixed `unsigned short` and already
   four-byte unfixed ranges as rebuild controls. Use the independently
   compiled representation to decide when to require
   `ENUM_UNDERLYING_SIZE_CHANGED`. Gate producer-flag and build-manifest
   findings separately in the corresponding evidence tiers.
2. **Enforce entity-level coverage in the generator.** Add separate exported
   scalar, compact, padded and array witnesses with independent type facts.
   Require the enum-width finding and compare exact changed record extents
   and member offsets, including an offset-only change in the padded record.
   Include a padded-only export variant to test reachability independently
   of the other breaking findings. Keep matched optimization baselines and
   use layout probes for reverse comparisons. Reserve call-ABI claims for
   dedicated calling-convention evidence.
3. **Carry signedness through the binary DWARF enum path.**
   `abicheck/dwarf_metadata.py` and `abicheck/model/dwarf_facts.py` capture
   enum width and values. The layout projections in
   `abicheck/extract/debug_layout_semantic_ir.py` and
   `abicheck/compare/debug_layout_view.py` select representative underlying
   spellings by width. Preserve known signedness from the referenced DWARF
   base encoding through extraction, storage and projection, with explicit
   handling for unknown encodings. Add a public CLI regression using
   fixed signed and unsigned underlying types of equal width and identical
   enumerator names and values. That control isolates signedness from the
   value change in this campaign. Test scalar and embedded reachability and
   live/stored parity before assigning a compatibility verdict to the new
   fact.

### Enum reproducer

```python
#!/usr/bin/env python3
"""Reproduce the GCC 14 enum matrix on x86_64 Linux with the checkout's CLI."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from elftools.elf.elffile import ELFFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CXX = os.environ.get("ENUM_CAMPAIGN_CXX", "g++-14")
OPTS = ("O0", "O2")
MODES = ("default", "short")
FIELDS = {
    "Compact": ("lead", "value", "tail"),
    "Padded": ("anchor", "value", "tail"),
    "Array": ("lead", "values", "tail"),
}
PROVIDER = """#include "api.hpp"
extern "C" E enum_echo(E value) { return value; }
extern "C" E compact_value(const Compact* p) { return p->value; }
extern "C" E padded_value(const Padded* p) { return p->value; }
extern "C" E array_value(const Array* p) { return p->values[1]; }
"""
HEADER = """
struct Compact { unsigned char lead; E value; unsigned char tail; };
struct Padded { unsigned long long anchor; E value; unsigned char tail; };
struct Array { unsigned char lead; E values[2]; unsigned char tail; };
extern "C" E enum_echo(E);
extern "C" E compact_value(const Compact*);
extern "C" E padded_value(const Padded*);
extern "C" E array_value(const Array*);
"""


@dataclass(frozen=True)
class Case:
    name: str
    low: int
    high: int
    short_width: int
    fixed: bool = False

    @property
    def header(self) -> str:
        underlying = " : unsigned short" if self.fixed else ""
        return f"enum E{underlying} {{ Lo = {self.low}, Hi = {self.high} }};\n" + HEADER


CASES = (
    *(Case(f"u{high}", 0, high, width) for high, width in
      ((254, 1), (255, 1), (256, 2), (257, 2), (65534, 2), (65535, 2),
       (65536, 4), (65537, 4))),
    *(Case(f"n{-low}", low, 1, width) for low, width in
      ((-127, 1), (-128, 1), (-129, 2), (-32767, 2), (-32768, 2), (-32769, 4))),
    Case("s127", -1, 127, 1),
    Case("s128", -1, 128, 2),
    Case("s32767", -1, 32767, 2),
    Case("s32768", -1, 32768, 4),
    Case("u127", 0, 127, 1),
    Case("fixed255", 0, 255, 2, True),
    Case("fixed65535", 0, 65535, 2, True),
)
TRANSITIONS = (
    ("boundary", "u255", "u256"),
    ("boundary", "u65535", "u65536"),
    ("boundary", "n128", "n129"),
    ("boundary", "n32768", "n32769"),
    ("boundary", "s127", "s128"),
    ("boundary", "s32767", "s32768"),
    ("same_width_signedness", "u127", "s127"),
)


def command(*args: str | Path, codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=90,
        env=os.environ | {"LC_ALL": "C"},
    )
    if result.returncode not in codes:
        raise RuntimeError(f"{args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def probe_source() -> str:
    rows = ['std::printf("E %zu %zu %d\\n", sizeof(E), alignof(E), '
            'int(std::is_signed_v<std::underlying_type_t<E>>));']
    rows.append('std::printf("values %lld %lld\\n", static_cast<long long>(Lo), '
                'static_cast<long long>(Hi));')
    for record, fields in FIELDS.items():
        expressions = [f"sizeof({record})", f"alignof({record})"]
        expressions.extend(f"offsetof({record}, {field})" for field in fields)
        expressions.extend(f"sizeof({record}::{field})" for field in fields)
        formats = " ".join(["%zu"] * len(expressions))
        rows.append(f'std::printf("{record} {formats}\\n", {", ".join(expressions)});')
    return (
        '#include "api.hpp"\n#include <cstddef>\n#include <cstdio>\n#include <type_traits>\n'
        'int main() {\n    ' + "\n    ".join(rows) + "\n}\n"
    )


def parse_probe(stdout: str) -> dict:
    observed = {}
    for line in stdout.splitlines():
        name, *tokens = line.split()
        values = list(map(int, tokens))
        if name == "E":
            size, align, signed = values
            observed[name] = {"size": size, "align": align, "signed": bool(signed)}
        elif name == "values":
            observed[name] = dict(zip(("Lo", "Hi"), values, strict=True))
        else:
            size, align, *fields = values
            observed[name] = {
                "size": size, "align": align,
                "offsets": dict(zip(FIELDS[name], fields[:3], strict=True)),
                "field_sizes": dict(zip(FIELDS[name], fields[3:], strict=True)),
            }
    return observed


def expected_probe(case: Case, mode: str) -> dict:
    # Pinned x86_64 GCC 14 baselines. Tuples hold size, alignment and three offsets.
    layouts = {
        1: ((3, 1, 0, 1, 2), (16, 8, 0, 8, 9), (4, 1, 0, 1, 3)),
        2: ((6, 2, 0, 2, 4), (16, 8, 0, 8, 10), (8, 2, 0, 2, 6)),
        4: ((12, 4, 0, 4, 8), (16, 8, 0, 8, 12), (16, 4, 0, 4, 12)),
    }
    width = case.short_width if mode == "short" or case.fixed else 4
    expected = {
        "E": {"size": width, "align": width, "signed": case.low < 0},
        "values": {"Lo": case.low, "Hi": case.high},
    }
    member_sizes = ((1, width, 1), (8, width, 1), (1, 2 * width, 1))
    for (name, fields), layout, sizes in zip(
        FIELDS.items(), layouts[width], member_sizes, strict=True,
    ):
        size, align, *offsets = layout
        expected[name] = {
            "size": size, "align": align,
            "offsets": dict(zip(fields, offsets, strict=True)),
            "field_sizes": dict(zip(fields, sizes, strict=True)),
        }
    return expected


def dwarf_facts(path: Path) -> dict:
    def name(die) -> str:
        return die.attributes["DW_AT_name"].value.decode()

    def attr(die, key: str) -> int | None:
        attribute = die.attributes.get(f"DW_AT_{key}")
        return attribute.value if attribute else None

    facts = {}
    with path.open("rb") as stream:
        dwarf = ELFFile(stream).get_dwarf_info()
        for unit in dwarf.iter_CUs():
            for die in unit.iter_DIEs():
                if "DW_AT_name" not in die.attributes:
                    continue
                if die.tag == "DW_TAG_enumeration_type" and name(die) == "E":
                    base = die.get_DIE_from_attribute("DW_AT_type")
                    facts["E"] = {
                        "size": attr(die, "byte_size"),
                        "encoding": attr(die, "encoding"),
                        "base_name": name(base),
                        "base_size": attr(base, "byte_size"),
                        "base_encoding": attr(base, "encoding"),
                        "values": {
                            name(child): attr(child, "const_value")
                            for child in die.iter_children()
                            if child.tag == "DW_TAG_enumerator"
                        },
                    }
                if die.tag == "DW_TAG_structure_type" and name(die) in FIELDS:
                    facts[name(die)] = {
                        "size": attr(die, "byte_size"),
                        "offsets": {
                            name(child): attr(child, "data_member_location")
                            for child in die.iter_children()
                            if child.tag == "DW_TAG_member"
                        },
                    }
    return facts


def build(work: Path, case: Case, opt: str, mode: str) -> dict:
    key = f"{opt}/{mode}/{case.name}"
    directory = work / key
    directory.mkdir(parents=True)
    sources = {"api.hpp": case.header, "provider.cpp": PROVIDER, "probe.cpp": probe_source()}
    for name, content in sources.items():
        (directory / name).write_text(content)
    flags = (CXX, "-std=c++20", f"-{opt}", "-g", "-gdwarf-5", f"-f{'' if mode == 'short' else 'no-'}short-enums")
    library = directory / "libenum.so"
    probe = directory / "probe"
    command(*flags, "-shared", "-fPIC", "-Wl,-soname,libenum.so",
            directory / "provider.cpp", "-o", library)
    command(*flags, directory / "probe.cpp", "-o", probe)
    result = command(probe)
    observed = parse_probe(result.stdout)
    expected = expected_probe(case, mode)
    assert observed == expected, (key, expected, observed)
    dwarf = dwarf_facts(library)
    assert dwarf["E"]["size"] == dwarf["E"]["base_size"] == observed["E"]["size"], key
    assert dwarf["E"]["base_encoding"] in ((5, 6) if observed["E"]["signed"] else (7, 8)), key
    assert dwarf["E"]["values"] == observed["values"], key
    for name in FIELDS:
        assert dwarf[name]["size"] == observed[name]["size"], (key, name)
        assert dwarf[name]["offsets"] == observed[name]["offsets"], (key, name)
    return {
        "id": key, "case": case.name, "optimization": opt, "mode": mode,
        "flags": flags[1:], "probe_stdout": result.stdout, "probe": observed,
        "expected": expected, "dwarf": dwarf,
        "sha256": {name: sha256(content.encode()).hexdigest() for name, content in sources.items()}
        | {"libenum.so": sha256(library.read_bytes()).hexdigest()},
    }


def comparisons() -> list[tuple[str, str, str]]:
    pairs = []
    for opt, case in product(OPTS, CASES):
        default, short = (f"{opt}/{mode}/{case.name}" for mode in MODES)
        pairs.extend((("flag_forward", default, short), ("flag_reverse", short, default)))
    for mode, case in product(MODES, CASES):
        pairs.append(("optimization", f"O0/{mode}/{case.name}", f"O2/{mode}/{case.name}"))
    for opt, mode in product(OPTS, MODES):
        for category, old, new in TRANSITIONS:
            pairs.append((category, f"{opt}/{mode}/{old}", f"{opt}/{mode}/{new}"))
        control = f"{opt}/{mode}/u255"
        pairs.append(("self", control, control))
    return pairs


def compare(work: Path, probes: dict, pair: tuple[str, str, str]) -> dict:
    category, old, new = pair
    report_path = work / f"{category}-{old.replace('/', '-')}-{new.replace('/', '-')}.json"
    result = command(
        sys.executable, "-m", "abicheck", "compare",
        work / old / "libenum.so", work / new / "libenum.so",
        "--depth", "binary", "-o", f"json={report_path}", codes=(0, 2, 4),
    )
    report = json.loads(report_path.read_text())
    assert report["old_evidence_depth"] == report["new_evidence_depth"] == "binary", pair
    before, after = (probes[key]["probe"] for key in (old, new))
    delta = {
        name: {field: [value, after[name][field]] for field, value in metrics.items()
               if value != after[name][field]}
        for name, metrics in before.items() if metrics != after[name]
    }
    changes = report["changes"]
    if not delta:
        # GCC's DW_AT_producer can expose the toggle even on identical layouts.
        allowed = {"toolchain_flag_drift"} if category.startswith("flag_") else set()
        assert result.returncode == 0 and report["verdict"] in {"NO_CHANGE", "COMPATIBLE"}, pair
        assert {change["kind"] for change in changes} <= allowed, (pair, changes)
        if category == "self":
            assert report["verdict"] == "NO_CHANGE", pair
    return {
        "category": category, "old": old, "new": new, "probe_delta": delta,
        "exit_code": result.returncode, "verdict": report["verdict"],
        "evidence_tier": report["evidence_tier"],
        "analysis_assurance": report["analysis_assurance"],
        "change_kinds": dict(Counter(change["kind"] for change in changes)),
        "changes": changes,
    }


def write_receipt(name: str, receipt: dict) -> None:
    temp = HERE / f".{name}.tmp"
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    temp.replace(HERE / name)


def main() -> None:
    if not __debug__:
        raise SystemExit("Run with Python assertions enabled.")
    version = command(CXX, "-dumpfullversion").stdout.strip()
    target = command(CXX, "-dumpmachine").stdout.strip()
    if version.split(".")[0] != "14" or target != "x86_64-linux-gnu":
        raise SystemExit(f"Expected GCC 14 targeting x86_64-linux-gnu, got {version} / {target}")
    metadata = {
        "compiler": command(CXX, "--version").stdout.splitlines()[0],
        "target": target,
        "python": sys.version.split()[0],
        "checker_head": command("git", "rev-parse", "HEAD").stdout.strip(),
        "checker_tracked_tree_clean": not command("git", "diff", "HEAD", "--name-only").stdout,
        "checker_version": command(sys.executable, "-m", "abicheck", "--version").stdout.strip(),
    }
    pairs = comparisons()
    assert len(pairs) == len(set(pairs)) == 158
    with TemporaryDirectory(prefix="abicheck-enum-") as temporary, ThreadPoolExecutor(max_workers=4) as pool:
        work = Path(temporary)
        built = list(pool.map(lambda entry: build(work, *entry), product(CASES, OPTS, MODES)))
        probes = {row["id"]: row for row in built}
        assert len(probes) == len(built) == 84
        write_receipt("probes.json", {
            "environment": metadata, "cases": [asdict(case) for case in CASES], "builds": probes,
        })
        print(f"Validated {len(probes)} independent probes and provider DWARF readings.", flush=True)
        checked = list(pool.map(lambda pair: compare(work, probes, pair), pairs))
    write_receipt("checker.json", {
        "environment": metadata, "argv": ["-m", "abicheck", "compare", "OLD", "NEW",
                                           "--depth", "binary", "-o", "json=REPORT"],
        "counts_by_category": dict(Counter(row["category"] for row in checked)),
        "counts_by_verdict": dict(Counter(row["verdict"] for row in checked)),
        "comparisons": checked,
    })
    print(f"Completed {len(checked)} comparisons: {dict(Counter(row['verdict'] for row in checked))}")


if __name__ == "__main__":
    main()

```

## Signedness-only isolation follow-up

An additional pair fixes the enum width at32 bits and preserves both names and
values: `enum E : unsigned int { Zero=0, One=1 };` changes to
`enum E : int { Zero=0, One=1 };`. Both export
`extern "C" E identity(E e) { return e; }` and are built with GCC14.2
`-std=c++20 -O0 -g -shared -fPIC`. Public dump/compare without headers returns
NO_CHANGE, exit0, zero findings. This confirms that the DWARF comparison does not
report this isolated distinction in the tested lane. It does not establish a
calling-convention break: equal-width signed/unsigned enum transfer needs a separate
ABI/consumer analysis before changing severity. Preserve the fact first, then
classify its consequences rather than assigning BREAKING by construction.

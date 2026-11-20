# C++ by-value call ABI pilot

Run `python run.py` in this directory on Linux x86-64 with
`/usr/bin/g++-14` version 14.2.0 and GNU binutils. The script generates two
independent C++20 shared libraries and two clients for each profile under
`fixtures/<profile>/{old,new}/`. Each side has `fixture.hpp`, `library.cpp`,
`client.cpp`, `libprobe.so`, and `client`. All 99 functions per DSO have
stable C linkage so linker name changes cannot mask changes to argument
passing. The translation units and headers still use C++ semantics.

For each function, the first i arguments are uint64_t and the next f are
double. The last argument is a named 16-byte, 8-byte-aligned Payload passed by
value. A callee prints the raw bits of its two fields. Both clients use the
same distinct sentinel words. The matched builds supply the baseline.
The old binary is linked once against the old DSO. Three isolated executions
per cell cover old/old, new/new, and old/new. Each run sets an absolute
LD_LIBRARY_PATH and has a 30-second timeout. Separate loader traces confirm
the chosen DSO for every profile and pairing. Results record arguments,
working directories implied by the fixture tree, compiler and linker
versions, symbol tables, size probes, exit status, and output.

| Old → new field types | Old/new unequal | Old/new equal |
|---|---:|---:|
| Integer + integer → double + double | 83 | 16 |
| Integer + integer → integer + double | 63 | 36 |
| Integer + double → double + integer | 48 | 51 |
| Integer control | 0 | 99 |
| Double control | 0 | 99 |
| Mixed control | 0 | 99 |
| Nontrivial destructor removed, integer fields unchanged | 89 | 10 |
| Total | 283 | 410 |

There were 693 cells and 2,079 payload executions. All 1,386 matched
baselines and all 297 old/new controls returned the sentinel words.
All child processes exited normally. Each profile retained all 99 defined
dynamic symbol names and kinds across versions. The symbol-table addresses
are allowed to differ.

## Where the register mismatch disappears

The three trivial mutations compare two eightbyte payloads. The old and new
definitions can require different register banks. If either required bank
cannot fit the whole aggregate, this payload is passed on the stack.

| Mutation | Cells where both sides use the stack | Count |
|---|---|---:|
| II → DD | i ≥ 5 and f ≥ 7 | 16 |
| II → ID | i ≥ 6, or i = 5 and f ≥ 8 | 36 |
| ID → DI | i ≥ 6 or f ≥ 8 | 51 |

Across all 297 cells for these mutations, both sides used the stack exactly
where the selected bit pattern survived. The identical prefix and stack
layout explain the match. At a fixed signature in this region the tested
argument locations agree even though its aggregate classification has changed.
That observation preserves neither the type contract nor the behavior of
arbitrary clients. Old/new executes across incompatible type definitions and
is outside the C++ language contract. Agreement measures the chosen machine
code and payload.

Disassembly confirms the boundary for II → DD. At (0,0) the old callee reads
RDI/RSI and the new one reads XMM0/XMM1. At (5,7) both read the same two stack
slots. Inspect with `objdump -d --disassemble=probe_0_0` and
`objdump -d --disassemble=probe_5_7` on the two DSOs.

The destructor profile uses nontrivial → trivial so the new callee reads
value words and does not dereference a bogus pointer. Disassembly at (0,0)
shows the old callee reading through RDI and the new callee reading RDI/RSI
as values. At i=5 and f∈{0..8,10}, the sentinel output happened to survive.
These are accidental matches for this code generation. The old caller has
one general-purpose argument register left for the invisible reference,
whereas the new callee expects a stack value. The match at f=10 and failure
at f=9 illustrate why this slice must be scored by calling protocol as well
as output. The reverse mutation is outside this run.

The 9×11 grid covers this bounded family of scalar prefixes. Other type
layouts, return values, variadic functions, compiler flags, calling
conventions, and targets remain separate coverage obligations.

## Public checker audit

Pinned checker b0b8793989c150a5b05f48e4e999a7df4219365a. Four mutated DSO pairs
reported BREAKING; the three identity controls reported NO_CHANGE and zero findings.
The field mutations produced type_field_type_changed; destructor removal produced
value_abi_trait_changed for all 99 affected functions. These are seven whole-library
comparisons, not 693 independent classifier decisions. The tool detects changed
contracts even where one payload happens to survive. This result is not a false
positive rate estimate or proof of all calling-convention detection.

## Resource-boundary selection

For this fixed family of two-eightbyte payloads and scalar prefix arguments,
partition integer prefix counts into 0..4, 5, >=6 and floating counts into
0..6, 7, >=8. Representatives (0,5,6) x (0,7,8) give nine per profile, 63 across
seven profiles instead of 693. A small independent placement model checked on
all 693 cells preserves whether old/new argument locations/protocols agree.
It models six integer and eight SSE argument registers, whole-aggregate stack
fallback, and an explicit distinct indirect protocol for the nontrivial type.
This is not full exact-location preservation (register indices vary within a
region), nor an all-signatures proof. The ten accidental destructor matches
remain protocol mismatches; runtime outputs cannot define the equivalence classes.
The first model attempt incorrectly treated an exhausted unused register bank as
a reason to spill; grid validation caught this and the rule now compares required
registers with remaining capacity. The checked script below contains the correction.

The previous P0 flags are not fully enacted here: the script records actual flags
and uses compiler default ABI-version settings. Do not label this full P0
certification. Reverse trivial-to-nontrivial, return values and varargs remain open.

## Reproduce and inspect

Save run.py, checker_audit.py and select.py together under research/call-abi-pilot
in the pinned repository. Run them in that order. The public audit requires an
installed abicheck; the compiler experiment needs /usr/bin/g++-14 and binutils.
Generated fixture source, binaries, loader checks and command receipts are retained
by run.py; results.json contains every execution. Report observations separately
from predictions; incompatible old/new execution is not defined by ISO C++.

## run.py

```python
"""Build and run the bounded GCC 14.2 SysV call experiment."""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parent
CXX = "/usr/bin/g++-14"
FLAGS = ["-std=c++20", "-m64", "-march=x86-64", "-O2", "-g", "-fno-lto"]
PROFILES = {
    "integer_to_sse": ("II", "DD"),
    "integer_to_mixed": ("II", "ID"),
    "mixed_swap": ("ID", "DI"),
    "integer_control": ("II", "II"),
    "sse_control": ("DD", "DD"),
    "mixed_control": ("ID", "ID"),
    "destructor_removed": ("NN", "II"),
}
GRID = list(itertools.product(range(9), range(11)))
ENV = {k: v for k, v in os.environ.items() if not k.startswith("LD_")}
VALUES = ("0x3ff000000000002aULL", "0x4000000000000057ULL")
EXPECTED = f"{int(VALUES[0][:-3], 16):x} {int(VALUES[1][:-3], 16):x}"
PREAMBLE = "#include <bit>\n#include <cstdint>\n#include <cstdio>\n#include <cstdlib>\n"


def run(directory: Path, argv: list[str], env: dict | None = None) -> dict:
    try:
        process = subprocess.run(argv, cwd=directory, env=env or ENV,
                                 capture_output=True, text=True, timeout=30)
        return {"argv": argv, "rc": process.returncode,
                "out": process.stdout, "err": process.stderr}
    except subprocess.TimeoutExpired:
        return {"argv": argv, "rc": None, "out": "", "err": "timeout"}


def checked(directory: Path, argv: list[str], env: dict | None = None) -> dict:
    receipt = run(directory, argv, env)
    if receipt["rc"] != 0:
        raise RuntimeError(receipt)
    return receipt


def signature(i: int, f: int) -> str:
    args = ["std::uint64_t"] * i + ["double"] * f + ["Payload p"]
    return f'extern "C" void probe_{i}_{f}({", ".join(args)})'


def generate(directory: Path, shape: str) -> None:
    types = ["double" if c == "D" else "std::uint64_t" for c in shape]
    destructor = "~Payload() {}" if shape == "NN" else ""
    header = PREAMBLE + f"struct Payload {{ {types[0]} x; {types[1]} y; {destructor} }};\n"
    header += "\n".join(signature(i, f) + ";" for i, f in GRID)
    (directory / "fixture.hpp").write_text(header + "\n")
    include = '#include "fixture.hpp"\n'
    definitions = []
    cases = []
    for index, (i, f) in enumerate(GRID):
        definitions.append(signature(i, f) +
            ' { std::printf("%llx %llx\\n", '
            'static_cast<unsigned long long>(std::bit_cast<std::uint64_t>(p.x)), '
            'static_cast<unsigned long long>(std::bit_cast<std::uint64_t>(p.y))); }')
        args = ["17ULL"] * i + ["3.25"] * f + ["p"]
        cases.append(f'case {index}: probe_{i}_{f}({", ".join(args)}); break;')
    (directory / "library.cpp").write_text(include + "\n".join(definitions) + "\n")
    init = ", ".join(f"std::bit_cast<{t}>(std::uint64_t{{{v}}})"
                     for t, v in zip(types, VALUES))
    (directory / "client.cpp").write_text(include + f"""
int main(int argc, char** argv) {{
  if (argc != 2) return 2;
  const int index = std::atoi(argv[1]);
  if (index == -1) {{ std::printf("%zu %zu\\n", sizeof(Payload), alignof(Payload)); return 0; }}
  Payload p{{{init}}};
  switch (index) {{
    {chr(10).join(cases)}
    default: return 2;
  }}
}}
""")


def main() -> None:
    assert platform.system() == "Linux" and platform.machine() == "x86_64"
    version = checked(ROOT, [CXX, "-dumpfullversion"])
    assert version["out"].strip() == "14.2.0", version
    report = {"toolchain": [version] + [checked(ROOT, cmd) for cmd in
              ([CXX, "-v"], ["ld", "--version"], ["uname", "-a"])],
              "expected": EXPECTED, "profiles": {}}
    fixtures = ROOT / "fixtures"
    fixtures.mkdir(exist_ok=True)
    for profile, shapes in PROFILES.items():
        directory = fixtures / profile
        info = {"sides": {}, "cases": []}
        exports = []
        for side, shape in zip(("old", "new"), shapes):
            folder = directory / side
            folder.mkdir(parents=True, exist_ok=True)
            generate(folder, shape)
            build = [checked(folder, [CXX, *FLAGS, "-fPIC", "-shared", "-Wl,-soname,libprobe.so",
                                      "-o", "libprobe.so", "library.cpp"]),
                     checked(folder, [CXX, *FLAGS, "-o", "client", "client.cpp", "-L.", "-lprobe"])]
            symbols = checked(folder, ["nm", "-D", "--defined-only", "--format=posix", "libprobe.so"])
            exports.append(sorted(tuple(line.split()[:2]) for line in symbols["out"].splitlines()))
            info["sides"][side] = {"shape": shape, "build": build, "symbols": symbols,
                "size_probe": checked(folder, ["./client", "-1"], {**ENV, "LD_LIBRARY_PATH": str(folder)})}
        info["equal_defined_dynamic_symbols"] = exports[0] == exports[1]
        assert info["equal_defined_dynamic_symbols"], profile
        assert len(exports[0]) == len(GRID), (profile, exports[0])
        assert all(s["size_probe"]["out"].strip() == "16 8" for s in info["sides"].values()), profile
        info["loader_checks"] = {}
        for client, library in [("old", "old"), ("new", "new"), ("old", "new")]:
            path = str(directory / library)
            receipt = checked(directory, [f"./{client}/client"],
                              {**ENV, "LD_LIBRARY_PATH": path, "LD_TRACE_LOADED_OBJECTS": "1"})
            assert f"libprobe.so => {path}/libprobe.so " in receipt["out"], receipt
            info["loader_checks"][f"{client}/{library}"] = {
                **receipt, "LD_LIBRARY_PATH": path, "LD_TRACE_LOADED_OBJECTS": "1"}
        for index, (i, f) in enumerate(GRID):
            case = {"integer_prefix": i, "floating_prefix": f, "symbol": f"probe_{i}_{f}"}
            for client, library in [("old", "old"), ("new", "new"), ("old", "new")]:
                env = {**ENV, "LD_LIBRARY_PATH": str(directory / library)}
                receipt = run(directory, [f"./{client}/client", str(index)], env)
                receipt["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH"]
                receipt["matches"] = receipt["rc"] == 0 and receipt["out"].strip() == EXPECTED
                case[f"{client}/{library}"] = receipt
                if client == library or profile.endswith("_control"):
                    assert receipt["matches"], (profile, case)
            info["cases"].append(case)
        report["profiles"][profile] = info
        print(profile, sum(not c["old/new"]["matches"] for c in info["cases"]), flush=True)
    (ROOT / "results.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()

```

## checker_audit.py

```python
import json,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
runtime=json.loads((P/'results.json').read_text());results={}
for name,profile in runtime['profiles'].items():
 folder=P/'fixtures'/name;out=folder/'checker';out.mkdir(exist_ok=True);commands=[]
 for side in ('old','new'):
  args=[sys.executable,'-m','abicheck','dump',str(folder/side/'libprobe.so'),'-o',str(out/f'{side}.json')]
  p=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=180);commands.append({'argv':args,'rc':p.returncode,'stderr':p.stderr});assert p.returncode==0
 args=[sys.executable,'-m','abicheck','compare',str(out/'old.json'),str(out/'new.json'),'-o',f'json={out/"report.json"}']
 p=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,timeout=180);commands.append({'argv':args,'rc':p.returncode,'stderr':p.stderr});assert p.returncode in (0,1,2,4)
 (out/'commands.json').write_text(json.dumps(commands,indent=2))
 report=json.loads((out/'report.json').read_text());results[name]={'verdict':report['verdict'],'kinds':sorted(set(c['kind'] for c in report['changes'])),'findings':len(report['changes']),'runtime_mismatches':sum(not c['old/new']['matches'] for c in profile['cases']),'runtime_cases':len(profile['cases'])}
 print(name,results[name],flush=True)
(P/'checker-results.json').write_text(json.dumps(results,indent=2))

```

## select.py

```python
"""Bounded resource-boundary selection; protocol equality, not runtime-output equality."""
import json
from pathlib import Path
P=Path(__file__).resolve().parent;r=json.loads((P/'results.json').read_text())
shapes={'integer_to_sse':('II','DD'),'integer_to_mixed':('II','ID'),'mixed_swap':('ID','DI'),'integer_control':('II','II'),'sse_control':('DD','DD'),'mixed_control':('ID','ID'),'destructor_removed':('NN','II')}
def location(shape,i,f):
 if shape=='NN':return ('indirect',) # distinguish pointer protocol regardless of placement
 gi=shape.count('I');fp=shape.count('D')
 if gi>max(0,6-i) or fp>max(0,8-f):return ('stack',0,8)
 fields=[]
 for c in shape:
  if c=='I':fields.append(('gpr',i));i+=1
  else:fields.append(('sse',f));f+=1
 return tuple(fields)
def representative(i,f):return (0 if i<5 else min(i,6),0 if f<7 else min(f,8))
summary={}
for name,p in r['profiles'].items():
 old,new=shapes[name];by={(c['integer_prefix'],c['floating_prefix']):c for c in p['cases']};selected=set();contradictions=[]
 for (i,f),c in by.items():
  ri,rf=representative(i,f);selected.add((ri,rf));different=location(old,i,f)!=location(new,i,f)
  assert different==(location(old,ri,rf)!=location(new,ri,rf))
  if different and c['old/new']['matches']:contradictions.append([i,f])
 summary[name]={'dense':len(by),'selected':len(selected),'representatives':sorted(selected),'protocol_different_output_equal':contradictions,'protocol_equality_preserved_on_grid':True}
(P/'selection-results.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))

```

## checker-results.json

```json
{
  "integer_to_sse": {
    "verdict": "BREAKING",
    "kinds": [
      "soname_bump_recommended",
      "type_field_type_changed"
    ],
    "findings": 3,
    "runtime_mismatches": 83,
    "runtime_cases": 99
  },
  "integer_to_mixed": {
    "verdict": "BREAKING",
    "kinds": [
      "soname_bump_recommended",
      "type_field_type_changed"
    ],
    "findings": 2,
    "runtime_mismatches": 63,
    "runtime_cases": 99
  },
  "mixed_swap": {
    "verdict": "BREAKING",
    "kinds": [
      "soname_bump_recommended",
      "type_field_type_changed"
    ],
    "findings": 3,
    "runtime_mismatches": 48,
    "runtime_cases": 99
  },
  "integer_control": {
    "verdict": "NO_CHANGE",
    "kinds": [],
    "findings": 0,
    "runtime_mismatches": 0,
    "runtime_cases": 99
  },
  "sse_control": {
    "verdict": "NO_CHANGE",
    "kinds": [],
    "findings": 0,
    "runtime_mismatches": 0,
    "runtime_cases": 99
  },
  "mixed_control": {
    "verdict": "NO_CHANGE",
    "kinds": [],
    "findings": 0,
    "runtime_mismatches": 0,
    "runtime_cases": 99
  },
  "destructor_removed": {
    "verdict": "BREAKING",
    "kinds": [
      "soname_bump_recommended",
      "value_abi_trait_changed"
    ],
    "findings": 100,
    "runtime_mismatches": 89,
    "runtime_cases": 99
  }
}
```

## selection-results.json

```json
{
  "integer_to_sse": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "integer_to_mixed": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "mixed_swap": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "integer_control": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "sse_control": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "mixed_control": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [],
    "protocol_equality_preserved_on_grid": true
  },
  "destructor_removed": {
    "dense": 99,
    "selected": 9,
    "representatives": [
      [
        0,
        0
      ],
      [
        0,
        7
      ],
      [
        0,
        8
      ],
      [
        5,
        0
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        6,
        0
      ],
      [
        6,
        7
      ],
      [
        6,
        8
      ]
    ],
    "protocol_different_output_equal": [
      [
        5,
        0
      ],
      [
        5,
        1
      ],
      [
        5,
        2
      ],
      [
        5,
        3
      ],
      [
        5,
        4
      ],
      [
        5,
        5
      ],
      [
        5,
        6
      ],
      [
        5,
        7
      ],
      [
        5,
        8
      ],
      [
        5,
        10
      ]
    ],
    "protocol_equality_preserved_on_grid": true
  }
}
```

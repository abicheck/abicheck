# Return ABI experiment: protocol evidence versus output agreement

GCC 14.2.0 (Debian 14.2.0-19), C++20, Linux x86-64 ELF, `-O0 -g`, abicheck checkout `b0b8793989c150a5b05f48e4e999a7df4219365a`. The client is linked once per version. For the replacement run the old executable loads the new shared library using `LD_LIBRARY_PATH`. The retained `ldd` receipts verify all 27 client/provider path selections. Both libraries expose the same 99 C-linkage probe names and the test type has size 16 and alignment 8 on both sides.

Each profile crosses 0–8 integer prefix arguments with 0–10 floating prefix arguments. Providers ignore those arguments and return the same two 64-bit payloads via `std::bit_cast`. This measures result transfer. An sret pointer shifts integer argument locations, but the experiment does not measure whether the callee receives the explicit input arguments correctly. Return register allocation is independent of prefix register occupancy. The emitted instructions can nevertheless leave different incidental values in registers outside the ABI return contract.

| Profile | Old/new value mismatches out of 99 | DWARF compare |
|---|---:|---|
| INTEGER/INTEGER → SSE/SSE | 66 | BREAKING |
| INTEGER/INTEGER → INTEGER/SSE | 66 | BREAKING |
| INTEGER/SSE → SSE/INTEGER | 99 | BREAKING |
| Nontrivial destructor removed, sret → INTEGER/INTEGER | 99 | BREAKING |
| Five identity controls, including sret | 0 of 495 | NO_CHANGE in all five |

All 1,782 matched-version baselines returned the expected pair. The 891 old/new runs produced 330 value mismatches and no timeouts or nonzero exits. Each child has a two-second timeout. The results record observed behavior for these binaries only. The mismatched cross-version calls deliberately violate the binary interface.

The first two mutations each matched the expected payloads in 33 cells, covering integer prefix counts 0–2. In the retained disassembly for the SSE/SSE provider, `probe0` loads both sentinels into RAX/RDX as intermediates before moving them to XMM0/XMM1, while `probe33` uses RCX/RBX. The old client reads RAX/RDX in both cells. A successful run therefore cannot certify that two return protocols agree.

For the destructor case the old client pre-fills aligned storage with 0xa5, then uses placement construction with a same-type prvalue. The old client disassembly shows the pre-filled buffer passed as the hidden return destination for `probe0`. The new provider returns in registers and does not write through that pointer. All 99 old/new outputs contained two unchanged 0xa5 words. The identity control replaces the fill with the expected sentinels in all 99 cells. The reverse direction would pass no destination to a callee that writes through a hidden pointer, so it is outside the experiment.

The public abicheck CLI dumped each unstripped ELF without headers and compared the resulting snapshots. The three register-class mutations report `type_field_type_changed`. The destructor mutation reports `struct_return_convention_changed` for all 99 symbols. All four breaking comparisons also recommend a SONAME bump. The field-type findings establish that the checker flags those changes, while the disassembly identifies the return locations.

Run `python research/return-abi-pilot/run.py` from the checkout with GCC 14, binutils, ldd, and the abicheck Python dependencies installed. The script retains generated source, libraries, clients and raw checker dumps/reports under `fixtures/`. `commands.json` records commands, per-command environment overrides, exit codes and output, including loader checks and sampled disassembly. See `results.json` for each runtime cell and `checker_results.json` for checker summaries.

## Optimization replication

The entire experiment was repeated with `-O2` replacing `-O0`; all other
script inputs were unchanged. At each optimization level this is 891 cells,
2,673 payload executions, 27 verified loader selections and nine public checker
comparisons. Across both builds: 1,782 cells and 5,346 payload executions;
these are repeated configurations of the same nine profiles, not 1,782 distinct
ABI mechanisms. All 3,564 matched-version executions and all 990 cross-version
identity controls passed.

| Mutation | O0 mismatches / 99 | O2 mismatches / 99 |
|---|---:|---:|
| II to DD | 66 | 99 |
| II to ID | 66 | 99 |
| ID to DI | 99 | 99 |
| sret to II | 99 | 99 |

The 66 accidental output agreements at O0 disappeared at O2. Both checker runs
reported the same four BREAKING and five NO_CHANGE verdicts. The unchanged
0xa5 return buffer was observed for all 99 sret-removal cells at both levels.
No timeout or process failure occurred in either run. This is evidence about
these generated binaries, not a universal claim about optimization.

## Consequences for coverage and reduction

A test oracle that observes a matching result cannot establish protocol equality.
Keep separate denominators for semantic ABI classes, compiler configurations,
generated signatures, runtime observations and checker comparisons. The checker
comparison unit here is a library pair with 99 exports, not each runtime cell.

Prefix counts do not consume return registers in the assumed platform protocol,
so return classification should not inherit the argument-exhaustion matrix.
However, the measured accidental output agreements depend on generated code.
Consequently a reduction preserving protocol classification need not preserve
incidental runtime outputs. An observation and its assumptions must be named
before claiming completeness or selecting representatives.

The next missing interaction is hidden-return-pointer placement x explicit
argument transfer. Providers here ignore explicit arguments; an experiment that
uses distinct per-position input sentinels is required to observe shifts. Other
open boundaries include larger MEMORY returns, vector classes, Clang, additional
ABI profiles and composition with nontrivial arguments. No inventory row is closed
by this pilot. See the [inventory](abiforge-abi-inventory.md) and the separate
[register-capacity theorem](abiforge-call-abi-experiment.md#mechanized-register-capacity-reduction).

## Reproduction script

Save the following as `research/return-abi-pilot/run.py` in the pinned abicheck
checkout and execute it with Python. For the optimization replication save the
same script under `research/return-abi-o2/run.py` and replace the single `"-O0"`
compiler flag with `"-O2"`. Separate directories prevent overwriting evidence.
The script regenerates all sources, outputs, loader and disassembly receipts.

```python
"""Build and run the return ABI pilot. Run with Python 3 from any directory."""
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROFILES = {
    "ii_dd": ("II", "DD"), "ii_id": ("II", "ID"), "id_di": ("ID", "DI"),
    "ii_identity": ("II", "II"), "dd_identity": ("DD", "DD"),
    "id_identity": ("ID", "ID"), "di_identity": ("DI", "DI"),
    "sret_identity": ("IIn", "IIn"), "sret_removed": ("IIn", "II"),
}
VALUES = (0x3ff123456789abcd, 0x400abcdef1234567)
EXPECTED = " ".join(f"{value:016x}" for value in VALUES)
CELLS = list(itertools.product(range(9), range(11)))
COMMANDS = []


def run(argv, timeout=60, allowed=(0,), **env):
    argv = list(map(str, argv))
    record = {"argv": argv, "env_overrides": env}
    COMMANDS.append(record)
    try:
        proc = subprocess.run(argv, cwd=ROOT, env=os.environ | env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        record["timeout"] = timeout
        if allowed is not None:
            raise
    else:
        record.update(rc=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        if allowed is not None and proc.returncode not in allowed:
            raise RuntimeError(record)
    return record


def build(folder, code):
    folder.mkdir(parents=True, exist_ok=True)
    types = ["U" if c == "I" else "double" for c in code[:2]]
    destructor = "~T() {}" if code.endswith("n") else ""
    header = [
        "#include <bit>\n#include <cstdint>\nusing U = std::uint64_t;",
        f"struct T {{ {types[0]} a; {types[1]} b; {destructor} }};",
        "static_assert(sizeof(T)==16 && alignof(T)==8);",
    ]
    provider = ['#include "api.hpp"']
    client = ['#include "api.hpp"', "#include <cstdio>", "#include <cstdlib>",
              "#include <cstring>", "#include <new>", "int main(int argc, char** argv) {",
              "if (argc != 2) return 2;", "switch (std::atoi(argv[1])) {"]
    values = ", ".join(f"std::bit_cast<{t}>(U(0x{v:x}ULL))" for t, v in zip(types, VALUES))
    for index, (integers, floats) in enumerate(CELLS):
        signature = ", ".join(["U"] * integers + ["double"] * floats)
        arguments = ", ".join(["U(37)"] * integers + ["2.5"] * floats)
        header.append(f'extern "C" T probe{index}({signature});')
        provider.append(f"T probe{index}({signature}) {{ return T{{{values}}}; }}")
        client.extend([
            f"case {index}: {{",
            "alignas(T) unsigned char storage[sizeof(T)];",
            "std::memset(storage, 0xa5, sizeof(storage));",
            f"T* result = ::new (storage) T(probe{index}({arguments}));",
            "U words[2]; std::memcpy(words, storage, sizeof(words));",
            "result->~T();",
            'std::printf("%016llx %016llx\\n", (unsigned long long)words[0], (unsigned long long)words[1]);',
            "return 0; }",
        ])
    client.append("} return 2; }")
    for filename, lines in [("api.hpp", header), ("provider.cpp", provider), ("client.cpp", client)]:
        (folder / filename).write_text("\n".join(lines) + "\n")
    flags = ["g++", "-std=c++20", "-O0", "-g"]
    lib = folder / "libprobe.so"
    run(flags + ["-shared", "-fPIC", folder / "provider.cpp", "-Wl,-soname,libprobe.so", "-o", lib])
    run(flags + [folder / "client.cpp", "-L", folder, "-lprobe", "-o", folder / "client"])
    symbols = run(["nm", "-D", "--defined-only", lib])["stdout"]
    return sorted(re.findall(r" T (probe\d+)$", symbols, re.M))


def audit(folder):
    out = folder / "checker"
    out.mkdir(exist_ok=True)
    cli = [sys.executable, "-m", "abicheck"]
    for side in ("old", "new"):
        run(cli + ["dump", folder / side / "libprobe.so", "-o", out / f"{side}.json"])
    record = run(cli + ["compare", out / "old.json", out / "new.json",
                        "-o", f"json={out / 'report.json'}"], allowed=(0, 1, 2, 4))
    report = json.loads((out / "report.json").read_text())
    return {"verdict": report["verdict"], "rc": record["rc"],
            "kinds": sorted({c["kind"] for c in report["changes"]}),
            "findings": len(report["changes"])}


def save(name, data):
    (HERE / name).write_text(json.dumps(data, indent=2) + "\n")


def main():
    compiler = run(["g++", "--version"])["stdout"].splitlines()[0]
    assert run(["g++", "-dumpversion"])["stdout"].strip() == "14"
    assert run(["uname", "-sm"])["stdout"].strip() == "Linux x86_64"
    runtime, checker = {}, {}
    try:
        for name, codes in PROFILES.items():
            folder = HERE / "fixtures" / name
            exports = [build(folder / side, code) for side, code in zip(("old", "new"), codes)]
            assert exports[0] == exports[1] and len(exports[0]) == len(CELLS)
            for caller, provider in (("old", "old"), ("new", "new"), ("old", "new")):
                expected = folder / provider / "libprobe.so"
                receipt = run(["ldd", folder / caller / "client"], LD_LIBRARY_PATH=str(expected.parent))
                assert f"libprobe.so => {expected} " in receipt["stdout"], receipt
            for side in ("old", "new"):
                for symbol in ("probe0", "probe33", "probe98"):
                    run(["objdump", "-d", f"--disassemble={symbol}", folder / side / "libprobe.so"])
            if name.startswith("sret"):
                run(["objdump", "-d", "--disassemble=main", folder / "old" / "client"])
            cases = []
            for index, (integers, floats) in enumerate(CELLS):
                case = {"integer_prefix": integers, "floating_prefix": floats, "symbol": f"probe{index}"}
                for caller, provider in (("old", "old"), ("new", "new"), ("old", "new")):
                    result = run([folder / caller / "client", index], timeout=2, allowed=None,
                                 LD_LIBRARY_PATH=str(folder / provider))
                    output = result.get("stdout", "").strip()
                    status = ("timeout" if "timeout" in result else
                              "process_failure" if result["rc"] else
                              "match" if output == EXPECTED else "value_mismatch")
                    case[f"{caller}/{provider}"] = {"status": status, "output": output, "rc": result.get("rc")}
                cases.append(case)
            assert all(c[k]["status"] == "match" for c in cases for k in ("old/old", "new/new"))
            runtime[name] = cases
            checker[name] = audit(folder)
            print(name, sum(c["old/new"]["status"] != "match" for c in cases), checker[name], flush=True)
    finally:
        save("results.json", {"compiler": compiler, "expected": EXPECTED, "profiles": runtime})
        save("checker_results.json", checker)
        save("commands.json", COMMANDS)


if __name__ == "__main__":
    main()

```

## Recorded summaries

```json
{
  "O0": {
    "ii_dd": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 33,
      "cross_value_mismatches": 66,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 3
      }
    },
    "ii_id": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 33,
      "cross_value_mismatches": 66,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 2
      }
    },
    "id_di": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 3
      }
    },
    "ii_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "dd_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "id_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "di_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "sret_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "sret_removed": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "struct_return_convention_changed"
        ],
        "findings": 100
      }
    }
  },
  "O2": {
    "ii_dd": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 3
      }
    },
    "ii_id": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 2
      }
    },
    "id_di": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "type_field_type_changed"
        ],
        "findings": 3
      }
    },
    "ii_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "dd_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "id_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "di_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "sret_identity": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 99,
      "cross_value_mismatches": 0,
      "checker": {
        "verdict": "NO_CHANGE",
        "rc": 0,
        "kinds": [],
        "findings": 0
      }
    },
    "sret_removed": {
      "cells": 99,
      "matched_baselines": 198,
      "cross_matches": 0,
      "cross_value_mismatches": 99,
      "checker": {
        "verdict": "BREAKING",
        "rc": 4,
        "kinds": [
          "soname_bump_recommended",
          "struct_return_convention_changed"
        ],
        "findings": 100
      }
    }
  }
}
```

## Hidden return pointer and explicit inputs

A follow-up uses 1–8 integer arguments with distinct values 100, 101, ...,
and two double arguments 1.25 and 2.5. The provider records received inputs in
private globals, exposed through unchanged scalar accessor functions. This
observes input transfer independently of the incompatible result transfer.
Three profiles (destructor removed, nontrivial identity, trivial identity) at
O0/O2, with old/old, new/new and old/new combinations, yield 144 executions.
All 96 matched-version baselines and 32 cross-version identity controls passed.
All 16 mutation runs had incorrect integer arguments; the two floating arguments
were unchanged. For two integer arguments, the new provider saw a buffer address
and 100 rather than 100 and 101. At seven arguments the shift extended across
the register/stack boundary. This bounded experiment does not prove arbitrary
argument sequences or other platforms. Cross-version calls deliberately violate
the ABI; reverse mutation with an invalid write destination was not executed.
This follow-up has not been independently audited through the checker.

Save as `research/sret-input-pilot/run.py` and run with Python and GCC 14.

```python
from pathlib import Path
import subprocess as sp, os,json
P=Path(__file__).resolve().parent
rows=[]
def run(args,env=None):
 return sp.check_output(list(map(str,args)),env=env,text=True,timeout=60)
for opt in ['-O0','-O2']:
 for profile,traits in [('removed',[True,False]),('sret_control',[True,True]),('register_control',[False,False])]:
  base=P/opt[1:]/profile
  for side,nontrivial in zip(['old','new'],traits):
   d=base/side;d.mkdir(parents=True,exist_ok=True)
   header='#include <cstdint>\nusing U=uint64_t;\nstruct T { U a,b; '+('~T() {}' if nontrivial else '')+' };\n'
   provider=['#include "api.hpp"','static U seen[8]; static double fs[2];','extern "C" U read_i(int i) { return seen[i]; }','extern "C" double read_f(int i) { return fs[i]; }']
   client=['#include "api.hpp"','#include <cstdio>','#include <cstdlib>','extern "C" U read_i(int); extern "C" double read_f(int);','int main(int argc,char** argv) {switch(std::atoi(argv[1])) {']
   for n in range(1,9):
    args=', '.join([f'U a{i}' for i in range(n)]+['double f0','double f1'])
    header+=f'extern "C" T probe{n}({args});\n'
    provider.append(f'T probe{n}({args}) {{ '+''.join(f'seen[{i}]=a{i};' for i in range(n))+'fs[0]=f0;fs[1]=f1;return T{11,22}; }')
    client.append(f'case {n}: {{ T t=probe{n}('+','.join([str(100+i) for i in range(n)]+['1.25','2.5'])+');'+f'for(int i=0;i<{n};i++) std::printf("%llu ",(unsigned long long)read_i(i)); std::printf("%.2f %.2f\\n",read_f(0),read_f(1));return 0; }}')
   client.append('}return 2;}')
   (d/'api.hpp').write_text(header)
   (d/'provider.cpp').write_text('\n'.join(provider))
   (d/'client.cpp').write_text('\n'.join(client))
   flags=['g++','-std=c++20',opt,'-g']
   run(flags+['-shared','-fPIC',d/'provider.cpp','-o',d/'libprobe.so'])
   run(flags+[d/'client.cpp','-L'+str(d),'-lprobe','-o',d/'client'])
  for n in range(1,9):
   expected=' '.join([str(100+i) for i in range(n)]+['1.25','2.50'])
   for caller,provider in [('old','old'),('new','new'),('old','new')]:
    env=os.environ|{'LD_LIBRARY_PATH':str(base/provider)}
    out=run([base/caller/'client',str(n)],env).strip()
    if caller==provider: assert out==expected,(out,expected)
    rows.append(dict(opt=opt,profile=profile,n=n,caller=caller,provider=provider,output=out,expected=expected,match=out==expected))
(P/'results.json').write_text(json.dumps(rows,indent=2))
for opt in ['-O0','-O2']:
 for p in ['removed','sret_control','register_control']:
  subset=[r for r in rows if r['opt']==opt and r['profile']==p and r['caller']!=r['provider']]
  print(opt,p,'mismatches',sum(not r['match'] for r in subset),'/',len(subset))

```

## Crossed client/provider optimization experiment

Reused the retained binaries and crossed client O0/O2 with provider O0/O2.
Nine profiles x 99 signatures x three version combinations x four optimization
pairs = 10,692 executions. This reruns the same-optimization cells; it is not
10,692 new semantic scenarios. All 7,128 matched-version executions and all
1,980 cross-version identity controls passed. Loader resolution was checked for
each of the 108 executable/provider groups. No new checker audit was performed.

Accidental matches for each of II-to-DD and II-to-ID, out of 99:

| Client | Provider O0 | Provider O2 |
|---|---:|---:|
| O0 | 33 | 0 |
| O2 | 33 | 0 |

ID-to-DI and sret removal mismatched in all 99 cells for every optimization pair.
Thus in these fixtures optimization alone did not produce an observed interface
failure. Provider optimization changed the visibility of the intentionally
introduced type/protocol mismatch. This is not a claim that all compiler flags
preserve ABI, nor a proof for arbitrary optimization combinations or programs.

Run this after generating both original optimization directories. It retains
per-execution results and a compact summary beside the original experiment.

```python
from pathlib import Path
import json,subprocess,os,itertools
P=Path(__file__).resolve().parent
roots={'O0':P,'O2':P.parent/'return-abi-o2'}
expected=json.loads((P/'results.json').read_text())['expected']
profiles=list(json.loads((P/'results.json').read_text())['profiles'])
rows=[];summary=[]
for co,lo in itertools.product(roots,repeat=2):
 for name in profiles:
  for caller,provider in [('old','old'),('new','new'),('old','new')]:
   exe=roots[co]/'fixtures'/name/caller/'client'
   lib=roots[lo]/'fixtures'/name/provider
   env=os.environ|{'LD_LIBRARY_PATH':str(lib)}
   receipt=subprocess.check_output(['ldd',str(exe)],env=env,text=True)
   assert f'libprobe.so => {lib}/libprobe.so ' in receipt
   matches=0
   for i in range(99):
    r=subprocess.run([str(exe),str(i)],env=env,text=True,capture_output=True,timeout=2)
    match=r.returncode==0 and r.stdout.strip()==expected
    rows.append(dict(client_opt=co,library_opt=lo,profile=name,caller=caller,provider=provider,index=i,rc=r.returncode,output=r.stdout.strip(),match=match))
    matches+=match
   if caller==provider or 'identity' in name: assert matches==99
   summary.append(dict(client_opt=co,library_opt=lo,profile=name,caller=caller,provider=provider,matches=matches,total=99))
(P/'cross-opt-results.json').write_text(json.dumps(rows,indent=2))
(P/'cross-opt-summary.json').write_text(json.dumps(summary,indent=2))
for s in summary:
 if s['caller']!=s['provider'] and 'identity' not in s['profile']: print(s)

```

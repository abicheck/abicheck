"""Run on Linux x86-64 LP64 with GCC 14 and binutils. No Python dependencies."""
import csv
import itertools
import os
import platform
import struct
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent
TYPES = ("_Bool,char,signed char,unsigned char,short,unsigned short,int,unsigned int,"
         "long,unsigned long,long long,unsigned long long,float,double,long double,"
         "float _Complex,double _Complex,long double _Complex").split(",")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def save(name, header, rows):
    with (OUT / name).open("w") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def layouts():
    code = ['#include <stddef.h>', '#include <stdio.h>', 'int main(void){']
    keys = []
    for kind, arity in itertools.product(("struct", "union"), (1, 2)):
        for fields in itertools.product(TYPES, repeat=arity):
            tag = f"{kind} t{len(keys)}"
            decl = " ".join(f"{t} f{i};" for i, t in enumerate(fields))
            code.append(f"{tag} {{ {decl} }};")
            obs = [f"sizeof({tag})", f"offsetof(struct {{ char lead; {tag} x; }}, x)"]
            obs += [f"offsetof({tag},f{i})" for i in range(arity)]
            fmt = ",".join(["%zu"] * len(obs))
            code.append(f'printf("{fmt}\\n",{",".join(obs)});')
            keys.append((kind, arity, *fields))
    Path("layout.c").write_text("\n".join(code + ["return 0;}"]))
    groups = defaultdict(set)
    for mode in ("c99", "c11", "c17", "c23"):
        run("gcc", "-m64", "-pedantic-errors", "-std=" + mode, "layout.c", "-o", "layout")
        rows = []
        for key, value in zip(keys, run("./layout").splitlines(), strict=True):
            groups[(mode, *key[:2])].add(value)
            rows.append([key[0], *key[2:], *([""] if key[1] == 1 else []),
                         *value.split(","), *([""] if key[1] == 1 else [])])
        if mode == "c99":
            baseline = rows
            save("layouts.csv", ["kind","field0","field1","size","embed","off0","off1"], rows)
        print(mode, len(rows), "layouts", [len(groups[(mode,k,n)]) for k in ("struct","union") for n in (1,2)],
              "changed since C99", sum(a != b for a,b in zip(rows, baseline, strict=True)))


def calls():
    code = ['#include <stdio.h>', 'unsigned long slots[16];']
    asm = ['.text','probe:']
    regs = ['rdi','rsi','rdx','rcx','r8','r9'] + [f'xmm{i}' for i in range(8)]
    for i, reg in enumerate(regs):
        asm.append(f'movq %{reg},slots+{8*i}(%rip)')
    for i in range(2):
        asm += [f'movq {8+i*8}(%rsp),%rax', f'movq %rax,slots+{112+8*i}(%rip)']
    asm.append('ret')
    cases = list(itertools.product(('II','IS','SI','SS'), range(7), range(9)))
    for n,(shape,g,s) in enumerate(cases):
        a,b = ['long' if c == 'I' else 'double' for c in shape]
        tag = f'struct T{n}'
        sig = ['long']*g + ['double']*s + [tag]
        args = ['17L']*g + ['19.0']*s + [f'({tag}){{101,203}}']
        code += [f'{tag} {{ {a} a; {b} b; }};', f'extern void p{n}({",".join(sig)});',
                 f'void c{n}(void) {{p{n}({",".join(args)});',
                 'for(int i=0;i<16;i++) printf("%lx ",slots[i]); puts("");}']
        asm += [f'.globl p{n}',f'.set p{n},probe']
    code.append('int main(void){' + ''.join(f'c{i}();' for i in range(len(cases))) + 'return 0;}')
    Path("call.c").write_text("\n".join(code))
    Path("entry.S").write_text("\n".join(asm + ['.section .note.GNU-stack,"",@progbits','']))
    run("gcc","-std=c99","-m64","-O2","call.c","entry.S","-o","call")
    rows = []
    for (shape,g,s), line in zip(cases, run("./call").splitlines(), strict=True):
        slots = [int(x,16) for x in line.split()]
        fits = g+shape.count("I") <= 6 and s+shape.count("S") <= 8
        pos = {"I":g,"S":6+s}
        observed = []
        for i,(kind,v) in enumerate(zip(shape,(101,203))):
            slot = pos[kind] if fits else 14+i
            pos[kind] += 1
            expected = v if kind == "I" else int.from_bytes(struct.pack("<d",v),"little")
            if slots[slot] != expected:
                raise ValueError((shape,g,s,slot,slots[slot],expected))
            observed.append(slot)
        rows.append((shape,g,s,shape if fits else 'stack',*observed))
    save("calls.csv",["shape","prior_gp","prior_sse","transport","slot0","slot1"],rows)
    print("C99 calls", Counter(row[3] for row in rows))


DIALECTS = ("c++98","c++03","c++11","c++14","c++17","c++20","c++23")
# Header, provider expression, old consumer expression.
STABLE = ("struct Pair { long x; double y; }; long take(Pair);",
          "long take(Pair p){return p.x+(long)p.y;}",
          "Pair p={17,24};return take(p)!=41;")


def pair(case, old, new, before, after):
    def provider(mode, fixture):
        Path("api.h").write_text(fixture[0])
        Path("lib.cc").write_text('#include "api.h"\n'+fixture[1])
        run("g++","-m64","-std="+mode,"-pedantic-errors","-fPIC","-shared","lib.cc","-o","libp.so")
        return sorted(x.split()[-1] for x in run("nm","-D","--defined-only","libp.so").splitlines())

    previous = provider(old,before)
    Path("client.cc").write_text('#include "api.h"\nint main(){'+before[2]+'}')
    run("g++","-m64","-std="+old,"-pedantic-errors","client.cc","-L.","-lp","-Wl,-rpath,$ORIGIN","-o","client")
    run("./client")
    current = provider(new,after)
    result = subprocess.run(["./client"],capture_output=True,text=True,env={**os.environ,"LC_ALL":"C"})
    missing = set(previous)-set(current)
    if result.returncode == 0:
        verdict = "pass"
    elif any(name in result.stderr for name in missing) and "undefined symbol" in result.stderr:
        verdict = "unresolved"
    else:
        raise RuntimeError(result.stderr or str(result.returncode))
    return case,old,new,verdict," ".join(previous)," ".join(current)


def cpp():
    rows = [pair("pair-control",a,b,STABLE,STABLE) for a,b in itertools.product(DIALECTS,repeat=2)]
    callback = ('long take(void (*)() noexcept);',
                'long take(void (*)() noexcept){return 41;}', 'return take(0)!=41;')
    rows.append(pair("noexcept-callback","c++14","c++17",callback,callback))
    utf = ('long take(decltype(u8"x"[0]));','long take(decltype(u8"x"[0])){return 41;}',
           'return take(u8"x"[0])!=41;')
    rows.append(pair("utf8-reference","c++17","c++20",utf,utf))
    save("cpp.csv",["case","old","new","outcome","old_exports","new_exports"],rows)
    print("C++",Counter((r[0],r[3]) for r in rows))


if __name__ == "__main__":
    print(platform.machine(),run("gcc","-dumpfullversion"),run("g++","-dumpfullversion"))
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        layouts()
        calls()
        cpp()

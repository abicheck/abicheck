"""Independent old-client probes for Linux x86-64, GCC/G++ and GNU ld."""
import csv
import itertools
import os
from pathlib import Path
import subprocess
import tempfile

OUT = Path(__file__).with_name("operations.csv").resolve()
rows = []


def execute(client, old, new, cc, mode, name, labels):
    ext = "cc" if cc == "g++" else "c"
    src = Path("provider." + ext)
    app = Path("client." + ext)
    app.write_text(client)
    flags = [cc, "-std=" + mode, "-O1", "-m64"]
    for stage, code in (("baseline", old), ("replacement", new)):
        src.write_text(code)
        subprocess.run([*flags, "-shared", "-fPIC", str(src), "-o", "libp.so"], check=True)
        if stage == "baseline":
            subprocess.run([*flags, str(app), "-L.", "-lp", "-Wl,-rpath,$ORIGIN", "-o", "client"], check=True)
        values = subprocess.check_output(["./client"], text=True).splitlines()
        for label, value in zip(labels, values, strict=True):
            rows.append((name, mode, stage, label, value))


def aggregates():
    api, lib, body, labels = [], [], [], []
    for shape in ("II", "IS", "SI", "SS", "III"):
        fields = ["long" if c == "I" else "double" for c in shape]
        tag = "struct T" + shape
        api.append(tag + " {" + "".join(f"{t} x{i};" for i,t in enumerate(fields)) + "};")
        vals = ",".join(str(11+i) for i in range(len(shape)))
        checksum = "+".join(f"(long)v.x{i}*{10**i}" for i in range(len(shape)))
        for op in ("return", "pointer", "varargs"):
            contexts = itertools.product(range(6),range(9)) if op == "varargs" else [(0,0)]
            for g,s in contexts:
                key = f"{op}_{shape}_{g}_{s}"
                if op == "return":
                    sig = f"{tag} {key}(void)"
                    impl = f"return ({tag}){{{vals}}};"
                    call = f"{tag} v={key}(); long result={checksum};"
                elif op == "pointer":
                    sig = f"long {key}(const {tag} *p)"
                    impl = f"{tag} v=*p; return {checksum};"
                    call = f"{tag} v={{{vals}}}; long result={key}(&v);"
                else:
                    sig = f"long {key}(int marker, ...)"
                    consume = "(void)va_arg(ap,long);"*g + "(void)va_arg(ap,double);"*s
                    impl = f"va_list ap; va_start(ap,marker); {consume} {tag} v=va_arg(ap,{tag}); va_end(ap); return {checksum};"
                    args = ",".join(["1"] + ["7L"]*g + ["9.0"]*s + ["v"])
                    call = f"{tag} v={{{vals}}}; long result={key}({args});"
                api.append(sig+";")
                lib.append(sig+"{"+impl+"}")
                expected = sum((11+i)*10**i for i in range(len(shape)))
                body.append('{'+call+f' if(result!={expected}) return 1; printf("%ld\\n",result);'+'}')
                labels.append(key)
    header = '#include <stdarg.h>\n#include <stdio.h>\n'+'\n'.join(api)
    provider = header+'\n'+'\n'.join(lib)
    execute(header+'\nint main(void){'+'\n'.join(body)+'return 0;}', provider, provider,
            "gcc","c99","aggregate-control",labels)


def reordered_fields():
    for count in (2, 3):
        fields = [f"long f{i};" for i in range(count)]
        def header(order):
            return ("#include <stdarg.h>\n#include <stdio.h>\nstruct T {"+"".join(order)+"};"
                    "struct T give(void); long readptr(struct T*); long readva(int,...);")
        impl = ("struct T give(void){struct T t={.f0=11,.f1=22};return t;}"
                "long readptr(struct T* t){return t->f0;}"
                "long readva(int n,...){va_list a;va_start(a,n);struct T t=va_arg(a,struct T);"
                "va_end(a);return t.f0;}")
        old = header(fields)
        new = header([fields[1],fields[0],*fields[2:]])
        client = old + ('int main(void){struct T t={.f0=11,.f1=22};'
                        'printf("%ld\\n%ld\\n%ld\\n",give().f0,readptr(&t),readva(0,t));return 0;}')
        execute(client,old+impl,new+impl,"gcc","c99",f"reorder-{count}-longs",
                ["return","pointer","varargs"])


def objects():
    for mode in ("c++98","c++03"):
        for change in ("move-base","swap-slots"):
            def header(changed):
                members = "long pad[2];" if changed and change=="move-base" else "long pad;"
                slots = "virtual int b(); virtual int a();" if changed and change=="swap-slots" else "virtual int a(); virtual int b();"
                return ("struct P { virtual int p();"+members+"}; struct V {virtual int v();};"
                        "struct B: virtual V { B();"+slots+"}; struct D:P,B {int a(); int b();};"
                        'extern "C" D* object(); extern "C" B* base(); extern "C" int observe(B*);')
            def provider(h):
                return h + ('int P::p(){return 0;} int V::v(){return 0;} int B::a(){return 11;} int B::b(){return 22;}'
                            'int D::a(){return 33;} int D::b(){return 44;} static int ctor; B::B(){ctor=observe(this);}'
                            'D* object(){static D d;return &d;} B* base(){return object();} extern "C" int during(){return ctor;}')
            client = header(False) + ('\n#include <cstdio>\nextern "C" int during(); int observe(B* p){return p->a();}'
                                     'int main(){D* d=object(); B* b=base(); std::printf("%d\\n%d\\n%d\\n",'
                                     'static_cast<B*>(d)==b,observe(b),during());}')
            execute(client,provider(header(False)),provider(header(True)),"g++",mode,change,
                    ["secondary-base-address","complete-dispatch","construction-dispatch"])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        aggregates()
        reordered_fields()
        objects()
    with OUT.open("w") as stream:
        writer = csv.writer(stream,lineterminator="\n")
        writer.writerow(["case","mode","stage","operation","observed"])
        writer.writerows(rows)

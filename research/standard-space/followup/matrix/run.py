"""From an installed abicheck checkout run PYTHONPATH=$PWD python .../run.py."""
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from abiforge.operators import matrix

OUT = Path(__file__).resolve().parent
CASES = {
    ("struct_swap_fields", "c_core"): ("", "", 'struct point p={11,22};printf("%.0f",scale(p,1));',
                                        "55", "77", "point", ["x", "y"]),
    ("primary_base_grew", "cxx_more"): ('extern "C" MI* object(); extern "C" B2* base();',
        'extern "C" MI* object(){static MI obj;return &obj;} extern "C" B2* base(){return object();}',
        'printf("%d",static_cast<B2*>(object())==base());', "1", "0", "B1", ["pad"]),
    ("virtual_reorder", "cxx_classes"): ('extern "C" Shape* shape();',
        'extern "C" Shape* shape(){static Shape s;s.w=7;return &s;}',
        'printf("%d/%d",shape_area(*shape()),shape()->area());', "70/7", "70/1", "Shape", ["area","kind"]),
}


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root, op, seed):
    decl, adapter, client, before, broken, _, _ = CASES[op.name,seed.name]
    root.mkdir()
    cc, ext = ("gcc","c") if seed.name=="c_core" else ("g++","cc")
    records = []
    for stage in ("old","break","control"):
        d = root/stage
        d.mkdir()
        hdr, src = seed.render(op.edit(seed.roles) if stage=="break" else {})
        if stage=="control":
            # Introduce an internal data object, whose address is never exported.
            # The volatile access forces a real private implementation change.
            if op.name=="virtual_reorder":
                a,b = (seed.roles[k][1] for k in ("shape.area","shape.kind"))
                assert a in src and b in src
                src=src.replace(a,"@swap@").replace(b,a).replace("@swap@",b)
            else:
                pattern="return (p.x * 3 + p.y) * f;" if cc=="gcc" else "int B1::f1() { return 1; }"
                replacement=("static volatile int factor=3; return (p.x * factor + p.y) * f;" if cc=="gcc"
                             else "int B1::f1() { static volatile int one=1; return one; }")
                assert pattern in src
                src=src.replace(pattern,replacement)
        (d/"edit_seed.h").write_text(hdr+"\n"+decl+"\n")
        (d/f"lib.{ext}").write_text(src+"\n"+adapter)
        run([cc,"-g",f"-fdebug-prefix-map={d}=/fixture","-fPIC","-shared","-O1",f"lib.{ext}","-o","libp.so"],cwd=d)
        run(["strip","--strip-unneeded","-o","stripped.so","libp.so"],cwd=d)
        if stage=="old":
            (d/f"client.{ext}").write_text('#include "edit_seed.h"\n#include <stdio.h>\n'+decl+"int main(){"+client+"return 0;}")
            run([cc,"-O1",f"client.{ext}","-L.","-lp","-o","client"],cwd=d)
        output=run([str(root/"old/client")],env={**os.environ,"LD_LIBRARY_PATH":str(d)+":"+os.environ.get("LD_LIBRARY_PATH","")})
        assert output==(broken if stage=="break" else before),(op.name,stage,output)
        records.append(output)
    return records


def compare(job):
    root, name, seed, stage, stripped, headers, oracle = job
    old,new=root/"old",root/stage
    lib="stripped.so" if stripped else "libp.so"
    with tempfile.TemporaryDirectory() as tmp:
        report=Path(tmp)/"result.json"
        args=[sys.executable,"-m","abicheck.cli","compare",str(old/lib),str(new/lib),"-o",f"json={report}"]
        if headers:
            args+=["--header",f"old={old}/edit_seed.h","--header",f"new={new}/edit_seed.h"]
        else:
            args+=["--depth","binary"]
        proc=subprocess.run(args,text=True,capture_output=True)
        if not report.exists():
            raise RuntimeError(proc.stderr+proc.stdout)
        data=json.loads(report.read_text())
        if headers and not re.search(r"L2 public header AST\s+old=present\s+new=present",proc.stderr+proc.stdout):
            raise RuntimeError(proc.stderr)
        owner, members = CASES[name,seed][-2:]
        relevant = [f for f in data["changes"] if f["kind"] in {
            "type_field_offset_changed","type_vtable_changed","type_field_added","type_size_changed",
            "vtable_thunk_offset_changed","base_class_offset_changed"}]
        text = " ".join(f.get("description","") + " " + run(["c++filt",f["symbol"]]) for f in relevant)
        hit = [member for member in members if re.search(r"\b"+member+r"\b",text)]
        score = dict(owner=owner,owner_reported=bool(re.search(r"\b"+owner+r"\b",text)),
                     expected_members=members, reported_members=hit) if stage=="break" else None
        return dict(operator=name,seed=seed,stage=stage,stripped=stripped,headers=headers,
                    old_sha256=sha(old/lib),new_sha256=sha(new/lib),old_client=oracle[0],
                    replacement_client=oracle[1 if stage=="break" else 2],verdict=data["verdict"],
                    attribution=score,exit_code=proc.returncode,assurance=data["analysis_assurance"]["status"],
                    evidence_lines=[line.strip() for line in (proc.stderr+proc.stdout).splitlines() if "public header AST" in line],
                    findings=[{k:c.get(k) for k in ("kind","symbol","description")} for c in data["changes"]])


if __name__=="__main__":
    with tempfile.TemporaryDirectory() as tmp:
        jobs=[]
        for op,seed in matrix():
            if (op.name,seed.name) in CASES:
                root=Path(tmp)/op.name
                output=build(root,op,seed)
                jobs.extend((root,op.name,seed.name,*cell,output) for cell in
                            itertools.product(("break","control"),(False,True),(False,True)))
        assert len(jobs)==24
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(compare,jobs))
        (OUT/"results.json").write_text(json.dumps(results,indent=2)+"\n")

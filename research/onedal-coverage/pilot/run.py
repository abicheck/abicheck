"""Run with python run.py PATH_TO_ONEDAL from this directory."""
import csv, itertools, json, os, re, shutil, subprocess, sys
from pathlib import Path
root = Path.cwd()
src = Path(sys.argv[1]).resolve() / "cpp/daal/include/algorithms/tree_utils"
base, leaf = "tree_utils.h", "tree_utils_regression.h"
rows = []
def call(args, **kw):
    return subprocess.run(args, text=True, **kw)
def build(case, file=None, pair=None, edit=None):
    d = root / "out" / case
    h = d / "algorithms/tree_utils"
    h.mkdir(parents=True, exist_ok=True)
    for name in [base,leaf]:
        shutil.copy(src/name,h/name)
    shutil.copy("probe.hpp",d/"probe.hpp")
    if file:
        p = h/file
        s = p.read_text()
        if pair:
            a,b = pair
            lines = s.splitlines(True)
            def locate(term):
                matches = [i for i,v in enumerate(lines) if re.search(term,v)]
                assert len(matches)==1, (term,matches)
                return matches[0]
            i,j = locate(a),locate(b)
            lines[i],lines[j] = lines[j],lines[i]
            s = ''.join(lines)
        else:
            old,new = edit
            assert s.count(old)==1
            s=s.replace(old,new)
        p.write_text(s)
    shutil.copy("provider.cpp",d/"provider.cpp")
    call(["g++","-std=c++17","-O0","-g","-fPIC","-shared","-I.", "provider.cpp","-o","libpilot.so"],cwd=d,check=True)
    return d
old=build("old")
call(["g++","-std=c++17","-O0","-I"+str(old),"client.cpp","-L"+str(old),"-lpilot","-o","out/client"],check=True)
cases=[("identity",None,None,None)]
for name,file,fields in [("base",base,["level","impurity","nNodeSampleCount"]),("split",base,["featureIndex","featureValue","coverValue"]),("leaf",leaf,["response","coverValue"])]:
    for a,b in itertools.combinations(fields,2):
        cases.append((name+"_"+a+"_"+b,file,(r"^\s*(?:size_t|double) "+a+";",r"^\s*(?:size_t|double) "+b+";"),None))
cases += [("slots",base,(r"^\s*virtual bool onSplitNode",r"^\s*virtual bool onLeafNode"),None),
          ("field_alias",base,None,("double impurity;", "using score_type = double; score_type impurity;")),
          ("nonvirtual",leaf,None,("double response;", "double getResponse() const { return response; }\n    double response;"))]
for name,file,pair,edit in cases:
    d=build(name,file,pair,edit)
    client=call(["out/client"],env={**os.environ,"LD_LIBRARY_PATH":str(d)}).returncode
    for lane in ["symbols","dwarf"] + (["headers"] if shutil.which("clang++") or shutil.which("castxml") else []):
        libs=[]
        for loc in [old,d]:
            lib=loc/"libpilot.so"
            if lane=="symbols":
                target=loc/"stripped/libpilot.so"
                target.parent.mkdir(exist_ok=True)
                call(["strip","--strip-debug","-o",target,lib],check=True)
                lib=target
            libs.append(lib)
        report=d/(lane+".json")
        opts= ["--header","old="+str(old/"probe.hpp"),"--header","new="+str(d/"probe.hpp"),"-I","old="+str(old),"-I","new="+str(d)] if lane=="headers" else []
        with (d/(lane+".log")).open("w") as log:
            rc=call([sys.executable,"-m","abicheck.cli","compare",*libs,*opts,"-o","json="+str(report)],stdout=log,stderr=log).returncode
        data=json.loads(report.read_text()) if report.exists() else {}
        rows.append([name,lane,client,rc,data.get("verdict"),",".join(str(c.get("kind")) for c in data.get("changes",[]))])
        print(rows[-1],flush=True)
with open("results.csv","w") as f:
    w=csv.writer(f); w.writerow(["case","lane","client_exit","checker_exit","verdict","kinds"]); w.writerows(rows)

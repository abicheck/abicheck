"""Replay one historical extractor fix and test two bounded selectors under faults."""
import ast
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DOC = ROOT / "docs/contribute/plans"
FIX = "dac367b706a71d6dc7091c5be1a6bb7e4ec9d387"


def block(file, prefix):
    blocks = re.findall(r"```python\n(.*?)\n```", (DOC / file).read_text(), re.S)
    return next(b for b in blocks if b.startswith(prefix))


def command(args, cwd, **kw):
    return subprocess.check_output(args, cwd=cwd, text=True, **kw)


def modules(path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(path), env.get("PYTHONPATH", "")])
    return env


def selected_arrays():
    path = DOC / "abiforge-array-checker-experiment.md"
    report = json.loads(path.read_text().split("## checker-results.json")[1].split("```json")[1].split("```")[0])
    return [tuple(case) for case in report["selected_lengths"]]


def prepare_arrays(path):
    plan = block("abiforge-space-reduction-research.md", '"""Symbolic residue representatives')
    exp = block("abiforge-array-checker-experiment.md", '"""Compare dense and affine-selected')
    (path / "run.py").write_text(plan)
    (path / "checker.py").write_text(exp.replace("ROOT=HERE.parents[2]", "ROOT=HERE"))
    # The original receipt logs the repository commit from ROOT.
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git","-c","user.email=replay@localhost","-c","user.name=replay",
                    "commit","--allow-empty","-qm","temporary benchmark"],cwd=path,check=True)
    command([sys.executable, "checker.py"], path, env=modules(ROOT))
    helpers = exp.split("\nselected=[]")[0].replace("ROOT=HERE.parents[2]", "ROOT=HERE")
    augment = """
selected = json.loads((HERE/'selected-checker/cases.json').read_text())
profiles.append(('move-first', ('char x;',1,1,0), ('char pad;char x;',2,1,1)))
run('augmented-checker', selected + [(2,32),(4,1)])
"""
    (path/"augment.py").write_text(helpers+augment)
    command([sys.executable,"augment.py"], path, env=modules(ROOT))



def historical_arrays(tmp, suites):
    records = []
    for revision in (FIX+"^", FIX):
        checkout = tmp / ("fixed" if revision == FIX else "parent")
        checkout.mkdir()
        sha = command(["git","rev-parse",revision],ROOT).strip()
        archive = tmp / "checker.tar"
        subprocess.run(["git","archive","--output",str(archive),sha,"abicheck"], cwd=ROOT,check=True)
        subprocess.run(["tar","xf",str(archive)],cwd=checkout,check=True)
        for name,path in suites.items():
            start = perf_counter()
            cases = json.loads((path/"cases.json").read_text())
            output = checkout / "types.json"
            command([sys.executable,"-m","abicheck","dump",str(path/"old.so"),"-o",str(output)],
                    checkout,env=modules(checkout),stderr=subprocess.DEVNULL)
            dump = json.loads(output.read_text())
            types = {t["name"]:t for t in dump["sections"]["types"]["payload"]["types"]}
            mismatches = []
            for index,(profile,n) in enumerate(cases):
                field = next(f for f in types[f"O{index}"]["fields"] if f["name"]=="items")
                if not field["type"].endswith(f"[{n}]"):
                    mismatches.append([profile,n,field["type"]])
            records.append(dict(suite=name,sha=sha,cases=len(cases),wrong_bounds=len(mismatches),
                                first_mismatch=mismatches[:1],seconds=round(perf_counter()-start,3)))
    return records


def array_faults(suites,tmp):
    records=[]
    checkout=tmp/"mutant"
    shutil.copytree(ROOT/"abicheck",checkout/"abicheck",ignore=shutil.ignore_patterns("__pycache__"))
    detector=checkout/"abicheck/diff_types.py"
    source=detector.read_text()
    needle="and f_old.offset_bits != f_new.offset_bits"
    assert source.count(needle)==1
    for fault,guard in [("skip_zero_origin","and f_old.offset_bits != 0"),
                        ("byte_offset_narrowed_to_uint8","and (f_new.offset_bits - f_old.offset_bits) % 2048 != 0")]:
        detector.write_text(source.replace(needle,needle+"\n        "+guard))
        for name,path in suites.items():
            start=perf_counter()
            output=checkout/"report.json"
            cmd=[sys.executable,"-m","abicheck","compare",str(path/"old.json"),str(path/"new.json"),
                 "-o","json="+str(output)]
            proc=subprocess.run(cmd,cwd=checkout,env=modules(checkout),capture_output=True,text=True)
            assert proc.returncode==4,proc.stderr
            def facts(file):
                return {(c["symbol"],c.get("field_name"),c.get("old_value"),c.get("new_value"))
                        for c in json.loads(file.read_text())["changes"] if c["kind"]=="type_field_offset_changed"
                        and c.get("field_name") in ("x","tail","items")}
            baseline=facts(path/"report.json"); changed=facts(output)
            records.append(dict(suite=name,fault=fault,missing=len(baseline-changed),
                                extra=len(changed-baseline),seconds=round(perf_counter()-start,3)))
    return records


def call_faults():
    code=block("abiforge-call-abi-experiment.md",'"""Bounded resource-boundary selection')
    tree=ast.parse(code)
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef)]
    ns={}
    exec(compile(ast.Module(body=functions,type_ignores=[]),"selector","exec"),ns)
    location=ns["location"]
    dense=list(itertools.product(range(9),range(11)))
    selected=sorted({ns["representative"](i,f) for i,f in dense})
    shapes=[("II","DD"),("II","ID"),("ID","DI"),("II","II"),("DD","DD"),("ID","ID"),("NN","II")]
    faults={
        "unused_bank_negative_capacity":("max(0,6-i)","(6-i)"),
        "early_gpr_exhaustion":("max(0,6-i)","max(0,5-i)"),
        "early_sse_exhaustion":("max(0,8-f)","max(0,7-f)"),
        "ignore_sse_pressure":("max(0,8-f)","8"),
    }
    results=[]
    location_src=ast.get_source_segment(code,functions[0])
    for fault,(before,after) in faults.items():
        faulty={}
        src=location_src.replace(before,after)
        if fault=="unused_bank_negative_capacity":
            src=src.replace("max(0,8-f)","(8-f)")
        exec(src,faulty)
        for name,grid in [("dense",dense),("selected",selected),("augmented",selected+[(0,9)])]:
            start=perf_counter();errors=[]
            for (a,b),(i,f) in itertools.product(shapes,grid):
                def changed(fn):
                    return fn(a,i,f)!=fn(b,i,f)
                if changed(location)!=changed(faulty["location"]):
                    errors.append([a,b,i,f])
            results.append(dict(fault=fault,suite=name,checked=len(grid)*len(shapes),
                                mismatches=len(errors),first=errors[:1],seconds=round(perf_counter()-start,6)))
    return results


def main():
    result={"checker_sha":command(["git","rev-parse","HEAD"],ROOT).strip(),
            "gcc":command(["gcc","-dumpfullversion"],ROOT).strip(),"calls":call_faults()}
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)
        prepare_arrays(path)
        suites={k:path/(k+"-checker") for k in ("dense","selected","augmented")}
        for p in suites.values():
            summary=json.loads((p/"summary.json").read_text())
            assert all(not summary[k] for k in ("missing","unexpected","wrong_values","extraction_mismatches","duplicates"))
        assert [tuple(c) for c in json.loads((suites["selected"]/"cases.json").read_text())]==selected_arrays()
        result["arrays_baseline"]={k:json.loads((v/"summary.json").read_text()) for k,v in suites.items()}
        result["arrays_historical"]=historical_arrays(path,suites)
        result["arrays_injected"]=array_faults(suites,path)
    (HERE/"results.json").write_text(json.dumps(result,indent=2)+"\n")


if __name__=="__main__":
    main()

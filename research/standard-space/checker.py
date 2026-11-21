"""Run from the checkout with PYTHONPATH set to its root and abicheck dependencies installed."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import operations
from abiforge.operators import matrix

OUT = Path(__file__).resolve().with_name("checker.json")
results = []
operators = {"struct_swap_fields", "primary_base_grew", "virtual_reorder"}


def compare(name, compiler, flags, versions):
    directories = [Path("old"), Path("new")]
    for directory, (header, source) in zip(directories, versions, strict=True):
        directory.mkdir(exist_ok=True)
        (directory / "edit_seed.h").write_text(header)
        file = "provider.c" if compiler == "gcc" else "provider.cc"
        (directory / file).write_text(source)
        subprocess.run([compiler, *flags, "-O1", "-g", "-shared", "-fPIC", file, "-o", "libp.so"],
                       cwd=directory, check=True)
    for evidence in ("dwarf", "stripped"):
        if evidence == "stripped":
            for directory in directories:
                subprocess.run(["strip", "--strip-unneeded", directory/"libp.so"], check=True)
        report = Path("diff.json")
        report.unlink(missing_ok=True)
        cmd = [sys.executable, "-m", "abicheck.cli", "compare", "old/libp.so", "new/libp.so",
               "--depth", "binary", "-o", "json=diff.json"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if not report.exists():
            raise RuntimeError(proc.stderr + proc.stdout)
        data = json.loads(report.read_text())
        results.append(dict(case=name, evidence=evidence, exit=proc.returncode, verdict=data["verdict"],
                            assurance=data["analysis_assurance"]["status"],
                            findings=sorted({(c["kind"],c.get("symbol","")) for c in data["changes"]})))
        print(name,evidence,data["verdict"],flush=True)


def capture(client, old, new, compiler, mode, name, labels):
    # Reuse the exact source constructors used by operations.py.
    compare(name+"/"+mode, compiler, ["-std="+mode], [("",old),("",new)])


with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    with patch.object(operations, "execute", capture):
        operations.aggregates()
        operations.reordered_fields()
        operations.objects()
    for op, seed in matrix():
        if op.name in operators:
            compare("abiforge/"+seed.name+"/"+op.name,"gcc" if seed.name=="c_core" else "g++",[],
                    [seed.render({}),seed.render(op.edit(seed.roles))])
OUT.write_text(json.dumps(results,indent=2)+"\n")

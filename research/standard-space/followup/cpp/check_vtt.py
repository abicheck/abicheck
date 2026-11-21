"""Run with abicheck and its dependencies on PYTHONPATH."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from run import EXPECTED, HERE, build_provider


def compare(root, label, evidence):
    old, new = [root/name/"libp.so" for name in ("old",label)]
    report = root / f"{label}-{evidence}.json"
    cmd = [sys.executable, "-m", "abicheck.cli", "compare", str(old), str(new),
           "--depth", "binary", "-o", f"json={report}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if not report.exists():
        raise RuntimeError(proc.stderr)
    data = json.loads(report.read_text())
    output = subprocess.check_output([root/"client"], text=True,
                                     env={**os.environ, "LD_LIBRARY_PATH": str(new.parent)})
    observations = tuple(map(int, output.strip().split(",")))
    if observations != EXPECTED["baseline" if label == "identity" else label]:
        raise ValueError((label, observations))
    size, target = vtt_slot(new)
    old_size, old_target = vtt_slot(old)
    if size != old_size or (target == old_target) != (label == "identity"):
        raise ValueError((label, size, target, old_size, old_target))
    return dict(case=label, old_vtt_bytes=old_size, new_vtt_bytes=size,
                old_entry=old_target, new_entry=target, client_values=observations, evidence=evidence, exit=proc.returncode, verdict=data["verdict"],
                assurance=data["analysis_assurance"]["status"],
                findings=[dict(kind=c["kind"],symbol=c.get("symbol")) for c in data["changes"]])


def vtt_slot(lib):
    symbols = subprocess.check_output(["nm","-DS",lib],text=True).splitlines()
    row, = [line.split() for line in symbols if line.endswith(" _ZTT1D")]
    address = int(row[0],16) + 16
    relocs = subprocess.check_output(["readelf","-Wr",lib],text=True).splitlines()
    line, = [line for line in relocs if line.startswith(f"{address:016x} ")]
    return int(row[1],16), " ".join(line.split()[2:])


def main():
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for name in ("old","identity","vtt-entry"):
            (d/name).mkdir()
        build_provider(d/"old",os.getenv("GXX","g++"),"baseline","-g")
        build_provider(d/"vtt-entry",os.getenv("GXX","g++"),"vtt-entry","-g")
        shutil.copy2(d/"old/libp.so",d/"identity/libp.so")
        subprocess.run([os.getenv("GXX","g++"),"-std=c++11","-O0",HERE/"client.cc",
                        "-L"+str(d/"old"),"-lp","-o",d/"client"],check=True)
        for evidence in ("dwarf","stripped"):
            if evidence == "stripped":
                for path in d.glob("*/libp.so"):
                    subprocess.run(["strip","--strip-unneeded",path],check=True)
            for label in ("identity","vtt-entry"):
                rows.append(compare(d,label,evidence))
    (HERE/"checker.json").write_text(json.dumps(rows,indent=2)+"\n")


if __name__ == "__main__":
    main()

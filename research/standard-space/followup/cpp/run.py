"""Run the old-client matrix on Linux AMD64 with GCC, Clang and libstdc++."""
import csv
import itertools
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = {"gcc": os.getenv("GXX", "g++"), "clang": os.getenv("CLANGXX", "clang++")}
COLUMNS = ("ctor_phase", "ctor_base_agrees", "complete_phase", "complete_base_agrees",
           "typeid_matches", "crosscast_marker", "handler", "cleanups")
EXPECTED = {
    "baseline": (20, 1, 30, 1, 1, 40, 1, 1),
    "vtt-entry": (10, 1, 30, 1, 1, 40, 1, 1),
    "private-right": (20, 1, 30, 1, 1, 0, 1, 1),
    "private-error": (20, 1, 30, 1, 1, 40, 2, 1),
}


def build_provider(directory, compiler, variant, debug="-g0"):
    flags = [compiler, "-std=c++11", "-O0", debug, "-fPIC"]
    define = {"private-right": ["-DPRIVATE_RIGHT"], "private-error": ["-DPRIVATE_ERROR"]}
    asm = directory / "provider.s"
    subprocess.run([*flags, *define.get(variant, []), "-S", HERE/"provider.cc", "-o", asm], check=True)
    if variant == "vtt-entry":
        # A single VTT relocation for V during L-in-D construction. Verify both
        # source and replacement address points match this compiled fixture.
        text = asm.read_text()
        prefix, table = text.split("\n_ZTT1D:\n")
        entries = table.splitlines()
        assert entries[2].strip().split() == [".quad", "_ZTC1D0_1L+56"]
        vtable = text.split("\n_ZTV1V:\n")[1].splitlines()
        assert vtable[2].strip().split() == [".quad", "_ZN1V5phaseEv"]
        table = table.replace(entries[2], "\t.quad\t_ZTV1V+16", 1)
        asm.write_text(prefix + "\n_ZTT1D:\n" + table)
    subprocess.run([compiler, "-shared", asm, "-o", directory/"libp.so"], check=True)


def main():
    rows = []
    versions = {k: subprocess.check_output([v, "--version"], text=True).splitlines()[0]
                for k, v in TOOLS.items()}
    for caller, provider in itertools.product(TOOLS, repeat=2):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for variant, expected in EXPECTED.items():
                build_provider(directory, TOOLS[provider], variant)
                if variant == "baseline":
                    subprocess.run([TOOLS[caller], "-std=c++11", "-O0", HERE/"client.cc",
                                    "-L"+tmp, "-lp", "-Wl,-rpath,$ORIGIN", "-o", directory/"client"],
                                   check=True)
                output = subprocess.check_output([directory/"client"], text=True)
                actual = tuple(map(int, output.strip().split(",")))
                if actual != expected:
                    raise RuntimeError((caller, provider, variant, actual, expected))
                rows.append([versions[caller], versions[provider], variant, *actual])
    with (HERE/"results.csv").open("w") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["client_compiler", "provider_compiler", "variant", *COLUMNS])
        writer.writerows(rows)


if __name__ == "__main__":
    main()

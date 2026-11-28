"""Frozen Linux x86-64 clients for PLT binding and virtual deletion."""

import os
import subprocess
from pathlib import Path

from abiforge.model.config import default_config
from abiforge.model.probes import PROBE_ROLES
from abiforge.model.seeds import C_SEED
from abiforge.operators import OP_BY_NAME
from abiforge.run.harness import build_library, label_toolchain


def plt_binding(root: Path):
    config = default_config("c", label_toolchain("gcc"))
    baseline = build_library(C_SEED, config, root / "baseline")
    removed = OP_BY_NAME["func_remove"].edit(C_SEED.roles)
    mutant = build_library(C_SEED, config, root / "removed", overrides=removed)
    source = root / "client.c"
    source.write_text("""#include "edit_seed.h"
#include <unistd.h>
int main(int argc, char **argv) {
    if (write(1, "entered main\\n", 13) != 13) return 2;
    if (argc > 1) return get_count();
    return add_ints(2, 3) != 5;
}
""")
    client = root / "client"
    subprocess.run(
        [
            "gcc",
            "-fplt",
            source,
            "-I",
            baseline.dir,
            "-L",
            baseline.dir,
            "-Wl,-z,lazy",
            "-ledit",
            "-o",
            client,
        ],
        check=True,
    )
    relocations = subprocess.check_output(["readelf", "-Wr", client], text=True)
    if not any(
        "R_X86_64_JUMP_SLOT" in line and " get_count " in line
        for line in relocations.splitlines()
    ):
        raise ValueError("client must import get_count through the PLT")
    results = {}
    for library in (baseline, mutant):
        for binding in ("lazy", "now"):
            env = {**os.environ, "LD_LIBRARY_PATH": str(library.dir), "LC_ALL": "C"}
            env.pop("LD_BIND_NOW", None)
            if binding == "now":
                env["LD_BIND_NOW"] = "1"
            for branch, args in (("keep", []), ("removed", ["call"])):
                run = subprocess.run(
                    [client, *args], env=env, text=True, capture_output=True
                )
                if (
                    run.returncode == 127
                    and "undefined symbol: get_count" not in run.stderr
                ):
                    raise ValueError(run.stderr)
                results[library.dir.name, binding, branch] = run.returncode, run.stdout
    return results


def virtual_deletion(root: Path):
    role = "vt.dtorrm"
    changed = OP_BY_NAME["destructor_became_nonvirtual"].edit(PROBE_ROLES)
    # Extra virtuals keep the old deleting slot inside the concrete vtable.
    # A wrong dispatch reaches a known exit instead of calling past its end.
    provider = """#include "probe.hpp"
#include <cstdio>
#include <cstdlib>
#include <new>
static int destructors, releases;
int VDr::f() { return 9; }
VDr::~VDr() {}
struct Derived final : VDr {
    ~Derived() { ++destructors; }
    static void operator delete(void* p) {
        ++releases;
        ::operator delete(p);
    }
    virtual void first_guard() { std::_Exit(41); }
    virtual void second_guard() {
        std::puts("wrong deleting slot");
        std::fflush(stdout);
        std::_Exit(42);
    }
};
VDr* vdr_make() { return new Derived; }
int destroyed() { return destructors; }
int freed() { return releases; }
"""
    client = root / "delete-client"
    results = {}
    for label, roles in (("baseline", PROBE_ROLES), ("removed", changed)):
        directory = root / label
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "probe.hpp").write_text(
            roles[role][0] + "\nint destroyed(); int freed();\n"
        )
        (directory / "provider.cpp").write_text(provider)
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O0",
                "-shared",
                "-fPIC",
                directory / "provider.cpp",
                "-o",
                directory / "libprobe.so",
            ],
            check=True,
        )
        if label == "baseline":
            source = root / "delete.cpp"
            source.write_text("""#include "probe.hpp"
#include <cstdio>
int main() {
    delete vdr_make();
    std::printf("%d %d\\n", destroyed(), freed());
}
""")
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-O0",
                    source,
                    "-I",
                    directory,
                    "-L",
                    directory,
                    "-lprobe",
                    "-o",
                    client,
                ],
                check=True,
            )
        run = subprocess.run(
            [client],
            env={**os.environ, "LD_LIBRARY_PATH": str(directory)},
            text=True,
            capture_output=True,
        )
        results[label] = run.returncode, run.stdout
    return results

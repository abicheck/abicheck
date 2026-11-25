# SPDX-License-Identifier: Apache-2.0
"""Observe the SharedPtr reaching each oneDAL entry point from a frozen GCC client."""

from __future__ import annotations

import os
import subprocess

# The free-function observer spells out the implicit receiver of nonstatic methods.
PROBES = (
    ("8adaboost", "void", "adaboost::Model*, classifier::ModelPtr p"),
    ("10brownboost", "void", "brownboost::Model*, classifier::ModelPtr p"),
    ("10logitboost", "void", "logitboost::Model*, regression::ModelPtr p"),
    (
        "118MergedNumericTable",
        "services::Status",
        "MergedNumericTable*, NumericTablePtr p",
    ),
    (
        "121RowMergedNumericTable",
        "services::Status",
        "RowMergedNumericTable*, NumericTablePtr p",
    ),
    (
        "SOANumericTable",
        "services::SharedPtr<SOANumericTable>",
        "NumericTableDictionaryPtr p, size_t, NumericTableIface::AllocationFlag, services::Status*",
    ),
)


def observe(prefix, out, symbols, client_source):
    flags = [
        "g++",
        "-std=c++17",
        "-O0",
        f"-I{prefix}/include",
        f"-I{prefix}/include/dal",
    ]
    lib = prefix / "lib"
    # Keeping the caller in a DSO lets the dynamic loader interpose even the
    # two weak inline definitions in the SDK. Main itself contains no SDK code.
    subprocess.run(
        [
            *flags,
            "-DONEDAL_ARGUMENT_CLIENT",
            "-shared",
            "-fPIC",
            client_source,
            lib / "libonedal_core.so.3",
            lib / "libonedal_thread.so.3",
            "-o",
            out / "caller.so",
        ],
        check=True,
    )
    main = out / "main.cpp"
    main.write_text("""#include <cstdlib>
extern "C" int run_argument(int);
extern "C" void *argument_token() { static auto p = std::malloc(64); return p; }
int main(int argc, char **argv) { return argc == 2 ? run_argument(std::atoi(argv[1])) : 64; }
""")
    exe = out / "client"
    subprocess.run(
        [
            "g++",
            main,
            out / "caller.so",
            "-rdynamic",
            f"-Wl,-rpath-link,{lib}",
            "-o",
            exe,
        ],
        check=True,
    )
    source = [
        '#include "daal.h"',
        "#include <cstdlib>",
        "using namespace daal;",
        "using namespace daal::algorithms;",
        "using namespace daal::data_management;",
        'extern "C" void *argument_token();',
    ]
    ordered = []
    for i, (fragment, ret, params) in enumerate(PROBES):
        (symbol,) = (s for s in symbols if fragment in s)
        ordered.append(symbol)
        source.append(f'''{ret} observe{i}({params}) asm("{symbol}");
{ret} observe{i}({params}) {{ std::_Exit(p.get() == argument_token() ? 0 : 41); }}''')
    cpp = out / "observers.cpp"
    cpp.write_text("\n".join(source))
    version_map = out / "symbols.map"
    version_map.write_text("{ global: " + "; ".join(ordered) + "; local: *; };")
    observed = {s: {} for s in ordered}
    for name in ("old", "control", "mutant"):
        directory = out / name
        so = directory / "observer.so"
        subprocess.run(
            [
                flags[0],
                "-I",
                directory,
                "-I",
                directory / "dal",
                *flags[1:],
                "-shared",
                "-fPIC",
                cpp,
                f"-Wl,--version-script={version_map}",
                "-o",
                so,
            ],
            check=True,
        )
        for i, symbol in enumerate(ordered):
            env = {**os.environ, "LD_LIBRARY_PATH": str(lib), "LD_PRELOAD": str(so)}
            observed[symbol][name] = subprocess.run([exe, str(i)], env=env).returncode
    return observed

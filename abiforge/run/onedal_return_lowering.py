# SPDX-License-Identifier: Apache-2.0
"""Verify each remaining SDK return type's lowering using Clang IR on Linux x86-64."""

import argparse
import csv
import os
import re
import subprocess
from pathlib import Path

from .onedal_release_matrix import obligations


def run(prefix, out):
    from clang import cindex as ci

    if os.environ.get("LIBCLANG"):
        ci.Config.set_library_file(os.environ["LIBCLANG"])

    out.mkdir(parents=True, exist_ok=True)
    rows = {
        r["key"]: r
        for r in obligations()
        if r["kind"] == "by_value_return"
        and r["detail"] not in ("services::Status", "data_management::NumericTablePtr")
    }
    headers = sorted(
        {r["header"] for r in rows.values() if r["header"].startswith("oneapi/")}
    )
    includes = '#include "daal.h"\n' + "".join(f'#include "{h}"\n' for h in headers)
    src = out / "returns.cpp"
    src.write_text(includes)
    flags = ["-std=c++17", f"-I{prefix}/include", f"-I{prefix}/include/dal"]
    unit = ci.Index.create().parse(
        str(src),
        args=[
            *flags,
            "-isystem",
            subprocess.check_output(
                ["gcc", "-print-file-name=include"], text=True
            ).strip(),
        ],
        options=ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES,
    )
    errors = [str(d) for d in unit.diagnostics if d.severity >= ci.Diagnostic.Error]
    if errors:
        raise ValueError(errors)
    observed = {}

    def visit(cursor):
        for child in cursor.get_children():
            if child.location.file and not Path(
                child.location.file.name
            ).is_relative_to(prefix / "include"):
                continue
            if child.kind in (ci.CursorKind.CXX_METHOD, ci.CursorKind.FUNCTION_DECL):
                key = child.get_usr()
                if key in rows:
                    observed[key] = child.result_type.get_canonical().spelling
            elif (
                child.kind.is_declaration() or child.kind == ci.CursorKind.LINKAGE_SPEC
            ):
                visit(child)

    visit(unit.cursor)
    if rows.keys() != observed.keys():
        raise ValueError(
            f"unresolved SDK declarations: {sorted(rows.keys() - observed.keys())}"
        )
    types = sorted(set(observed.values()))
    src.write_text(
        includes
        + "".join(
            f'using T{i} = {t}; T{i} source_{i}(); extern "C" T{i} probe_{i}() {{ return source_{i}(); }}\n'
            for i, t in enumerate(types)
        )
    )
    ir = subprocess.check_output(
        [
            "clang++",
            *flags,
            "-Wno-return-type-c-linkage",
            "-S",
            "-emit-llvm",
            "-o",
            "-",
            src,
        ],
        text=True,
    )
    signatures = {
        int(i): (ret.strip(), "sret(" in params)
        for ret, i, params in re.findall(
            r"^define .*?dso_local (.*?) @probe_(\d+)\((.*?)\) [^\n]*\{", ir, re.M
        )
    }
    if len(signatures) != len(types):
        raise ValueError("incomplete IR lowering table")
    with (out / "return-lowering.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["symbol", "sdk_type", "llvm_return", "hidden_result_pointer"])
        for key, row in sorted(rows.items()):
            t = observed[key]
            ret, sret = signatures[types.index(t)]
            writer.writerow([row["symbol"], t, ret, int(sret)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    run(args.prefix.resolve(), args.out)

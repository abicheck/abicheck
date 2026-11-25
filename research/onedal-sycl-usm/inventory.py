"""Inventory macro-dependent public callables visible through the supplied headers."""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

CALLABLES = {"FunctionDecl", "CXXMethodDecl", "CXXConversionDecl", "CXXConstructorDecl"}
RECORDS = {"CXXRecordDecl", "RecordDecl"}


def collect(node, scope, header, out):
    loc = node.get("loc", {})
    loc = loc.get("expansionLoc", loc)
    header = loc.get("file", header)
    kind = node.get("kind")
    name = node.get("name", "")
    if node.get("isImplicit") or name in {"detail", "backend"}:
        return
    if kind in CALLABLES:
        if (
            "/oneapi/dal/" in header
            and "/detail/" not in header
            and "/backend/" not in header
        ):
            out.add(
                (
                    header.split("/oneapi/dal/")[-1],
                    "::".join((*scope, name)),
                    node["type"]["qualType"],
                )
            )
        return
    if kind in RECORDS | {"NamespaceDecl"}:
        scope = (*scope, name)
    elif kind not in {"ClassTemplateDecl", "FunctionTemplateDecl", "LinkageSpecDecl"}:
        return
    access = "private" if node.get("tagUsed") == "class" else "public"
    for child in node.get("inner", []):
        if child["kind"] == "AccessSpecDecl":
            access = child["access"]
        elif access == "public":
            collect(child, scope, header, out)


def inventory(args, enabled):
    flags = ["-DONEDAL_DATA_PARALLEL"] if enabled else ["-UONEDAL_DATA_PARALLEL"]
    cmd = [
        args.compiler,
        "-std=c++17",
        "-fsycl",
        "--offload-host-only",
        *flags,
        "-I" + args.include,
        *args.extra_flag,
        "-fsyntax-only",
        "-Xclang",
        "-ast-dump=json",
        "-Xclang",
        "-ast-dump-filter=oneapi::dal",
        "-xc++",
        "-",
    ]
    source = "".join(f"#include <{h}>\n" for h in args.headers)
    with subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as proc:
        proc.stdin.write(source)
        proc.stdin.close()
        result = set()
        block = []
        for line in proc.stdout:
            block.append(line)
            if line.rstrip() == "}":
                collect(json.loads("".join(block)), ("oneapi",), "", result)
                block.clear()
        if proc.wait() or block or not result:
            raise RuntimeError("incomplete host AST")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("compiler")
    parser.add_argument("include")
    parser.add_argument("headers", nargs="+")
    parser.add_argument(
        "--extra-flag",
        action="append",
        default=[],
        help="additional compile context for installed SDK roots",
    )
    args = parser.parse_args()
    args.include = str(Path(args.include).resolve())
    for header in args.headers:
        if not (Path(args.include) / header).is_file():
            parser.error(f"missing requested SDK header: {header}")
    host = inventory(args, False)
    parallel = inventory(args, True)
    writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    writer.writerow(("header", "qualified_name", "signature"))
    writer.writerows(sorted(parallel - host))

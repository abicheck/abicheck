# SPDX-License-Identifier: Apache-2.0
"""Locate concrete frozen-client imports in the SDK using the compiler's C++ AST."""

import argparse
import json
import subprocess
from pathlib import Path

from .onedal_release_matrix import PRODUCT

PROBES = PRODUCT.parents[1] / "onedal-sycl-usm"


def ast(compiler, include, source, selector, extra):
    cmd = [
        compiler,
        "-std=c++17",
        "-fsyntax-only",
        "-I",
        include,
        "-I",
        include / "dal",
        *extra,
        "-Wno-deprecated-declarations",
        "-Xclang",
        "-ast-dump=json",
        "-Xclang",
        "-ast-dump-filter=" + selector,
        "-xc++",
        "-",
    ]
    with subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as process:
        process.stdin.write(source)
        process.stdin.close()
        block = []
        for line in process.stdout:
            block.append(line)
            if line.rstrip() == "}":
                yield json.loads("".join(block))
                block.clear()
        if process.wait() or block:
            raise ValueError(f"incomplete compiler AST for {selector}")


def run(sdk, compiler, out, mpi_include):
    include = sdk.resolve() / "include"
    targets = json.loads((PRODUCT / "export-surface.json").read_text())[
        "sdk_inventory"
    ]["client_imports_outside_sdk"]
    found = {}
    files = {}

    def visit(node, inherited, names, record=None):
        loc = node.get("loc", {})
        loc = loc.get("spellingLoc", loc)
        filename = loc.get("file", inherited)
        symbol = node.get("mangledName")
        if (
            record
            and node.get("kind") == "CXXRecordDecl"
            and node.get("completeDefinition")
            and node.get("name") == record[1]
        ):
            symbol = record[0]
        if symbol in names and filename and Path(filename).is_relative_to(include):
            path = Path(filename)
            if path not in files:
                files[path] = path.read_bytes()
            offset = loc.get("offset")
            if offset is not None:
                token = files[path][offset : offset + loc["tokLen"]].decode()
                if (
                    token == node.get("name")
                    or node.get("name", "").startswith(token + "<")
                    or (
                        token == "operator"
                        and node.get("name", "").startswith("operator")
                    )
                ):
                    found[symbol] = {
                        "header": str(path.relative_to(include)),
                        "line": files[path].count(b"\n", 0, offset) + 1,
                        "declaration": node["name"],
                        "type": node.get("type", {}).get("qualType", node["kind"]),
                    }
        for child in node.get("inner", []):
            visit(child, filename, names, record)

    rows = {}
    for provider, names in targets.items():
        if not names:
            continue
        names = set(names)
        found.clear()
        if "_core" in provider:
            source = (
                PRODUCT.parents[2] / "abiforge/fixtures/onedal/release_client.cpp"
            ).read_text()
            for tree in ast(
                compiler, include, source, "SerializableKeyValueCollection", []
            ):
                visit(tree, "", names)
            for symbol in sorted(names):
                if symbol.startswith(("_ZTI", "_ZTV")):
                    owner = (
                        subprocess.check_output(["c++filt", symbol], text=True)
                        .strip()
                        .split(" for ")[1]
                    )
                    for tree in ast(compiler, include, source, owner, []):
                        visit(tree, "", names, (symbol, owner.split("::")[-1]))
        else:
            device = "_dpc" in provider
            clients = ["layout", "sparse_indexing"]
            if device:
                clients += ["buffer", "csr", "event", "dependency", "ownership", "spmd"]
            source = "".join(
                f'#define main entry_{c}\n#include "{PROBES / (c + "_consumer.cpp")}"\n#undef main\n'
                for c in clients
            )
            extra = (
                [
                    "-fsycl",
                    "--offload-host-only",
                    "-DONEDAL_DATA_PARALLEL",
                    "-I",
                    mpi_include,
                ]
                if device
                else []
            )
            for tree in ast(compiler, include, source, "oneapi::dal", extra):
                visit(tree, "", names)
        rows[provider] = dict(sorted(found.items()))
        print(provider, len(found), "/", len(names), flush=True)
        for missing in sorted(names - found.keys()):
            print(missing, flush=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")
    return all(
        set(rows.get(provider, {})) == set(names) for provider, names in targets.items()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("sdk", "compiler", "mpi_include", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    raise SystemExit(not run(args.sdk, args.compiler, args.out, args.mpi_include))

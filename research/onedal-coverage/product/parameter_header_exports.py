"""Probe standalone CPU parameter headers and find their exported call operators."""

import argparse
import csv
import json
import os
import subprocess
from pathlib import Path


def audit(prefix, roots):
    include = prefix / "include"
    libraries = sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")})
    if not libraries:
        raise ValueError(f"no providers: {prefix}")
    rows = {}
    for root in roots:
        header = include / root
        if not header.is_file():
            raise ValueError(f"missing root: {header}")
        compiled = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-fsyntax-only",
                "-x",
                "c++",
                "-I",
                include,
                "-I",
                include / "dal",
                "-",
            ],
            input=f"#include <{root}>\n",
            text=True,
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        errors = [
            s.partition("error: ")[2]
            for s in compiled.stderr.splitlines()
            if "error: " in s
        ]
        rows[root] = {
            "compile_exit": compiled.returncode,
            "first_error": next(iter(errors), ""),
            "providers": {},
        }
    for library in libraries:
        output = subprocess.check_output(
            ["nm", "-D", "-C", "--defined-only", library], text=True
        )
        names = [
            fields[2]
            for line in output.splitlines()
            if len(fields := line.split(maxsplit=2)) == 3
        ]
        for root, row in rows.items():
            header = Path(root)
            algorithm = header.parents[2].name
            prefix_name = f"oneapi::dal::{algorithm}::parameters::{header.stem}_cpu<"
            matches = sorted(
                n for n in names if n.startswith(prefix_name) and ">::operator()(" in n
            )
            if matches:
                row["providers"][library.name] = matches
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with Path(__file__).with_name("unparsed.csv").open() as stream:
        roots = [row["root"] for row in csv.DictReader(stream)]
    releases = {}
    for prefix in args.prefix:
        if prefix.name in releases:
            raise ValueError(f"duplicate release label: {prefix.name}")
        releases[prefix.name] = audit(prefix, roots)
    args.out.write_text(json.dumps(releases, sort_keys=True, indent=2) + "\n")

"""Exercise every single-record field permutation and slot order of the oneDAL tree visitors.

python -m abiforge.run.onedal_tree OUT --source PATH_TO_ONEDAL
The library is a bridge compiled against the product declarations. Each old
client checks callback identity, order, and every field it receives. The L2
checker gets the stripped bridge and its corresponding headers.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
from pathlib import Path

from abiforge.checkers import abicheck_cli

_BASE = ("level", "impurity", "nNodeSampleCount")
_SPLIT = ("featureIndex", "featureValue", "coverValue")
_LEAVES = {
    "regression": ("response", "coverValue"),
    "classification": ("label", "prob", "cover"),
}
_SLOTS = ("onSplitNode", "onLeafNode")
_HEADER_DIR = Path("algorithms/tree_utils")


def cases(task: str):
    yield "identity", (), ()
    for group, members in (
        ("base", _BASE),
        ("split", _SPLIT),
        ("leaf", _LEAVES[task]),
        ("slots", _SLOTS),
    ):
        if group != "slots":
            yield group, members, (*members, "extra")
        for order in itertools.permutations(members):
            if order != members:
                yield group, members, order


def reorder(header: str, members: tuple[str, ...], order: tuple[str, ...]) -> str:
    lines = header.splitlines(keepends=True)
    positions = []
    for member in members:
        marker = (
            rf"^\s*virtual bool {member}\("
            if members == _SLOTS
            else rf"^\s*(?:size_t|double|const double\s*\*)\s*{member};"
        )
        matches = [i for i, line in enumerate(lines) if re.search(marker, line)]
        if len(matches) != 1:
            raise ValueError(
                f"expected one declaration for {member}, found {len(matches)}"
            )
        positions.append(matches[0])
    if order[-1:] == ("extra",):
        lines.insert(positions[-1] + 1, "    size_t extra;\n")
        return "".join(lines)
    originals = dict(zip(members, (lines[i] for i in positions), strict=True))
    for pos, member in zip(positions, order, strict=True):
        lines[pos] = originals[member]
    return "".join(lines)


def _sources(task: str) -> tuple[str, str, str]:
    header = f"""#pragma once
#include <stddef.h>
#define DAAL_EXPORT
#include "algorithms/tree_utils/tree_utils_{task}.h"
namespace tree = daal::algorithms::tree_utils;
using Visitor = tree::{task}::TreeNodeVisitor;
using Split = tree::SplitNodeDescriptor;
using Leaf = tree::{task}::LeafNodeDescriptor;
const Split* split_array();
const Leaf* leaf_array();
bool feed(Visitor&, const double*, const double*);
"""
    values = ("11.25", "13.25") if task == "regression" else ("11", "prob", "cover")
    leaf = dict(zip(_LEAVES[task], values, strict=True))
    split = dict(
        zip(_BASE + _SPLIT, ("3", "1.5", "42", "7", "4.5", "8.5"), strict=True)
    )
    leaf.update({name: split[name] for name in _BASE})

    def assign(var, fields):
        return " ".join(f"{var}.{k} = {v};" for k, v in fields.items())

    def check(fields):
        return " && ".join(f"x.{k} == {v}" for k, v in fields.items())

    provider = f"""#include "api.hpp"
const Split* split_array() {{ static Split x[2]{{}}; x[1].level = 37; return x; }}
const Leaf* leaf_array() {{ static Leaf x[2]{{}}; x[1].level = 53; return x; }}
bool feed(Visitor& v, const double* prob, const double* cover) {{
    Split s{{}}; Leaf l{{}};
    {assign("s", split)}
    {assign("l", leaf)}
    return v.onSplitNode(s) && v.onLeafNode(l);
}}
"""
    client = f"""#include "api.hpp"
struct Client : Visitor {{
    const double *prob, *cover;
    int calls = 0;
    bool onSplitNode(const Split& x) override {{ return ++calls == 1 && {check(split)}; }}
    bool onLeafNode(const Leaf& x) override {{ return ++calls == 2 && {check(leaf)}; }}
}};
int main() {{
    double a = 1, b = 2; Client c; c.prob = &a; c.cover = &b;
    if (split_array()[1].level != 37 || leaf_array()[1].level != 53) return 2;
    return feed(c, &a, &b) && c.calls == 2 ? 0 : 1;
}}
"""
    return header, provider, client


def exact_changes(report, group, members, order):
    """Check x86-64 bit offsets and virtual slots independently of the verdict."""
    if not any(
        layer["layer"] == "L2" and layer["status"] == "present"
        for layer in report["layer_coverage"]
    ):
        return False
    if not order:
        return not report["changes"]
    if order[-1:] == ("extra",):
        owner = {
            "base": "NodeDescriptor",
            "split": "SplitNodeDescriptor",
            "leaf": "LeafNodeDescriptor",
        }[group]
        bits = 64 * (len(members) + (0 if group == "base" else len(_BASE)))
        return any(
            c["kind"] == "type_size_changed"
            and c["symbol"] == owner
            and (c["old_value"], c["new_value"]) == (str(bits), str(bits + 64))
            for c in report["changes"]
        )
    if group == "slots":
        actual = [
            (c["symbol"], c["old_value"], c["new_value"])
            for c in report["changes"]
            if c["kind"] == "type_vtable_changed" and c["symbol"] in members
        ]
        expected = [
            (m, f"position {i}", f"position {order.index(m)}")
            for i, m in enumerate(members)
        ]
    else:
        owner = {
            "base": "NodeDescriptor",
            "split": "SplitNodeDescriptor",
            "leaf": "LeafNodeDescriptor",
        }[group]
        start = 0 if group == "base" else len(_BASE)
        actual = [
            (c["field_name"], c["old_value"], c["new_value"])
            for c in report["changes"]
            if c["kind"] == "type_field_offset_changed" and c["symbol"] == owner
        ]
        expected = [
            (m, str(64 * (start + i)), str(64 * (start + order.index(m))))
            for i, m in enumerate(members)
            if order.index(m) != i
        ]
    return sorted(expected) == sorted(actual)


def run(out: Path, source: Path) -> list[dict]:
    src = source / "cpp/daal/include" / _HEADER_DIR
    rows = []
    for task in _LEAVES:
        header, provider, client = _sources(task)
        leaf = f"tree_utils_{task}.h"
        originals = {name: (src / name).read_text() for name in ("tree_utils.h", leaf)}
        old = out / task / "identity"
        exe = old / "client"
        for group, members, order in cases(task):
            name = "_".join((group, *order))
            path = out / task / name
            dest = path / _HEADER_DIR
            dest.mkdir(parents=True, exist_ok=True)
            for file, original in originals.items():
                changed = file == (leaf if group == "leaf" else "tree_utils.h")
                (dest / file).write_text(
                    reorder(original, members, order) if changed else original
                )
            (path / "api.hpp").write_text(header)
            (path / "provider.cpp").write_text(provider)
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-O0",
                    "-shared",
                    "-fPIC",
                    "-s",
                    "-I.",
                    "provider.cpp",
                    "-o",
                    "libtree.so",
                ],
                cwd=path,
                check=True,
            )
            if path == old:
                (old / "client.cpp").write_text(client)
                subprocess.run(
                    [
                        "g++",
                        "-std=c++17",
                        "-I.",
                        "client.cpp",
                        "-L.",
                        "-ltree",
                        "-o",
                        "client",
                    ],
                    cwd=old,
                    check=True,
                )
            rc = subprocess.run(
                [exe], env={**os.environ, "LD_LIBRARY_PATH": str(path)}
            ).returncode
            report_path = path / "compare.json"
            report_path.unlink(missing_ok=True)
            result = abicheck_cli.run(
                old / "libtree.so",
                path / "libtree.so",
                path,
                headers=([old / "api.hpp"], [path / "api.hpp"]),
                includes=(old, path),
                stem="compare",
            )
            report = abicheck_cli.parse(report_path.read_text())
            broken = bool(order)
            expected = (
                (
                    "BREAKING",
                    4,
                    2
                    if order[-1:] == ("extra",)
                    or (group == "base" and order[0] != "level")
                    else 1,
                )
                if broken
                else ("NO_CHANGE", 0, 0)
            )
            observed = (report["verdict"], result.steps[-1].exit, rc)
            rows.append(
                dict(
                    task=task,
                    case=name,
                    client_exit=rc,
                    verdict=report["verdict"],
                    checker_exit=result.steps[-1].exit,
                    matches=observed == expected
                    and exact_changes(report, group, members, order),
                )
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    rows = run(args.out.resolve(), args.source)
    (args.out / "receipt.json").write_text(json.dumps(rows, indent=2) + "\n")
    return int(any(not row["matches"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())

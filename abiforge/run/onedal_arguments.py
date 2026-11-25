# SPDX-License-Identifier: Apache-2.0
"""Compile the ledger's oneDAL by-value argument calls and inspect their SysV lowering.

Run on x86-64 Linux with Clang and the pinned oneDAL CPU development package.
The negative control edits only SharedPtr's copy constructor and destructor.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path

from .onedal_argument_checker import compare_arguments
from .onedal_argument_client import observe

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "abiforge/fixtures/onedal/value_arguments.cpp"
LEDGER = ROOT / "research/onedal-coverage/product/ledger.csv.gz"


def mutate(header):
    edits = [
        (
            "SharedPtr(const SharedPtr<T> & ptr);",
            "SharedPtr(const SharedPtr<T> & ptr) = default;",
        ),
        ("~SharedPtr() { _remove(); }", "~SharedPtr() = default;"),
    ]
    for old, new in edits:
        if header.count(old) != 1:
            raise ValueError(f"unexpected SharedPtr declaration for {old}")
        header = header.replace(old, new)
    pattern = r"template <class T>\nSharedPtr<T>::SharedPtr\(const SharedPtr<T> & other\) :[^\n]*\n\{[^{}]*\n\}\n"
    header, count = re.subn(pattern, "", header)
    if count != 1:
        raise ValueError("expected one out-of-line SharedPtr copy constructor")
    return header


def compile_calls(prefix, work, header, symbols):
    overlay = work / "dal/services"
    overlay.mkdir(parents=True, exist_ok=True)
    (overlay / "daal_shared_ptr.h").write_text(header)
    ir = work / "calls.ll"
    subprocess.run(
        [
            "clang++",
            "-std=c++17",
            "-O0",
            "-S",
            "-emit-llvm",
            "-Wno-deprecated-declarations",
            "-I",
            work,
            "-I",
            work / "dal",
            "-I",
            prefix / "include",
            "-I",
            prefix / "include/dal",
            CLIENT,
            "-o",
            ir,
        ],
        check=True,
    )
    lines = ir.read_text().splitlines()
    if not any(
        re.match(r'target triple = "x86_64-[^-]+-linux-', line) for line in lines
    ):
        raise ValueError("campaign requires the x86-64 Linux ABI")
    result = {}
    for symbol in symbols:
        calls = [
            line
            for line in lines
            if f"@{symbol}(" in line and re.search(r"\b(call|invoke)\b", line)
        ]
        if not calls:
            raise ValueError(f"missing call to {symbol}")
        # Exactly one source argument is a SharedPtr in each of these six signatures.
        # sret refers to the separate result slot. A byval pointer denotes a stack copy.
        signatures = set()
        for call in calls:
            operands = call.split(f"@{symbol}(", 1)[1].split(") to label", 1)[0]
            args = operands.split(", ")
            # The member receiver and any sret slot precede the record argument.
            index = int("sret(" in args[0]) + int(
                "SOANumericTable6create" not in symbol
            )
            arg = args[index]
            if "byval(" in arg:
                signatures.add("stack-by-value")
            elif re.fullmatch(r"ptr noundef %[-.\w]+\)?", arg.strip()):
                signatures.add("indirect")
            else:
                raise ValueError(f"unrecognized argument lowering in {call}")
        if len(signatures) != 1:
            raise ValueError(f"conflicting calls for {symbol}")
        result[symbol] = signatures.pop()
    return result


def argument_inventory():
    rows = {}
    for ledger in (LEDGER, LEDGER.with_name("extended-obligations.csv.gz")):
        with gzip.open(ledger, "rt") as stream:
            for row in csv.DictReader(stream):
                match = re.fullmatch(r"by_value_arg(\d+)", row["kind"])
                if match:
                    rows[row["key"], match[1], row["symbol"]] = row
    inventory = {}
    symbols = set()
    for (_, position, symbol), row in rows.items():
        counts = inventory.setdefault(position, {"declarations": 0, "exported": 0})
        counts["declarations"] += 1
        if row["providers"]:
            counts["exported"] += 1
            if position != "0":
                raise ValueError(f"add an executable p{position} witness for {symbol}")
            symbols.add(symbol)
    return inventory, symbols


def run(prefix, output, check=False):
    inventory, symbols = argument_inventory()
    if len(symbols) != 6:
        raise ValueError("review the campaign against the changed ledger")
    header = (prefix / "include/dal/services/daal_shared_ptr.h").read_text()
    variants = {"old": header, "control": header + "\n", "mutant": mutate(header)}
    evidence = {
        name: compile_calls(prefix, output / name, text, symbols)
        for name, text in variants.items()
    }
    rows = {s: {k: v[s] for k, v in evidence.items()} for s in sorted(symbols)}
    receipt = {
        "compiler": subprocess.check_output(
            ["clang++", "--version"], text=True
        ).splitlines()[0],
        "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
        "calls": rows,
        "positions": inventory,
    }
    runtime = observe(prefix, output, symbols, CLIENT)
    receipt["client_exits"] = runtime
    passed = all(v == {"old": 0, "control": 0, "mutant": 41} for v in runtime.values())
    if check:
        result = compare_arguments(prefix, output, CLIENT, symbols)
        receipt["checker"] = result
        passed &= result["control"] == {
            "verdict": "NO_CHANGE",
            "exit": 0,
            "arguments": {},
        }
        passed &= (
            result["mutant"]["exit"] == 4 and result["mutant"]["verdict"] == "BREAKING"
        )
        passed &= result["mutant"]["arguments"] == {
            s: ["p0:nontrivial", "p0:trivial"] for s in symbols
        }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return passed and all(
        r == {"old": "indirect", "control": "indirect", "mutant": "stack-by-value"}
        for r in rows.values()
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="also compare all three SDK header snapshots with the public L2 checker",
    )
    args = parser.parse_args()
    raise SystemExit(not run(args.prefix.resolve(), args.out.resolve(), args.check))

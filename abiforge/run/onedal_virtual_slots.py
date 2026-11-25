# SPDX-License-Identifier: Apache-2.0
"""Map inventory virtual methods to observed ELF vtable relocations by table and byte offset."""

import argparse
import bisect
import csv
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from elftools.elf.elffile import ELFFile

from .onedal_release_matrix import obligations


def cells(path, wanted):
    positions = defaultdict(set)
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        if elf.elfclass != 64 or elf.header.e_machine != "EM_X86_64":
            raise ValueError("requires an x86-64 ELF provider")
        dyn = elf.get_section_by_name(".dynsym")
        tables = []
        functions = defaultdict(set)
        for sym in dyn.iter_symbols():
            if sym["st_shndx"] == "SHN_UNDEF":
                continue
            addr = sym["st_value"]
            if sym.name.startswith("_ZTV") and sym["st_size"]:
                tables.append((addr, addr + sym["st_size"], sym.name))
            if sym["st_info"]["type"] == "STT_FUNC":
                functions[addr].add(sym.name)
        tables.sort()
        starts = [t[0] for t in tables]
        rel = elf.get_section_by_name(".rela.dyn")
        if rel is None:
            return positions
        for entry in rel.iter_relocations():
            offset = entry["r_offset"]
            i = bisect.bisect_right(starts, offset) - 1
            if i < 0 or offset >= tables[i][1]:
                continue
            if entry["r_info_type"] == 1 and entry["r_addend"] == 0:
                symbol = dyn.get_symbol(entry["r_info_sym"]).name
            elif entry["r_info_type"] == 8:
                targets = functions.get(entry["r_addend"], set())
                if len(targets) != 1:
                    continue
                symbol = next(iter(targets))
            else:
                continue
            if symbol in wanted:
                positions[symbol].add((tables[i][2], offset - starts[i]))
    return positions


def run(prefixes, output):
    targets = defaultdict(set)
    for row in obligations():
        if row["kind"] == "virtual_slot":
            for provider in row["providers"].split(","):
                targets[provider].add(row["symbol"])
    with output.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["release", "provider", "symbol", "vtable", "byte_offset"])
        for i, prefix in enumerate(prefixes):
            for provider, wanted in sorted(targets.items()):
                image = provider if i < 2 else provider.removesuffix(".3") + ".4"
                observed = cells(prefix / "lib" / image, wanted)
                for symbol in sorted(wanted):
                    for table, offset in sorted(observed.get(symbol, {("", "")})):
                        writer.writerow([prefix.name, image, symbol, table, offset])

    src = Path(__file__).resolve().parents[1] / "fixtures/onedal/header_slots.cpp"
    with (
        tempfile.TemporaryDirectory() as work,
        output.with_name("header-slots.csv").open("w", newline="") as stream,
    ):
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["release", "symbol", "byte_offset_from_address_point"])
        for prefix in prefixes[:2]:
            exe = Path(work) / "header_slots"
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-Wno-deprecated-declarations",
                    f"-I{prefix}/include",
                    f"-I{prefix}/include/dal",
                    src,
                    "-o",
                    exe,
                ],
                check=True,
            )
            for symbol, offset in csv.reader(
                subprocess.check_output([exe], text=True).splitlines()
            ):
                writer.writerow([prefix.name, symbol, int(offset)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs=3, type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.prefixes, args.output)

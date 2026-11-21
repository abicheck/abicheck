"""Measure a fixture record's layout two independent ways: compiler probe and raw DWARF.

``probe`` compiles and runs a C program that, for each named member, zeroes
the whole object, assigns the member all-ones by value, copies the object's
bytes with ``memcpy`` and reports the contiguous bit range that changed
(little-endian bit numbering); ``sizeof``/``_Alignof`` come from compiled
expressions. No member address and no ``offsetof`` is taken, so bit-fields
are measured exactly as the language allows.

``dwarf`` builds a shared library with ``-g`` and reads GNU ``readelf``'s
dump: ``DW_AT_byte_size`` of the record, ``DW_AT_data_bit_offset`` /
``DW_AT_bit_size`` of each bit-field and ``DW_AT_data_member_location`` of
each ordinary member (converted from bytes to bits). DWARF carries no record
alignment here, so ``dwarf`` leaves it ``None`` (unknown), never a guess.

Neither reads abicheck; both are oracles for ``model/layout_model``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from abiforge.model.layout_model import Record

__all__ = ["Measured", "dwarf", "probe"]


@dataclass(frozen=True)
class Measured:
    size: int  # bits
    align: int | None  # bits; None = not established by this method
    offsets: dict[str, int]
    widths: dict[str, int]


def _flags(pack: int | None, opt: str) -> list[str]:
    return [f"-{opt}", *([f"-fpack-struct={pack}"] if pack is not None else [])]


def _decls(records: tuple[Record, ...]) -> str:
    return "#include <stdint.h>\n" + "\n".join(r.render() for r in records) + "\n"


def probe(
    cc: str,
    records: tuple[Record, ...],
    *,
    pack: int | None,
    work: Path,
    opt: str = "O0",
) -> Measured:
    top = records[-1]
    lines = [
        "#include <stdio.h>",
        "#include <string.h>",
        _decls(records),
        "static void rng(const char *n, const unsigned char *m, size_t sz) {",
        "  int lo = -1, hi = -1, i;",
        "  for (i = 0; i < (int)(8 * sz); i++)",
        "    if ((m[i / 8] >> (i % 8)) & 1) { if (lo < 0) lo = i; hi = i; }",
        '  printf("%s %d %d\\n", n, lo, hi - lo + 1);',
        "}",
        "int main(void) {",
        f"  {top.spelling} x; unsigned char m[sizeof x];",
    ]
    for mem in top.members:
        if mem.name is None or mem.extent is not None or mem.ctype.startswith("union "):
            continue
        lines += [
            "  memset(&x, 0, sizeof x);",
            f"  x.{mem.name} = ({mem.ctype})~0ull;",
            "  memcpy(m, &x, sizeof x);",
            f'  rng("{mem.name}", m, sizeof x);',
        ]
    lines += [
        f'  printf("@size %zu\\n@align %zu\\n", sizeof x, _Alignof({top.spelling}));',
        "  return 0;",
        "}",
    ]
    work.mkdir(parents=True, exist_ok=True)
    src, exe = work / "probe.c", work / "probe"
    src.write_text("\n".join(lines))
    subprocess.run(
        [cc, "-std=c11", "-w", *_flags(pack, opt), str(src), "-o", str(exe)],
        check=True,
        capture_output=True,
        text=True,
    )
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    offsets: dict[str, int] = {}
    widths: dict[str, int] = {}
    size = align = 0
    for line in out.splitlines():
        name, *vals = line.split()
        if name == "@size":
            size = 8 * int(vals[0])
        elif name == "@align":
            align = 8 * int(vals[0])
        else:
            lo, n = int(vals[0]), int(vals[1])
            if lo < 0:
                raise RuntimeError(f"probe saw no bit of {name}")
            offsets[name] = lo
            mem = next(m for m in top.members if m.name == name)
            if mem.width is not None:
                widths[name] = n
    return Measured(size, align, offsets, widths)


_DIE = re.compile(r"<(\d+)><[0-9a-f]+>: Abbrev Number: \d+ \((DW_TAG_\w+)\)")
_ATTR = re.compile(r"\s+<[0-9a-f]+>\s+(DW_AT_\w+)\s*:\s*(.*)")


def dwarf(
    cc: str,
    records: tuple[Record, ...],
    *,
    pack: int | None,
    work: Path,
    opt: str = "O0",
) -> Measured:
    top = records[-1]
    work.mkdir(parents=True, exist_ok=True)
    src, so = work / "lib.c", work / "lib.so"
    src.write_text(
        _decls(records)
        + f"unsigned long lf_probe_use({top.spelling} *p) {{ return sizeof *p; }}\n"
    )
    subprocess.run(
        [
            cc,
            "-std=c11",
            "-w",
            "-g",
            "-gdwarf-5",
            "-shared",
            "-fPIC",
            *_flags(pack, opt),
            str(src),
            "-o",
            str(so),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dump = subprocess.run(
        ["readelf", "--debug-dump=info", str(so)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # Walk the DIEs: find the top record, then its direct members.
    size: int | None = None
    offsets: dict[str, int] = {}
    widths: dict[str, int] = {}
    in_top = False
    top_depth = -1
    cur: dict[str, str] = {}
    cur_tag = ""
    cur_depth = -1

    def close() -> None:
        nonlocal size
        if (
            cur_tag in ("DW_TAG_structure_type", "DW_TAG_union_type")
            and cur.get("DW_AT_name", "").endswith(top.name)
            and "DW_AT_byte_size" in cur
        ):
            size = 8 * int(cur["DW_AT_byte_size"])
        if in_top and cur_tag == "DW_TAG_member" and cur_depth == top_depth + 1:
            name = cur.get("DW_AT_name", "").split(":")[-1].strip()
            if "DW_AT_data_bit_offset" in cur:
                offsets[name] = int(cur["DW_AT_data_bit_offset"])
                widths[name] = int(cur["DW_AT_bit_size"])
            elif "DW_AT_data_member_location" in cur:
                offsets[name] = 8 * int(cur["DW_AT_data_member_location"])
            elif top.tag == "union":
                offsets[name] = 0

    for line in dump.splitlines():
        if m := _DIE.search(line):
            close()
            cur, cur_tag, cur_depth = {}, m.group(2), int(m.group(1))
            if in_top and cur_depth <= top_depth:
                in_top = False
            continue
        if m := _ATTR.match(line):
            cur[m.group(1)] = m.group(2).strip()
            if (
                m.group(1) == "DW_AT_name"
                and cur_tag in ("DW_TAG_structure_type", "DW_TAG_union_type")
                and m.group(2).strip().split(":")[-1].strip() == top.name
            ):
                in_top, top_depth = True, cur_depth
    close()
    if size is None:
        raise RuntimeError(f"no DWARF record {top.name} in {so}")
    return Measured(size, None, offsets, widths)

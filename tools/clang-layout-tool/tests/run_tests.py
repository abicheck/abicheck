#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hand-verified correctness tests for abicheck-clang-layout-tool.

Runs the compiled binary against each fixture in tests/fixtures/ and asserts
specific offsets/sizes computed by hand against the real Itanium x86-64 C++
ABI (see each assertion's comment for the reasoning). No castxml/libabigail
was available to cross-check against when these were written, so correctness
was instead confirmed by observing where a FURTHER-derived class's own field
actually lands -- an empirical probe of clang's real tail-padding-reuse
decision, not just trusting the tool's own numbers in isolation.

Usage:
    python3 run_tests.py /path/to/abicheck-clang-layout-tool

Exits 0 if every assertion passes, 1 otherwise (with a diff-style message
for each failure).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"

failures: list[str] = []


def run_tool(binary: str, fixture: str, *extra_flags: str) -> dict:
    path = FIXTURES_DIR / fixture
    flags = list(extra_flags) if extra_flags else ["-std=c++17"]
    result = subprocess.run(
        [binary, str(path), "--", *flags],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, f"{fixture}: tool reported ok=false"
    return {r["qualified_name"]: r for r in payload["records"]}


def check(fixture: str, name: str, records: dict, expected: dict) -> None:
    rec = records.get(name)
    if rec is None:
        failures.append(f"{fixture}: {name} not found in output")
        return
    for key, want in expected.items():
        got = rec.get(key)
        if got != want:
            failures.append(f"{fixture}: {name}.{key} = {got!r}, expected {want!r}")


def field_offset(rec: dict, field_name: str) -> int | None:
    for f in rec.get("fields", []):
        if f["name"] == field_name:
            return f["offset_bits"]
    return None


def base_offset(rec: dict, base_name: str) -> int | None:
    for b in rec.get("bases", []):
        if b["name"] == base_name:
            return b["offset_bits"]
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} /path/to/abicheck-clang-layout-tool")
        return 2
    binary = sys.argv[1]

    # simple.cpp: struct Foo { int a; double b; char c; } in namespace ns.
    # a@0 (4B); b needs 8B align -> padded to @8 (8B), ends @16; c@16 (1B).
    # Struct align = max(4,8,1) = 8B -> size rounds 17 -> 24B = 192 bits.
    records = run_tool(binary, "simple.cpp")
    check(
        "simple.cpp", "ns::Foo", records,
        {"size_bits": 192, "alignment_bits": 64, "data_size_bits": 192,
         "is_standard_layout": True, "is_trivially_copyable": True,
         "vptr_offset_bits": None},
    )
    rec = records["ns::Foo"]
    assert field_offset(rec, "a") == 0
    assert field_offset(rec, "b") == 64
    assert field_offset(rec, "c") == 128

    # tailpad.cpp: Foo is POD (no user-declared special members) -- the
    # Itanium ABI does NOT let a derived class reuse a POD base's tail
    # padding, so Foo's own dsize == its full sizeof (192 bits, not the
    # naive 136-bit "used bytes only" figure), and Derived::d must land at
    # byte 24 (192 bits), not byte 17.
    records = run_tool(binary, "tailpad.cpp")
    check("tailpad.cpp", "Foo", records, {"size_bits": 192, "data_size_bits": 192})
    check("tailpad.cpp", "Derived", records, {"size_bits": 256})
    assert field_offset(records["Derived"], "d") == 192

    # nonpod_tailpad.cpp: Base has a user-declared ctor (non-POD-for-layout),
    # so its tail padding (17 -> 24 bytes = 7 bytes unused) IS reusable --
    # dsize = 136 bits (17 bytes), and Derived::d packs into that reclaimed
    # space at byte 17 (136 bits), keeping Derived's total size equal to
    # Base's (192 bits), not growing to a new 256-bit total.
    records = run_tool(binary, "nonpod_tailpad.cpp")
    check("nonpod_tailpad.cpp", "Base", records, {"size_bits": 192, "data_size_bits": 136})
    check("nonpod_tailpad.cpp", "Derived", records, {"size_bits": 192})
    assert field_offset(records["Derived"], "d") == 136

    # polymorphic.cpp: single/multiple polymorphic inheritance, vptr always
    # at offset 0 (either "owns" the vtable, or inherits the primary base's,
    # which the ABI always places at offset 0 too).
    records = run_tool(binary, "polymorphic.cpp")
    check("polymorphic.cpp", "Base", records, {"vptr_offset_bits": 0})
    assert field_offset(records["Base"], "a") == 64
    check("polymorphic.cpp", "Derived", records, {"vptr_offset_bits": 0})
    assert field_offset(records["Derived"], "b") == 96
    check("polymorphic.cpp", "Diamond", records, {"vptr_offset_bits": 0, "size_bits": 256})
    assert base_offset(records["Diamond"], "Left") == 0
    assert base_offset(records["Diamond"], "Right") == 128
    assert field_offset(records["Diamond"], "d") == 224

    # virtual_inherit.cpp: diamond virtual inheritance -- VBase appears
    # exactly once in C's base list (deduplicated across A's and B's virtual
    # paths), placed after A and B's own non-virtual portions.
    records = run_tool(binary, "virtual_inherit.cpp")
    check("virtual_inherit.cpp", "C", records, {"vptr_offset_bits": 0, "size_bits": 384})
    c_bases = {b["name"]: b for b in records["C"]["bases"]}
    assert c_bases["A"]["is_virtual"] is False
    assert c_bases["A"]["offset_bits"] == 0
    assert c_bases["B"]["is_virtual"] is False
    assert c_bases["B"]["offset_bits"] == 128
    assert c_bases["VBase"]["is_virtual"] is True
    assert c_bases["VBase"]["offset_bits"] == 256
    assert field_offset(records["C"], "c") == 224

    # c_record.c: a plain C struct (parsed in C mode, -x c) is an ordinary
    # RecordDecl, not a CXXRecordDecl -- VisitCXXRecordDecl never fires for
    # it. Same layout as simple.cpp's Foo (identical member list), but
    # WITHOUT the C++-only keys (is_standard_layout/is_trivially_copyable/
    # vptr_offset_bits/bases), which don't apply to C at all.
    records = run_tool(binary, "c_record.c", "-x", "c", "-std=gnu11")
    check("c_record.c", "Foo", records, {"size_bits": 192, "data_size_bits": 192})
    rec = records["Foo"]
    assert field_offset(rec, "a") == 0
    assert field_offset(rec, "b") == 64
    assert field_offset(rec, "c") == 128
    assert "is_standard_layout" not in rec
    assert "bases" not in rec

    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All abicheck-clang-layout-tool layout assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

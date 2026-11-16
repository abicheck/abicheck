# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Binary purity evidence of a header-built snapshot (WS-3 A6).

Bug class ``evidence.header_purity_not_persisted``: a binary-only dump proves
a virtual pure from the vtable slot the emitted vtable binds to
``__cxa_pure_virtual``, but a header-built snapshot kept only castxml's
header attribute, which ``--depth binary`` discards. So a pure virtual that
became concrete (or the reverse) was reported as a binary-only finding that
the header-projected run missed.

The oracle is the binary itself, read by ``readelf`` and ``nm`` (not by
abicheck's own ELF reader): the slot indexes that hold a relocation against
``__cxa_pure_virtual`` inside each ``_ZTV`` object, and the object's size.
The expected names come from the source, one sibling per shape:

* a single pure method;
* a pure method next to a concrete one (the pure slot alone is claimed);
* an inherited pure method (the concrete class is abstract through its base);
* a multiple-inheritance class, which must yield no claim at all.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_codec import save_snapshot

#: The same ELF gate ``test_comdat_groups.py`` uses. The purity claim is an
#: ELF fact: a Windows runner's MinGW build is a PE, whose snapshot carries no
#: ``binary_purity`` (``elf=None``), so these tests run on Linux only.
requires_elf_toolchain = pytest.mark.skipif(
    shutil.which("g++") is None or not sys.platform.startswith("linux"),
    reason="needs g++ on an ELF platform (the purity claim is ELF-only)",
)

pytestmark = [
    pytest.mark.integration,
    requires_elf_toolchain,
    pytest.mark.skipif(shutil.which("readelf") is None, reason="needs readelf"),
    pytest.mark.skipif(shutil.which("nm") is None, reason="needs nm"),
    pytest.mark.skipif(shutil.which("c++filt") is None, reason="needs c++filt"),
]

_HEADER = """\
#pragma once
struct Shape {
    virtual ~Shape() {}
    virtual void draw() = 0;
    virtual int id() const;
};

struct Plain {
    virtual int value() const;
};

struct Child : Plain {
    int value() const override;
    virtual void extra();
};

struct Mid : Shape {
    // Inherits draw() pure: Mid stays abstract.
    int id() const override;
};

struct Concrete : Shape {
    void draw() override;
};

struct MiA {
    virtual void first() = 0;
    virtual ~MiA() {}
};

struct MiB {
    virtual void second();
    virtual ~MiB() {}
};

struct MiDerived : MiA, MiB {
    void first() override;
    void second() override;
};
"""

_SOURCE = """\
#include "shapes.h"
int Shape::id() const { return 1; }
int Plain::value() const { return 2; }
int Child::value() const { return 3; }
void Child::extra() {}
int Mid::id() const { return 4; }
void Concrete::draw() {}
void MiB::second() {}
void MiDerived::first() {}
void MiDerived::second() {}
"""

_SIBLINGS = {
    # class -> the pure-slot facts the binary proves (the oracle's expectation)
    "Shape": {"functions": {"Shape::draw": True, "Shape::id": False}, "abstract": True},
    "Child": {
        "functions": {"Child::value": False, "Child::extra": False},
        "abstract": False,
    },
    "Mid": {"functions": {"Shape::draw": True, "Mid::id": False}, "abstract": True},
    "Concrete": {"functions": {"Concrete::draw": False}, "abstract": False},
}


def _build(tmp_path: Path) -> tuple[Path, Path]:
    header = tmp_path / "shapes.h"
    source = tmp_path / "shapes.cpp"
    header.write_text(_HEADER)
    source.write_text(_SOURCE)
    lib = tmp_path / "libshapes.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-O0", "-o", str(lib), str(source)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return lib, header


def _oracle_pure_slots(lib: Path) -> dict[str, tuple[int, frozenset[int]]]:
    """``{class: (vtable size in slots, pure slot indexes)}`` read with
    ``readelf``/``nm``: a ``_ZTV`` object's slots are its words after the
    two header words, and a slot is pure when a relocation against
    ``__cxa_pure_virtual`` lands in it."""
    symbols = subprocess.run(
        ["nm", "-C", "--size-sort", "-S", str(lib)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    vtables: dict[str, tuple[int, int]] = {}  # class -> (address, size)
    for line in symbols.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4 and fields[3].startswith("vtable for "):
            vtables[fields[3][len("vtable for ") :]] = (
                int(fields[0], 16),
                int(fields[1], 16),
            )
    relocs = subprocess.run(
        ["readelf", "-rW", str(lib)], capture_output=True, text=True, check=True
    ).stdout
    pure_offsets: list[int] = []
    for line in relocs.splitlines():
        if "__cxa_pure_virtual" in line:
            match = re.match(r"^([0-9a-f]+)\s", line)
            if match:
                pure_offsets.append(int(match.group(1), 16))
    out: dict[str, tuple[int, frozenset[int]]] = {}
    word = 8
    for cls, (address, size) in vtables.items():
        slots = size // word - 2
        pure = frozenset(
            (off - address) // word - 2
            for off in pure_offsets
            if address + 2 * word <= off < address + size
        )
        out[cls] = (slots, pure)
    return out


@pytest.mark.parametrize(
    "cls",
    sorted(_SIBLINGS),
)
def test_claims_match_the_binary_vtable(tmp_path: Path, cls: str) -> None:
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    lib, header = _build(tmp_path)
    snap = dump(lib, headers=[header], version="v1")
    claims = snap.binary_purity
    assert claims is not None, "a header dump of an emitted vtable must claim"

    slots, pure = _oracle_pure_slots(lib)[cls]
    expected = _SIBLINGS[cls]
    assert claims["records"][cls] == expected["abstract"]
    assert expected["abstract"] == bool(pure), "fixture and oracle agree"
    assert slots >= 1
    for mangled_name, is_pure in expected["functions"].items():
        mangled = _mangled_for(snap, mangled_name)
        assert claims["functions"][mangled] is is_pure, mangled_name


def test_multiple_inheritance_yields_no_claim(tmp_path: Path) -> None:
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    lib, header = _build(tmp_path)
    snap = dump(lib, headers=[header], version="v1")
    claims = snap.binary_purity
    assert claims is not None
    assert "MiDerived" not in claims["records"], "two bases: no unambiguous slot map"
    assert "unobserved" in claims
    assert "MiDerived" in claims["unobserved"]
    assert "multiple" in claims["unobserved"]["MiDerived"]
    for name in ("MiDerived::first", "MiDerived::second"):
        assert _mangled_for(snap, name) not in claims["functions"]


def _mangled_for(snap: object, qualified: str) -> str:
    """The mangled name of the declaration whose demangled spelling is
    *qualified* (``c++filt``, not abicheck's demangler)."""
    functions = snap.declarations.functions  # type: ignore[attr-defined]
    names = [f.mangled for f in functions if f.mangled.startswith("_Z")]
    demangled = subprocess.run(
        ["c++filt"], input="\n".join(names), capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for mangled, text in zip(names, demangled, strict=True):
        if text == qualified or text.startswith(qualified + "("):
            return mangled
    raise AssertionError(f"no declaration named {qualified}")


def test_claims_survive_a_save_and_load(tmp_path: Path) -> None:
    """The claims are an extension fact: a stored header snapshot read back
    still proves the same purity, so a baseline keeps what a live dump saw."""
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    lib, header = _build(tmp_path)
    snap = dump(lib, headers=[header], version="v1")
    stored = tmp_path / "shapes.abi.json"
    save_snapshot(snap, stored)
    reloaded = load_snapshot(stored)
    assert reloaded.binary_purity == snap.binary_purity
    assert reloaded.binary_purity is not None
    assert reloaded.binary_purity["records"]["Shape"] is True


_NAMESPACED_HEADER = """\
#pragma once
namespace a {
struct Pure {
    virtual void f() = 0;
    virtual int g() const;
};
}
namespace b {
struct Pure {
    virtual int g() const;
};
}
"""

_NAMESPACED_SOURCE = """\
#include "ns.h"
namespace a { int Pure::g() const { return 1; } }
namespace b { int Pure::g() const { return 2; } }
"""


def test_same_named_classes_in_two_namespaces_keep_their_own_claims(
    tmp_path: Path,
) -> None:
    """Two classes both spelled ``Pure``: each is claimed from its own vtable,
    keyed by its qualified name, so neither overwrites the other's claim."""
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    header = tmp_path / "ns.h"
    source = tmp_path / "ns.cpp"
    header.write_text(_NAMESPACED_HEADER)
    source.write_text(_NAMESPACED_SOURCE)
    lib = tmp_path / "libns.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", "-O0", "-o", str(lib), str(source)],
        check=True,
        capture_output=True,
    )
    snap = dump(lib, headers=[header], version="v1")
    claims = snap.binary_purity
    assert claims is not None
    assert claims["records"]["a::Pure"] is True
    assert claims["records"]["b::Pure"] is False
    assert _mangled_for(snap, "a::Pure::f") in claims["functions"]
    assert claims["functions"][_mangled_for(snap, "a::Pure::f")] is True


@pytest.mark.parametrize(
    ("vtable", "slots"),
    [
        (["~Shape", "_ZN5Shape4drawEv"], 3),
        (["_ZN5ShapeD1Ev", "_ZN5Shape4drawEv"], 3),
        (["_ZN5ShapeD0Ev", "_ZN5Shape4drawEv"], 3),
        (["_ZN1A2D1Ev"], 1),
        (["_ZN1A2D1Ev", "_ZN1A2D2Ev"], 2),
    ],
)
def test_destructor_spellings_expand_to_two_slots_only_for_destructors(
    vtable: list[str], slots: int
) -> None:
    """A destructor takes two Itanium slots whichever form the header backend
    spells it in; a method whose name starts with ``D`` (``A::D1``) takes one."""
    from abicheck.extract.header_binary_purity import _slot_entries

    assert len(_slot_entries(vtable)) == slots


def test_a_32_bit_library_reads_its_slots_at_four_bytes(tmp_path: Path) -> None:
    """ELF32 pointers are four bytes, so a two-slot vtable is 16 bytes, not 32."""
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    header = tmp_path / "shapes.h"
    source = tmp_path / "shapes.cpp"
    header.write_text(_HEADER)
    source.write_text(_SOURCE)
    lib = tmp_path / "libshapes32.so"
    try:
        subprocess.run(
            # -nostdlib: this host has no 32-bit C++ runtime to link against; a
            # shared object may leave the runtime symbols undefined.
            [
                "g++",
                "-m32",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                "-nostdlib",
                "-o",
                str(lib),
                str(source),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        pytest.skip("this toolchain cannot build 32-bit code (g++ -m32)")
    from abicheck.extract.header_binary_purity import _word_size

    assert _word_size(lib) == 4
    snap = dump(lib, headers=[header], version="v1")
    claims = snap.binary_purity
    assert claims is not None
    assert claims["records"].get("Shape") is True


def test_a_mixed_pair_reads_the_same_purity_in_either_operand_order(
    tmp_path: Path,
) -> None:
    """A header-built side and a binary-only side of the same library agree on
    the pure virtual at binary depth, whichever one is OLD: the purity a
    binary-only side reads from its debug declaration is evidence the header
    side's vtable claim matches, so neither side drops the function."""
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    from abicheck.policy.depth_projection import project_pair_to_depth

    lib, header = _build(tmp_path)
    header_snap = dump(lib, headers=[header], version="v1")
    binary_snap = dump(lib, headers=[], version="v1")
    mangled = _mangled_for(header_snap, "Shape::draw")
    for old, new in ((header_snap, binary_snap), (binary_snap, header_snap)):
        p_old, p_new = project_pair_to_depth(old, new, "binary")
        for side in (p_old, p_new):
            pure = {f.mangled: f.is_pure_virtual for f in side.declarations.functions}
            assert pure.get(mangled) is True

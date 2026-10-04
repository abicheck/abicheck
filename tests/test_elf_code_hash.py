"""``extract/elf_code_hash.CodeHasher`` and how the rename matcher uses it.

Oracle for the hasher: BLAKE2b-128 of the bytes sliced directly out of the
file image at ``sh_offset + (st_value - sh_addr)``, computed here rather
than through the hasher. Every symbol outside its section, below the size
floor, in a NOBITS section, or with a non-integer section index hashes to
"" (unknown), never to a digest of the wrong bytes.

Matcher contract: equal hashes may raise a size match to 1.0 and break a
same-size tie, but only for a partner the name predicate accepts; unequal
hashes never remove a partner.
"""

from __future__ import annotations

import hashlib
import io
import random
import sys
from types import SimpleNamespace

import pytest

from abicheck.binary_fingerprint import FunctionFingerprint, match_renamed_functions
from abicheck.extract.elf_code_hash import MIN_HASHED_SIZE, CodeHasher
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict


def _elf(
    image: bytes, sections: dict[int, tuple[int, int, int, str]], machine="EM_X86_64"
):
    def get_section(i):
        addr, off, size, typ = sections[i]
        return SimpleNamespace(
            header=SimpleNamespace(
                sh_addr=addr, sh_offset=off, sh_size=size, sh_type=typ
            )
        )

    return SimpleNamespace(
        stream=io.BytesIO(image),
        header=SimpleNamespace(e_machine=machine),
        get_section=get_section,
    )


def _oracle(image: bytes, start: int, size: int) -> str:
    return hashlib.blake2b(image[start : start + size], digest_size=16).hexdigest()


@pytest.mark.parametrize("seed", range(40))
def test_hash_matches_direct_slice_oracle(seed: int) -> None:
    rng = random.Random(seed)
    image = rng.randbytes(4096)
    sec_off = rng.randrange(0, 1024)
    sec_addr = rng.randrange(0x1000, 0x100000)
    sec_size = rng.randrange(64, 4096 - sec_off)
    hasher = CodeHasher(_elf(image, {1: (sec_addr, sec_off, sec_size, "SHT_PROGBITS")}))
    for _ in range(30):
        rel = rng.randrange(-32, sec_size + 32)
        size = rng.randrange(0, 128)
        got = hasher.hash(sec_addr + rel, size, 1)
        inside = rel >= 0 and rel + size <= sec_size
        if inside and size >= MIN_HASHED_SIZE:
            assert got == _oracle(image, sec_off + rel, size)
        else:
            assert got == ""


@pytest.mark.parametrize(
    ("shndx", "typ"),
    [("SHN_UNDEF", "SHT_PROGBITS"), ("SHN_ABS", "SHT_PROGBITS"), (1, "SHT_NOBITS")],
)
def test_unhashable_symbols_are_unknown(shndx, typ) -> None:
    hasher = CodeHasher(_elf(b"\x90" * 256, {1: (0, 0, 256, typ)}))
    assert hasher.hash(0, 16, shndx) == ""


def test_no_elf_file_hashes_nothing() -> None:
    assert CodeHasher(None).hash(0, 16, 1) == ""


def test_arm_thumb_bit_is_cleared() -> None:
    image = bytes(range(256))
    hasher = CodeHasher(
        _elf(image, {1: (0x100, 0, 256, "SHT_PROGBITS")}, machine="EM_ARM")
    )
    assert hasher.hash(0x100 + 16 + 1, 16, 1) == _oracle(image, 16, 16)


def _fp(name: str, size: int, h: str = "") -> FunctionFingerprint:
    return FunctionFingerprint(name=name, size=size, code_hash=h)


def test_equal_hash_breaks_a_same_size_tie() -> None:
    old = {"lib_open": _fp("lib_open", 64, "h1")}
    new = {
        "lib_open2": _fp("lib_open2", 64, "h1"),
        "lib_open3": _fp("lib_open3", 64, "h2"),
    }
    # Without hashes the two same-size candidates are ambiguous: no match.
    no_hash = {k: _fp(k, v.size) for k, v in new.items()}
    assert match_renamed_functions({"lib_open": _fp("lib_open", 64)}, no_hash) == []
    result = match_renamed_functions(old, new)
    assert [(c.new_name, c.confidence) for c in result] == [("lib_open2", 1.0)]


def test_equal_hash_still_needs_the_name_predicate() -> None:
    old = {"alpha": _fp("alpha", 64, "same")}
    new = {"omega": _fp("omega", 64, "same")}
    assert match_renamed_functions(old, new, name_filter=lambda a, b: False) == []


@pytest.mark.parametrize("seed", range(60))
def test_unequal_hashes_change_nothing(seed: int) -> None:
    """When no old hash equals any new hash, the result is exactly the
    hash-less result: inequality is never evidence."""
    rng = random.Random(seed)
    sizes = [rng.choice([16, 32, 48, 64, 64, 96]) for _ in range(8)]
    old = {f"o{i}": _fp(f"o{i}", s) for i, s in enumerate(sizes)}
    new = {f"n{i}": _fp(f"n{i}", rng.choice(sizes)) for i in range(8)}

    def pairs(result):
        return [(c.old_name, c.new_name, c.confidence) for c in result]

    base = pairs(match_renamed_functions(old, new))
    hashed_old = {k: _fp(k, v.size, f"old{rng.randrange(3)}") for k, v in old.items()}
    hashed_new = {k: _fp(k, v.size, f"new{rng.randrange(3)}") for k, v in new.items()}
    assert pairs(match_renamed_functions(hashed_old, hashed_new)) == base


def test_code_hash_round_trips_and_is_omitted_when_empty() -> None:
    snap = AbiSnapshot(
        library="libx.so",
        version="1",
        elf=ElfMetadata(
            symbols=[
                ElfSymbol(name="f", size=32, code_hash="ab" * 16),
                ElfSymbol(name="g", size=32),
            ]
        ),
    )
    d = snapshot_to_dict(snap)
    syms = {s["name"]: s for s in d["elf"]["symbols"]}
    assert syms["f"]["code_hash"] == "ab" * 16
    assert "code_hash" not in syms["g"]
    back = snapshot_from_dict(d)
    assert {s.name: s.code_hash for s in back.elf.symbols} == {"f": "ab" * 16, "g": ""}


_V1 = "int lib_mix(int a, int b) { int r = 0; for (int i = a; i < b; ++i) r += i ^ 3; return r; }\n"
_V2 = (
    "int lib_mix2(int a, int b) { int r = 0; for (int i = a; i < b; ++i) r += i ^ 3; return r; }\n"
    "int lib_mix3(int a, int b) { int r = 0; for (int i = a; i < b; ++i) r += i ^ 5; return r; }\n"
)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="needs a toolchain that emits ELF")
def test_real_stripped_library_rename_is_resolved_by_the_hash(tmp_path) -> None:
    """A stripped library renames ``lib_mix`` and adds a same-size decoy. By
    size alone the rename is ambiguous; the dump-time hash resolves it."""
    import shutil
    import subprocess

    from abicheck.checker import ChangeKind, compare
    from abicheck.dumper import dump

    if shutil.which("gcc") is None:
        pytest.skip("gcc not available")
    libs = []
    for name, src in (("v1", _V1), ("v2", _V2)):
        (tmp_path / f"{name}.c").write_text(src)
        out = tmp_path / f"lib{name}.so"
        subprocess.run(
            [
                "gcc",
                "-O2",
                "-shared",
                "-fPIC",
                "-s",
                str(tmp_path / f"{name}.c"),
                "-o",
                str(out),
            ],
            check=True,
        )
        libs.append(dump(out, []))
    old, new = libs
    sizes = {s.name: s.size for s in new.elf.symbols if s.name.startswith("lib_mix")}
    # The scenario needs the decoy to collide on size; otherwise it proves nothing.
    assert sizes["lib_mix2"] == sizes["lib_mix3"], sizes
    hashes = {
        s.name: s.code_hash
        for s in (*old.elf.symbols, *new.elf.symbols)
        if s.name.startswith("lib_mix")
    }
    assert hashes["lib_mix"] == hashes["lib_mix2"] != hashes["lib_mix3"]
    renames = [
        c for c in compare(old, new).changes if c.kind is ChangeKind.FUNC_LIKELY_RENAMED
    ]
    assert [(c.old_value, c.new_value) for c in renames] == [("lib_mix", "lib_mix2")]


@pytest.mark.parametrize("exc", ["elf", "construct", "index"])
def test_malformed_section_lookup_is_unknown_not_an_error(exc) -> None:
    from elftools.common.exceptions import ELFError
    from elftools.construct import ConstructError

    error = {
        "elf": ELFError("bad"),
        "construct": ConstructError("bad"),
        "index": IndexError("bad"),
    }[exc]

    def get_section(_i):
        raise error

    elf = SimpleNamespace(
        stream=io.BytesIO(b"\x00" * 64),
        header=SimpleNamespace(e_machine="EM_X86_64"),
        get_section=get_section,
    )
    assert CodeHasher(elf).hash(0, 16, 1) == ""

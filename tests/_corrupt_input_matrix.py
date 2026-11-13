# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The corrupt-input matrix: format x corruption x side x mode, run through the CLI.

Each cell writes a small valid seed (or two), replaces one side with a
corrupted copy, and runs the public ``abicheck`` command for the cell's mode.
The seeds are generated, never committed blobs: the ELF seed is compiled
with gcc (cells skip without it), the PE seed is the committed 3.5 KiB
``tests/fixtures/containers/sources/corpus_pe.dll``, the Mach-O seed is the
hand-built dylib of :mod:`tests._macho_synthetic`, and the snapshot seed is a
storage-v2 document serialized from a hand-built ``AbiSnapshot``.

Every corruption is located from the seed's own structure (the ELF section
headers, the PE data directory, the Mach-O load commands, the JSON text), so
a seed rebuilt by another compiler still cuts where the cell's name says.

A non-``test_`` helper: ``tests/test_corrupt_input_matrix.py`` is the runner.
"""

from __future__ import annotations

import io
import json
import random
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PE_SEED = REPO_ROOT / "tests" / "fixtures" / "containers" / "sources" / "corpus_pe.dll"

_ELF_SOURCE = """
struct point { int x; int y; };
struct box { struct point lo; struct point hi; long tag; };
int g_counter = 3;
int add(int a, int b) { return a + b + g_counter; }
long dist2(struct point p) { return (long)p.x * p.x + (long)p.y * p.y; }
long area(struct box b) { return (long)(b.hi.x - b.lo.x) * (b.hi.y - b.lo.y) + b.tag; }
"""

FORMATS = ("elf", "pe", "macho", "snapshot")
SIDES = ("old", "new", "both")
#: ``compare`` of two files, ``compare`` of two directories (release mode),
#: and ``dump`` of the corrupted file alone.
MODES = ("compare", "directory", "dump")

#: Member file name per format: the name a directory compare pairs on.
FILE_NAMES = {
    "elf": "libseed.so",
    "pe": "seed.dll",
    "macho": "libseed.dylib",
    "snapshot": "seed.abi.json",
}

#: A clean-compatible verdict word in the CLI's summary line.
CLEAN_VERDICTS = ("COMPATIBLE", "NO_CHANGE")


def _rand_tail(data: bytes, keep: int, seed: int = 1234) -> bytes:
    """*data*'s first *keep* bytes, then the same length of seeded random bytes."""
    rng = random.Random(seed)
    return data[:keep] + bytes(rng.randrange(256) for _ in range(len(data) - keep))


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Seeds:
    elf: bytes | None
    #: Why the ELF seed is missing (gcc absent), when it is.
    elf_skip: str
    pe: bytes
    macho: bytes
    snapshot: bytes

    def get(self, fmt: str) -> bytes | None:
        return getattr(self, fmt)  # type: ignore[no-any-return]


def build_elf_seed(work: Path) -> bytes:
    """A gcc ``-shared -g`` object with three exports and DWARF structs."""
    work.mkdir(parents=True, exist_ok=True)
    src = work / "seed.c"
    src.write_text(_ELF_SOURCE)
    out = work / "libseed.so"
    # A configured gcc that fails raises CalledProcessError: a failure, not a skip.
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-O0", "-o", str(out), str(src)],
        check=True,
        capture_output=True,
    )
    return out.read_bytes()


def build_macho_seed(work: Path) -> bytes:
    """The three-export synthetic dylib: the same names in the nlist and the trie."""
    from tests._macho_synthetic import N_EXT, N_SECT, MachoSymbol, write_dylib

    work.mkdir(parents=True, exist_ok=True)
    names = ("_add", "_dist2", "_g_counter")
    path = write_dylib(
        work / "libseed.dylib",
        [MachoSymbol(n, N_EXT | N_SECT) for n in names],
        export_trie=[(n, 0) for n in names],
    )
    return path.read_bytes()


def build_snapshot_seed() -> bytes:
    """A storage-v2 snapshot document with four functions and one variable."""
    from abicheck.model import AbiSnapshot, Function, Variable, Visibility
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(
        library="libseed.so",
        version="1.0",
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z2f{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(4)
        ],
        variables=[
            Variable(name="g", mangled="g", type="int", visibility=Visibility.PUBLIC)
        ],
    )
    return snapshot_to_json(snap).encode()


def build_seeds(work: Path) -> Seeds:
    elf: bytes | None = None
    skip = ""
    if shutil.which("gcc") is None:
        skip = "gcc is required to build the ELF seed"
    else:
        elf = build_elf_seed(work / "elf")
        if not elf.startswith(b"\x7fELF"):
            # A host gcc that links PE (MinGW) or Mach-O (Apple clang).
            elf, skip = None, "the host gcc does not link ELF shared objects"
    return Seeds(
        elf=elf,
        elf_skip=skip,
        pe=PE_SEED.read_bytes(),
        macho=build_macho_seed(work / "macho"),
        snapshot=build_snapshot_seed(),
    )


# ---------------------------------------------------------------------------
# Corruptions, located from each seed's own structure
# ---------------------------------------------------------------------------


def _elf_section(data: bytes, name: str) -> Any:
    from elftools.elf.elffile import ELFFile

    elf = ELFFile(io.BytesIO(data))
    sec = elf.get_section_by_name(name)
    assert sec is not None, f"ELF seed has no {name}"
    return sec.header


def _elf_cut_in(name: str) -> Callable[[bytes], bytes]:
    def cut(data: bytes) -> bytes:
        hdr = _elf_section(data, name)
        return data[: hdr.sh_offset + hdr.sh_size // 2]

    return cut


def _elf_cut_section_table(data: bytes) -> bytes:
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    entsize = struct.unpack_from("<H", data, 0x3A)[0]
    return data[: shoff + entsize * 2]


def _elf_flip_abbrev(data: bytes) -> bytes:
    hdr = _elf_section(data, ".debug_abbrev")
    out = bytearray(data)
    out[hdr.sh_offset] ^= 0xFF
    return bytes(out)


def _pe_header_end(data: bytes) -> int:
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    return int(struct.unpack_from("<I", data, lfanew + 24 + 60)[0])  # SizeOfHeaders


def _pe_cut_section_table(data: bytes) -> bytes:
    lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    opt_size = struct.unpack_from("<H", data, lfanew + 20)[0]
    return data[: lfanew + 24 + opt_size + 40 + 20]  # mid second section header


def _pe_cut_last_section(data: bytes) -> bytes:
    """Cut inside the last section's raw data; the export directory survives."""
    import pefile  # type: ignore[import-untyped]

    last = pefile.PE(data=data, fast_load=True).sections[-1]
    return data[: last.PointerToRawData + last.SizeOfRawData // 2]


def _pe_export_dir(data: bytes) -> tuple[Any, int]:
    """The parsed export directory and its file offset."""
    import pefile  # type: ignore[import-untyped]

    pe = pefile.PE(data=data, fast_load=True)
    entry = pe.OPTIONAL_HEADER.DATA_DIRECTORY[0]
    return entry, pe.get_offset_from_rva(entry.VirtualAddress)


def _pe_cut_export_directory(data: bytes) -> bytes:
    entry, offset = _pe_export_dir(data)
    return data[: offset + 20]  # inside IMAGE_EXPORT_DIRECTORY itself


def _pe_cut_export_names(data: bytes) -> bytes:
    """Cut inside the name strings the export directory points at."""
    entry, offset = _pe_export_dir(data)
    return data[: offset + entry.Size - 8]


def _pe_cut_before_export_directory(data: bytes) -> bytes:
    """Cut inside ``.rdata`` before the export directory: its RVA lies past EOF."""
    _entry, offset = _pe_export_dir(data)
    return data[: offset - 160]


def _macho_symtab(data: bytes) -> tuple[int, int, int, int]:
    """``(symoff, nsyms, stroff, strsize)`` of the seed's LC_SYMTAB."""
    ncmds = struct.unpack_from("<I", data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, at)
        if cmd == 0x2:
            return struct.unpack_from("<IIII", data, at + 8)  # type: ignore[return-value]
        at += size
    raise AssertionError("Mach-O seed has no LC_SYMTAB")


def _macho_trie(data: bytes) -> tuple[int, int]:
    ncmds = struct.unpack_from("<I", data, 16)[0]
    at = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, at)
        if cmd == 0x80000033:
            return struct.unpack_from("<II", data, at + 8)  # type: ignore[return-value]
        at += size
    raise AssertionError("Mach-O seed has no LC_DYLD_EXPORTS_TRIE")


def _macho_cut_load_commands(data: bytes) -> bytes:
    return data[: 32 + 40]


def _macho_cut_symbol_table(data: bytes) -> bytes:
    symoff, _n, _stroff, _size = _macho_symtab(data)
    return data[: symoff + 20]


def _macho_cut_string_table(data: bytes) -> bytes:
    _symoff, _n, stroff, size = _macho_symtab(data)
    return data[: stroff + size // 2]


def _macho_cut_export_trie(data: bytes) -> bytes:
    off, size = _macho_trie(data)
    return data[: off + size // 2]


def _json_doc(data: bytes) -> Any:
    return json.loads(data)


def _snapshot_drop_mangled(data: bytes) -> bytes:
    doc = _json_doc(data)
    del doc["sections"]["declarations"]["payload"]["functions"][0]["mangled"]
    return json.dumps(doc).encode()


def _snapshot_functions_wrong_type(data: bytes) -> bytes:
    doc = _json_doc(data)
    doc["sections"]["declarations"]["payload"]["functions"] = {"not": "a list"}
    return json.dumps(doc).encode()


def _snapshot_function_entry_wrong_type(data: bytes) -> bytes:
    doc = _json_doc(data)
    doc["sections"]["declarations"]["payload"]["functions"][0] = "f0"
    return json.dumps(doc).encode()


def _first_byte(data: bytes, value: bytes) -> bytes:
    return value + data[len(value) :]


#: format -> corruption name -> corrupter. Names are the matrix's column ids.
CORRUPTIONS: dict[str, dict[str, Callable[[bytes], bytes]]] = {
    "elf": {
        "bad_magic": lambda d: _first_byte(d, b"\x7fXLF"),
        "zero_length": lambda d: b"",
        "cut_header": lambda d: d[:40],
        "cut_section_table": _elf_cut_section_table,
        "cut_string_table": _elf_cut_in(".dynstr"),
        "cut_symbol_table": _elf_cut_in(".dynsym"),
        "cut_dwarf": _elf_cut_in(".debug_info"),
        "corrupt_dwarf_abbrev": _elf_flip_abbrev,
        "random_after_header": lambda d: _rand_tail(d, 64),
    },
    "pe": {
        "bad_magic": lambda d: _first_byte(d, b"ZM"),
        "zero_length": lambda d: b"",
        "cut_header": lambda d: d[:64],
        "cut_section_table": _pe_cut_section_table,
        "cut_before_export_directory": _pe_cut_before_export_directory,
        "cut_export_directory": _pe_cut_export_directory,
        "cut_export_names": _pe_cut_export_names,
        "cut_last_section": _pe_cut_last_section,
        "random_after_header": lambda d: _rand_tail(d, _pe_header_end(d)),
    },
    "macho": {
        "bad_magic": lambda d: _first_byte(d, b"\x00\x00\x00\x00"),
        "zero_length": lambda d: b"",
        "cut_header": lambda d: d[:16],
        "cut_load_commands": _macho_cut_load_commands,
        "cut_symbol_table": _macho_cut_symbol_table,
        "cut_string_table": _macho_cut_string_table,
        "cut_export_trie": _macho_cut_export_trie,
        "random_after_header": lambda d: _rand_tail(d, 32),
    },
    "snapshot": {
        "bad_magic": lambda d: _first_byte(d, b"X"),
        "zero_length": lambda d: b"",
        "cut_10pct": lambda d: d[: len(d) // 10],
        "cut_50pct": lambda d: d[: len(d) // 2],
        "cut_90pct": lambda d: d[: len(d) * 9 // 10],
        "random_after_header": lambda d: _rand_tail(d, 64),
        "missing_function_key": _snapshot_drop_mangled,
        "functions_wrong_type": _snapshot_functions_wrong_type,
        "function_entry_wrong_type": _snapshot_function_entry_wrong_type,
    },
}

#: (format, mode) pairs the public CLI does not offer: a stored snapshot is
#: not a release-directory member, and ``dump`` reads a binary, not a snapshot.
NOT_APPLICABLE = frozenset({("snapshot", "directory"), ("snapshot", "dump")})


@dataclass(frozen=True)
class Cell:
    fmt: str
    corruption: str
    mode: str
    #: ``old`` / ``new`` / ``both``; ``-`` for ``dump`` (one input).
    side: str

    @property
    def id(self) -> str:
        return f"{self.fmt}-{self.corruption}-{self.mode}-{self.side}"


def all_cells() -> Iterator[Cell]:
    for fmt in FORMATS:
        for corruption in CORRUPTIONS[fmt]:
            for mode in MODES:
                if (fmt, mode) in NOT_APPLICABLE:
                    continue
                for side in ("-",) if mode == "dump" else SIDES:
                    yield Cell(fmt, corruption, mode, side)


@dataclass(frozen=True)
class CellResult:
    exit_code: int
    output: str
    #: The exception the command raised, other than its own ``SystemExit``.
    exception: BaseException | None
    #: The verdict word of a ``Compatibility:`` summary line, if one was printed.
    verdict: str
    #: The ``Error:`` line, if one was printed.
    error: str
    #: For ``dump``: the snapshot the command wrote, if it wrote one.
    dumped: dict[str, Any] | None

    @property
    def summary(self) -> str:
        if self.exception is not None:
            return f"raised {type(self.exception).__name__}: {self.exception}"
        failed = failed_members(self.output)
        if failed:
            return f"member ERROR ({', '.join(failed)}); release verdict {self.verdict}"
        if self.verdict:
            return f"verdict {self.verdict}"
        if self.error:
            return self.error[:110]
        return "(no verdict, no error)"


def _verdict(output: str) -> str:
    """The scalar summary's verdict word, or the release report's
    ``| **Verdict** |`` cell for a directory compare."""
    for line in output.splitlines():
        if line.startswith("Compatibility:"):
            return line.split()[1]
        if line.startswith("| **Verdict** |"):
            return line.split("|")[2].strip()
    return ""


def failed_members(output: str) -> list[str]:
    """Release-report ``Libraries`` rows whose verdict is ``ERROR``."""
    return [
        line.split("|")[1].strip().strip("`")
        for line in output.splitlines()
        if line.startswith("| `") and "`ERROR`" in line
    ]


def _error(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip().lstrip("│").strip()
        if stripped.startswith(("Error:", "error:")) or "abicheck: error" in stripped:
            return stripped
    return ""


def run_cell(
    cell: Cell, seeds: Seeds, work: Path, invoke: Callable[[list[str]], Any]
) -> CellResult:
    """Lay out *cell*'s inputs under *work* and run its command through *invoke*.

    *invoke* takes the argv after ``abicheck`` and returns an object with
    ``exit_code``, ``output`` and ``exception`` (Click's ``Result``).
    """
    seed = seeds.get(cell.fmt)
    assert seed is not None
    corrupt = CORRUPTIONS[cell.fmt][cell.corruption](seed)
    assert corrupt != seed, f"{cell.id}: the corrupter left the seed unchanged"
    name = FILE_NAMES[cell.fmt]
    old_dir = work / "old"
    new_dir = work / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old = old_dir / name
    new = new_dir / name
    old.write_bytes(corrupt if cell.side in ("old", "both") else seed)
    new.write_bytes(corrupt if cell.side in ("new", "both") else seed)
    dumped_path = work / "dumped.json"
    if cell.mode == "compare":
        argv = ["compare", str(old), str(new)]
    elif cell.mode == "directory":
        argv = ["compare", str(old_dir), str(new_dir)]
    else:
        target = work / f"dump-{name}"
        target.write_bytes(corrupt)
        argv = ["dump", str(target), "-o", str(dumped_path)]
    result = invoke(argv)
    output = result.output
    exc = result.exception
    if isinstance(exc, SystemExit):
        exc = None
    dumped = None
    if cell.mode == "dump" and dumped_path.is_file() and dumped_path.stat().st_size:
        try:
            dumped = json.loads(dumped_path.read_text())
        except ValueError:
            dumped = None
    return CellResult(
        exit_code=result.exit_code,
        output=output,
        exception=exc,
        verdict=_verdict(output),
        error=_error(output),
        dumped=dumped,
    )


def fresh_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="corrupt-matrix-"))

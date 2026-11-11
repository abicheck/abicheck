"""ADR-090 D1(c) across stored and live operands: the recorded binary SHA-256.

A dump of a binary records the SHA-256 of the binary's bytes
(``abicheck.binary_sha256``). With it, a stored dump of a binary and that
binary compared live are one input: a pair with no type-level evidence on
either side is the exempt identical pair (NO_CHANGE, exit 0), not an
evidence gap. Any other pair keeps ADR-090's UNCONFIRMED answer:

* a binary that differs in one byte, even one no extractor reads (a PE
  rebuild that moved only ``TimeDateStamp``, an ELF ``.comment`` byte);
* a stored dump older than the fact, which recorded no hash, so its identity
  with a live binary is unproven.

Every cell states its one answer. The oracle for "same input" is the file
bytes themselves (``hashlib`` over the two operands), never the fact under
test.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.serialization import load_snapshot
from abicheck.storage.extension_fact_catalog import BINARY_SHA256
from tests._unconfirmed import assert_claim_outcome

_PE_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "containers"
    / "sources"
    / "corpus_pe.dll"
)
_ELF_SOURCE = "int keep(void) { return 3; }\nint also(int a) { return a + 1; }\n"
_BOTH = ["old", "new"]
_SAME_BINARY_NOTE = "old and new binaries are byte-identical"


def _invoke(*args: str) -> tuple[int, dict]:
    res = CliRunner().invoke(main, [*args, "-o", "json=-"])
    assert res.exception is None or isinstance(res.exception, SystemExit), res.output
    return res.exit_code, json.loads(res.stdout)


def _dump(binary: Path, out: Path) -> Path:
    res = CliRunner().invoke(main, ["dump", str(binary), "-o", str(out)])
    assert res.exit_code == 0, res.output
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_recorded_sha(stored: Path, out: Path) -> Path:
    """The same stored dump as a writer that predates the fact wrote it."""
    opener = gzip.open if stored.suffix == ".gz" else open
    with opener(stored, "rt", encoding="utf-8") as fh:
        doc = json.load(fh)
    found = 0
    for section in doc["sections"].values():
        for holder in (section, section.get("header") or {}):
            ext = holder.get("extensions")
            if isinstance(ext, dict) and BINARY_SHA256.name in ext:
                del ext[BINARY_SHA256.name]
                found += 1
                if not ext:
                    del holder["extensions"]
    assert found == 1, "the dump must have recorded exactly one binary_sha256"
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def _pe_timestamp_rebuild(src: Path, out: Path) -> Path:
    """*src* with only the COFF ``TimeDateStamp`` moved: what an MSVC or
    MinGW rebuild of unchanged source produces."""
    data = bytearray(src.read_bytes())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert bytes(data[pe : pe + 4]) == b"PE\0\0"
    stamp = struct.unpack_from("<I", data, pe + 8)[0]
    struct.pack_into("<I", data, pe + 8, (stamp + 0x1000) & 0xFFFFFFFF)
    out.write_bytes(bytes(data))
    return out


# --- the fact itself -------------------------------------------------------


def test_a_dump_records_the_binary_bytes_and_the_load_restores_them(
    tmp_path: Path,
) -> None:
    dll = shutil.copy(_PE_FIXTURE, tmp_path / "a.dll")
    stored = _dump(Path(dll), tmp_path / "a.json")
    assert load_snapshot(stored).binary_sha256 == _sha(Path(dll))
    older = _strip_recorded_sha(stored, tmp_path / "older.json")
    assert load_snapshot(older).binary_sha256 is None


@pytest.mark.parametrize(
    "value",
    ["", "ABC", "g" * 64, "A" * 64, "0" * 63, "0" * 65, 7, None],
)
def test_the_fact_refuses_anything_but_a_lowercase_sha256(value: object) -> None:
    with pytest.raises(ValueError):
        BINARY_SHA256.decode(value)


def test_the_fact_keeps_a_lowercase_sha256() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    assert BINARY_SHA256.decode(digest) == digest


# --- PE: the committed DLL, no compiler needed -----------------------------

#: cell: (old operand, new operand, same input proven). Operand keys name a
#: binary (``*.dll``) or a dump of one (``*.json``); ``older.json`` is
#: ``a.json`` as a writer before the fact wrote it.
_PE_CELLS = {
    "stored_vs_live_identical": ("a.json", "a.dll", True),
    "live_vs_stored_identical": ("a.dll", "a.json", True),
    "stored_vs_stored_identical": ("a.json", "c.json", True),
    "stored_vs_live_timestamp_rebuild": ("a.json", "b.dll", False),
    "stored_vs_stored_timestamp_rebuild": ("a.json", "b.json", False),
    "live_vs_live_timestamp_rebuild": ("a.dll", "b.dll", False),
    "older_stored_vs_live_identical": ("older.json", "a.dll", False),
    # Neither side live and not both recorded: the evidence identity decides,
    # which the recorded hash does not enter, so this pair reads as before.
    "older_stored_vs_stored_identical": ("older.json", "a.json", True),
    # The same rebuild under the *same* file name (``old/a.dll``,
    # ``new/a.dll``): the hashes differ, but with neither side live they never
    # override the evidence identity, and the two dumps' extracted facts and
    # name agree -- NO_CHANGE, as before the fact existed. Live on either side,
    # the bytes decide and it stays UNCONFIRMED.
    "stored_vs_stored_same_name_timestamp_rebuild": ("old/a.json", "new/a.json", True),
    "stored_vs_live_same_name_timestamp_rebuild": ("old/a.json", "new/a.dll", False),
}

#: Cells proven by the dumps' unchanged evidence identity, not by bytes:
#: both stored, one file name, and a rebuild that changed no extracted fact.
_PROVEN_BY_EVIDENCE = {"stored_vs_stored_same_name_timestamp_rebuild"}


def _pe_operands(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """Every operand, and the binary each one stands for."""
    a = Path(shutil.copy(_PE_FIXTURE, tmp_path / "a.dll"))
    c = Path(shutil.copy(a, tmp_path / "c.dll"))
    b = _pe_timestamp_rebuild(a, tmp_path / "b.dll")
    a_json = _dump(a, tmp_path / "a.json")
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    old_a = Path(shutil.copy(a, tmp_path / "old" / "a.dll"))
    new_a = Path(shutil.copy(b, tmp_path / "new" / "a.dll"))
    operands = {
        "a.dll": a,
        "b.dll": b,
        "a.json": a_json,
        "b.json": _dump(b, tmp_path / "b.json"),
        "c.json": _dump(c, tmp_path / "c.json"),
        "older.json": _strip_recorded_sha(a_json, tmp_path / "older.json"),
        "old/a.json": _dump(old_a, tmp_path / "old" / "a.json"),
        "new/a.json": _dump(new_a, tmp_path / "new" / "a.json"),
        "new/a.dll": new_a,
    }
    stands_for = {"a.dll": a, "b.dll": b, "a.json": a, "b.json": b, "c.json": c}
    stands_for.update(
        {"older.json": a, "old/a.json": a, "new/a.json": b, "new/a.dll": b}
    )
    return operands, stands_for


@pytest.mark.parametrize("cell", sorted(_PE_CELLS))
def test_pe_pairs_read_the_recorded_identity(tmp_path: Path, cell: str) -> None:
    operands, stands_for = _pe_operands(tmp_path)
    old_key, new_key, proven = _PE_CELLS[cell]
    same_bytes = _sha(stands_for[old_key]) == _sha(stands_for[new_key])
    # Proven only on identical bytes, and never for an older dump facing a
    # live binary (its identity was not recorded).
    older_vs_live = "older.json" in (old_key, new_key) and (
        old_key.endswith(".dll") or new_key.endswith(".dll")
    )
    if cell in _PROVEN_BY_EVIDENCE:
        assert not same_bytes and proven  # a real byte difference, still exempt
    else:
        assert proven is (same_bytes and not older_vs_live)
    code, report = _invoke("compare", str(operands[old_key]), str(operands[new_key]))
    # The PE fixture has no PDB and no headers: no side has type evidence.
    assert_claim_outcome(
        code, report, observed="NO_CHANGE", lacking=[] if proven else _BOTH
    )
    # The same-binary note reads the same identity: it names the binaries
    # byte-identical exactly when both sides' bytes are known (live or
    # recorded) and equal -- never from an older dump that recorded none.
    binary_note = [w for w in report["coverage_warnings"] if _SAME_BINARY_NOTE in w]
    expect_note = same_bytes and "older.json" not in (old_key, new_key)
    assert len(binary_note) == int(expect_note), report["coverage_warnings"]
    if expect_note:
        assert f"(sha256 {_sha(stands_for[old_key])[:12]}...)" in binary_note[0]


def test_the_cells_exercise_both_answers() -> None:
    assert {proven for *_, proven in _PE_CELLS.values()} == {True, False}


def test_the_tier1_dump_records_the_binary_bytes(tmp_path: Path) -> None:
    from abicheck.service import run_dump

    dll = Path(shutil.copy(_PE_FIXTURE, tmp_path / "a.dll"))
    assert run_dump(dll, "pe").binary_sha256 == _sha(dll)


def test_the_recorded_hash_is_not_part_of_the_evidence_identity(
    tmp_path: Path,
) -> None:
    """A dump of a timestamp-only rebuild records another hash but the same
    extracted facts, so its evidence digest is the original's; the hash is
    capture, like ``build_id``."""
    from abicheck.storage.snapshot_evidence import snapshot_evidence_digest

    a = Path(shutil.copy(_PE_FIXTURE, tmp_path / "a.dll"))
    b = _pe_timestamp_rebuild(a, tmp_path / "b.dll")
    snap_a = load_snapshot(_dump(a, tmp_path / "a.json"))
    snap_b = load_snapshot(_dump(b, tmp_path / "b.json"))
    snap_b.library = snap_a.library  # the name is evidence; only bytes differ
    assert snap_a.binary_sha256 != snap_b.binary_sha256
    assert snapshot_evidence_digest(snap_a) == snapshot_evidence_digest(snap_b)
    snap_b.binary_sha256 = None
    assert snapshot_evidence_digest(snap_a) == snapshot_evidence_digest(snap_b)


# --- ELF: built with gcc, linker script included ----------------------------


def _gcc_build(tmp_path: Path, name: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not found")
    src = tmp_path / "lib.c"
    src.write_text(_ELF_SOURCE, encoding="utf-8")
    out = tmp_path / name
    proc = subprocess.run(
        [gcc, "-shared", "-fPIC", "-o", str(out), str(src)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return out


def _flip_comment_byte(src: Path, out: Path) -> Path:
    """*src* with one ``.comment`` byte changed: no extracted fact moves."""
    data = bytearray(src.read_bytes())
    at = data.rfind(b"GCC:")
    assert at > 0, "the gcc build must carry a .comment"
    data[at] ^= 0x20
    out.write_bytes(bytes(data))
    return out


_ELF_CELLS = {
    # cell: (old, new, same input proven)
    "stored_vs_live_identical": ("lib.json", "lib.so", True),
    "stored_vs_stored_identical": ("lib.json", "copy.json", True),
    "stored_vs_live_one_byte": ("lib.json", "flip.so", False),
    "stored_vs_stored_one_byte": ("lib.json", "flip.json", False),
    "older_stored_vs_live_identical": ("older.json", "lib.so", False),
    "linker_script_stored_vs_live_target": ("script.json", "lib.so", True),
    # The one-byte rebuild under the same file name, both stored. The flipped
    # byte is in ``.comment``, which the dump reads (the build-mode producer),
    # so the two dumps' evidence differs too: UNCONFIRMED, exactly as before
    # the fact existed (the PE same-name cell is the one no fact separates).
    "stored_vs_stored_same_name_one_byte": (
        "old/lib.json",
        "new/lib.json",
        False,
    ),
}


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds and reads an ELF .so")
@pytest.mark.parametrize("cell", sorted(_ELF_CELLS))
def test_elf_pairs_read_the_recorded_identity(tmp_path: Path, cell: str) -> None:
    lib = _gcc_build(tmp_path, "lib.so")
    flip = _flip_comment_byte(lib, tmp_path / "flip.so")
    copy = Path(shutil.copy(lib, tmp_path / "copy.so"))
    script = tmp_path / "libscript.so"
    script.write_text(f"INPUT({lib.name})\n", encoding="utf-8")
    stored = _dump(lib, tmp_path / "lib.json")
    operands = {
        "lib.so": lib,
        "flip.so": flip,
        "lib.json": stored,
        "copy.json": _dump(copy, tmp_path / "copy.json"),
        "flip.json": _dump(flip, tmp_path / "flip.json"),
        "older.json": _strip_recorded_sha(stored, tmp_path / "older.json"),
        "script.json": _dump(script, tmp_path / "script.json"),
    }
    for side, binary in (("old", lib), ("new", flip)):
        (tmp_path / side).mkdir()
        same_name = Path(shutil.copy(binary, tmp_path / side / "lib.so"))
        operands[f"{side}/lib.json"] = _dump(same_name, tmp_path / side / "lib.json")
    old_key, new_key, proven = _ELF_CELLS[cell]
    # Oracle: the bytes each operand stands for (a linker script resolves to
    # its target, as the loader's link step would).
    stands_for = {"lib": lib, "copy": copy, "flip": flip, "older": lib, "script": lib}
    stands_for.update({"old/lib": lib, "new/lib": flip})
    same_bytes = _sha(stands_for[old_key.split(".")[0]]) == _sha(
        stands_for[new_key.split(".")[0]]
    )
    assert proven is (same_bytes and not cell.startswith("older_"))
    code, report = _invoke("compare", str(operands[old_key]), str(operands[new_key]))
    # Built without -g and compared without headers: no side has type evidence.
    assert_claim_outcome(
        code, report, observed="NO_CHANGE", lacking=[] if proven else _BOTH
    )

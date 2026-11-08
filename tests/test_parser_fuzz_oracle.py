"""Property-based fuzzing of abicheck's input parsers against one oracle.

The oracle is ``_parser_fuzz_oracle.enforce``. For a corrupted input a parser
must either raise an allowed exception type, record an explicit failure, or
read as non-empty. It must never come back empty and clean, and it must never
raise a type outside its allowed set or hang.

Mutators: truncation, byte flips and huge 4-byte counts for binary and XML
inputs; for the JSON snapshot, structural mutations as well (dropped keys,
wrong types, huge integers).

Each parser has a fast test (fixed seed, few examples: the default lane) and a
``slow`` test (many examples). Open gaps are non-strict ``xfail`` (see ``XFAIL_RAISES``), each with its
``docs/contribute/known-gaps.md`` entry, and each gap has a canary that asserts
its gapped answer and fails when the gap closes.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("hypothesis")
from _parser_fuzz_oracle import (  # noqa: E402
    CASTXML_SEED,
    PE_SEED,
    OracleViolation,
    Outcome,
    build_shared_object,
    enforce,
    flip_byte,
    huge_count,
    json_mutate,
    snapshot_seed_document,
    truncate,
)
from hypothesis import (  # noqa: E402
    HealthCheck,
    given,
    settings,
    strategies as st,  # noqa: E402
)

from abicheck.dwarf_metadata import parse_dwarf_metadata  # noqa: E402
from abicheck.elf_metadata import parse_elf_metadata  # noqa: E402
from abicheck.errors import AbicheckError  # noqa: E402
from abicheck.extract.parse_failures import recording_export_read_failures  # noqa: E402
from abicheck.pe_metadata import parse_pe_metadata  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402
from abicheck.storage.castxml_xml import parse_castxml_text  # noqa: E402

FAST = dict(max_examples=12, derandomize=True, deadline=None)
SLOW = dict(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

PE_GAP = (
    "known gap: a truncated or corrupted PE export directory parses with zero "
    "exports and export_read 'unrecorded'; no failure is recorded"
)
DWARF_GAP = (
    "known gap: parse_dwarf_metadata swallows ELF and per-CU errors without a "
    "recorded failure, and lets a MemoryError escape: corrupt DWARF reads as "
    "empty or crashes"
)
SNAPSHOT_GAP = (
    "known gap: a malformed storage-v2 snapshot document leaks KeyError, "
    "TypeError or AttributeError (eagerly from load, or lazily on first "
    "declarations access) instead of a typed SnapshotError/ValidationError"
)

# Hypothesis groups distinct failures of one test into an ExceptionGroup, so an
# xfail must accept it alongside OracleViolation.
#
# Gap xfails are non-strict on purpose. Hypothesis generates different examples
# on each Python version (py3.15 pins hypothesis<6.140), so whether a fixed-seed
# fast test reaches a gap is version-dependent: strict would turn an unlucky
# draw into a red CI run. The deterministic canaries assert the gapped answer
# with strict semantics, so closing a gap still turns something red.
XFAIL_RAISES = (OracleViolation, ExceptionGroup)

_SEEDS: dict[str, bytes] = {}


def _so_seed(*, debug: bool) -> bytes:
    key = "dwarf" if debug else "elf"
    if key not in _SEEDS:
        if shutil.which("gcc") is None:
            pytest.skip("gcc is required to build the ELF/DWARF parser seeds")
        with tempfile.TemporaryDirectory(prefix="parser-fuzz-seed-") as d:
            # A configured gcc that fails raises CalledProcessError: a failure.
            _SEEDS[key] = build_shared_object(Path(d), debug=debug)
    return _SEEDS[key]


def _bytes_mutation(data: bytes, op: str, a: int, b: int) -> bytes:
    if op == "truncate":
        return truncate(data, a % (len(data) + 1))
    if op == "flip":
        return flip_byte(data, a, b)
    return huge_count(data, a)


_BYTES_OPS = st.tuples(
    st.sampled_from(["truncate", "flip", "huge"]),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.integers(min_value=0, max_value=255),
)
_STRUCT_OPS = st.tuples(
    st.sampled_from(["drop", "retype", "huge"]),
    st.integers(min_value=0, max_value=2**31 - 1),
    st.sampled_from([None, "x", [], {}, -1, 2**40, True]),
)


def _bytes_case(
    name: str,
    seed: bytes,
    parse: Callable[[Path], Any],
    fingerprint: Callable[[Any], int],
    mutation: tuple[str, int, int],
    *,
    has_channel: bool,
    allowed: tuple[type[BaseException], ...] = (),
) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        work = Path(d)
        seed_path = work / "seed.bin"
        seed_path.write_bytes(seed)
        seed_fp = fingerprint(parse(seed_path))
        target = work / "mut.bin"
        target.write_bytes(_bytes_mutation(seed, *mutation))
        return enforce(
            name,
            lambda: parse(target),
            fingerprint,
            seed_fp=seed_fp,
            allowed=allowed,
            has_failure_channel=has_channel,
        )


def _json_case(doc: dict[str, Any], seed_fp: int) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / "snap.json"
        target.write_bytes(json.dumps(doc).encode())
        return enforce(
            "snapshot",
            lambda: load_snapshot(target),
            _snapshot_fp,
            seed_fp=seed_fp,
            allowed=(AbicheckError, ValueError),
            has_failure_channel=False,
        )


def _snapshot_fp(snap: Any) -> int:
    decls = snap.declarations
    return len(decls.functions) + len(decls.variables) + len(decls.types)


def _snapshot_seed_fp() -> int:
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        seed = Path(d) / "seed.json"
        seed.write_bytes(json.dumps(snapshot_seed_document()).encode())
        return _snapshot_fp(load_snapshot(seed))


# --------------------------------------------------------------------------
# ELF (parse_elf_metadata): failures recorded through the export-read channel.
# --------------------------------------------------------------------------


def _elf_fp(meta: Any) -> int:
    return len(meta.symbols) + len(meta.needed) + (meta.soname is not None)


@settings(**FAST)
@given(mutation=_BYTES_OPS)
def test_elf_fast(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "elf",
        _so_seed(debug=False),
        parse_elf_metadata,
        _elf_fp,
        mutation,
        has_channel=True,
    )
    assert (
        outcome.status == "clean"
        or outcome.status == "recorded"
        or outcome.status.startswith("raised:")
    )


@pytest.mark.slow
@settings(**SLOW)
@given(mutation=_BYTES_OPS)
def test_elf_slow(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "elf",
        _so_seed(debug=False),
        parse_elf_metadata,
        _elf_fp,
        mutation,
        has_channel=True,
    )
    assert (
        outcome.status == "clean"
        or outcome.status == "recorded"
        or outcome.status.startswith("raised:")
    )


# --------------------------------------------------------------------------
# PE (parse_pe_metadata): committed DLL seed. Known gap, see PE_GAP.
# --------------------------------------------------------------------------


def _pe_fp(meta: Any) -> int:
    return len(meta.exports) + len(meta.imports)


@pytest.mark.xfail(strict=False, raises=OracleViolation, reason=PE_GAP)
@settings(**FAST)
@given(mutation=_BYTES_OPS)
def test_pe_fast(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "pe",
        PE_SEED.read_bytes(),
        parse_pe_metadata,
        _pe_fp,
        mutation,
        has_channel=True,
    )
    assert outcome.status == "clean" or outcome.status == "recorded"


@pytest.mark.slow
@pytest.mark.xfail(strict=False, raises=OracleViolation, reason=PE_GAP)
@settings(**SLOW)
@given(mutation=_BYTES_OPS)
def test_pe_slow(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "pe",
        PE_SEED.read_bytes(),
        parse_pe_metadata,
        _pe_fp,
        mutation,
        has_channel=True,
    )
    assert outcome.status == "clean" or outcome.status == "recorded"


# --------------------------------------------------------------------------
# DWARF (parse_dwarf_metadata): no failure channel. Known gap, see DWARF_GAP.
# --------------------------------------------------------------------------


def _dwarf_fp(meta: Any) -> int:
    return len(meta.structs) + len(meta.enums) + len(meta.base_types)


@pytest.mark.xfail(strict=False, raises=OracleViolation, reason=DWARF_GAP)
@settings(**FAST)
@given(mutation=_BYTES_OPS)
def test_dwarf_fast(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "dwarf",
        _so_seed(debug=True),
        parse_dwarf_metadata,
        _dwarf_fp,
        mutation,
        has_channel=False,
    )
    assert outcome.status == "clean"


@pytest.mark.slow
@pytest.mark.xfail(strict=False, raises=OracleViolation, reason=DWARF_GAP)
@settings(**SLOW)
@given(mutation=_BYTES_OPS)
def test_dwarf_slow(mutation: tuple[str, int, int]) -> None:
    outcome = _bytes_case(
        "dwarf",
        _so_seed(debug=True),
        parse_dwarf_metadata,
        _dwarf_fp,
        mutation,
        has_channel=False,
    )
    assert outcome.status == "clean"


# --------------------------------------------------------------------------
# castxml XML (parse_castxml_text): text in, element tree out.
# --------------------------------------------------------------------------


def _xml_fp(root: ET.Element) -> int:
    return len(list(root))


def _text_mutation(text: str, op: str, a: int, b: int) -> str:
    if op == "truncate":
        return text[: a % (len(text) + 1)]
    if op == "flip":
        i = a % len(text)
        return text[:i] + chr(0x20 + b % 0x5F) + text[i + 1 :]
    i = next((k for k, c in enumerate(text) if c.isdigit()), 0)
    return text[:i] + "9" * 12 + text[i + 1 :]


def _castxml_case(mutation: tuple[str, int, int]) -> Outcome:
    op, a, b = mutation
    return enforce(
        "castxml",
        lambda: parse_castxml_text(_text_mutation(CASTXML_SEED, op, a, b)),
        _xml_fp,
        seed_fp=_xml_fp(parse_castxml_text(CASTXML_SEED)),
        allowed=(ET.ParseError, ValueError),
        has_failure_channel=False,
    )


@settings(**FAST)
@given(mutation=_BYTES_OPS)
def test_castxml_fast(mutation: tuple[str, int, int]) -> None:
    outcome = _castxml_case(mutation)
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


@pytest.mark.slow
@settings(**SLOW)
@given(mutation=_BYTES_OPS)
def test_castxml_slow(mutation: tuple[str, int, int]) -> None:
    outcome = _castxml_case(mutation)
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


# --------------------------------------------------------------------------
# Storage snapshot (load_snapshot): byte-level and structural mutations.
# --------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, raises=XFAIL_RAISES, reason=SNAPSHOT_GAP)
@settings(**FAST)
@given(mutation=_BYTES_OPS)
def test_snapshot_bytes_fast(mutation: tuple[str, int, int]) -> None:
    seed = json.dumps(snapshot_seed_document()).encode()
    outcome = _bytes_case(
        "snapshot",
        seed,
        load_snapshot,
        _snapshot_fp,
        mutation,
        has_channel=False,
        allowed=(AbicheckError, ValueError),
    )
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


@pytest.mark.slow
@pytest.mark.xfail(strict=False, raises=XFAIL_RAISES, reason=SNAPSHOT_GAP)
@settings(**SLOW)
@given(mutation=_BYTES_OPS)
def test_snapshot_bytes_slow(mutation: tuple[str, int, int]) -> None:
    seed = json.dumps(snapshot_seed_document()).encode()
    outcome = _bytes_case(
        "snapshot",
        seed,
        load_snapshot,
        _snapshot_fp,
        mutation,
        has_channel=False,
        allowed=(AbicheckError, ValueError),
    )
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


@pytest.mark.xfail(strict=False, raises=XFAIL_RAISES, reason=SNAPSHOT_GAP)
@settings(**FAST)
@given(mutation=_STRUCT_OPS)
def test_snapshot_struct_fast(mutation: tuple[str, int, Any]) -> None:
    op, pick, value = mutation
    outcome = _json_case(
        json_mutate(snapshot_seed_document(), op, pick, value), _snapshot_seed_fp()
    )
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


@pytest.mark.slow
@pytest.mark.xfail(strict=False, raises=XFAIL_RAISES, reason=SNAPSHOT_GAP)
@settings(**SLOW)
@given(mutation=_STRUCT_OPS)
def test_snapshot_struct_slow(mutation: tuple[str, int, Any]) -> None:
    op, pick, value = mutation
    outcome = _json_case(
        json_mutate(snapshot_seed_document(), op, pick, value), _snapshot_seed_fp()
    )
    assert outcome.status == "clean" or outcome.status.startswith("raised:")


# --------------------------------------------------------------------------
# Known-gap canaries: each asserts the gap's *current* answer and passes today.
# They fail when the gap closes; see docs/contribute/known-gaps.md.
# --------------------------------------------------------------------------


@pytest.mark.known_gap_canary
def test_pe_truncated_export_directory_reads_empty_unrecorded() -> None:
    """The committed DLL cut to 1405 bytes parses, returns no exports, and records
    no failure: export_read stays 'unrecorded'."""
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / "pe-trunc-1405.dll"
        target.write_bytes(PE_SEED.read_bytes()[:1405])
        with recording_export_read_failures() as sink:
            meta = parse_pe_metadata(target)
    assert meta.exports == []
    assert sink == []
    assert meta.export_read == "unrecorded"


@pytest.mark.known_gap_canary
def test_dwarf_single_byte_corruption_reads_empty_without_failure() -> None:
    """Some single-byte corruption of the DWARF seed reads as no structs, enums or
    base types, with no failure recorded: the silent-empty read."""
    seed = _so_seed(debug=True)
    step = max(1, len(seed) // 400)
    silent: list[int] = []
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / "corrupt.so"
        for off in range(0, len(seed), step):
            target.write_bytes(flip_byte(seed, off, 0xFF))
            try:
                with recording_export_read_failures() as sink:
                    meta = parse_dwarf_metadata(target)
            except Exception:  # noqa: BLE001 - a crash is the other half of the gap
                continue
            if not sink and _dwarf_fp(meta) == 0:
                silent.append(off)
    assert silent, "no silent-empty corruption found: the DWARF gap may have closed"


@pytest.mark.known_gap_canary
def test_snapshot_missing_function_mangled_key_leaks_keyerror() -> None:
    """Dropping one function's ``mangled`` key from a storage-v2 snapshot is not a
    typed error: load or first declarations access raises KeyError."""
    doc = snapshot_seed_document()
    del doc["sections"]["declarations"]["payload"]["functions"][0]["mangled"]
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / "snap-no-mangled.json"
        target.write_bytes(json.dumps(doc).encode())
        with pytest.raises(KeyError, match="mangled"):
            load_snapshot(target).declarations

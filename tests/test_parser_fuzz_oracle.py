"""Property-based fuzzing of abicheck's input parsers against one oracle.

The oracle is ``_parser_fuzz_oracle.enforce``. For a corrupted input a parser
must raise an allowed exception type, record an explicit ``partial``/``failed``
export read, or read as non-empty (and, for a truncation, lose no fact). It must
never raise a type outside its allowed set or run past its time budget.

Mutators: truncation, byte flips and huge 4-byte counts for binary and XML
inputs; structural mutations (dropped keys, wrong types, huge integers) for the
JSON snapshot and the clang AST.

Each parser has a fast test (fixed seed, few examples: the default lane) and a
``slow`` test (many examples); DWARF is ``slow`` only, its fast-lane signal is
its canary. A parser with an open gap runs through :func:`_against_gap`: the
test is marked xfail only when *every* failure it found matches that gap's
message pattern, so a new kind of violation still fails. Each gap has a
``docs/contribute/known-gaps.md`` entry and a canary that asserts the gapped
answer and fails when the gap closes.
"""

from __future__ import annotations

import copy
import io
import json
import re
import shutil
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("hypothesis")
from _parser_fuzz_oracle import (  # noqa: E402
    CASTXML_EXPORTS,
    CASTXML_SEED,
    CLANG_EXPORTS,
    CLANG_HEADER,
    PE_SEED,
    OracleViolation,
    Outcome,
    _BudgetExceeded,
    build_macho_seed,
    build_shared_object,
    call_with_budget,
    clang_seed_document,
    enforce,
    flip_byte,
    huge_count,
    json_mutate,
    snapshot_seed_document,
    truncate,
)
from hypothesis import (  # noqa: E402
    HealthCheck,
    assume,
    given,
    settings,
    strategies as st,  # noqa: E402
)

from abicheck.dumper_clang import _ClangAstParser  # noqa: E402
from abicheck.dwarf_metadata import parse_dwarf_metadata  # noqa: E402
from abicheck.dwarf_unified import (  # noqa: E402
    parse_dwarf_metadata as parse_dwarf_metadata_unified,
)
from abicheck.elf_metadata import parse_elf_metadata  # noqa: E402
from abicheck.errors import AbicheckError  # noqa: E402
from abicheck.extract.headers.castxml.dumper import _CastxmlParser  # noqa: E402
from abicheck.extract.parse_failures import (  # noqa: E402
    note_export_read_failure,
    recording_export_read_failures,
)
from abicheck.macho_metadata import parse_macho_metadata  # noqa: E402
from abicheck.model.export_index import (  # noqa: E402
    all_export_names,
    build_raw_export_index_from_elf,
    build_raw_export_index_from_macho,
    build_raw_export_index_from_pe,
)
from abicheck.model.pe_facts import PeExport, PeMetadata  # noqa: E402
from abicheck.pe_metadata import parse_pe_metadata  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402
from abicheck.storage.castxml_xml import parse_castxml_text  # noqa: E402

FAST = dict(max_examples=12, derandomize=True, deadline=None)
SLOW = dict(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# --------------------------------------------------------------------------
# Known gaps: a reason and the violation messages that belong to it.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownGap:
    reason: str
    #: Every violation the test raises must match this (``re.search``).
    pattern: str


PE_GAP = KnownGap(
    "known gap: a truncated or corrupted PE export directory reads as fewer or "
    "zero exports with the read 'complete'; no failure is recorded",
    r"^pe: silent (empty read|partial loss) ",
)
MACHO_GAP = KnownGap(
    "known gap: a Mach-O cut inside its string table or export trie reads as "
    "fewer or zero exports with the read 'complete'; no failure is recorded",
    r"^macho: silent (empty read|partial loss) ",
)
DWARF_GAP = KnownGap(
    "known gap: parse_dwarf_metadata swallows ELF and per-CU errors without a "
    "recorded failure, and lets a MemoryError escape: corrupt DWARF reads as "
    "empty or crashes",
    r"^dwarf(_unified)?: (silent (empty read|partial loss) |unexpected MemoryError: )",
)
CLANG_GAP = KnownGap(
    "known gap: a clang AST node of the wrong JSON type leaks TypeError or "
    "AttributeError instead of MalformedClangAstError",
    r"^clang: unexpected (TypeError|AttributeError): ",
)
SNAPSHOT_GAP = KnownGap(
    "known gap: a malformed storage-v2 snapshot document leaks KeyError, "
    "TypeError or AttributeError (eagerly from load, or lazily on first "
    "declarations access) instead of a typed SnapshotError/ValidationError",
    r"^snapshot: unexpected (KeyError|TypeError|AttributeError): ",
)


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


def _against_gap(prop: Callable[[], None], gap: KnownGap | None) -> None:
    """Run the Hypothesis property *prop*; xfail only on *gap*'s own violations.

    Hypothesis raises one failure as itself and several distinct ones as an
    ``ExceptionGroup``. Either way every leaf must be an
    :class:`OracleViolation` matching ``gap.pattern`` to count as the known
    gap; anything else (a new violation kind, an error in the harness) fails.
    """
    try:
        prop()
    except (OracleViolation, BaseExceptionGroup) as exc:
        leaves = list(_leaves(exc))
        if gap is not None and all(
            isinstance(leaf, OracleViolation) and re.search(gap.pattern, str(leaf))
            for leaf in leaves
        ):
            pytest.xfail(f"{gap.reason} -- e.g. {leaves[0]}")
        raise


# --------------------------------------------------------------------------
# Seeds and per-parser specs (seed fact counts cached once per session).
# --------------------------------------------------------------------------

_SEEDS: dict[str, bytes] = {}
_SEED_FACTS: dict[str, int] = {}


def _so_seed(*, debug: bool) -> bytes:
    key = "dwarf" if debug else "elf"
    if key not in _SEEDS:
        if shutil.which("gcc") is None:
            pytest.skip("gcc is required to build the ELF/DWARF parser seeds")
        with tempfile.TemporaryDirectory(prefix="parser-fuzz-seed-") as d:
            # A configured gcc that fails raises CalledProcessError: a failure.
            _SEEDS[key] = build_shared_object(Path(d), debug=debug)
    return _SEEDS[key]


def _macho_seed() -> bytes:
    if "macho" not in _SEEDS:
        with tempfile.TemporaryDirectory(prefix="parser-fuzz-seed-") as d:
            _SEEDS["macho"] = build_macho_seed(Path(d))
    return _SEEDS["macho"]


@dataclass(frozen=True)
class BytesSpec:
    name: str
    seed: Callable[[], bytes]
    parse: Callable[[Path], Any]
    facts: Callable[[Any], int]
    export_index: Callable[[Any], Any] | None = None
    allowed: tuple[type[BaseException], ...] = ()


def _exports(index_of: Callable[[Any], Any]) -> Callable[[Any], int]:
    return lambda meta: len(all_export_names(index_of(meta)))


def _dwarf_facts(meta: Any) -> int:
    return len(meta.structs) + len(meta.enums) + len(meta.base_types)


def _snapshot_facts(snap: Any) -> int:
    decls = snap.declarations
    return len(decls.functions) + len(decls.variables) + len(decls.types)


def _snapshot_seed_bytes() -> bytes:
    return json.dumps(snapshot_seed_document()).encode()


ELF = BytesSpec(
    "elf",
    lambda: _so_seed(debug=False),
    parse_elf_metadata,
    _exports(build_raw_export_index_from_elf),
    export_index=build_raw_export_index_from_elf,
)
PE = BytesSpec(
    "pe",
    PE_SEED.read_bytes,
    parse_pe_metadata,
    _exports(build_raw_export_index_from_pe),
    export_index=build_raw_export_index_from_pe,
)
MACHO = BytesSpec(
    "macho",
    _macho_seed,
    parse_macho_metadata,
    _exports(build_raw_export_index_from_macho),
    export_index=build_raw_export_index_from_macho,
)
DWARF = BytesSpec(
    "dwarf", lambda: _so_seed(debug=True), parse_dwarf_metadata, _dwarf_facts
)
DWARF_UNIFIED = BytesSpec(
    "dwarf_unified",
    lambda: _so_seed(debug=True),
    parse_dwarf_metadata_unified,
    _dwarf_facts,
)
SNAPSHOT_BYTES = BytesSpec(
    "snapshot",
    _snapshot_seed_bytes,
    load_snapshot,
    _snapshot_facts,
    allowed=(AbicheckError, ValueError),
)


def _seed_facts(spec: BytesSpec, work: Path) -> int:
    if spec.name not in _SEED_FACTS:
        seed_path = work / "seed.bin"
        seed_path.write_bytes(spec.seed())
        _SEED_FACTS[spec.name] = spec.facts(spec.parse(seed_path))
        assert _SEED_FACTS[spec.name] > 0, f"{spec.name} seed holds no facts"
    return _SEED_FACTS[spec.name]


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


def _bytes_case(spec: BytesSpec, mutation: tuple[str, int, int]) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        work = Path(d)
        seed_facts = _seed_facts(spec, work)
        target = work / "mut.bin"
        target.write_bytes(_bytes_mutation(spec.seed(), *mutation))
        return enforce(
            spec.name,
            lambda: spec.parse(target),
            spec.facts,
            seed_facts=seed_facts,
            allowed=spec.allowed,
            export_index=spec.export_index,
            strict_loss=mutation[0] == "truncate",
        )


def _bytes_property(spec: BytesSpec, profile: dict[str, Any]) -> Callable[[], None]:
    @settings(**profile)
    @given(mutation=_BYTES_OPS)
    def prop(mutation: tuple[str, int, int]) -> None:
        _bytes_case(spec, mutation)

    return prop


# --------------------------------------------------------------------------
# Binary parsers. ELF has no open gap: any violation fails.
# --------------------------------------------------------------------------


def test_elf_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(ELF, FAST), None)


@pytest.mark.slow
def test_elf_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(ELF, SLOW), None)


def test_pe_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(PE, FAST), PE_GAP)


@pytest.mark.slow
def test_pe_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(PE, SLOW), PE_GAP)


def test_macho_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(MACHO, FAST), MACHO_GAP)


@pytest.mark.slow
def test_macho_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(MACHO, SLOW), MACHO_GAP)


@pytest.mark.slow
def test_dwarf_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(DWARF, SLOW), DWARF_GAP)


@pytest.mark.slow
def test_dwarf_unified_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(DWARF_UNIFIED, SLOW), DWARF_GAP)


# --------------------------------------------------------------------------
# castxml: XML text -> element tree (parse_castxml_text) -> model (_CastxmlParser).
# --------------------------------------------------------------------------


def _castxml_facts(text: str) -> int:
    parser = _CastxmlParser(parse_castxml_text(text), set(CASTXML_EXPORTS), set())
    return len(parser.parse_functions()) + len(parser.parse_types())


def _text_mutation(text: str, op: str, a: int, b: int) -> str:
    if op == "truncate":
        return text[: a % (len(text) + 1)]
    if op == "flip":
        i = a % len(text)
        return text[:i] + chr(0x20 + b % 0x5F) + text[i + 1 :]
    i = next((k for k, c in enumerate(text) if c.isdigit()), 0)
    return text[:i] + "9" * 12 + text[i + 1 :]


def _castxml_property(profile: dict[str, Any]) -> Callable[[], None]:
    seed_facts = _castxml_facts(CASTXML_SEED)

    @settings(**profile)
    @given(mutation=_BYTES_OPS)
    def prop(mutation: tuple[str, int, int]) -> None:
        text = _text_mutation(CASTXML_SEED, *mutation)
        enforce(
            "castxml",
            lambda: text,
            _castxml_facts,
            seed_facts=seed_facts,
            allowed=(ET.ParseError, ValueError),
            strict_loss=mutation[0] == "truncate",
        )

    return prop


def test_castxml_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_castxml_property(FAST), None)


@pytest.mark.slow
def test_castxml_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_castxml_property(SLOW), None)


# --------------------------------------------------------------------------
# clang -ast-dump=json tree (_ClangAstParser): structural mutations.
# --------------------------------------------------------------------------


def _clang_facts(tree: dict[str, Any]) -> int:
    parser = _ClangAstParser(
        tree, set(CLANG_EXPORTS), set(), public_header_paths=[CLANG_HEADER]
    )
    return (
        len(parser.parse_functions())
        + len(parser.parse_variables())
        + len(parser.parse_types())
        + len(parser.parse_enums())
    )


def _clang_property(profile: dict[str, Any]) -> Callable[[], None]:
    seed_facts = _clang_facts(clang_seed_document())

    @settings(**profile)
    @given(mutation=_STRUCT_OPS)
    def prop(mutation: tuple[str, int, Any]) -> None:
        tree = json_mutate(clang_seed_document(), *mutation)
        # A translation unit with no ``inner`` (absent, or ``null``, which
        # ast_inner documents as absent) is a valid empty header, not damage.
        assume(tree.get("inner") is not None)
        enforce(
            "clang",
            lambda: copy.deepcopy(tree),
            _clang_facts,
            seed_facts=seed_facts,
            allowed=(AbicheckError, ValueError),
        )

    return prop


def test_clang_ast_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_clang_property(FAST), CLANG_GAP)


@pytest.mark.slow
def test_clang_ast_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_clang_property(SLOW), CLANG_GAP)


# --------------------------------------------------------------------------
# Storage snapshot (load_snapshot): byte-level and structural mutations.
# --------------------------------------------------------------------------


def _snapshot_struct_property(profile: dict[str, Any]) -> Callable[[], None]:
    @settings(**profile)
    @given(mutation=_STRUCT_OPS)
    def prop(mutation: tuple[str, int, Any]) -> None:
        doc = json_mutate(snapshot_seed_document(), *mutation)
        with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
            work = Path(d)
            seed_facts = _seed_facts(SNAPSHOT_BYTES, work)
            target = work / "snap.json"
            target.write_bytes(json.dumps(doc).encode())
            enforce(
                "snapshot",
                lambda: load_snapshot(target),
                _snapshot_facts,
                seed_facts=seed_facts,
                allowed=(AbicheckError, ValueError),
            )

    return prop


def test_snapshot_bytes_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(SNAPSHOT_BYTES, FAST), SNAPSHOT_GAP)


@pytest.mark.slow
def test_snapshot_bytes_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(SNAPSHOT_BYTES, SLOW), SNAPSHOT_GAP)


def test_snapshot_struct_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_snapshot_struct_property(FAST), SNAPSHOT_GAP)


@pytest.mark.slow
def test_snapshot_struct_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_snapshot_struct_property(SLOW), SNAPSHOT_GAP)


# --------------------------------------------------------------------------
# The oracle itself: each check fires on the input it exists for.
# --------------------------------------------------------------------------


def _pe_meta(*names: str) -> PeMetadata:
    return PeMetadata(
        machine="IMAGE_FILE_MACHINE_AMD64",
        exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(names)],
    )


def test_oracle_flags_partial_loss_without_a_recorded_failure() -> None:
    with pytest.raises(
        OracleViolation, match=r"^pe: silent partial loss \(seed had 3 .* has 1"
    ):
        enforce(
            "pe",
            lambda: _pe_meta("a"),
            _exports(build_raw_export_index_from_pe),
            seed_facts=3,
            export_index=build_raw_export_index_from_pe,
            strict_loss=True,
        )


def test_oracle_accepts_partial_loss_recorded_as_partial() -> None:
    def parse() -> PeMetadata:
        note_export_read_failure("export directory cut short")
        return _pe_meta("a")

    outcome = enforce(
        "pe",
        parse,
        _exports(build_raw_export_index_from_pe),
        seed_facts=3,
        export_index=build_raw_export_index_from_pe,
        strict_loss=True,
    )
    assert outcome == Outcome("recorded:partial", 1)


def test_oracle_flip_loss_is_not_a_partial_loss_violation() -> None:
    """Without ``strict_loss`` (a byte flip) fewer facts is not judged damage."""
    outcome = enforce(
        "pe",
        lambda: _pe_meta("a"),
        _exports(build_raw_export_index_from_pe),
        seed_facts=3,
        export_index=build_raw_export_index_from_pe,
    )
    assert outcome == Outcome("clean", 1)


def test_oracle_flags_silent_empty_read() -> None:
    with pytest.raises(OracleViolation, match=r"^pe: silent empty read"):
        enforce(
            "pe",
            _pe_meta,
            _exports(build_raw_export_index_from_pe),
            seed_facts=3,
            export_index=build_raw_export_index_from_pe,
        )


def test_oracle_flags_unexpected_exception_type() -> None:
    def parse() -> Any:
        raise KeyError("mangled")

    with pytest.raises(OracleViolation, match=r"^x: unexpected KeyError: 'mangled'"):
        enforce("x", parse, len, seed_facts=1, allowed=(ValueError,))


def _spin_swallowing(stop: threading.Event) -> None:
    """A hang that a blanket ``except Exception`` would hide (PE has one)."""
    while not stop.is_set():
        try:
            time.sleep(0.01)
        except Exception:  # noqa: BLE001, S110 - the shape under test
            pass


@pytest.mark.parametrize("prefer_signal", [True, False], ids=["alarm", "watchdog"])
def test_budget_interrupts_a_hang(prefer_signal: bool) -> None:
    stop = threading.Event()
    t0 = time.monotonic()
    try:
        with pytest.raises(_BudgetExceeded):
            call_with_budget(
                lambda: _spin_swallowing(stop), 0.2, prefer_signal=prefer_signal
            )
    finally:
        stop.set()
    assert time.monotonic() - t0 < 5


def test_oracle_reports_a_hang_as_a_violation() -> None:
    stop = threading.Event()
    try:
        with pytest.raises(
            OracleViolation, match=r"^slow: hang \(ran past the 0.2s budget\)"
        ):
            enforce(
                "slow", lambda: _spin_swallowing(stop), len, seed_facts=0, budget_s=0.2
            )
    finally:
        stop.set()


def test_against_gap_fails_on_a_violation_outside_the_gap() -> None:
    def prop() -> None:
        raise OracleViolation("pe: unexpected KeyError: 'x'")

    with pytest.raises(OracleViolation, match="unexpected KeyError"):
        _against_gap(prop, PE_GAP)


def test_against_gap_fails_when_one_group_member_is_outside_the_gap() -> None:
    def prop() -> None:
        raise ExceptionGroup(
            "two bugs",
            [
                OracleViolation("pe: silent empty read (seed had 3 facts)"),
                OracleViolation("pe: hang (ran past the 20.0s budget)"),
            ],
        )

    with pytest.raises(ExceptionGroup):
        _against_gap(prop, PE_GAP)


def test_against_gap_xfails_on_the_gap_own_violations() -> None:
    def prop() -> None:
        raise ExceptionGroup(
            "two examples",
            [
                OracleViolation("pe: silent empty read (seed had 3 facts)"),
                OracleViolation("pe: silent partial loss (seed had 3 facts)"),
            ],
        )

    with pytest.raises(pytest.xfail.Exception, match="known gap"):
        _against_gap(prop, PE_GAP)


# --------------------------------------------------------------------------
# Known-gap canaries: each asserts the gap's *current* answer and passes today.
# They fail when the gap closes; see docs/contribute/known-gaps.md.
# --------------------------------------------------------------------------


def _parse_recorded(
    parse: Callable[[Path], Any], data: bytes, name: str
) -> tuple[Any, list[str]]:
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / name
        target.write_bytes(data)
        with recording_export_read_failures() as sink:
            meta = parse(target)
    return meta, sink


@pytest.mark.known_gap_canary
def test_pe_truncated_export_directory_reads_empty_unrecorded() -> None:
    """The committed DLL cut to 1405 bytes parses, returns no exports, and records
    no failure: export_read stays 'unrecorded'."""
    meta, sink = _parse_recorded(
        parse_pe_metadata, PE_SEED.read_bytes()[:1405], "pe.dll"
    )
    assert meta.exports == []
    assert sink == []
    assert meta.export_read == "unrecorded"


@pytest.mark.known_gap_canary
def test_macho_truncated_string_table_reads_empty_unrecorded() -> None:
    """The Mach-O seed cut before its string table ends parses with no exports
    and no recorded failure."""
    seed = _macho_seed()
    meta, sink = _parse_recorded(
        parse_macho_metadata, seed[: len(seed) - 40], "m.dylib"
    )
    assert all_export_names(build_raw_export_index_from_macho(meta)) == frozenset()
    assert sink == []
    assert meta.export_read == "unrecorded"


@pytest.mark.known_gap_canary
def test_dwarf_corrupt_abbrev_table_reads_empty_without_failure() -> None:
    """Flipping the first byte of ``.debug_abbrev`` makes both DWARF readers return
    no structs, enums or base types, with no failure recorded and no exception."""
    from elftools.elf.elffile import ELFFile

    seed = _so_seed(debug=True)
    abbrev = ELFFile(io.BytesIO(seed)).get_section_by_name(".debug_abbrev")
    assert abbrev is not None, "the -g seed has no .debug_abbrev"
    corrupt = flip_byte(seed, abbrev["sh_offset"], 0xFF)
    for parse in (parse_dwarf_metadata, parse_dwarf_metadata_unified):
        meta, sink = _parse_recorded(parse, corrupt, "corrupt.so")
        assert sink == []
        assert _dwarf_facts(meta) == 0


@pytest.mark.known_gap_canary
def test_clang_ast_object_valued_kind_leaks_typeerror() -> None:
    """A FunctionDecl whose ``kind`` is a JSON object is not a typed error: the
    walk raises TypeError (unhashable dict) rather than MalformedClangAstError."""
    tree = clang_seed_document()
    tree["inner"][0]["kind"] = {}
    with pytest.raises(TypeError, match="unhashable"):
        _clang_facts(tree)


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

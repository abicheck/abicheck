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
    object_format,
    section_names_unusable,
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
    recording_debug_info_read_failures,
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


DWARF_GAP = KnownGap(
    "known gap: a corrupted .debug_abbrev entry that still parses ends the "
    "DIE stream early, so the DWARF readers read no types and record nothing",
    r"^dwarf(_unified)?: silent (empty read|partial loss) ",
)
CLANG_GAP = KnownGap(
    "known gap: a clang AST node of the wrong JSON type leaks TypeError or "
    "AttributeError instead of MalformedClangAstError",
    r"^clang: unexpected (TypeError|AttributeError): ",
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
#: Why the host cannot build the ELF/DWARF seeds, once found (see _so_seed).
_NO_ELF_SEED: list[str] = []
_SEED_FACTS: dict[str, int] = {}


def _so_seed(*, debug: bool) -> bytes:
    key = "dwarf" if debug else "elf"
    if _NO_ELF_SEED:
        pytest.skip(_NO_ELF_SEED[0])
    if key not in _SEEDS:
        if shutil.which("gcc") is None:
            pytest.skip("gcc is required to build the ELF/DWARF parser seeds")
        with tempfile.TemporaryDirectory(prefix="parser-fuzz-seed-") as d:
            # A configured gcc that fails raises CalledProcessError: a failure.
            seed = build_shared_object(Path(d), debug=debug)
        # The capability the seeds need is an ELF-producing gcc, not a `gcc` on
        # PATH: macOS's gcc is clang emitting Mach-O and a Windows gcc is MinGW
        # emitting PE, and the ELF/DWARF oracles then fuzzed a non-ELF seed
        # ("elf seed holds no facts", "ELFError: Magic number does not match").
        fmt = object_format(seed)
        if fmt != "ELF":
            _NO_ELF_SEED.append(
                f"this host's gcc emits {fmt}, not ELF: the ELF/DWARF parser "
                "seeds need an ELF-targeting gcc"
            )
            pytest.skip(_NO_ELF_SEED[0])
        _SEEDS[key] = seed
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
    #: A debug-info reader: a recorded debug-info failure is its failure record.
    debug_info: bool = False


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
    "dwarf",
    lambda: _so_seed(debug=True),
    parse_dwarf_metadata,
    _dwarf_facts,
    debug_info=True,
)
DWARF_UNIFIED = BytesSpec(
    "dwarf_unified",
    lambda: _so_seed(debug=True),
    parse_dwarf_metadata_unified,
    _dwarf_facts,
    debug_info=True,
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
        mutated = _bytes_mutation(spec.seed(), *mutation)
        target.write_bytes(mutated)
        return enforce(
            spec.name,
            lambda: spec.parse(target),
            spec.facts,
            seed_facts=seed_facts,
            allowed=spec.allowed,
            export_index=spec.export_index,
            strict_loss=mutation[0] == "truncate",
            debug_info=spec.debug_info,
            absent_ok=spec.debug_info and section_names_unusable(mutated),
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
    _against_gap(_bytes_property(PE, FAST), None)


@pytest.mark.slow
def test_pe_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(PE, SLOW), None)


def test_macho_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(MACHO, FAST), None)


@pytest.mark.slow
def test_macho_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(MACHO, SLOW), None)


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
    _against_gap(_bytes_property(SNAPSHOT_BYTES, FAST), None)


@pytest.mark.slow
def test_snapshot_bytes_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_bytes_property(SNAPSHOT_BYTES, SLOW), None)


def test_snapshot_struct_fast() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_snapshot_struct_property(FAST), None)


@pytest.mark.slow
def test_snapshot_struct_slow() -> None:  # test-quality: ok enforce() asserts
    _against_gap(_snapshot_struct_property(SLOW), None)


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


#: A stand-in gap for the harness's own tests (the PE gap they used is closed).
_HARNESS_GAP = KnownGap(
    "known gap: harness example", r"^pe: silent (empty read|partial loss) "
)


def test_against_gap_fails_on_a_violation_outside_the_gap() -> None:
    def prop() -> None:
        raise OracleViolation("pe: unexpected KeyError: 'x'")

    with pytest.raises(OracleViolation, match="unexpected KeyError"):
        _against_gap(prop, _HARNESS_GAP)


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
        _against_gap(prop, _HARNESS_GAP)


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
        _against_gap(prop, _HARNESS_GAP)


# --------------------------------------------------------------------------
# Regressions for gaps this suite found and that are now closed (2026-11-13):
# a corrupt PE export directory, a Mach-O cut in its string table or export
# trie, corrupt DWARF, and a malformed storage-v2 document. Each states the
# invariant over a small exhaustive domain, judged against the intact seed's
# own parse rather than the parser's recording helper.
# --------------------------------------------------------------------------


def _parse_recorded(
    parse: Callable[[Path], Any], data: bytes, name: str
) -> tuple[Any, list[str]]:
    """``parse`` of *data*, plus every export-table and debug-info failure it
    recorded."""
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / name
        target.write_bytes(data)
        with (
            recording_export_read_failures() as sink,
            recording_debug_info_read_failures() as debug_sink,
        ):
            meta = parse(target)
    return meta, [*sink, *debug_sink]


def _pe_names(meta: Any) -> frozenset[str]:
    return frozenset(e.name for e in meta.exports)


def _macho_names(meta: Any) -> frozenset[str]:
    return all_export_names(build_raw_export_index_from_macho(meta))


@pytest.mark.parametrize(
    ("fmt", "seed", "parse", "names", "suffix"),
    (
        ("pe", lambda: PE_SEED.read_bytes(), parse_pe_metadata, _pe_names, ".dll"),
        ("macho", lambda: _macho_seed(), parse_macho_metadata, _macho_names, ".dylib"),
    ),
)
def test_every_truncation_records_a_failure_or_keeps_every_export(
    fmt: str,
    seed: Callable[[], bytes],
    parse: Callable[[Path], Any],
    names: Callable[[Any], frozenset[str]],
    suffix: str,
) -> None:
    """Every cut of the seed, at every byte, either records an export-table
    failure or still reads the intact seed's whole export set.

    The 2026-11-08 gaps were the PE seed cut to 1405-1683 bytes reading as
    zero to two of its three exports, and the Mach-O seed cut in its string
    table or export trie reading as none, both with nothing recorded.
    """
    data = seed()
    intact, intact_sink = _parse_recorded(parse, data, f"seed{suffix}")
    assert intact_sink == []
    expected = names(intact)
    assert len(expected) == 3, f"{fmt} seed should export three names"
    silent = []
    for cut in range(len(data)):
        meta, sink = _parse_recorded(parse, data[:cut], f"cut{suffix}")
        if not sink and names(meta) != expected:
            silent.append((cut, sorted(names(meta))))
    assert silent == [], f"{fmt}: cuts that lost exports silently: {silent[:5]}"


def test_pe_cut_inside_export_names_is_recorded() -> None:
    """The reported reproduction: the committed DLL cut to 1405 bytes."""
    meta, sink = _parse_recorded(
        parse_pe_metadata, PE_SEED.read_bytes()[:1405], "pe.dll"
    )
    assert meta.exports == []
    assert any("truncated image" in reason or "export" in reason for reason in sink)


@pytest.mark.parametrize("parse", (parse_dwarf_metadata, parse_dwarf_metadata_unified))
@pytest.mark.parametrize(
    "corruption",
    ("abbrev_first_byte", "debug_info_name_lost", "debug_info_unit_length"),
)
def test_unreadable_dwarf_is_recorded_not_empty(
    parse: Callable[[Path], Any], corruption: str
) -> None:
    """Debug info that is present but unreadable records a debug-info failure.

    Three independent ways one corrupted field hides the DWARF of a gcc
    ``-g`` seed: the abbreviation table's first byte, the ``.debug_info``
    section header's name, and the first unit's length. Each used to
    read as "no DWARF" (no structs, no enums), which a compare then reported
    as those types having been added.
    """
    from elftools.elf.elffile import ELFFile

    seed = _so_seed(debug=True)
    elf = ELFFile(io.BytesIO(seed))
    if corruption == "abbrev_first_byte":
        abbrev = elf.get_section_by_name(".debug_abbrev")
        assert abbrev is not None, "the -g seed has no .debug_abbrev"
        corrupt = flip_byte(seed, abbrev["sh_offset"], 0xFF)
    elif corruption == "debug_info_name_lost":
        index = next(
            i for i, sec in enumerate(elf.iter_sections()) if sec.name == ".debug_info"
        )
        at = elf.header["e_shoff"] + index * elf.header["e_shentsize"]
        # sh_name -> offset 0 of .shstrtab, the empty name.
        corrupt = seed[:at] + b"\0\0\0\0" + seed[at + 4 :]
    else:
        info = elf.get_section_by_name(".debug_info")
        assert info is not None
        at = info["sh_offset"]
        # The first unit's length claims more bytes than the section holds.
        corrupt = seed[:at] + b"\xf0\xff\xff\x0f" + seed[at + 4 :]
    intact, intact_sink = _parse_recorded(parse, seed, "seed.so")
    assert intact_sink == [] and _dwarf_facts(intact) > 0
    meta, sink = _parse_recorded(parse, corrupt, "corrupt.so")
    assert sink, (
        f"{corruption}: unreadable DWARF read as {_dwarf_facts(meta)} facts, unrecorded"
    )


def test_a_stripped_object_records_no_debug_failure() -> None:
    """The other half of the rule: no debug sections at all is absence, not a
    failure (``-g`` off), so the debug-info read records nothing."""
    for parse in (parse_dwarf_metadata, parse_dwarf_metadata_unified):
        meta, sink = _parse_recorded(parse, _so_seed(debug=False), "plain.so")
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


def _function_key_drops() -> list[str]:
    return sorted(
        snapshot_seed_document()["sections"]["declarations"]["payload"]["functions"][0]
    )


@pytest.mark.parametrize("key", ("mangled", "name", "return_type"))
def test_snapshot_missing_function_key_is_a_snapshot_error(key: str) -> None:
    """A storage-v2 document with a required function key removed is a typed
    ``SnapshotError`` at load or first declarations access -- not the
    ``KeyError`` it used to leak (2026-11-08)."""
    from abicheck.errors import SnapshotError

    assert key in _function_key_drops()
    doc = snapshot_seed_document()
    del doc["sections"]["declarations"]["payload"]["functions"][0][key]
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / f"snap-no-{key}.json"
        target.write_bytes(json.dumps(doc).encode())
        with pytest.raises(SnapshotError, match="malformed snapshot document"):
            load_snapshot(target).declarations


@pytest.mark.parametrize("value", (None, "x", 7, ["list"], True))
def test_snapshot_function_entry_of_the_wrong_type_is_a_snapshot_error(
    value: Any,
) -> None:
    """A function entry that is not an object raises ``SnapshotError``."""
    from abicheck.errors import SnapshotError

    doc = snapshot_seed_document()
    doc["sections"]["declarations"]["payload"]["functions"][0] = value
    with tempfile.TemporaryDirectory(prefix="parser-fuzz-") as d:
        target = Path(d) / "snap-bad-entry.json"
        target.write_bytes(json.dumps(doc).encode())
        with pytest.raises(SnapshotError):
            load_snapshot(target).declarations


@pytest.mark.known_gap_canary
def test_dwarf_abbrev_entry_that_still_parses_reads_empty_unrecorded() -> None:
    """Some single-byte change inside ``.debug_abbrev`` leaves every abbreviation
    decodable but ends the first unit's DIE stream early: the unified reader
    returns no structs, enums or base types and records no failure.

    The search stops at the first such byte; on the gcc seed several exist
    (e.g. zeroing byte 24). The gap closes when no byte produces this."""
    from elftools.elf.elffile import ELFFile

    seed = _so_seed(debug=True)
    abbrev = ELFFile(io.BytesIO(seed)).get_section_by_name(".debug_abbrev")
    assert abbrev is not None
    gapped = [
        i
        for i in range(abbrev["sh_size"])
        if _abbrev_flip_reads_empty_unrecorded(seed, abbrev["sh_offset"] + i)
    ]
    assert gapped, "no abbreviation byte reads as no types any more: the gap closed"
    # The same answer from the legacy reader at the first gapped byte.
    meta, sink = _parse_recorded(
        parse_dwarf_metadata,
        flip_byte(seed, abbrev["sh_offset"] + gapped[0], 0),
        "c.so",
    )
    assert (sink, _dwarf_facts(meta)) == ([], 0)


def _abbrev_flip_reads_empty_unrecorded(seed: bytes, at: int) -> bool:
    meta, sink = _parse_recorded(
        parse_dwarf_metadata_unified, flip_byte(seed, at, 0), "c.so"
    )
    return not sink and _dwarf_facts(meta) == 0

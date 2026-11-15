"""abiforge's evidence sets and what the harness gives each side (plan B0).

The unit tests pin the lattice and the dumper's flag contract. The integration
tests build real libraries and list their sections with the host's own tool
(``readelf``, ``objdump`` or ``otool``, by object format), so a ``{B,H}`` side
that still carried DWARF would fail here, not in a trust-the-flag assertion.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from abiforge.checkers import abicheck as checker_adapter
from abiforge.model import config as cfgmod
from abiforge.model.config import (
    BINARY,
    DWARF,
    HEADERS,
    HEADERS_STRIPPED,
    Config,
    EvidenceSet,
    Flags,
    Toolchain,
    default_config,
)
from abiforge.model.expect import evidence_floor
from abiforge.model.seeds import SEED_BY_NAME
from abiforge.operators import OP_BY_NAME
from abiforge.run import harness

GCC = Toolchain("gcc", 13, "gcc", "g++")


# --- the lattice -------------------------------------------------------------


def test_named_sets_are_the_plan_configurations() -> None:
    assert BINARY.atoms == frozenset({"B"})
    assert DWARF.atoms == frozenset({"B", "D"})
    assert HEADERS.atoms == frozenset({"B", "D", "H"})
    assert HEADERS_STRIPPED.atoms == frozenset({"B", "H"})


def test_inclusion_orders_levels_and_leaves_dwarf_and_stripped_headers_incomparable() -> (
    None
):
    assert BINARY <= DWARF <= HEADERS
    assert BINARY <= HEADERS_STRIPPED <= HEADERS
    # A rank cannot say this: neither dwarf nor stripped headers contains the other.
    assert not (DWARF <= HEADERS_STRIPPED)
    assert not (HEADERS_STRIPPED <= DWARF)


def test_an_evidence_set_must_contain_b_and_only_known_atoms() -> None:
    with pytest.raises(ValueError, match="always contains B"):
        EvidenceSet.of("H")
    with pytest.raises(ValueError, match="unknown evidence atoms"):
        EvidenceSet.of("B", "X")
    # The plan's full vocabulary is accepted (BD/S/G reach the set in PR 2).
    assert EvidenceSet.of("B", "H", "BD", "S", "G").atoms == frozenset(
        {"B", "H", "BD", "S", "G"}
    )


# --- Config: the stripped axis ----------------------------------------------


def test_stripped_is_only_meaningful_at_headers_and_normalizes_off_elsewhere() -> None:
    dwarf = default_config("c", GCC).with_(evidence="dwarf", stripped=True)
    assert dwarf.stripped is False
    assert dwarf.id == default_config("c", GCC).with_(evidence="dwarf").id
    assert dwarf.evidence_set == DWARF


def test_stripped_headers_is_a_distinct_build_with_its_own_atoms() -> None:
    base = default_config("c", GCC)
    stripped = base.with_(stripped=True)
    assert stripped.evidence_set == HEADERS_STRIPPED
    assert base.evidence_set == HEADERS
    assert stripped.build_id != base.build_id
    assert stripped.evidence_label == "headers.stripped"
    assert stripped.space_evidence == "headers.stripped.castxml"
    # The default headers cell keeps its historical id.
    assert base.evidence_label == "headers"


def test_build_data_and_sources_add_their_atoms() -> None:
    cfg = default_config("c", GCC).with_(stripped=True, build_data=True)
    assert cfg.evidence_set == EvidenceSet.of("B", "H", "BD")
    assert cfg.with_(sources=True).evidence_set == EvidenceSet.of("B", "H", "BD", "S")


def test_an_unknown_evidence_level_is_refused() -> None:
    with pytest.raises(ValueError, match="not in"):
        Config(std=cfgmod.STANDARD_BY_NAME["c17"], producer=GCC, evidence="stripped")


# --- floors: what a cell can show --------------------------------------------


def test_a_dwarf_fact_is_unobservable_in_stripped_headers_but_not_in_the_default_cell() -> (
    None
):
    op = OP_BY_NAME["enum_change_value"]  # min_evidence "dwarf"
    default = default_config("c", GCC)
    stripped = default.with_(stripped=True)
    assert op.expect(default).observable is True
    exp = op.expect(stripped)
    assert exp.observable is False
    assert exp.verdict_min == "NO_CHANGE"
    assert "headers.stripped evidence cannot show this edit" in (exp.note or "")


def test_a_header_fact_stays_observable_in_stripped_headers() -> None:
    exp = evidence_floor(
        OP_BY_NAME["enum_change_value"].base(),
        default_config("c", GCC).with_(stripped=True),
        "headers",
    )
    assert exp.observable is not False


# --- the dumper's flags -------------------------------------------------------


def _capture_dump_options(
    monkeypatch: pytest.MonkeyPatch, config: Config, out: Path
) -> list[str]:
    seen: dict[str, str | None] = {}

    def fake_snapshot(
        so: Path,
        headers: list[Path],
        version: str,
        is_cxx: bool,
        backend: str,
        gcc_options: str | None,
        **kw: object,
    ) -> object:
        seen["gcc_options"] = gcc_options
        return object()

    monkeypatch.setattr(checker_adapter, "snapshot", fake_snapshot)
    harness.build(SEED_BY_NAME["c_core"], config, out)
    assert seen["gcc_options"] is not None
    return seen["gcc_options"].split()


LAYOUT = Flags(pack=1, short_enums=True)


def test_stripped_headers_dumper_gets_only_std_include_and_public_defines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = default_config("c", GCC).with_(stripped=True, flags=LAYOUT)
    tokens = _capture_dump_options(monkeypatch, config, tmp_path / "bh")
    assert [t for t in tokens if not t.startswith("-I")] == ["-std=c17"]
    assert tokens[-1].startswith("-I") and tokens[-1].endswith("bh")
    for layout in ("-fpack-struct=1", "-fshort-enums"):
        assert layout not in tokens


def test_dwarf_cells_keep_the_layout_flags_the_build_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = default_config("c", GCC).with_(flags=LAYOUT)
    tokens = _capture_dump_options(monkeypatch, config, tmp_path / "bdh")
    assert "-fpack-struct=1" in tokens and "-fshort-enums" in tokens


def test_build_data_cells_still_hand_the_dumper_the_layout_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = default_config("c", GCC).with_(
        stripped=True, flags=LAYOUT, build_data=True
    )
    tokens = _capture_dump_options(monkeypatch, config, tmp_path / "bh_bd")
    assert "-fpack-struct=1" in tokens and "-fshort-enums" in tokens


# --- real binaries ------------------------------------------------------------


def _run_tool(cmd: list[str]) -> str:
    # A failed tool fails the test; it is never read as "no DWARF".
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{cmd[0]} failed: {proc.stderr}"
    assert proc.stdout.strip(), f"{cmd[0]} printed nothing"
    return proc.stdout


def _debug_sections_of(so: Path) -> list[str]:
    """The platform's own tools (not the harness's helper) for the host format."""
    if sys.platform == "darwin":
        sections = _run_tool(["otool", "-l", str(so)])
        stabs = _run_tool(["nm", "-a", "-p", str(so)])
        names = re.findall(r"sectname\s+(__debug_\S+)", sections)
        return names + re.findall(r"\bOSO\b\s+(\S.*)$", stabs, re.M)
    if sys.platform == "win32":
        return re.findall(
            r"^\s+\d+\s+(\.debug_\S+)",
            _run_tool(["objdump", "-h", str(so)]),
            re.M,
        )
    return re.findall(r"\]\s+(\.debug\S*)", _run_tool(["readelf", "-SW", str(so)]))


@pytest.mark.integration
def test_the_stripped_headers_side_has_no_debug_sections(tmp_path: Path) -> None:
    base = default_config("c", GCC)
    stripped = harness.build(
        SEED_BY_NAME["c_core"], base.with_(stripped=True), tmp_path / "bh"
    )
    assert _debug_sections_of(stripped.so) == []
    # Control: the same build with DWARF does carry sections, so the check can fail.
    with_dwarf = harness.build(SEED_BY_NAME["c_core"], base, tmp_path / "bdh")
    assert _debug_sections_of(with_dwarf.so)


@pytest.mark.integration
def test_the_binary_side_has_no_debug_sections(tmp_path: Path) -> None:
    built = harness.build(
        SEED_BY_NAME["c_core"],
        default_config("c", GCC).with_(evidence="binary"),
        tmp_path / "b",
    )
    assert _debug_sections_of(built.so) == []

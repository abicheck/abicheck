# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A stored snapshot and its live counterpart agree on the advanced-DWARF
extraction status, the verdict and the findings (schema v68, ADR-080).

The two libraries are real ``gcc -g`` builds of ``tests/fixtures/advanced_dwarf_status``.
The new side adds ``-fshort-wchar``, which changes the DWARF producer's
toolchain flags: the advanced-DWARF detector reports ``wchar_model_changed``
from those flags. Before schema v68 a stored snapshot did not carry the
extraction status, so it read ``unrecorded`` and fell back to the payload
inference; this test pins that the status round-trips as ``completed`` on
both paths and that they decide the same thing.

Oracle: the build flags this test passes to ``gcc`` (the compiler's own
choice, recorded in the DWARF producer string), and the live path's own
verdict for the same inputs. Excluded from the comparison, each for a stated
reason: timings and file identity/version labels (a live input is labelled
``old``/``new``; a stored snapshot without ``--version`` carries none).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.dumper import dump
from abicheck.model.dwarf_facts import AdvancedExtractionStatus
from abicheck.serialization import snapshot_from_dict

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("gcc")),
        reason="needs castxml and gcc",
    ),
    # The advanced-DWARF payload is read from an ELF image.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF-only fixture"),
]

FIXTURE = Path(__file__).parent / "fixtures" / "advanced_dwarf_status"
HEADER = FIXTURE / "api.h"
#: Keys the two paths must agree on. Everything else is excluded (see the
#: module docstring).
_AGREED_KEYS = (
    "verdict",
    "exit",
    "changes",
    "confidence",
    "coverage_warnings",
    "result_counts",
)
_MIXED_NOTE = "mixed provenance"


def _build(tmp: Path, name: str, source: str, *flags: str) -> Path:
    lib = tmp / f"libadv_{name}.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-g",
            "-grecord-gcc-switches",
            *flags,
            "-Wl,-soname,libadv.so.1",
            "-o",
            str(lib),
            str(FIXTURE / source),
        ],
        check=True,
    )
    return lib


def _invoke(args: list[str], report: Path) -> dict:
    result = CliRunner().invoke(
        main, ["compare", *args, "-o", f"json={report}"], catch_exceptions=False
    )
    assert result.exit_code in (0, 4), result.output
    return json.loads(report.read_text(encoding="utf-8"))


def _dump(lib: Path, out: Path) -> Path:
    result = CliRunner().invoke(
        main,
        ["dump", str(lib), "-H", str(HEADER), "-o", str(out)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return out


def _agreed_view(report: dict) -> dict:
    return {key: report[key] for key in _AGREED_KEYS}


def _kinds(report: dict) -> list[tuple[str, str]]:
    return sorted((c["kind"], c["symbol"]) for c in report["changes"])


@pytest.fixture(scope="module")
def libs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("live_stored_advanced_dwarf")
    return {
        "v1": _build(tmp, "v1", "v1.c"),
        "v2": _build(tmp, "v2", "v2.c", "-fshort-wchar"),
    }


def test_stored_status_is_completed_as_the_live_extraction_recorded_it(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    # Live: the in-process dump records the extraction's own outcome.
    for side, lib in libs.items():
        live = dump(lib, [HEADER], version=side)
        assert live.declarations.debug_advanced is not None
        assert live.declarations.debug_advanced.extraction is (
            AdvancedExtractionStatus.COMPLETED
        ), side

    # Stored: the same status reads back from the written document.
    for side, lib in libs.items():
        out = _dump(lib, tmp_path / f"{side}.abi.json")
        stored = snapshot_from_dict(json.loads(out.read_text(encoding="utf-8")))
        assert stored.declarations.debug_advanced is not None
        assert stored.declarations.debug_advanced.extraction is (
            AdvancedExtractionStatus.COMPLETED
        ), side


def test_live_and_stored_compare_agree_on_verdict_findings_and_status(
    libs: dict[str, Path], tmp_path: Path
) -> None:
    live = _invoke(
        [str(libs["v1"]), str(libs["v2"]), "--header", str(HEADER)],
        tmp_path / "live.json",
    )
    old = _dump(libs["v1"], tmp_path / "old.abi.json")
    new = _dump(libs["v2"], tmp_path / "new.abi.json")
    stored = _invoke([str(old), str(new)], tmp_path / "stored.json")

    # Oracle: the new side was built with -fshort-wchar and the old one was
    # not, so the advanced-DWARF channel must see that one flag difference.
    assert ("wchar_model_changed", "<wchar_t>") in _kinds(live)
    assert _kinds(stored) == _kinds(live)
    assert _agreed_view(stored) == _agreed_view(live)

    # Both paths read the same advanced-DWARF channel verdict: the status is
    # recorded on both sides, so the assurance rollup has no provenance note.
    for report in (live, stored):
        assurance = report["analysis_assurance"]
        assert assurance["dwarf_context_status"] == "clean", assurance["notes"]
        assert not [n for n in assurance["notes"] if _MIXED_NOTE in n]
    assert (
        stored["analysis_assurance"]["dwarf_context_status"]
        == live["analysis_assurance"]["dwarf_context_status"]
    )

# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""A stored snapshot and its live counterpart agree on the undeclared-export
census (schema v67).

The fixture under ``tests/fixtures/live_preprocessor_hidden/`` exports two
functions the public header does not declare under the parse's configuration:
``hidden_fn`` (declared only under ``#if 0``, which names no macro) and
``guarded_fn`` (declared only under ``#ifdef LIVE_STORED_NOT_DEFINED``). The
branch scan that attributes an export to its guard macro used to be runtime-only,
so the same two libraries read live attributed ``guarded_fn`` to its macro and
read stored did not. The scan is now part of the document, and this test pins
that the two paths report the same verdict, findings, confidence and census.

Oracle: the fixture's own sources and header text, and the compiler-built
export table -- never the scanner's helpers. Excluded from the comparison, each
for a stated reason: timings; file identity and version labels (a live input is
labelled ``old``/``new``, a stored snapshot without ``--label`` carries none);
``pattern_preprocessor_scan`` (a stored snapshot is never granted the source-read
licence, by design -- ``buildsource/source_inputs.py``).
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("gcc")),
        reason="needs castxml and gcc",
    ),
    # The census reads the ELF export table; the fixture is ELF-only.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF-only fixture"),
]

FIXTURE = Path(__file__).parent / "fixtures" / "live_preprocessor_hidden"
GUARD = "LIVE_STORED_NOT_DEFINED"
#: Keys the two paths must agree on. Everything else is excluded (see the module
#: docstring).
_AGREED_KEYS = (
    "verdict",
    "exit",
    "changes",
    "confidence",
    "undeclared_export_census",
    "coverage_warnings",
    "result_counts",
)


def _build(tmp: Path, name: str, source: str) -> Path:
    lib = tmp / f"libapi_{name}.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-fvisibility=default",
            "-Wl,-soname,libapi.so.1",
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


def _agreed_view(report: dict) -> dict:
    return {key: report[key] for key in _AGREED_KEYS}


def _dump(lib: Path, header: str, out: Path) -> Path:
    result = CliRunner().invoke(
        main,
        ["dump", str(lib), "-H", str(FIXTURE / header), "-o", str(out)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return out


@pytest.fixture(scope="module")
def sides(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("live_stored_census")
    return {
        "v1": _build(tmp, "v1", "v1.c"),
        "v2": _build(tmp, "v2", "v2.c"),
    }


def test_live_and_stored_census_agree(sides: dict[str, Path], tmp_path: Path) -> None:
    live = _invoke(
        [
            str(sides["v1"]),
            str(sides["v2"]),
            "--header",
            f"old={FIXTURE / 'api.h'}",
            "--header",
            f"new={FIXTURE / 'api_v2.h'}",
        ],
        tmp_path / "live.json",
    )
    snap_old = _dump(sides["v1"], "api.h", tmp_path / "old.abi.json")
    snap_new = _dump(sides["v2"], "api_v2.h", tmp_path / "new.abi.json")
    stored = _invoke([str(snap_old), str(snap_new)], tmp_path / "stored.json")

    # Oracle: the fixture's own declarations. The header declares normal_fn
    # (its return type changed), and the two exports it leaves out are
    # hidden_fn and guarded_fn on both sides.
    assert live["verdict"] == "BREAKING"
    for side in ("old", "new"):
        census = live["undeclared_export_census"][side]
        assert census["undeclared_total"] == 2
        assert census["attributed"] == 1
        assert {row["macro"]: row["count"] for row in census["by_guard"]} == {GUARD: 1}

    assert _agreed_view(stored) == _agreed_view(live)

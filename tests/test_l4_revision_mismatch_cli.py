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

"""``compare`` across a stored 0.13 and a live 0.14 L4 clang surface (plan b2).

Through the real CLI: the old side is a source-only snapshot dumped while the
clang extractor still stamped ``producer_version`` ``"0.13"`` (the constant is
patched for that dump only); the new side is dumped at the shipped ``"0.14"``
stamp. Oracle, from ADR-063's amendment "L4 clang payload comparability":

* A1: the pair is a payload-revision mismatch, so the pinned intersection is
  the only thing compared (zero pinned facts here, the tree has no typedefs).
* A4: the run reports ``source_fact_coverage_incomplete`` and nothing else;
  the L4 checks it withheld are not reported as breaks, and the fact-set
  comparability reads ``unknown``, not ``comparable``.
* Control: the same pair at one revision reports no change and reads
  ``comparable``, so the A4 finding comes from the mismatch and not the tree.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.source_extractors import clang as clang_mod
from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="L4 clang replay on Linux"),
    pytest.mark.skipif(
        shutil.which("clang") is None, reason="needs clang for the L4 replay"
    ),
]

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples/workflows/evidence-depth"

#: The shipped producer stamp, read before any test patches it.
_SHIPPED_STAMP = clang_mod.CLANG_EXTRACTOR_VERSION


def _side(root: Path, version: str) -> Path:
    """A C++ tree with ``widget.{h,cpp}`` and the compile database L4 replays."""
    side = root / version
    side.mkdir()
    shutil.copy(_EXAMPLE / version / "widget.h", side / "widget.h")
    source = side / "widget.cpp"
    shutil.copy(_EXAMPLE / version / "widget.c", source)
    (side / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(side),
                    "arguments": [
                        "g++",
                        f"-I{side}",
                        "-c",
                        source.name,
                        "-o",
                        "widget.o",
                    ],
                    "file": str(source),
                }
            ]
        ),
        encoding="utf-8",
    )
    return side


def _dump(
    side: Path, out: Path, *, stamp: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dump ``side``'s source surface with the clang producer stamped ``stamp``."""
    monkeypatch.setattr(clang_mod, "CLANG_EXTRACTOR_VERSION", stamp)
    result = CliRunner().invoke(main, ["dump", "--sources", str(side), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert f'"producer_version": "{stamp}"' in out.read_text(encoding="utf-8")


def _compare(old: Path, new: Path, tmp_path: Path) -> dict:
    report = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--depth",
            "source",
            "-o",
            f"json={report}",
        ],
    )
    assert "Traceback" not in result.output, result.output
    return json.loads(report.read_text(encoding="utf-8"))


def test_pre_change_surface_is_withheld_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    v1 = _side(tmp_path, "v1")
    v2 = _side(tmp_path, "v2")
    old_json = tmp_path / "old_013.src.json"
    new_json = tmp_path / "new_014.src.json"
    _dump(v1, old_json, stamp="0.13", monkeypatch=monkeypatch)
    _dump(v2, new_json, stamp=_SHIPPED_STAMP, monkeypatch=monkeypatch)

    report = _compare(old_json, new_json, tmp_path)

    kinds = [c["kind"] for c in report["changes"]]
    assert kinds == ["source_fact_coverage_incomplete"], kinds
    (finding,) = report["changes"]
    assert "payload revision mismatch" in finding["description"]
    assert "old producer_version='0.13'" in finding["description"]
    assert "new producer_version='0.14'" in finding["description"]
    assert report["analysis_assurance"]["fact_set_comparability"] == "unknown"


def test_same_revision_pair_is_comparable_and_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    v1 = _side(tmp_path, "v1")
    v2 = _side(tmp_path, "v2")
    old_json = tmp_path / "old_014.src.json"
    new_json = tmp_path / "new_014.src.json"
    _dump(v1, old_json, stamp=_SHIPPED_STAMP, monkeypatch=monkeypatch)
    _dump(v2, new_json, stamp=_SHIPPED_STAMP, monkeypatch=monkeypatch)

    report = _compare(old_json, new_json, tmp_path)

    assert report["changes"] == []
    assert report["analysis_assurance"]["fact_set_comparability"] == "comparable"

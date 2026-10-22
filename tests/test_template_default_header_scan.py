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

"""ADR-085 D3: the castxml header scan, its snapshot field, and the gap rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.extract.headers.template_default_scan import (
    header_declares_template_default,
    template_default_header_scan,
)
from abicheck.model import AbiSnapshot
from abicheck.policy.analysis_assurance._template_defaults import template_default_gap
from abicheck.serialization import (
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)


@pytest.mark.parametrize(
    ("text", "declares"),
    [
        ("template <class T, class U = T> struct Box;", True),
        ("template <class T> struct Box;", False),
        ("// template <class T = int> struct X;", False),
        ("/* template <class T = int> */ struct Y;", False),
        ('const char* s = "template<class T = int>";', False),
        ("template <bool B = (1 == 1)> struct Z;", True),
        # ``==`` after the list closes is a comparison in a base, not a default.
        ("template <int N> struct A : B<(N == 2)>;", False),
        (
            "template <typename T, typename = std::enable_if_t<(sizeof(T) > 1)>> "
            "struct Q;",
            True,
        ),
        ("template <template <class> class TT = X> struct R;", True),
        ("template<int N, bool B = (N >= 2)> struct S;", True),
        # Parentheses before the top-level ``=`` do not end the list.
        ("template <int (*F)(int), class X = int> struct S;", True),
        # A ``=`` inside parentheses is not a top-level default.
        ("template <int (*F)(int = 3)> struct S;", False),
        # An unterminated list never closes, so it declares no default.
        ("template <class T", False),
        # ``==`` at the top level is a comparison, not a default.
        ("template <int N == 2> struct S;", False),
    ],
)
def test_scan_finds_a_top_level_default_argument(text: str, declares: bool) -> None:
    assert header_declares_template_default(text) is declares


def test_scan_reads_the_files_it_is_given_and_skips_unreadable_ones(tmp_path) -> None:
    plain = tmp_path / "plain.h"
    plain.write_text("template <class T> struct A;\n")
    defaulted = tmp_path / "defaulted.h"
    defaulted.write_text("template <class T = int> struct B;\n")
    missing = tmp_path / "missing.h"
    assert template_default_header_scan([plain]) is False
    assert template_default_header_scan([plain, defaulted]) is True
    assert template_default_header_scan([missing, plain]) is False
    assert template_default_header_scan([]) is False


def _snap(producer: str | None, scan: bool | None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtpl.so.1",
        version="1.0",
        ast_producer=producer,
        template_default_header_scan=scan,
    )


@pytest.mark.parametrize(
    ("producer", "scan", "gap"),
    [
        # A castxml run whose headers declare a default is a gap, on either side.
        ("castxml", True, True),
        # No scan result, or a scan that found nothing, is no gap.
        ("castxml", False, False),
        ("castxml", None, False),
        # Only castxml cannot record the defaults; clang records them.
        ("clang", True, False),
        (None, True, False),
    ],
)
def test_castxml_gap_follows_the_scan_result_of_its_own_side(
    producer: str | None, scan: bool | None, gap: bool
) -> None:
    found, notes = template_default_gap(None, _snap(producer, scan))
    assert found is gap
    assert len(notes) == (1 if gap else 0)
    if gap:
        assert notes[0].startswith(
            "template-parameter defaults (castxml cannot record them)"
        )


def test_scan_field_round_trips_and_a_missing_key_reads_as_unscanned() -> None:
    for scan in (True, False, None):
        restored = snapshot_from_dict(snapshot_to_dict(_snap("castxml", scan)))
        assert restored.template_default_header_scan is scan
    # A pre-v63 document has no key at all: no scan ran, never "no default".
    document = snapshot_to_dict(_snap("castxml", True))
    assert document["template_default_header_scan"] is True
    del document["template_default_header_scan"]
    assert snapshot_from_dict(document).template_default_header_scan is None


_GAP = "template-parameter defaults (castxml cannot record them)"


def _compare_cli(tmp_path, old: AbiSnapshot, new: AbiSnapshot, *formats: str) -> Path:
    import subprocess
    import sys

    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(snapshot_to_json(old), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new), encoding="utf-8")
    out = tmp_path / "report.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            str(old_path),
            str(new_path),
            "-o",
            f"json={out}",
            *formats,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return out


def test_pre_v63_castxml_side_is_unscanned_and_only_the_scanned_side_reports_the_gap(
    tmp_path: Path,
) -> None:
    # A document from before schema v63 carries no scan: that side reports no
    # gap, and the scanned side still does, naming itself.
    out = _compare_cli(tmp_path, _snap("castxml", None), _snap("castxml", True))
    notes = json.loads(out.read_text(encoding="utf-8"))["analysis_assurance"]["notes"]
    gaps = [n for n in notes if n.startswith(_GAP)]
    assert len(gaps) == 1
    assert "the new snapshot" in gaps[0]


def test_castxml_gap_is_rendered_in_the_markdown_report(tmp_path: Path) -> None:
    out = _compare_cli(
        tmp_path,
        _snap("castxml", True),
        _snap("castxml", True),
        "-o",
        f"markdown={tmp_path / 'report.md'}",
    )
    assert out.exists()
    assert _GAP in (tmp_path / "report.md").read_text(encoding="utf-8")

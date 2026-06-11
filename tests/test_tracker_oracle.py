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

"""Offline tests for the abi-laboratory tracker-oracle harness.

The harness's parsing is pure (no network), so it is exercised here against a
small synthetic timeline fixture that mirrors the published table structure
(Version / Date / Soname / ChangeLog / BackwardCompat. / AddedSymbols /
RemovedSymbols). We deliberately do not vendor the tracker's real HTML.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path("validation/scripts/fetch_tracker_oracle.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_tracker_oracle", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A synthetic page in the same shape the tracker emits (newest-first rows,
# id='v<version>', 7 cells, backward-compat in cell 5). Three pairs result:
#   1.0.0 -> 1.0.1 : 100%, no removals      -> COMPATIBLE
#   1.0.1 -> 1.1.0 : 96.5%, 1 removed       -> BREAKING (compat < 100% + removal)
#   1.1.0 -> 2.0.0 : soname 1 -> 2          -> BREAKING (SONAME bump)
_FIXTURE = """
<table>
<tr><th>Version</th><th>Date</th><th>Soname</th><th>ChangeLog</th>
<th>BackwardCompat.</th><th>AddedSymbols</th><th>RemovedSymbols</th></tr>
<tr id='v2.0.0'><td>2.0.0</td><td>2024-03-01</td><td class='sover'>2</td>
<td><a href='#'>changelog</a></td>
<td class='danger'><a href='#'>50%</a></td>
<td class='added'><a class='num' href='#'>10 new</a></td>
<td class='removed'><a class='num' href='#'>40 removed</a></td></tr>
<tr id='v1.1.0'><td>1.1.0</td><td>2023-09-01</td><td class='sover'>1</td>
<td><a href='#'>changelog</a></td>
<td class='warning'><a href='#'>96.5%</a></td>
<td class='added'><a class='num' href='#'>3 new</a></td>
<td class='removed'><a class='num' href='#'>1 removed</a></td></tr>
<tr id='v1.0.1'><td>1.0.1</td><td>2023-06-01</td><td class='sover'>1</td>
<td><a href='#'>changelog</a></td>
<td class='ok'><a href='#'>100%</a></td>
<td class='added'><a class='num' href='#'>2 new</a></td>
<td class='ok'>0</td></tr>
<tr id='v1.0.0'><td>1.0.0</td><td>2023-01-01</td><td class='sover'>1</td>
<td><a href='#'>changelog</a></td>
<td class='ok'>&#160;</td>
<td class='ok'>0</td><td class='ok'>0</td></tr>
</table>
"""


def test_parse_timeline_extracts_rows_newest_first() -> None:
    mod = _load_module()
    rows = mod.parse_timeline(_FIXTURE)

    assert [r["version"] for r in rows] == ["2.0.0", "1.1.0", "1.0.1", "1.0.0"]
    # backward-compat parsed as float, missing figure -> None
    assert rows[0]["backward_compat"] == 50.0
    assert rows[1]["backward_compat"] == 96.5
    assert rows[-1]["backward_compat"] is None
    # count cells parsed to ints regardless of the "N new"/"N removed" wording
    assert rows[0]["added"] == 10 and rows[0]["removed"] == 40
    assert rows[2]["added"] == 2 and rows[2]["removed"] == 0


# Some tracker timelines (e.g. expat, libpng) omit the ChangeLog column, so
# rows have only 6 cells. Metrics are still the last three columns.
_FIXTURE_6COL = """
<table>
<tr><th>Version</th><th>Date</th><th>Soname</th>
<th>BackwardCompat.</th><th>AddedSymbols</th><th>RemovedSymbols</th></tr>
<tr id='v2.5.0'><td>2.5.0</td><td>2024-01-01</td><td class='sover'>1</td>
<td class='warning'><a href='#'>97.0%</a></td>
<td class='added'><a class='num' href='#'>1 new</a></td>
<td class='removed'><a class='num' href='#'>2 removed</a></td></tr>
<tr id='v2.4.0'><td>2.4.0</td><td>2023-01-01</td><td class='sover'>1</td>
<td class='ok'>&#160;</td><td class='ok'>0</td><td class='ok'>0</td></tr>
</table>
"""


def test_parse_timeline_handles_six_column_layout() -> None:
    # The ChangeLog-less 6-column layout must parse, or build_oracle yields
    # pair_count == 0 and the whole workflow fails for those libraries.
    mod = _load_module()
    rows = mod.parse_timeline(_FIXTURE_6COL)

    assert [r["version"] for r in rows] == ["2.5.0", "2.4.0"]
    assert rows[0]["soname"] == "1"
    assert rows[0]["backward_compat"] == 97.0
    assert rows[0]["added"] == 1 and rows[0]["removed"] == 2

    oracle = mod.build_oracle("expatish", _FIXTURE_6COL)
    assert oracle["pair_count"] == 1
    assert oracle["pairs"][0]["expected_verdict"] == "BREAKING"  # 97% + removals


# A layout with the optional Source-Compatibility and Total-Changes columns
# enabled: Version | Date | Soname | BackwardCompat. | SourceCompat. |
# AddedSymbols | RemovedSymbols | TotalChanges. The binary BackwardCompat. is
# *not* the third-from-last cell here, so positional indexing would mis-read it.
_FIXTURE_EXTRA_COLS = """
<table>
<tr><th>Version</th><th>Date</th><th>Soname</th><th>BackwardCompat.</th>
<th>SourceCompat.</th><th>AddedSymbols</th><th>RemovedSymbols</th><th>TotalChanges</th></tr>
<tr id='v2.0.0'><td>2.0.0</td><td>2024-01-01</td><td class='sover'>1</td>
<td class='ok'><a href='#'>100%</a></td>
<td class='danger'><a href='#'>80%</a></td>
<td class='added'><a class='num' href='#'>5 new</a></td>
<td class='ok'>0</td>
<td><a href='#'>12</a></td></tr>
<tr id='v1.0.0'><td>1.0.0</td><td>2023-01-01</td><td class='sover'>1</td>
<td class='ok'>&#160;</td><td class='ok'>&#160;</td>
<td class='ok'>0</td><td class='ok'>0</td><td class='ok'>0</td></tr>
</table>
"""


def test_parse_timeline_picks_binary_compat_with_extra_columns() -> None:
    # With Source-Compat and Total-Changes columns present, the parser must read
    # the *binary* BackwardCompat. cell (100%) and the Removed cell (0) by header
    # — not the source-compat (80%) or total-changes (12) cells. Mis-reading
    # either would flip COMPATIBLE -> BREAKING and corrupt the parity report.
    mod = _load_module()
    rows = mod.parse_timeline(_FIXTURE_EXTRA_COLS)

    newest = rows[0]
    assert newest["version"] == "2.0.0"
    assert newest["backward_compat"] == 100.0  # binary BC, not the 80% SC
    assert newest["added"] == 5
    assert newest["removed"] == 0  # the Removed cell, not TotalChanges (12)

    oracle = mod.build_oracle("extracols", _FIXTURE_EXTRA_COLS)
    assert oracle["pairs"][0]["expected_verdict"] == "COMPATIBLE"


def test_timeline_url_encodes_library() -> None:
    # A library name with query-string metacharacters must be percent-encoded so
    # it can't break out of the `l=` parameter or inject extra ones.
    mod = _load_module()
    assert mod.timeline_url("zstd") == (
        "https://abi-laboratory.pro/index.php?view=timeline&l=zstd"
    )
    assert "l=zstd%26view%3Dother" in mod.timeline_url("zstd&view=other")


def test_derive_verdict_rules() -> None:
    mod = _load_module()

    # clean minor: 100%, no removals
    assert mod.derive_verdict(100.0, 0) == "COMPATIBLE"
    # compat below 100% is breaking
    assert mod.derive_verdict(96.5, 0) == "BREAKING"
    # a removal is breaking even at a (rounded) 100%
    assert mod.derive_verdict(100.0, 1) == "BREAKING"
    # no published figure -> UNKNOWN (excluded from scoring)
    assert mod.derive_verdict(None, 0) == "UNKNOWN"


def test_soname_only_change_is_not_breaking() -> None:
    # A SONAME bump with 100% backward-compat and no removals must stay
    # COMPATIBLE: abicheck treats it as COMPATIBLE_WITH_RISK (-> COMPATIBLE),
    # so labelling it BREAKING would manufacture false ABICHECK_WEAKER pairs.
    mod = _load_module()
    fixture = """
    <table>
    <tr><th>Version</th><th>Date</th><th>Soname</th><th>ChangeLog</th>
    <th>BackwardCompat.</th><th>AddedSymbols</th><th>RemovedSymbols</th></tr>
    <tr id='v2.0.0'><td>2.0.0</td><td>2024-01-01</td><td>2</td><td>x</td>
    <td><a href='#'>100%</a></td><td>0</td><td class='ok'>0</td></tr>
    <tr id='v1.0.0'><td>1.0.0</td><td>2023-01-01</td><td>1</td><td>x</td>
    <td class='ok'>&#160;</td><td>0</td><td class='ok'>0</td></tr>
    </table>
    """
    oracle = mod.build_oracle("examplelib", fixture)
    pair = oracle["pairs"][0]
    assert pair["soname_changed"] is True  # still recorded for context
    assert pair["expected_verdict"] == "COMPATIBLE"


def test_build_oracle_pairs_consecutive_versions_oldest_first() -> None:
    mod = _load_module()
    oracle = mod.build_oracle("examplelib", _FIXTURE)

    assert oracle["library"] == "examplelib"
    assert oracle["release_count"] == 4
    assert oracle["pair_count"] == 3

    pairs = oracle["pairs"]
    # walked oldest -> newest, each pair is (older -> newer)
    assert pairs[0]["old_ver"] == "1.0.0" and pairs[0]["new_ver"] == "1.0.1"
    assert pairs[0]["expected_verdict"] == "COMPATIBLE"

    assert pairs[1]["old_ver"] == "1.0.1" and pairs[1]["new_ver"] == "1.1.0"
    assert pairs[1]["expected_verdict"] == "BREAKING"
    assert pairs[1]["removed_symbols"] == 1

    # soname 1 -> 2 flagged and treated as breaking
    assert pairs[2]["soname_changed"] is True
    assert pairs[2]["expected_verdict"] == "BREAKING"
    assert pairs[2]["pair"] == "examplelib_1.1.0_to_2.0.0"


def test_compare_to_results_scores_agreement_and_divergence() -> None:
    mod = _load_module()
    oracle = mod.build_oracle("examplelib", _FIXTURE)

    # 1.0.0->1.0.1 oracle=COMPATIBLE, 1.0.1->1.1.0 oracle=BREAKING, 1.1.0->2.0.0 oracle=BREAKING
    results = {
        "examplelib_1.0.0_to_1.0.1": "COMPATIBLE",  # MATCH
        "examplelib_1.0.1_to_1.1.0": "COMPATIBLE",  # ABICHECK_WEAKER (missed a break)
        "examplelib_1.1.0_to_2.0.0": "BREAKING",  # MATCH
    }
    report = mod.compare_to_results(oracle, results)

    assert report["counts"]["MATCH"] == 2
    assert report["counts"]["ABICHECK_WEAKER"] == 1
    assert report["comparable_pairs"] == 3
    assert report["agreement_rate"] == 2 / 3
    weaker = [r for r in report["rows"] if r["status"] == "ABICHECK_WEAKER"]
    assert weaker[0]["pair"] == "examplelib_1.0.1_to_1.1.0"


def test_compare_marks_stricter_and_uncomparable() -> None:
    mod = _load_module()
    oracle = mod.build_oracle("examplelib", _FIXTURE)

    results = {
        "examplelib_1.0.0_to_1.0.1": "BREAKING",  # oracle COMPATIBLE -> ABICHECK_STRICTER
        # 1.0.1->1.1.0 omitted entirely -> UNCOMPARABLE (missing result)
        "examplelib_1.1.0_to_2.0.0": "noise",  # unrecognized -> UNKNOWN -> UNCOMPARABLE
    }
    report = mod.compare_to_results(oracle, results)

    assert report["counts"]["ABICHECK_STRICTER"] == 1
    assert report["counts"]["UNCOMPARABLE"] == 2
    assert report["comparable_pairs"] == 1
    assert report["agreement_rate"] == 0.0


def test_load_results_map_accepts_run_matrix_list() -> None:
    mod = _load_module()

    # run_matrix.py emits a list of records with 'tag'/'pair' and 'verdict'
    raw = [
        {"tag": "lib_1.0_to_1.1", "verdict": "COMPATIBLE", "exit_code": 0},
        {"pair": "lib_1.1_to_2.0", "verdict": "BREAKING"},
        {"tag": "lib_x", "verdict": None},  # dropped
    ]
    m = mod.load_results_map(raw)
    assert m == {"lib_1.0_to_1.1": "COMPATIBLE", "lib_1.1_to_2.0": "BREAKING"}

    # also accepts a plain {pair: verdict} object
    assert mod.load_results_map({"a": "COMPATIBLE"}) == {"a": "COMPATIBLE"}


def test_load_results_map_aggregates_duplicate_pairs_conservatively() -> None:
    mod = _load_module()

    # Same pair id from two shared objects: a BREAKING result must not be masked
    # by a COMPATIBLE sibling, regardless of record order.
    raw = [
        {"pair": "lib_1.0_to_1.1", "verdict": "COMPATIBLE"},
        {"pair": "lib_1.0_to_1.1", "verdict": "BREAKING"},
    ]
    assert mod.load_results_map(raw) == {"lib_1.0_to_1.1": "BREAKING"}

    raw_reversed = list(reversed(raw))
    assert mod.load_results_map(raw_reversed) == {"lib_1.0_to_1.1": "BREAKING"}

    # API_BREAK normalizes to BREAKING and likewise wins over COMPATIBLE.
    raw_api = [
        {"pair": "p", "verdict": "API_BREAK"},
        {"pair": "p", "verdict": "COMPATIBLE_WITH_RISK"},
    ]
    assert mod.load_results_map(raw_api) == {"p": "API_BREAK"}


def test_main_rejects_from_file_with_multiple_libraries(tmp_path, capsys) -> None:
    mod = _load_module()
    html_file = tmp_path / "page.html"
    html_file.write_text(_FIXTURE)

    rc = mod.main(["foo", "bar", "--from-file", str(html_file)])
    assert rc == 2
    assert "exactly one library" in capsys.readouterr().err


def test_run_compare_fails_gracefully_on_missing_results(tmp_path, capsys) -> None:
    mod = _load_module()
    html_file = tmp_path / "page.html"
    html_file.write_text(_FIXTURE)

    # results path does not exist -> clean non-zero exit, no traceback
    rc = mod.main(
        [
            "examplelib",
            "--from-file",
            str(html_file),
            "--compare",
            str(tmp_path / "missing.json"),
        ]
    )
    assert rc == 1
    assert "compare failed" in capsys.readouterr().err

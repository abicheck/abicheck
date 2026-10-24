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

"""Per-finding ``affected_use_cases`` carried into HTML and SARIF.

Every test drives the public ``compare`` CLI. The oracle is the JSON report's
``changes`` entries (``affected_use_cases`` keyed by ``(kind, symbol)``): the
HTML table rows and the SARIF results must name exactly the same use-case
ids for every finding, and a run without ``--use-cases`` must carry no trace
of the feature in either format.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

_UNIVERSE = ("a", "b", "c", "d")


def _snapshot(tmp_path: Path, name: str, functions: tuple[str, ...]) -> Path:
    """A snapshot exporting *functions*, whose source graph maps every name in
    the universe to its like-named binary symbol, so an entrypoint naming a
    symbol reaches it on either side."""
    graph = SourceGraphSummary()
    for sym in _UNIVERSE:
        graph.add_node(
            GraphNode(
                id=f"decl://{sym}",
                kind="source_decl",
                label=sym,
                attrs={"visibility": "public_header"},
            )
        )
        graph.add_node(
            GraphNode(id=f"binary_symbol://{sym}", kind="binary_symbol", label=sym)
        )
        graph.add_edge(
            GraphEdge(
                src=f"decl://{sym}",
                dst=f"binary_symbol://{sym}",
                kind="SOURCE_DECL_MAPS_TO_SYMBOL",
            )
        )
    snap = AbiSnapshot(
        library="libfoo.so",
        version=name,
        functions=[Function(name=f, mangled=f, return_type="void") for f in functions],
    )
    snap.build_source = BuildSourcePack(root="", source_graph=graph)
    out = tmp_path / f"{name}.json"
    save_snapshot(snap, out)
    return out


def _manifest(tmp_path: Path, use_cases: dict[str, tuple[str, ...]]) -> Path:
    lines = []
    for name, entries in use_cases.items():
        lines.append(f"- use_case: {name}")
        lines.append(f"  entrypoints: [{', '.join(entries)}]")
    path = tmp_path / "impact-use-cases.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _run(tmp_path: Path, fmt: str, *args: str) -> tuple[int, Path]:
    out = tmp_path / f"report.{fmt}"
    exports = ["-o", f"{fmt}={out}"]
    if "--use-cases" in args:
        # The CLI refuses an attribution no rendered export carries, so a
        # `--use-cases` run needs a JSON companion alongside the format under test.
        exports += ["-o", f"json={tmp_path / f'companion-{fmt}.json'}"]
    res = CliRunner().invoke(main, ["compare", *args, *exports], catch_exceptions=False)
    assert out.exists(), res.output
    return res.exit_code, out


def _json_oracle(
    tmp_path: Path, *args: str
) -> tuple[int, dict[tuple[str, str], list[str]]]:
    """``(kind, symbol)`` -> the use cases JSON attributes to that finding."""
    code, path = _run(tmp_path, "json", *args)
    report = json.loads(path.read_text(encoding="utf-8"))
    return code, {
        (entry["kind"], entry["symbol"]): entry["affected_use_cases"]
        for entry in report["changes"]
    }


_CELL = re.compile(r"<code>(.*?)</code>|none")


def _html_attribution(path: Path) -> dict[tuple[str, str], list[str]]:
    """Per changes-table row, ``(kind, symbol)`` -> the use cases its
    ``Affected use cases`` line names. A row with no such line is absent."""
    text = path.read_text(encoding="utf-8")
    found: dict[tuple[str, str], list[str]] = {}
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.S):
        symbol = re.search(r"<td class='sym'>(.*?)</td>", row, re.S)
        if symbol is None:
            continue  # the header, and metadata tables outside the changes table
        kind = re.search(r"kind-badge'>([^<]*)<", row)
        assert kind is not None, row
        sym_text = html.unescape(re.sub(r"<[^>]+>", "", symbol.group(1)))
        line = re.search(r"Affected use cases: (.*?)</div>", row, re.S)
        if line is None:
            continue
        cell = _CELL.search(line.group(1))
        assert cell is not None, line.group(1)
        names = cell.group(1)
        found[(kind.group(1), sym_text)] = (
            [] if names is None else [n.strip() for n in names.split(",")]
        )
    return found


def _sarif_attribution(path: Path) -> dict[tuple[str, str], list[str]]:
    """Per SARIF result that carries ``affectedUseCases``, ``(kind, symbol)``
    -> its list."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    found: dict[tuple[str, str], list[str]] = {}
    for result in doc["runs"][0]["results"]:
        props = result["properties"]
        if "affectedUseCases" not in props:
            continue
        symbol = result["locations"][0]["logicalLocations"][0]["name"]
        found[(result["ruleId"], symbol)] = list(props["affectedUseCases"])
    return found


# Old exports a, b, c; new exports are chosen per scenario. `b` and `c` are
# removed by every scenario that keeps only `a`; `d` is added when present.
# The manifest drives the three shapes the field can take: a finding two use
# cases reach, one reaching a single use case, and one no use case reaches.
_MANIFEST = {"train": ("b", "c"), "serve": ("b",)}
_SCENARIOS = {
    # b -> [serve, train]; c -> [train]; d (added) -> [] (no use case).
    "multiple_one_and_none": ("a", "d"),
    # Only the removed-and-attributed findings; no added finding at all.
    "removals_only": ("a",),
}


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_html_and_sarif_carry_the_json_attribution(
    tmp_path: Path, scenario: str
) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", _SCENARIOS[scenario])
    manifest = _manifest(tmp_path, _MANIFEST)
    args = (str(old), str(new), "--use-cases", str(manifest))

    json_code, oracle = _json_oracle(tmp_path, *args)
    assert oracle, "scenario must produce at least one finding"
    html_code, html = _run(tmp_path, "html", *args)
    sarif_code, sarif = _run(tmp_path, "sarif", *args)

    # Verdict and exit code are untouched by the carriage.
    assert html_code == json_code == sarif_code

    html_map = _html_attribution(html)
    sarif_map = _sarif_attribution(sarif)
    # Every JSON finding is rendered in both formats with the same use cases.
    assert html_map == oracle
    assert sarif_map == oracle


def test_the_three_shapes_are_all_present(tmp_path: Path) -> None:
    """Guards the scenario choice itself: without a finding of each shape the
    parametrized equality above could pass vacuously on the easy cases."""
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", ("a", "d"))
    _, oracle = _json_oracle(
        tmp_path, str(old), str(new), "--use-cases", str(_manifest(tmp_path, _MANIFEST))
    )
    assert oracle[("func_removed", "b")] == ["serve", "train"]
    assert oracle[("func_removed", "c")] == ["train"]
    assert oracle[("func_added", "d")] == []


def test_no_use_cases_flag_leaves_html_and_sarif_untouched(tmp_path: Path) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", ("a", "d"))
    code_plain, html = _run(tmp_path, "html", str(old), str(new))
    code_json, json_report = _run(tmp_path, "json", str(old), str(new))
    _, sarif = _run(tmp_path, "sarif", str(old), str(new))

    assert code_plain == code_json
    assert "Affected use cases" not in html.read_text(encoding="utf-8")
    assert "affectedUseCases" not in sarif.read_text(encoding="utf-8")
    # The JSON side agrees: no per-finding field, no block.
    report = json.loads(json_report.read_text(encoding="utf-8"))
    assert all("affected_use_cases" not in e for e in report["changes"])
    assert "use_case_impact" not in report


def test_html_values_are_escaped(tmp_path: Path) -> None:
    """A use-case name is manifest text, so it must reach the page escaped."""
    old = _snapshot(tmp_path, "old", ("a", "b"))
    new = _snapshot(tmp_path, "new", ("a",))
    manifest = tmp_path / "impact-use-cases.yaml"
    manifest.write_text(
        "- use_case: '<b>train</b>'\n  entrypoints: [b]\n", encoding="utf-8"
    )
    _, html = _run(tmp_path, "html", str(old), str(new), "--use-cases", str(manifest))
    text = html.read_text(encoding="utf-8")
    assert "&lt;b&gt;train&lt;/b&gt;" in text
    assert "<b>train</b>" not in text

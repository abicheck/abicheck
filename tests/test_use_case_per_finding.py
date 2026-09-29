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

"""Per-finding ``affected_use_cases`` under ``compare --use-cases``.

The contract: each finding's list is the report-level
``use_case_impact.by_use_case`` read the other way round. The oracle in
these tests is a brute-force membership scan over the emitted block (or,
for the model-level property, over the rows the test itself built) -- never
``UseCaseImpact.use_cases_by_finding``, which is what is under test.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from hypothesis import given, settings, strategies as st

from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.cli import main
from abicheck.impact.use_case_impact import UseCaseChange, UseCaseImpact
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot

_UNIVERSE = ("a", "b", "c", "d")


def _snapshot(tmp_path: Path, name: str, functions: tuple[str, ...]) -> Path:
    """A snapshot exporting *functions*, whose source graph maps every name in
    the universe to its like-named binary symbol (so an entrypoint naming a
    symbol reaches it on either side)."""
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


def _compare(tmp_path: Path, *args: str) -> dict:
    out = tmp_path / "report.json"
    res = CliRunner().invoke(main, ["compare", *args, "-o", f"json={out}"])
    assert out.exists(), res.output
    return json.loads(out.read_text(encoding="utf-8"))


def _oracle(report: dict) -> dict[str, list[str]]:
    """Per finding, the use cases whose ``by_use_case`` rows name it --
    computed by scanning the emitted block, independent of the producer."""
    by_use_case = report["use_case_impact"]["by_use_case"]
    return {
        entry["finding_id"]: sorted(
            name
            for name, rows in by_use_case.items()
            if any(row["finding_id"] == entry["finding_id"] for row in rows)
        )
        for entry in report["changes"]
    }


def _assert_exact_inverse(report: dict) -> None:
    changes = report["changes"]
    oracle = _oracle(report)
    for entry in changes:
        assert entry["affected_use_cases"] == oracle[entry["finding_id"]], entry
    # And the other direction: every block row names a displayed finding
    # that lists the row's use case.
    by_id = {e["finding_id"]: e for e in changes}
    for name, rows in report["use_case_impact"]["by_use_case"].items():
        for row in rows:
            assert name in by_id[row["finding_id"]]["affected_use_cases"]
    # The unattributed count is exactly the findings listing nothing.
    block = report["use_case_impact"]
    assert block["unattributed_changes"] == sum(
        1 for e in changes if not e["affected_use_cases"]
    )
    assert block["total_changes"] == len(changes)


# A small, exhaustive-in-spirit domain of (OLD exports, NEW exports, manifest)
# triples: removals, additions, both, an entrypoint reaching nothing, a use
# case sharing a symbol with another, and a manifest reaching no finding.
_NEW_SIDES = (("a",), ("a", "b", "c", "d"), ("b", "d"), ())
_MANIFESTS = (
    {"train": ("a", "b"), "serve": ("b", "c")},
    {"only-d": ("d",)},
    {"x": ("zzz",)},
    {"all": _UNIVERSE, "some": ("c",)},
)


@pytest.mark.parametrize(
    ("new_exports", "use_cases"), list(itertools.product(_NEW_SIDES, _MANIFESTS))
)
def test_per_finding_field_is_the_exact_inverse_of_the_block(
    tmp_path: Path,
    new_exports: tuple[str, ...],
    use_cases: dict[str, tuple[str, ...]],
) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", new_exports)
    report = _compare(
        tmp_path, str(old), str(new), "--use-cases", str(_manifest(tmp_path, use_cases))
    )
    assert "use_case_impact" in report
    _assert_exact_inverse(report)


def test_a_finding_two_use_cases_reach_lists_both_and_counts_once(
    tmp_path: Path,
) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", ("a", "c"))  # `b` removed
    manifest = _manifest(tmp_path, {"train": ("a", "b"), "serve": ("b", "c")})
    report = _compare(tmp_path, str(old), str(new), "--use-cases", str(manifest))

    removed_b = [e for e in report["changes"] if e["symbol"] == "b"]
    assert len(removed_b) == 1
    assert removed_b[0]["affected_use_cases"] == ["serve", "train"]
    block = report["use_case_impact"]
    # One finding, reached twice: counted once in totals, attributed.
    assert block["total_changes"] == len(report["changes"])
    assert block["unattributed_changes"] == sum(
        1 for e in report["changes"] if e["symbol"] != "b"
    )


def test_no_key_without_use_cases(tmp_path: Path) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b"))
    new = _snapshot(tmp_path, "new", ("a",))
    report = _compare(tmp_path, str(old), str(new))
    assert report["changes"]
    assert all("affected_use_cases" not in e for e in report["changes"])
    assert "use_case_impact" not in report


def test_show_only_projects_the_field_with_the_block(tmp_path: Path) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b"))
    new = _snapshot(tmp_path, "new", ("a", "d"))  # b removed, d added
    manifest = _manifest(tmp_path, {"train": ("b", "d")})
    full = _compare(tmp_path, str(old), str(new), "--use-cases", str(manifest))
    scoped = _compare(
        tmp_path,
        str(old),
        str(new),
        "--use-cases",
        str(manifest),
        "--view",
        "show=removed",
    )
    assert {e["symbol"] for e in full["changes"]} >= {"b", "d"}
    assert [e["symbol"] for e in scoped["changes"]] == ["b"]
    assert scoped["changes"][0]["affected_use_cases"] == ["train"]
    _assert_exact_inverse(full)
    _assert_exact_inverse(scoped)


def test_markdown_rows_carry_the_note(tmp_path: Path) -> None:
    old = _snapshot(tmp_path, "old", ("a", "b", "c"))
    new = _snapshot(tmp_path, "new", ("c",))  # a and b removed
    manifest = _manifest(tmp_path, {"train": ("a",), "serve": ("a",)})
    md = tmp_path / "report.md"
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--use-cases",
            str(manifest),
            "-o",
            f"markdown={md}",
        ],
    )
    text = md.read_text(encoding="utf-8")
    assert "> Affects use cases: serve, train" in text, res.output
    # `b` is reached by no use case, so exactly one row carries the note.
    assert text.count("Affects use cases:") == 1

    plain = tmp_path / "plain.md"
    CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"markdown={plain}"])
    assert "Affects use cases" not in plain.read_text(encoding="utf-8")


def test_review_digest_groups_carry_the_use_cases(tmp_path: Path) -> None:
    """The digest's review groups name the use cases reaching their findings;
    a group no use case reaches carries no note."""
    old = _snapshot(tmp_path, "old", ("a", "b"))
    new = _snapshot(tmp_path, "new", ())
    manifest = _manifest(tmp_path, {"train": ("a",), "serve": ("a",)})
    out = tmp_path / "review.md"
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--use-cases",
            str(manifest),
            "-o",
            f"review={out}",
        ],
    )
    text = out.read_text(encoding="utf-8")
    group_a = text.split("**a** — removed", 1)[1].split("\n\n", 1)[0]
    group_b = text.split("**b** — removed", 1)[1].split("\n\n", 1)[0]
    assert "Affects use cases: serve, train" in group_a, res.output + text
    assert "Affects use cases" not in group_b

    plain = tmp_path / "plain.md"
    CliRunner().invoke(main, ["compare", str(old), str(new), "-o", f"review={plain}"])
    assert "Affects use cases" not in plain.read_text(encoding="utf-8")


def test_review_digest_impacted_list_and_mapping_round_trip() -> None:
    """The impacted-symbol fallback list (shown when no review group exists)
    renders each symbol's use cases, and both per-finding carriers survive
    the digest's document round trip."""
    from dataclasses import replace

    from abicheck.report.render_review import (
        ImpactedSymbol,
        ReviewDigest,
        render_review_digest,
    )
    from abicheck.report.review_digest_document import _review_digest_from_mapping

    digest = ReviewDigest(
        library="libfoo.so",
        old_version="1",
        new_version="2",
        verdict_emoji="x",
        verdict_label="BREAKING",
        effect="",
        manual_review_banner=False,
        coverage_warnings=(),
        additions_label="Additions",
        breaking_count=2,
        source_breaks_count=0,
        risk_count=0,
        additions_count=0,
        scoped=False,
        out_of_surface_count=0,
        bump_value="major",
        soname_value="bump",
        impacted=(
            ImpactedSymbol("a", "func_removed", ("serve", "train")),
            ImpactedSymbol("b", "func_removed"),
        ),
    )
    text = render_review_digest(digest)
    assert "- `a` — func_removed (affects: serve, train)" in text
    assert "- `b` — func_removed\n" in text

    with_groups = replace(digest, review_group_use_cases={"g1": ("train",)})
    mapping = {
        "impacted": [
            {"symbol": "a", "kind": "func_removed", "use_cases": ["serve", "train"]},
            {"symbol": "b", "kind": "func_removed"},
        ],
        "review_group_use_cases": {"g1": ["train"]},
    }
    for name in ReviewDigest.__dataclass_fields__:
        if name not in mapping:
            mapping[name] = getattr(with_groups, name)
    back = _review_digest_from_mapping(mapping)
    assert back.impacted == digest.impacted
    assert back.review_group_use_cases == {"g1": ("train",)}


def test_root_cause_view_findings_carry_the_field(tmp_path: Path) -> None:
    """`--view root-cause` nests the same finding entries under
    `root_causes[].findings`; each carries the field too."""
    old = _snapshot(tmp_path, "old", ("a", "b"))
    new = _snapshot(tmp_path, "new", ())
    manifest = _manifest(tmp_path, {"train": ("a",)})
    report = _compare(
        tmp_path,
        str(old),
        str(new),
        "--use-cases",
        str(manifest),
        "--view",
        "root-cause",
    )
    _assert_exact_inverse(report)
    nested = {
        f["finding_id"]: f["affected_use_cases"]
        for rc in report["root_causes"]
        for f in rc["findings"]
    }
    assert nested == {
        e["finding_id"]: e["affected_use_cases"] for e in report["changes"]
    }
    assert ["train"] in nested.values()


# ---------------------------------------------------------------------------
# Model-level property: use_cases_by_finding inverts by_use_case, for any
# generated attribution -- including one finding listed under several use
# cases and several findings sharing one symbol.
# ---------------------------------------------------------------------------

_fids = st.sampled_from([f"f{i}" for i in range(6)])
_names = st.sampled_from(["alpha", "beta", "gamma", "delta"])


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(_names, st.lists(_fids, min_size=1, max_size=5, unique=True)))
def test_use_cases_by_finding_is_the_inverse_of_by_use_case(
    attribution: dict[str, list[str]],
) -> None:
    impact = UseCaseImpact(
        manifest="m",
        use_case_count=len(attribution),
        total_changes=6,
        unattributed_changes=0,
        by_use_case={
            name: tuple(UseCaseChange("sym", "func_removed", fid) for fid in fids)
            for name, fids in attribution.items()
        },
    )
    inverse = impact.use_cases_by_finding()
    for fid in [f"f{i}" for i in range(6)]:
        expected = sorted(n for n, fids in attribution.items() if fid in fids)
        assert list(inverse.get(fid, ())) == expected
    # No finding is invented, and nothing is listed twice.
    assert set(inverse) == {fid for fids in attribution.values() for fid in fids}
    assert all(len(set(v)) == len(v) for v in inverse.values())

"""The directory/package (release) ``compare`` SARIF and ``review`` renderings.

Oracle: the release JSON document the same run writes with ``-o json=...``.
Every finding the document records under a library must appear in the SARIF
log as one result of the same ``ruleId`` (the change kind), stamped with that
library as its ``releaseMember``, and the SARIF log must name no finding the
document does not record. The ``review`` digest must state the same verdict,
exit code and breaking findings the document does. Adding a format to the run
must never change the verdict or the exit code, which is checked against a run
that asks only for JSON.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.report.release_formats import RELEASE_FORMATS
from abicheck.serialization import snapshot_to_json

# ── helpers ────────────────────────────────────────────────────────────────


def _snap(library: str, funcs: list[str], needed: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1",
        functions=[
            Function(name=f, mangled=f, return_type="int", visibility=Visibility.PUBLIC)
            for f in funcs
        ],
        elf=ElfMetadata(soname=library, needed=list(needed)),
    )


# A member's snapshot library name is its file name, as a real release's is.
_SPEC: dict[str, dict[str, tuple[list[str], list[str]] | None]] = {
    "libcore.so.1.json": {
        "old": (["core_a", "core_gone"], ["libc.so.6"]),
        "new": (["core_a"], ["libc.so.6"]),
    },
    "libmid.so.1.json": {
        "old": (["mid"], ["libcore.so.1.json"]),
        "new": (["mid", "mid_new"], ["libcore.so.1.json"]),
    },
    "libplain.so.1.json": {
        "old": (["plain"], []),
        "new": (["plain"], []),
    },
}


def _write_release(tmp_path: Path) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for member, sides in _SPEC.items():
        for side, d in (("old", old_dir), ("new", new_dir)):
            funcs, needed = sides[side]  # type: ignore[misc]
            (d / member).write_text(
                snapshot_to_json(_snap(member, funcs, needed)), encoding="utf-8"
            )
    return old_dir, new_dir


def _compare(tmp_path: Path, *formats: str) -> tuple[int, dict[str, Path]]:
    old_dir, new_dir = _write_release(tmp_path)
    outs = {fmt: tmp_path / f"r.{fmt}" for fmt in formats}
    args = ["compare", str(old_dir), str(new_dir)]
    for fmt in formats:
        args += ["-o", f"{fmt}={outs[fmt]}"]
    result = CliRunner().invoke(main, args)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, outs


def _json_findings(doc: dict) -> Counter[tuple[str, str, str]]:
    """(member, kind, symbol) for every finding the release document records."""
    return Counter(
        (lib["library"], f["kind"], f["symbol"])
        for lib in doc["libraries"]
        for f in lib.get("findings") or []
    )


# ── SARIF: the JSON document is the oracle ────────────────────────────────


def test_release_sarif_has_one_result_per_json_finding(tmp_path: Path) -> None:
    _code, outs = _compare(tmp_path, "json", "sarif")
    doc = json.loads(outs["json"].read_text())
    sarif = json.loads(outs["sarif"].read_text())

    json_findings = _json_findings(doc)
    assert json_findings, "the fixture must record findings for this to mean anything"

    sarif_by_key: Counter[tuple[str, str, str]] = Counter()
    for run in sarif["runs"]:
        for result in run.get("results", []):
            member = result["properties"]["releaseMember"]
            sarif_by_key[(member, result["ruleId"], "")] += 1

    expected_by_key: Counter[tuple[str, str, str]] = Counter()
    for member, kind, _symbol in json_findings.elements():
        expected_by_key[(member, kind, "")] += 1

    assert sarif_by_key == expected_by_key


def test_release_sarif_locates_each_result_in_its_member(tmp_path: Path) -> None:
    _code, outs = _compare(tmp_path, "json", "sarif")
    doc = json.loads(outs["json"].read_text())
    sarif = json.loads(outs["sarif"].read_text())

    members_with_findings = {
        lib["library"] for lib in doc["libraries"] if lib.get("findings")
    }
    members_in_sarif = {
        r["properties"]["releaseMember"]
        for run in sarif["runs"]
        for r in run.get("results", [])
    }
    # Every member the document records a finding for is named in SARIF, and
    # no member SARIF names is one the document does not list.
    assert members_in_sarif == members_with_findings
    assert members_in_sarif <= {lib["library"] for lib in doc["libraries"]}


def test_release_sarif_runs_are_one_per_member_with_their_own_rules(
    tmp_path: Path,
) -> None:
    _code, outs = _compare(tmp_path, "json", "sarif")
    sarif = json.loads(outs["sarif"].read_text())
    assert sarif["version"] == "2.1.0"
    for run in sarif["runs"]:
        rule_ids = {rule["id"] for rule in run["tool"]["driver"]["rules"]}
        for result in run.get("results", []):
            assert result["ruleId"] in rule_ids


# ── review: the JSON document is the oracle ───────────────────────────────


def test_release_review_states_the_documents_verdict_and_exit(tmp_path: Path) -> None:
    _code, outs = _compare(tmp_path, "json", "review")
    doc = json.loads(outs["json"].read_text())
    page = outs["review"].read_text()

    assert page.startswith("# Release review")
    assert f"**Verdict:** `{doc['verdict']}`" in page
    assert f"**Exit code:** `{doc['exit']['code']}`" in page


def test_release_review_lists_each_breaking_finding_of_the_document(
    tmp_path: Path,
) -> None:
    _code, outs = _compare(tmp_path, "json", "review")
    doc = json.loads(outs["json"].read_text())
    page = outs["review"].read_text()

    breaking = [
        (lib["library"], f["kind"], f["symbol"])
        for lib in doc["libraries"]
        for f in lib.get("findings") or []
        if f.get("bucket") == "breaking"
    ]
    assert breaking, "the fixture must record a breaking finding"
    for member, kind, symbol in breaking:
        assert f"### `{member}`" in page
        assert f"`{kind}` `{symbol}`" in page


# ── adding a format never changes the verdict or the exit code ────────────


@pytest.mark.parametrize("fmt", ["sarif", "review"])
def test_new_release_format_leaves_verdict_and_exit_unchanged(
    tmp_path: Path, fmt: str
) -> None:
    json_only = tmp_path / "only"
    json_only.mkdir()
    code_only, outs_only = _compare(json_only, "json")
    with_fmt = tmp_path / "with"
    with_fmt.mkdir()
    code_with, outs_with = _compare(with_fmt, "json", fmt)

    assert code_with == code_only
    doc_only = json.loads(outs_only["json"].read_text())
    doc_with = json.loads(outs_with["json"].read_text())
    assert doc_with["verdict"] == doc_only["verdict"]
    assert doc_with["exit"]["code"] == doc_only["exit"]["code"]


def test_release_formats_include_sarif_and_review() -> None:
    assert {"sarif", "review"} <= RELEASE_FORMATS


# ── the review renderer's edges ───────────────────────────────────────────


def _review_mapping(libraries: list[dict[str, object]]) -> dict[str, object]:
    return {"verdict": "BREAKING", "exit": {"code": 4}, "libraries": libraries}


def test_review_lists_at_most_ten_breaking_findings_per_member_and_counts_the_rest() -> (
    None
):
    from abicheck.report.render_release_sarif import render_release_review

    findings = [
        {"bucket": "breaking", "kind": "func_removed", "symbol": f"f{i}"}
        for i in range(13)
    ]
    page = render_release_review(
        _review_mapping(
            [
                {
                    "library": "libbig.so.1",
                    "verdict": "BREAKING",
                    "breaking": 13,
                    "findings": findings,
                }
            ]
        )
    )
    assert page.count("- `func_removed` `f") == 10
    assert "- ... and 3 more" in page


def test_review_omits_the_breaking_section_when_no_member_breaks() -> None:
    from abicheck.report.render_release_sarif import render_release_review

    page = render_release_review(
        _review_mapping(
            [
                {
                    "library": "libok.so.1",
                    "verdict": "COMPATIBLE",
                    "breaking": 0,
                    "compatible_additions": 1,
                    "findings": [
                        {"bucket": "compatible", "kind": "func_added", "symbol": "g"}
                    ],
                }
            ]
        )
    )
    assert "## Breaking findings" not in page
    assert "| `libok.so.1` | `COMPATIBLE` | 0 | 0 | 0 | 1 |" in page


def test_release_sarif_with_no_comparable_members_is_still_a_valid_log(
    tmp_path: Path,
) -> None:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    (old_dir / "libgone.so.1.json").write_text(
        snapshot_to_json(_snap("libgone.so.1.json", ["gone"], [])), encoding="utf-8"
    )
    (new_dir / "libnew.so.1.json").write_text(
        snapshot_to_json(_snap("libnew.so.1.json", ["new"], [])), encoding="utf-8"
    )
    out = tmp_path / "empty.sarif"
    CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "-o", f"sarif={out}"]
    )
    sarif = json.loads(out.read_text())
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"] == []


def test_review_states_matrix_bundle_and_warning_facts_when_present() -> None:
    from abicheck.report.render_release_sarif import render_release_review

    page = render_release_review(
        {
            "verdict": "BREAKING",
            "exit": {"code": 4},
            "matrix_verdict": "BREAKING",
            "bundle_verdict": "COMPATIBLE",
            "warnings": ["libold.so.1 was not found in NEW"],
            "libraries": [],
        }
    )
    assert "- **Matrix verdict:** `BREAKING`" in page
    assert "- **Bundle verdict:** `COMPATIBLE`" in page
    assert "## Warnings" in page
    assert "- libold.so.1 was not found in NEW" in page

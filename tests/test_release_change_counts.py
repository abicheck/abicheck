"""Release schema 2.7's ``change_counts``: an authoritative entity-by-operation
count in the release JSON, and the PR comment's exact rollup over it.

The bug class: a release (directory/package) PR comment summarized findings
by entity and operation from the release document's finding lists and had to
mark the table ``exact=False`` -- those lists can be narrowed by an
``output.view show=`` filter (and an older producer capped them) -- because
the document carried no complete count to read. Known gap "A release/bundle
report carries no authoritative entity-by-operation counts" (closed
2026-10-31).

Oracles, none of them the producer's own fold:

* the same document's complete finding lists, re-classified here from the
  change catalog (``REGISTRY``) rather than through ``report.change_summary``;
* each member's own scalar ``compare`` report (cardinality invariance: a
  release member counts what that pair counts alone);
* a release run under a ``show=`` view, which narrows ``findings`` and must
  leave ``change_counts`` untouched (record before disposing).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.pr_comment import build_model
from abicheck.pr_comment_render import render_comment
from abicheck.report.change_summary import (
    ChangeSummary,
    EntityRow,
    change_counts_to_dict,
    change_summary_from_counts,
    release_change_summary,
)
from abicheck.schemas.release_schema import RELEASE_SCHEMA_VERSION
from abicheck.serialization import snapshot_to_json


def _fn(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _var(name: str, type_: str = "int") -> Variable:
    return Variable(name=name, mangled=name, type=type_, visibility=Visibility.PUBLIC)


def _point(size_bits: int) -> RecordType:
    fields = [TypeField(name="x", type="int", offset_bits=0)]
    if size_bits > 32:
        fields.append(TypeField(name="y", type="int", offset_bits=32))
    return RecordType(name="Point", kind="struct", size_bits=size_bits, fields=fields)


#: (member, old snapshot kwargs, new snapshot kwargs) -- removals, additions
#: and modifications across functions, variables and types, unevenly spread
#: so a per-member mix-up changes the totals.
_MEMBERS: dict[str, tuple[dict[str, list], dict[str, list]]] = {
    "liba": (
        {"functions": [_fn("a_keep"), _fn("a_gone"), _fn("a_ret")]},
        {"functions": [_fn("a_keep"), _fn("a_new"), _fn("a_ret", "long")]},
    ),
    "libb": (
        {
            "functions": [_fn("b_draw", "void", ("Point",))],
            "variables": [_var("b_v"), _var("b_w")],
            "types": [_point(32)],
        },
        {
            "functions": [_fn("b_draw", "void", ("Point",))],
            "variables": [_var("b_v", "long"), _var("b_x")],
            "types": [_point(64)],
        },
    ),
    "libc": (
        {"functions": [_fn("c_same")]},
        {"functions": [_fn("c_same")]},
    ),
}


def _write_side(root: Path, side: int, members: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in members:
        fields = _MEMBERS[name][side]
        snap = AbiSnapshot(
            library=f"{name}.so",
            version=f"{side + 1}.0",
            from_headers=True,
            **fields,
        )
        (root / f"{name}.json").write_text(snapshot_to_json(snap), encoding="utf-8")
    return root


def _compare_json(old: Path, new: Path, *extra: str) -> dict:
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", "json=-", *extra]
    )
    assert result.exit_code in (0, 1, 2, 4), result.output
    return json.loads(result.stdout)


def _registry_counts(findings: list[dict]) -> Counter[tuple[str, str]]:
    """Independent oracle: classify each finding by its kind's catalog entry."""
    counts: Counter[tuple[str, str]] = Counter()
    for finding in findings:
        entity = REGISTRY.entity_for(finding["kind"])
        operation = REGISTRY.operation_for(finding["kind"])
        assert entity is not None and operation is not None, finding["kind"]
        counts[(entity.value, operation.value)] += 1
    return counts


def _block_counts(block: dict) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in block["rows"]:
        for op in ("removed", "modified", "added"):
            if row[op]:
                counts[(row["entity"], op)] += row[op]
    return counts


def _summary_counts(summary: ChangeSummary) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for row in summary.rows:
        for op in ("removed", "modified", "added"):
            if getattr(row, op):
                counts[(row.entity, op)] += getattr(row, op)
    return counts


_MEMBER_SETS = [["liba"], ["libb"], ["liba", "libb"], ["liba", "libb", "libc"]]


@pytest.mark.parametrize("members", _MEMBER_SETS, ids="+".join)
def test_counts_equal_the_complete_finding_lists(tmp_path: Path, members) -> None:
    doc = _compare_json(
        _write_side(tmp_path / "old", 0, members),
        _write_side(tmp_path / "new", 1, members),
    )
    assert doc["release_schema_version"] == RELEASE_SCHEMA_VERSION == "2.7"
    total: Counter[tuple[str, str]] = Counter()
    for lib in doc["libraries"]:
        own = _registry_counts(lib.get("findings", []))
        assert _block_counts(lib["change_counts"]) == own, lib["library"]
        assert lib["change_counts"]["counted"] == sum(own.values())
        total += own
    total += _registry_counts(doc.get("bundle_findings", []))
    total += _registry_counts(doc.get("matrix_findings", []))
    assert _block_counts(doc["change_counts"]) == total
    assert doc["change_counts"]["counted"] == sum(total.values())


@pytest.mark.parametrize("members", _MEMBER_SETS, ids="+".join)
def test_pr_comment_rollup_is_exact_and_equals_the_document(
    tmp_path: Path, members
) -> None:
    doc = _compare_json(
        _write_side(tmp_path / "old", 0, members),
        _write_side(tmp_path / "new", 1, members),
    )
    model = build_model(doc)
    expected = _block_counts(doc["change_counts"])
    if not expected:
        assert model.change_summary is None
        return
    assert model.change_summary is not None
    assert model.change_summary.exact is True
    assert _summary_counts(model.change_summary) == expected
    assert model.change_summary.counted == doc["change_counts"]["counted"]
    assert "Not exact totals" not in render_comment(model, sha="a")


@pytest.mark.parametrize("member", ["liba", "libb"])
def test_a_member_counts_what_its_scalar_compare_counts(
    tmp_path: Path, member: str
) -> None:
    old = _write_side(tmp_path / "old", 0, [member])
    new = _write_side(tmp_path / "new", 1, [member])
    release = _compare_json(old, new)
    scalar = _compare_json(old / f"{member}.json", new / f"{member}.json")
    scalar_counts: Counter[tuple[str, str]] = Counter(
        (c["entity"], c["operation"]) for c in scalar["changes"]
    )
    (lib,) = release["libraries"]
    assert _block_counts(lib["change_counts"]) == scalar_counts


def test_a_show_view_narrows_findings_but_not_the_counts(tmp_path: Path) -> None:
    members = ["liba", "libb"]
    old = _write_side(tmp_path / "old", 0, members)
    new = _write_side(tmp_path / "new", 1, members)
    full = _compare_json(old, new)
    cfg = tmp_path / "view.abicheck.yml"
    cfg.write_text('output:\n  view: ["show=variables"]\n', encoding="utf-8")
    viewed = _compare_json(old, new, "--config", str(cfg))
    narrowed = sum(len(lib.get("findings", [])) for lib in viewed["libraries"])
    complete = sum(len(lib.get("findings", [])) for lib in full["libraries"])
    assert narrowed < complete
    assert viewed["change_counts"] == full["change_counts"]
    for a, b in zip(viewed["libraries"], full["libraries"], strict=True):
        assert a["change_counts"] == b["change_counts"]
    summary = build_model(viewed).change_summary
    assert summary is not None and summary.exact is True
    assert _summary_counts(summary) == _block_counts(full["change_counts"])


def test_a_pre_27_document_stays_a_disclosed_floor() -> None:
    doc = {
        "libraries": [
            {"library": "liba.so", "findings": [{"kind": "func_removed"}]},
        ],
    }
    summary = release_change_summary(doc)
    assert summary is not None and summary.exact is False
    assert "2.7" in summary.inexact_reason
    assert _summary_counts(summary) == Counter({("function", "removed"): 1})


def _summary(**ops: int) -> ChangeSummary:
    row = EntityRow(entity="function", label="Functions", **ops)
    return ChangeSummary(rows=(row,), counted=row.total)


def test_the_block_round_trips() -> None:
    summary = _summary(removed=2, modified=1, added=3)
    assert change_summary_from_counts(change_counts_to_dict(summary)) == summary


def test_an_inexact_summary_is_never_serialized_as_counts() -> None:
    floor = ChangeSummary(rows=_summary(removed=1).rows, exact=False, counted=1)
    with pytest.raises(ValueError, match="complete counts"):
        change_counts_to_dict(floor)


_GOOD = {
    "unit": "findings",
    "counted": 3,
    "rows": [{"entity": "function", "removed": 1, "modified": 0, "added": 2}],
}


@pytest.mark.parametrize(
    "block",
    [
        None,
        [],
        {**_GOOD, "unit": "symbols"},
        {**_GOOD, "counted": 4},
        {**_GOOD, "counted": -3},
        {**_GOOD, "counted": True},
        {**_GOOD, "rows": [{**_GOOD["rows"][0], "removed": -1, "added": 4}]},
        {**_GOOD, "rows": [{**_GOOD["rows"][0], "entity": "Functions"}]},
        {**_GOOD, "rows": [{**_GOOD["rows"][0], "added": "2"}]},
        {**_GOOD, "rows": [_GOOD["rows"][0], _GOOD["rows"][0]], "counted": 6},
        {**_GOOD, "rows": "function"},
    ],
)
def test_a_malformed_block_is_not_trusted(block: object) -> None:
    assert change_summary_from_counts(block) is None
    doc = {
        "change_counts": block,
        "libraries": [{"findings": [{"kind": "func_added"}]}],
    }
    summary = release_change_summary(doc)
    assert summary is not None and summary.exact is False


def test_the_well_formed_block_is_trusted() -> None:
    summary = change_summary_from_counts(_GOOD)
    assert summary is not None and summary.exact is True
    assert _summary_counts(summary) == Counter(
        {("function", "removed"): 1, ("function", "added"): 2}
    )

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

"""``report.delta_baseline``: the gate-or-delta record in the compare (schema
7.3) and release (schema 3.1) JSON reports.

Invariants, over the whole small domain of records:

* a record is accepted exactly when the two roles' rules hold (a delta report
  is resolved, names its base and the gate's verdict; a gate report never
  carries a gate verdict), and the accepted block validates against the
  published schema -- the oracle is the rule written out here and the schema,
  not the reader;
* on every route, the block never changes the report's ``verdict``, ``exit``
  block or the process exit code: the run with the key and the run without it
  decide identically (gate isolation);
* a stored report's block reads back to the same notes the Action writes.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from schema_validation import validate_instance

from abicheck.cli import main
from abicheck.frontends.action.main_snapshot import (
    COMMENT_ONLY_OUTCOMES,
    DeltaOutcome,
    delta_notes,
)
from abicheck.frontends.action.report_publication import context_notes
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.report.delta_baseline import (
    DELTA_COMPARISON_ROLES,
    DELTA_LOOKUP_OUTCOMES,
    DELTA_REPORT_OUTCOMES,
    DeltaBaseline,
    DeltaBaselineError,
)
from abicheck.schemas import (
    RELEASE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    load_compare_report_schema,
)
from abicheck.serialization import snapshot_to_json

_SHA = "c" * 40


def _snapshot(version: str, *names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version=version,
        functions=[
            Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
            for n in names
        ],
    )


def _inputs(root: Path, route: str) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    old_snap, new_snap = _snapshot("1", "f", "g"), _snapshot("2", "f")
    if route == "pair":
        old, new = root / "old.json", root / "new.json"
        old.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new.write_text(snapshot_to_json(new_snap), encoding="utf-8")
        return old, new
    old, new = root / "old", root / "new"
    for side, snap in ((old, old_snap), (new, new_snap)):
        side.mkdir()
        (side / "libtest.abicheck.json").write_text(
            snapshot_to_json(snap), encoding="utf-8"
        )
    return old, new


def _valid(doc: dict[str, object]) -> bool:
    """The roles' rules, written independently of the reader."""
    if doc["comparison_role"] == "delta":
        return (
            doc["outcome"] == "resolved"
            and bool(doc.get("base_sha"))
            and bool(doc.get("gate_verdict"))
        )
    return not doc.get("gate_verdict")


def _domain() -> list[dict[str, object]]:
    docs = []
    for role, outcome, sha, branch, verdict in itertools.product(
        DELTA_COMPARISON_ROLES,
        DELTA_REPORT_OUTCOMES,
        (None, "", _SHA, "d" * 64),
        (None, "main"),
        (None, "BREAKING"),
    ):
        doc: dict[str, object] = {"comparison_role": role, "outcome": outcome}
        for key, value in (
            ("base_sha", sha),
            ("branch", branch),
            ("gate_verdict", verdict),
        ):
            if value is not None:
                doc[key] = value
        docs.append(doc)
    return docs


# ── the record ───────────────────────────────────────────────────────────


def test_the_outcome_vocabulary_is_the_action_s() -> None:
    assert set(DELTA_LOOKUP_OUTCOMES) == {o.value for o in DeltaOutcome}
    # Every report outcome is one the comment can word; `rerun_failed` is
    # decided after the gate ran, so no report records it.
    assert set(DELTA_REPORT_OUTCOMES) == (
        set(DELTA_LOOKUP_OUTCOMES) | set(COMMENT_ONLY_OUTCOMES)
    ) - {"rerun_failed"}


def test_the_whole_domain_is_accepted_exactly_by_the_rules() -> None:
    schema = load_compare_report_schema()
    sub = schema["properties"]["delta_baseline"] | {"$defs": schema["$defs"]}
    accepted = 0
    for doc in _domain():
        if not _valid(doc):
            with pytest.raises(DeltaBaselineError):
                DeltaBaseline.from_mapping(doc)
            continue
        block = DeltaBaseline.from_mapping(doc).to_report_dict()
        accepted += 1
        validate_instance(block, sub)
        assert block["asserted_by"] == "action"
        assert block["decides_exit_code"] is (doc["comparison_role"] == "gate")
        assert block["outcome"] == doc["outcome"]
        assert block["base_sha"] == (doc.get("base_sha") or None)
        assert block["gate_verdict"] == (doc.get("gate_verdict") or None)
        # A stored block reads back to the same record.
        assert DeltaBaseline.from_report_block(block) == DeltaBaseline.from_mapping(doc)
    assert accepted > len(DELTA_REPORT_OUTCOMES)


def test_the_schema_and_the_reader_agree_on_every_stored_block() -> None:
    """Two independent statements of the role rules -- the published JSON
    Schema and the Python reader -- accept exactly the same blocks, including
    ones no writer produces (a delta block claiming to decide the exit code)."""
    import jsonschema

    schema = load_compare_report_schema()
    sub = schema["properties"]["delta_baseline"] | {"$defs": schema["$defs"]}
    validator = jsonschema.Draft202012Validator(sub)
    seen = {True: 0, False: 0}
    for doc in _domain():
        for decides in (True, False):
            block = {
                "asserted_by": "action",
                "comparison_role": doc["comparison_role"],
                "decides_exit_code": decides,
                "outcome": doc["outcome"],
                "base_sha": doc.get("base_sha") or None,
                "branch": doc.get("branch"),
                "gate_verdict": doc.get("gate_verdict") or None,
            }
            schema_ok = validator.is_valid(block)
            try:
                DeltaBaseline.from_report_block(block)
                reader_ok = True
            except DeltaBaselineError:
                reader_ok = False
            assert schema_ok == reader_ok, block
            seen[reader_ok] += 1
    assert seen[True] and seen[False]


@pytest.mark.parametrize("name", ["base_sha", "branch", "gate_verdict"])
def test_a_trailing_newline_is_refused(name: str) -> None:
    doc = {
        "comparison_role": "delta",
        "outcome": "resolved",
        "base_sha": _SHA,
        "branch": "main",
        "gate_verdict": "BREAKING",
    }
    doc[name] = f"{doc[name]}\n"
    with pytest.raises(DeltaBaselineError, match=name):
        DeltaBaseline.from_mapping(doc)


def test_a_positional_diff_result_keeps_its_older_tail() -> None:
    """``delta_baseline`` is keyword-only, so it never takes a positional slot
    an older caller fills (the ``baseline_provenance`` neighbour sits before
    ``out_of_surface_changes`` in field order)."""
    from dataclasses import fields

    from abicheck.checker_types import DiffResult

    by_name = {f.name: f for f in fields(DiffResult)}
    assert by_name["delta_baseline"].kw_only is True
    positional = [f.name for f in fields(DiffResult) if not f.kw_only]
    assert "delta_baseline" not in positional
    assert (
        positional[positional.index("baseline_provenance") + 1]
        == "out_of_surface_changes"
    )


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "JSON object"),
        ({"outcome": "resolved"}, "comparison_role"),
        ({"comparison_role": "gate", "outcome": "nope"}, "outcome must be"),
        ({"comparison_role": "gate", "outcome": "rerun_failed"}, "outcome must be"),
        (
            {"comparison_role": "gate", "outcome": "resolved", "sha": _SHA},
            "unknown key",
        ),
        (
            {"comparison_role": "gate", "outcome": "resolved", "base_sha": "abc"},
            "base_sha",
        ),
        (
            {"comparison_role": "gate", "outcome": "resolved", "base_sha": "C" * 40},
            "base_sha",
        ),
        ({"comparison_role": "gate", "outcome": "resolved", "branch": "`x`"}, "branch"),
        ({"comparison_role": "gate", "outcome": "resolved", "branch": 3}, "branch"),
    ],
)
def test_a_malformed_record_is_refused(doc: object, message: str) -> None:
    with pytest.raises(DeltaBaselineError, match=message):
        DeltaBaseline.from_mapping(doc)


@pytest.mark.parametrize(
    "mutation",
    [
        {"asserted_by": "fork"},
        {"decides_exit_code": True},
        {"decides_exit_code": "false"},
        {"comparison_role": "gate"},  # a gate block carrying gate_verdict
    ],
)
def test_a_stored_block_that_contradicts_itself_is_refused(mutation: dict) -> None:
    block = DeltaBaseline.from_mapping(
        {
            "comparison_role": "delta",
            "outcome": "resolved",
            "base_sha": _SHA,
            "gate_verdict": "COMPATIBLE",
        }
    ).to_report_dict()
    with pytest.raises(DeltaBaselineError):
        DeltaBaseline.from_report_block(block | mutation)


# ── the CLI, every route; gate isolation ────────────────────────────────


@pytest.fixture
def _compare(monkeypatch: pytest.MonkeyPatch):
    def run(args: list[str], cwd: Path) -> tuple[int, str]:
        monkeypatch.chdir(cwd)
        result = CliRunner().invoke(main, ["compare", *args])
        return result.exit_code, result.output

    return run


_CASES = [
    {"comparison_role": "gate", "outcome": o, "base_sha": _SHA, "branch": "main"}
    for o in DELTA_REPORT_OUTCOMES
] + [
    {"comparison_role": "gate", "outcome": "base_unknown"},
    {
        "comparison_role": "delta",
        "outcome": "resolved",
        "base_sha": _SHA,
        "branch": "main",
        "gate_verdict": "BREAKING",
    },
    {
        "comparison_role": "delta",
        "outcome": "resolved",
        "base_sha": _SHA,
        "gate_verdict": "COMPATIBLE",
    },
]


def _decision(report: dict) -> tuple[object, ...]:
    """Everything that decides the check, for the gate-isolation oracle."""
    return (
        report.get("verdict"),
        report.get("severity"),
        json.dumps(report.get("exit"), sort_keys=True),
        json.dumps(report.get("summary"), sort_keys=True),
    )


@pytest.mark.parametrize("route", ["pair", "release"])
def test_the_block_is_stated_and_never_changes_the_decision(
    tmp_path: Path, _compare, route: str
) -> None:
    old, new = _inputs(tmp_path / "in", route)
    plain = tmp_path / "plain.json"
    base_code, output = _compare([str(old), str(new), "-o", f"json={plain}"], tmp_path)
    assert base_code == 4, output
    plain_doc = json.loads(plain.read_text(encoding="utf-8"))
    assert "delta_baseline" not in plain_doc
    for i, doc in enumerate(_CASES):
        rec = tmp_path / f"rec{i}.json"
        rec.write_text(json.dumps(doc), encoding="utf-8")
        out = tmp_path / f"r{i}.json"
        code, output = _compare(
            [
                str(old),
                str(new),
                "--set",
                f"report.delta_baseline=@{rec.name}",
                "-o",
                f"json={out}",
            ],
            tmp_path,
        )
        assert code == base_code, (doc, output)
        report = json.loads(out.read_text(encoding="utf-8"))
        assert (
            report["delta_baseline"] == DeltaBaseline.from_mapping(doc).to_report_dict()
        )
        assert _decision(report) == _decision(plain_doc), doc
        if route == "pair":
            assert report["report_schema_version"] == REPORT_SCHEMA_VERSION == "7.3"
            validate_instance(report, load_compare_report_schema())
        else:
            assert report["release_schema_version"] == RELEASE_SCHEMA_VERSION == "3.1"


@pytest.mark.parametrize("route", ["pair", "release"])
@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"comparison_role": "delta", "outcome": "base_pending"}),
        json.dumps(
            {
                "comparison_role": "gate",
                "outcome": "resolved",
                "gate_verdict": "BREAKING",
            }
        ),
        None,
    ],
)
def test_an_unusable_file_is_a_usage_error_before_analysis(
    tmp_path: Path, _compare, route: str, content: str | None
) -> None:
    if content is not None:
        (tmp_path / "rec.json").write_text(content, encoding="utf-8")
    old, new = _inputs(tmp_path / "in", route)
    out = tmp_path / "report.json"
    code, output = _compare(
        [
            str(old),
            str(new),
            "--set",
            "report.delta_baseline=@rec.json",
            "-o",
            f"json={out}",
        ],
        tmp_path,
    )
    assert code == 64, output
    assert "report.delta_baseline" in output
    assert not out.exists()


def test_config_file_path_resolves_against_the_config_directory(
    tmp_path: Path, _compare
) -> None:
    cfg_dir = tmp_path / "proj"
    cfg_dir.mkdir()
    (cfg_dir / "rec.json").write_text(
        json.dumps(
            {"comparison_role": "gate", "outcome": "base_failed", "base_sha": _SHA}
        ),
        encoding="utf-8",
    )
    cfg = cfg_dir / ".abicheck.yml"
    cfg.write_text("report:\n  delta_baseline: rec.json\n", encoding="utf-8")
    old, new = _inputs(tmp_path / "in", "pair")
    out = tmp_path / "report.json"
    code, output = _compare(
        [str(old), str(new), "--config", str(cfg), "-o", f"json={out}"], tmp_path
    )
    assert code == 4, output
    assert (
        json.loads(out.read_text(encoding="utf-8"))["delta_baseline"]["outcome"]
        == "base_failed"
    )


def test_no_baseline_refuses_the_key(tmp_path: Path, _compare) -> None:
    (tmp_path / "rec.json").write_text(
        json.dumps({"comparison_role": "gate", "outcome": "resolved"}), encoding="utf-8"
    )
    _, new = _inputs(tmp_path / "in", "pair")
    code, output = _compare(
        [str(new), "--no-baseline", "--set", "report.delta_baseline=@rec.json"],
        tmp_path,
    )
    assert code == 64, output
    assert "report.delta_baseline is not available with --no-baseline" in output


# ── a stored report's notes ─────────────────────────────────────────────


def _block(**doc: object) -> dict[str, object]:
    return DeltaBaseline.from_mapping(doc).to_report_dict()


@pytest.mark.parametrize("verdict", ["BREAKING", "COMPATIBLE"])
def test_a_stored_delta_report_renders_the_action_s_notes(verdict: str) -> None:
    data = {
        "verdict": "COMPATIBLE",  # the delta's own verdict: never the gate's
        "delta_baseline": _block(
            comparison_role="delta",
            outcome="resolved",
            base_sha=_SHA,
            branch="trunk",
            gate_verdict=verdict,
        ),
    }
    assert context_notes(data) == delta_notes(
        "resolved", base_sha=_SHA, branch="trunk", gate_verdict=verdict
    )
    assert f"Release gate: {verdict}" in context_notes(data)[1]


@pytest.mark.parametrize("outcome", sorted(set(DELTA_REPORT_OUTCOMES) - {"resolved"}))
def test_a_stored_unresolved_gate_report_names_its_outcome(outcome: str) -> None:
    data = {
        "verdict": "breaking",
        "delta_baseline": _block(
            comparison_role="gate", outcome=outcome, base_sha=_SHA
        ),
    }
    notes = context_notes(data)
    assert notes == delta_notes(outcome, base_sha=_SHA, gate_verdict="BREAKING")
    assert notes[0].startswith("Delta unavailable")


def test_a_resolved_gate_report_alone_is_not_labelled() -> None:
    data = {
        "verdict": "BREAKING",
        "delta_baseline": _block(
            comparison_role="gate", outcome="resolved", base_sha=_SHA
        ),
    }
    assert context_notes(data) == []


def test_the_analysis_context_wins_and_a_bad_block_yields_no_notes() -> None:
    ctx = {
        "schema": 1,
        "delta_outcome": "base_pending",
        "delta_base_sha": _SHA,
        "gate_verdict": "COMPATIBLE",
    }
    block = _block(
        comparison_role="delta",
        outcome="resolved",
        base_sha=_SHA,
        gate_verdict="BREAKING",
    )
    from abicheck.model.analysis_context import ANALYSIS_CONTEXT_SCHEMA

    ctx["schema"] = ANALYSIS_CONTEXT_SCHEMA
    notes = context_notes({"analysis_context": ctx, "delta_baseline": block})
    assert notes[0].startswith("Delta unavailable")
    assert context_notes({"delta_baseline": block | {"outcome": "base_pending"}}) == []
    assert context_notes({"delta_baseline": "resolved"}) == []


def test_an_unusable_context_or_block_yields_no_notes() -> None:
    from abicheck.model.analysis_context import ANALYSIS_CONTEXT_SCHEMA

    block = _block(comparison_role="gate", outcome="base_failed", base_sha=_SHA)
    # A malformed context drops the notes rather than falling back to the block.
    assert (
        context_notes({"analysis_context": {"bogus": 1}, "delta_baseline": block}) == []
    )
    # A well-formed context naming an outcome nothing words drops them too.
    ctx = {"schema": ANALYSIS_CONTEXT_SCHEMA, "delta_outcome": "made_up"}
    assert context_notes({"analysis_context": ctx}) == []
    # No context, no block.
    assert context_notes({"verdict": "BREAKING"}) == []
    # Every unresolved gate outcome is worded from the block alone.
    for outcome in sorted(set(DELTA_REPORT_OUTCOMES) - {"resolved"}):
        rec = _block(comparison_role="gate", outcome=outcome)
        assert context_notes({"delta_baseline": rec})[0].startswith("Delta unavailable")


def test_an_empty_file_reference_is_a_usage_error(tmp_path: Path, _compare) -> None:
    old, new = _inputs(tmp_path / "in", "pair")
    code, output = _compare(
        [str(old), str(new), "--set", "report.delta_baseline=@"], tmp_path
    )
    assert code == 64, output
    assert "report.delta_baseline: no file named" in output

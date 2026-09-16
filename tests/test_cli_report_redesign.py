from __future__ import annotations

import json

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json
from abicheck.service_render import render_output


def _write_pair(tmp_path):
    old = AbiSnapshot(library="libx.so", version="1", from_headers=True)
    new = AbiSnapshot(library="libx.so", version="2", from_headers=True)
    old.functions.append(
        Function("gone", "_Z4gonev", "void", visibility=Visibility.PUBLIC)
    )
    old_path, new_path = tmp_path / "old.json", tmp_path / "new.json"
    old_path.write_text(snapshot_to_json(old))
    new_path.write_text(snapshot_to_json(new))
    return old_path, new_path


def test_default_scalar_cli_is_bounded_review_with_final_gate(tmp_path) -> None:
    old, new = _write_pair(tmp_path)
    result = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert result.exit_code == 4
    assert "Compatibility: BREAKING" in result.stdout
    assert "Gate: REJECTED (exit 4)" in result.stdout
    assert "Findings:" in result.stdout
    assert "Details:" in result.stdout
    assert "## Library Files" not in result.stdout


def test_typed_terminal_renderer_uses_the_same_finalized_document() -> None:
    from abicheck.checker import compare

    old = AbiSnapshot(library="libx.so", version="1", from_headers=True)
    new = AbiSnapshot(library="libx.so", version="2", from_headers=True)
    old.functions.append(
        Function("gone", "_Z4gonev", "void", visibility=Visibility.PUBLIC)
    )
    text = render_output("terminal", compare(old, new), old, new)
    assert "Compatibility: BREAKING" in text
    assert "Gate: REJECTED (exit 4)" in text
    assert "1 gating" in text


def test_terminal_human_default_demangles_without_markdown_pipe_escaping() -> None:
    from abicheck.checker_policy import ChangeKind, Verdict
    from abicheck.checker_types import Change, DiffResult

    old = AbiSnapshot(library="libx.so", version="1")
    new = AbiSnapshot(library="libx.so", version="2")
    result = DiffResult(
        "1",
        "2",
        "libx.so",
        changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
        verdict=Verdict.BREAKING,
    )
    text = render_output("terminal", result, old, new)
    assert "1. foo() [_Z3foov]: removed" in text


def test_terminal_distinguishes_breaking_compatibility_from_accepted_gate() -> None:
    from abicheck.checker import compare
    from abicheck.policy.severity import PRESET_INFO_ONLY

    old = AbiSnapshot(library="libx.so", version="1")
    new = AbiSnapshot(library="libx.so", version="2")
    old.functions.append(
        Function("gone", "_Z4gonev", "void", visibility=Visibility.PUBLIC)
    )
    text = render_output(
        "terminal", compare(old, new), old, new, severity_config=PRESET_INFO_ONLY
    )
    assert "Compatibility: BREAKING" in text
    assert "Gate: ACCEPTED (exit 0)" in text
    assert "0 gating" in text


def test_terminal_keeps_asymmetric_failed_evidence_prominent() -> None:
    from abicheck.checker_types import DiffResult

    old = AbiSnapshot(library="libx.so", version="1")
    new = AbiSnapshot(library="libx.so", version="2")
    result = DiffResult("1", "2", "libx.so")
    result.coverage_warnings = [
        "OLD: header evidence available only on one side",
        "NEW: header extractor failed; comparison is incomplete",
    ]
    text = render_output("terminal", result, old, new)
    assert "OLD: header evidence available only on one side" in text
    assert "NEW: header extractor failed; comparison is incomplete" in text


def test_multiple_exports_execute_one_cli_comparison_and_keep_machine_detail(
    tmp_path, monkeypatch
) -> None:
    import abicheck.cli_compare_helpers as helpers

    old, new = _write_pair(tmp_path)
    review, markdown, machine = (
        tmp_path / "review.md",
        tmp_path / "full.md",
        tmp_path / "full.json",
    )
    real = helpers.run_compare
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(helpers, "run_compare", counted)
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "-o",
            "oneline=-",
            "-o",
            f"review={review}",
            "-o",
            f"markdown={markdown}",
            "-o",
            f"json={machine}",
        ],
    )
    assert result.exit_code == 4
    assert calls == 1
    assert review.is_file() and markdown.is_file() and machine.is_file()
    payload = json.loads(machine.read_text())
    assert any(change["kind"] == "func_removed" for change in payload["changes"])
    assert sum(
        len(group["member_finding_ids"]) for group in payload["review_groups"]
    ) == len(payload["changes"])
    assert payload["exit"]["code"] == 4


def test_one_member_release_carries_scalar_group_and_count_semantics(tmp_path) -> None:
    old, new = _write_pair(tmp_path)
    scalar_path = tmp_path / "scalar.json"
    scalar = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={scalar_path}"]
    )
    assert scalar.exit_code == 4
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old.rename(old_dir / "libx.json")
    new.rename(new_dir / "libx.json")
    release = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "-o", "json=-"]
    )
    assert release.exit_code == 4
    scalar_data = json.loads(scalar_path.read_text())
    [member] = json.loads(release.stdout)["libraries"]
    assert member["review_groups"] == scalar_data["review_groups"]
    assert member["result_counts"] == scalar_data["result_counts"]


def test_terminal_is_bounded_for_ten_thousand_findings_but_json_is_complete() -> None:
    from abicheck.checker_policy import ChangeKind
    from abicheck.checker_types import Change, DiffResult

    old = AbiSnapshot(library="large.so", version="1")
    new = AbiSnapshot(library="large.so", version="2")
    result = DiffResult(
        "1",
        "2",
        "large.so",
        changes=[
            Change(ChangeKind.FUNC_ADDED, f"N::function_{index}", "added")
            for index in range(10_000)
        ],
    )
    terminal = render_output("terminal", result, old, new)
    machine = json.loads(render_output("json", result, old, new))
    assert len(terminal.splitlines()) < 60
    assert len(terminal.encode()) < 20_000
    assert "9992 more groups omitted" in terminal
    assert len(machine["changes"]) == 10_000


def test_pr_comment_reads_canonical_groups_and_counts() -> None:
    from abicheck.pr_comment import build_model
    from abicheck.pr_comment_render import render_comment

    report = {
        "report_schema_version": "5.1",
        "library": "libx.so",
        "old_version": "1",
        "new_version": "2",
        "policy": "strict_abi",
        "verdict": "BREAKING",
        "changes": [],
        "review_groups": [
            {
                "display_name": "N::V",
                "transition": "declared entries reordered",
                "gating_findings": 2,
            }
        ],
        "result_counts": {"review_groups": 1, "gating_review_groups": 1},
    }
    text = render_comment(build_model(report), timestamp=None)
    assert "Review groups:** 1 gating; 1 retained" in text
    assert "N::V" in text and "declared entries reordered" in text


def test_pr_comment_derives_gating_groups_when_counts_are_unavailable() -> None:
    from abicheck.pr_comment import build_model
    from abicheck.pr_comment_render import render_comment

    report = {
        "report_schema_version": "5.1",
        "library": "libx.so",
        "old_version": "1",
        "new_version": "2",
        "policy": "strict_abi",
        "verdict": "BREAKING",
        "changes": [],
        "review_groups": [
            {"display_name": "N::V", "transition": "changed", "gating_findings": 2}
        ],
    }
    text = render_comment(build_model(report), timestamp=None)
    assert "**Review groups:** 1 gating; 1 retained total." in text

"""delta-baseline operand identity: is the main snapshot this PR's library?

Plan: docs/contribute/plans/pr-delta-vs-main-snapshot.md § Operand identity.
Bug class: an unmatched operand must never be compared as if it were the old
version of the PR's library -- that reads as every symbol removed (ADR-065:
absent is not removed). The oracle for "same library across a version bump"
is a hand-written stem rule below, not ``_canonical_library_key``.
"""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path

import pytest
from _action_run_sh_harness import REQUIRES_POSIX_SHELL
from click.testing import CliRunner

from abicheck.frontends.action.cli_main_snapshot import (
    delta_notes_cmd,
    delta_operand_cmd,
    operand_record_cmd,
)
from abicheck.frontends.action.main_snapshot import (
    COMMENT_ONLY_OUTCOMES,
    OPERAND_MATCH_NOTES,
    delta_notes,
)
from abicheck.frontends.action.report_publication import context_notes
from abicheck.model import AbiSnapshot
from abicheck.model.analysis_context import AnalysisContext
from abicheck.model.elf_facts import ElfMetadata
from abicheck.model.macho_facts import MachoMetadata
from abicheck.serialization import save_snapshot
from abicheck.workflows.delta_operand import (
    MatchRule,
    OperandIdentity,
    OperandMatchOutcome,
    identity_of_operand,
    identity_of_snapshot_artifact,
    match_delta_operand,
    parse_operand_map,
)

SHA = "a" * 40


def _stem(name: str) -> str:
    """Oracle: a library name without its version components."""
    name = re.sub(r"(\.so)(\.\d+)+$", r"\1", name)
    return re.sub(r"(\.\d+)+(\.dylib)$", r"\2", name)


# ── the decision ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("snap", "pr", "outcome", "rule"),
    [
        # Different build directory, same library.
        (
            OperandIdentity("build/libfoo.so", "libfoo.so", "libfoo.so.1"),
            OperandIdentity("out/x64/libfoo.so", "libfoo.so", "libfoo.so.1"),
            "resolved",
            "loader_name",
        ),
        # Renamed output file, same SONAME.
        (
            OperandIdentity("build/libfoo.so", "libfoo.so", "libfoo.so.1"),
            OperandIdentity("build/libfoo-core.so", "libfoo-core.so", "libfoo.so.1"),
            "resolved",
            "loader_name",
        ),
        # SONAME bump.
        (
            OperandIdentity("build/libfoo.so.1", "libfoo.so.1", "libfoo.so.1"),
            OperandIdentity("build/libfoo.so.2", "libfoo.so.2", "libfoo.so.2"),
            "resolved",
            "loader_name_version",
        ),
        # Mach-O compatibility-version bump in the install name.
        (
            OperandIdentity("", "libfoo.dylib", "libfoo.1.dylib"),
            OperandIdentity("b/libfoo.dylib", "libfoo.dylib", "libfoo.2.dylib"),
            "resolved",
            "loader_name_version",
        ),
        # PE: no loader name; same file name in another directory.
        (
            OperandIdentity("Release/foo.dll", "foo.dll", ""),
            OperandIdentity("build/bin/foo.dll", "foo.dll", ""),
            "resolved",
            "file_name",
        ),
        # Legacy artifact with neither path nor loader name, versioned file.
        (
            OperandIdentity("", "libfoo.so.1", ""),
            OperandIdentity("b/libfoo.so.2", "libfoo.so.2", ""),
            "resolved",
            "file_name_version",
        ),
        # Unrelated libraries.
        (
            OperandIdentity("build/libfoo.so", "libfoo.so", "libfoo.so.1"),
            OperandIdentity("build/libbar.so", "libbar.so", "libbar.so.1"),
            "operand_unmatched",
            "",
        ),
        (
            OperandIdentity("Release/foo.dll", "foo.dll", ""),
            OperandIdentity("Release/foo2.dll", "foo2.dll", ""),
            "operand_unmatched",
            "",
        ),
        # Same file, unrelated SONAMEs: the evidence disagrees.
        (
            OperandIdentity("build/libfoo.so", "libfoo.so", "libfoo.so.1"),
            OperandIdentity("build/libfoo.so", "libfoo.so", "libbar.so.1"),
            "operand_ambiguous",
            "",
        ),
    ],
)
def test_match_rules(
    snap: OperandIdentity, pr: OperandIdentity, outcome: str, rule: str
) -> None:
    got = match_delta_operand(snap, pr)
    assert (got.outcome.value, got.rule.value) == (outcome, rule)
    assert got.message


_NAMES = ["libfoo.so.1", "libfoo.so.2", "libbar.so.1", "libfoo.1.dylib", ""]
_FILES = ["libfoo.so", "libfoo.so.2", "libbar.so", "foo.dll"]
_PATHS = ["", "build/libfoo.so", "out/libfoo.so"]
_DOMAIN = [
    OperandIdentity(p, f, n) for p, f, n in itertools.product(_PATHS, _FILES, _NAMES)
]


@pytest.mark.parametrize("snap", _DOMAIN, ids=str)
def test_invariants_over_a_small_exhaustive_domain(snap: OperandIdentity) -> None:
    for pr in _DOMAIN:
        got = match_delta_operand(snap, pr)
        # Symmetric: which side is the snapshot never changes the answer.
        assert match_delta_operand(pr, snap).outcome is got.outcome
        both_named = bool(snap.loader_name and pr.loader_name)
        if both_named and _stem(snap.loader_name) != _stem(pr.loader_name):
            # Unrelated loader names are never one library without a map.
            assert not got.ok
        if both_named and snap.loader_name == pr.loader_name:
            assert got.rule is MatchRule.LOADER_NAME
        if not both_named and (snap.file_name == pr.file_name):
            assert got.ok


def test_declared_map_pairs_a_rename_and_is_decisive() -> None:
    snap = OperandIdentity("Release/foo.dll", "foo.dll", "")
    pr = OperandIdentity("Release/foo2.dll", "foo2.dll", "")
    assert not match_delta_operand(snap, pr).ok
    pairs = parse_operand_map("# renamed in this PR\nfoo.dll = Release/foo2.dll\n")
    got = match_delta_operand(snap, pr, pairs)
    assert (got.ok, got.rule) == (True, MatchRule.DECLARED)
    # A map that pairs the snapshot with something else wins over the
    # automatic rules, even where they would have matched.
    same = OperandIdentity("b/foo.dll", "foo.dll", "")
    other = match_delta_operand(snap, same, parse_operand_map("foo.dll=bar.dll"))
    assert other.outcome is OperandMatchOutcome.UNMATCHED
    # A map that does not name the snapshot's library at all.
    none = match_delta_operand(snap, same, parse_operand_map("x.dll=foo.dll"))
    assert none.outcome is OperandMatchOutcome.UNMATCHED
    assert "no delta-operand-map entry" in none.message


@pytest.mark.parametrize("text", ["foo.dll", "=x", "x=", "a=b\nbroken"])
def test_malformed_map_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="delta-operand-map"):
        parse_operand_map(text)


# ── reading each side ───────────────────────────────────────────────────────


def _snap(path: Path, library: str, **kw: object) -> Path:
    save_snapshot(AbiSnapshot(library=library, version="1", **kw), path)  # type: ignore[arg-type]
    return path


def test_snapshot_side_reads_loader_name_and_recorded_path(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    snap = _snap(
        art / "snapshot.json", "libfoo.so.1", elf=ElfMetadata(soname="libfoo.so.1")
    )
    # Before operand.json existed: names from the snapshot only.
    assert identity_of_snapshot_artifact(snap) == OperandIdentity(
        "", "libfoo.so.1", "libfoo.so.1"
    )
    res = CliRunner().invoke(
        operand_record_cmd,
        [str(art / "operand.json"), "--library", "./build/libfoo.so.1"],
    )
    assert res.exit_code == 0, res.output
    assert identity_of_snapshot_artifact(snap) == OperandIdentity(
        "build/libfoo.so.1", "libfoo.so.1", "libfoo.so.1"
    )
    mach = _snap(
        tmp_path / "m.json",
        "libfoo.dylib",
        macho=MachoMetadata(install_name="@rpath/libfoo.1.dylib"),
    )
    assert identity_of_operand(mach, "x/libfoo.dylib").loader_name == "libfoo.1.dylib"


def test_malformed_operand_record_is_refused_not_ignored(tmp_path: Path) -> None:
    snap = _snap(tmp_path / "snapshot.json", "libfoo.so")
    (tmp_path / "operand.json").write_text(json.dumps({"schema": 99, "path": "x"}))
    with pytest.raises(ValueError, match="unsupported operand record"):
        identity_of_snapshot_artifact(snap)


def test_pr_side_that_is_neither_library_nor_snapshot_is_refused(
    tmp_path: Path,
) -> None:
    junk = tmp_path / "libfoo.so"
    junk.write_bytes(b"not an elf")
    with pytest.raises(ValueError):
        identity_of_operand(junk)


# ── CLI + notes ─────────────────────────────────────────────────────────────


def _invoke(snap: Path, new: Path, *extra: str) -> tuple[int, dict[str, str]]:
    res = CliRunner().invoke(
        delta_operand_cmd, ["--snapshot", str(snap), "--new-library", str(new), *extra]
    )
    fields = dict(line.split("=", 1) for line in res.output.splitlines() if "=" in line)
    return res.exit_code, fields


def test_cli_outcomes(tmp_path: Path) -> None:
    snap = _snap(
        tmp_path / "s.json", "libfoo.so.1", elf=ElfMetadata(soname="libfoo.so.1")
    )
    bump = _snap(
        tmp_path / "n.json", "libfoo.so.2", elf=ElfMetadata(soname="libfoo.so.2")
    )
    other = _snap(
        tmp_path / "o.json", "libbar.so.1", elf=ElfMetadata(soname="libbar.so.1")
    )
    assert _invoke(snap, bump) == (
        0,
        {
            "outcome": "resolved",
            "rule": "loader_name_version",
            "message": _invoke(snap, bump)[1]["message"],
        },
    )
    code, fields = _invoke(snap, other)
    assert (code, fields["outcome"]) == (3, "operand_unmatched")
    code, fields = _invoke(snap, other, "--map", "libfoo.so.1=libbar.so.1")
    assert (code, fields["outcome"], fields["rule"]) == (0, "resolved", "declared")
    code, fields = _invoke(tmp_path / "missing.json", bump)
    assert (code, fields["outcome"]) == (3, "operand_unreadable")
    res = CliRunner().invoke(
        delta_operand_cmd,
        ["--snapshot", str(snap), "--new-library", str(bump), "--map", "nonsense"],
    )
    assert res.exit_code == 2


@pytest.mark.parametrize(
    "outcome", ["operand_unmatched", "operand_ambiguous", "operand_unreadable"]
)
def test_operand_outcomes_say_unavailable_never_removed(outcome: str) -> None:
    first, gate = delta_notes(outcome, base_sha=SHA, gate_verdict="BREAKING")
    assert (
        first
        == COMMENT_ONLY_OUTCOMES[outcome] + " Showing the release-baseline comparison."
    )
    assert first.startswith("Delta unavailable") and "removed" not in first
    assert gate.startswith("Release gate: BREAKING")


@pytest.mark.parametrize("rule", [r.value for r in MatchRule])
def test_match_note_survives_the_fork_context_round_trip(rule: str) -> None:
    direct = delta_notes(
        "resolved", base_sha=SHA, gate_verdict="COMPATIBLE", operand_match=rule
    )
    assert (len(direct) == 3) is (rule in OPERAND_MATCH_NOTES)
    ctx = AnalysisContext(
        tested_sha=SHA,
        delta_outcome="resolved",
        delta_base_sha=SHA,
        gate_verdict="COMPATIBLE",
        pr_base_ref="main",
        delta_operand_match=rule,
    )
    assert context_notes({"analysis_context": ctx.to_dict()}) == direct
    res = CliRunner().invoke(
        delta_notes_cmd,
        [
            "--outcome",
            "resolved",
            "--base-sha",
            SHA,
            "--gate-verdict",
            "COMPATIBLE",
            "--operand-match",
            rule,
        ],
    )
    assert res.output.splitlines() == direct


def test_unknown_match_rule_is_refused() -> None:
    with pytest.raises(ValueError, match="operand match rule"):
        delta_notes("resolved", base_sha=SHA, operand_match="bogus")
    ctx = AnalysisContext(tested_sha=SHA, delta_outcome="resolved", delta_base_sha=SHA)
    data = {"analysis_context": {**ctx.to_dict(), "delta_operand_match": "made_up"}}
    assert context_notes(data) == []


# ── the whole of action/run.sh ──────────────────────────────────────────────


@REQUIRES_POSIX_SHELL
@pytest.mark.parametrize(
    ("pr_library", "operand_map", "comment_outcome", "compared"),
    [
        ("libfoo.so.2", "", "resolved", True),
        ("libbar.so.1", "", "operand_unmatched", False),
        ("libbar.so.1", "libfoo.so.1=libbar.so.1", "resolved", True),
    ],
)
def test_run_sh_never_compares_an_unmatched_snapshot(
    tmp_path: Path,
    pr_library: str,
    operand_map: str,
    comment_outcome: str,
    compared: bool,
) -> None:
    from test_action_delta_baseline import _run

    snap = tmp_path / "base-snapshot.json"
    _snap(snap, "libfoo.so.1", elf=ElfMetadata(soname="libfoo.so.1"))
    new = tmp_path / "libnew.so"
    outputs, calls = _run(
        tmp_path,
        "direct",
        "resolved",
        snapshot=str(snap),
        extra={"INPUT_DELTA_OPERAND_MAP": operand_map},
        before=lambda: _snap(new, pr_library, elf=ElfMetadata(soname=pr_library)),
    )
    assert outputs["delta-comment-outcome"] == comment_outcome
    assert any(str(snap) in c for c in calls) is compared
    body = json.loads((tmp_path / "gh_bodies").read_text())["body"]
    if compared:
        assert "pr_only" in body and "release_only" not in body
    else:
        # The release comparison stays, labelled; nothing reads as removed
        # because of the unrelated snapshot.
        assert "release_only" in body and "pr_only" not in body
        assert COMMENT_ONLY_OUTCOMES["operand_unmatched"] in body


def test_fail_opt_in_covers_operand_refusals() -> None:
    import yaml

    action = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "action.yml").read_text(encoding="utf-8")
    )
    (step,) = [
        s
        for s in action["runs"]["steps"]
        if s.get("name") == "Require the delta base snapshot"
    ]
    assert (
        "startsWith(steps.run-abicheck.outputs.delta-comment-outcome, 'operand_')"
        in step["if"]
    )
    assert "delta-operand-map" in action["inputs"]
    assert "delta-operand-match" in action["outputs"]

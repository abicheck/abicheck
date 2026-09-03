# Copyright 2026 Nikolay Petrov
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

"""Tests for `compare-release`'s ADR-049 Phase 7 contract-coverage ledger
parity with single-pair `compare` — split out of test_compare_release.py
(that file's own 2000-line hard cap; CLAUDE.md's "Files that are large"
section).

Covers the release JSON's `contract_coverage_exit_contribution`/
`contract_coverage_failure_count` fields (release-level and per-library),
`_compare_one_library`'s real stamping of both, and `_finalize_release_
output`'s stderr notice for non-JSON formats — including the
`contract.unresolved=warn`-accepted advisory case, where the exit-code
contribution is deliberately zeroed while the failure count stays real
(CLI-audit P1/P2, Codex review).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.bundle_models import BundleSignatureEvidence
from abicheck.cli_compare_release import (
    _compare_one_library,
    _finalize_release_output,
    _format_release_json,
    _strip_diff_results_and_adjust_verdict,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(version: str = "1.0"):
    from abicheck.model import AbiSnapshot, Function, Visibility

    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


def test_release_json_emits_contract_coverage_block_when_active() -> None:
    # CLI-audit P1 (release/package contract parity): present, and matches
    # single-pair compare JSON's field name, whenever any library entry
    # carries the per-library contract_coverage_exit_contribution key --
    # the marker that --contract was active for this run.
    libs = [
        {
            "library": "liba.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_exit_contribution": 0,
        },
        {
            "library": "libb.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_exit_contribution": 1,
        },
    ]
    out = _format_release_json(
        "NO_CHANGE",
        Path("/o"),
        Path("/n"),
        libs,
        [],
        [],
        {},
        {},
        [],
        None,
        None,
        contract_coverage_exit_contribution=1,
        contract_coverage_failure_count=1,
    )
    data = json.loads(out)
    assert data["contract_coverage_exit_contribution"] == 1
    assert data["contract_coverage_failure_count"] == 1


def test_compare_one_library_stamps_contract_coverage_failure_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CLI-audit P2 follow-up (Codex review): `_compare_one_library` -- the
    # real per-library worker, not the JSON formatter tested above via
    # hand-built dicts -- is what actually stamps both `contract_coverage_
    # exit_contribution` and the new `contract_coverage_failure_count` onto
    # each entry. `_run_compare_pair` (real extraction/comparison) is
    # monkeypatched out; a `contract_context` of None exercises the "no
    # ledger" path (`coverage_failures_for_context(None) == ()`), pinning
    # that the new count field is computed and stamped without raising.
    from abicheck.api_types import CompareResult
    from abicheck.checker import DiffResult, Verdict

    old_path = tmp_path / "libfoo.so.1"
    new_path = tmp_path / "libfoo.so.2"
    old_snap = _snap("1.0")
    new_snap = _snap("1.0")

    def _fake_run_compare_pair(*_args: object, **_kwargs: object) -> CompareResult:
        result = DiffResult(
            old_version="1.0",
            new_version="1.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        assert result.contract_context is None
        return CompareResult(diff=result, old_snapshot=old_snap, new_snapshot=new_snap)

    monkeypatch.setattr(
        "abicheck.cli_compare_release_pairwise._run_compare_pair", _fake_run_compare_pair
    )
    entry = _compare_one_library(
        "libfoo.so",
        {"libfoo.so": old_path},
        {"libfoo.so": new_path},
        None,
        None,
        lambda _old, _dbg: None,
        [],
        [],
        [],
        [],
        "1.0",
        "1.0",
        "c",
        None,
        "strict_abi",
        None,
        None,
        contract_evaluation=True,
    )
    assert entry["contract_coverage_exit_contribution"] == 0
    assert entry["contract_coverage_failure_count"] == 0


def test_compare_one_library_stamps_coverage_warnings_onto_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A same-binary (or similar) `coverage_warnings` entry on the real
    `DiffResult` must reach the release's per-library entry dict -- it is
    embedded verbatim into both the primary release JSON's "libraries"
    list and `--output-dir`'s summary.json, so nothing else needs to copy
    it once it lands here (Codex review, fresh evidence)."""
    from abicheck.api_types import CompareResult
    from abicheck.checker import DiffResult, Verdict

    old_path = tmp_path / "libfoo.so.1"
    new_path = tmp_path / "libfoo.so.2"
    old_snap = _snap("1.0")
    new_snap = _snap("1.0")

    def _fake_run_compare_pair(*_args: object, **_kwargs: object) -> CompareResult:
        result = DiffResult(
            old_version="1.0",
            new_version="1.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        result.coverage_warnings = ["old and new binaries are byte-identical (sha256 abc...)"]
        return CompareResult(diff=result, old_snapshot=old_snap, new_snapshot=new_snap)

    monkeypatch.setattr(
        "abicheck.cli_compare_release_pairwise._run_compare_pair", _fake_run_compare_pair
    )
    entry = _compare_one_library(
        "libfoo.so",
        {"libfoo.so": old_path},
        {"libfoo.so": new_path},
        None,
        None,
        lambda _old, _dbg: None,
        [],
        [],
        [],
        [],
        "1.0",
        "1.0",
        "c",
        None,
        "strict_abi",
        None,
        None,
    )
    assert entry["coverage_warnings"] == [
        "old and new binaries are byte-identical (sha256 abc...)"
    ]


def test_compare_one_library_omits_coverage_warnings_key_when_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent, not an empty list -- matching every other format's own
    `if result.coverage_warnings:` convention (reporter.py)."""
    from abicheck.api_types import CompareResult
    from abicheck.checker import DiffResult, Verdict

    old_path = tmp_path / "libfoo.so.1"
    new_path = tmp_path / "libfoo.so.2"
    old_snap = _snap("1.0")
    new_snap = _snap("2.0")

    def _fake_run_compare_pair(*_args: object, **_kwargs: object) -> CompareResult:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        return CompareResult(diff=result, old_snapshot=old_snap, new_snapshot=new_snap)

    monkeypatch.setattr(
        "abicheck.cli_compare_release_pairwise._run_compare_pair", _fake_run_compare_pair
    )
    entry = _compare_one_library(
        "libfoo.so",
        {"libfoo.so": old_path},
        {"libfoo.so": new_path},
        None,
        None,
        lambda _old, _dbg: None,
        [],
        [],
        [],
        [],
        "1.0",
        "2.0",
        "c",
        None,
        "strict_abi",
        None,
        None,
    )
    assert "coverage_warnings" not in entry


def test_release_markdown_renders_the_per_library_coverage_warnings() -> None:
    """Codex review, fresh evidence: the release Markdown table only
    renders library/verdict/counts -- a byte-identical matched library
    otherwise still presents a clean report with no warning at all in
    this format."""
    from abicheck.cli_compare_release import _format_release_markdown

    libs = [
        {
            "library": "liba.so",
            "verdict": "NO_CHANGE",
            "coverage_warnings": ["old and new binaries are byte-identical (sha256 abc...)"],
        },
        {"library": "libb.so", "verdict": "NO_CHANGE"},
    ]
    md = _format_release_markdown(
        "NO_CHANGE", Path("/o"), Path("/n"), libs, [], [], {}, {}, None, None
    )
    assert "## ⚠️ Coverage Warnings" in md
    assert "`liba.so`: old and new binaries are byte-identical" in md


def test_release_markdown_omits_the_section_when_no_library_carries_a_warning() -> None:
    from abicheck.cli_compare_release import _format_release_markdown

    libs = [{"library": "liba.so", "verdict": "NO_CHANGE"}]
    md = _format_release_markdown(
        "NO_CHANGE", Path("/o"), Path("/n"), libs, [], [], {}, {}, None, None
    )
    assert "Coverage Warnings" not in md


def test_release_json_carries_the_per_library_coverage_warnings_through() -> None:
    """`_format_release_json` embeds `library_results` verbatim -- no
    separate wiring is needed for this field to reach the release JSON."""
    libs = [
        {
            "library": "liba.so",
            "verdict": "NO_CHANGE",
            "coverage_warnings": ["old and new binaries are byte-identical (sha256 abc...)"],
        },
    ]
    out = _format_release_json(
        "NO_CHANGE", Path("/o"), Path("/n"), libs, [], [], {}, {}, [], None, None
    )
    data = json.loads(out)
    assert data["libraries"][0]["coverage_warnings"] == [
        "old and new binaries are byte-identical (sha256 abc...)"
    ]


def test_compare_one_library_stashes_old_snapshot_only_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review, fresh evidence: `_old_snapshot` (added for a secondary
    JUnit render to read straight off the primary pass, see
    `_compare_one_library`'s own docstring) must not be stashed on every
    release entry unconditionally -- an `AbiSnapshot` can be large, and
    every entry in `library_results` is held until the whole release
    finishes, so an unconditional stash would grow peak memory by every
    library's old snapshot even for the common markdown/JSON-only case
    that never reads it. Gated on `collect_diff_results`, the same flag
    that already gates whether `_compare_release_libraries` reads it back.

    G38 stabilization Phase 9 (memory-regression fix): `collect_diff_
    results=True` alone now stashes only the much smaller
    `BundleSignatureEvidence` projection under `_old_bundle_evidence`/
    `_new_bundle_evidence` -- the full `AbiSnapshot` is stashed under
    `_old_snapshot`/`_new_snapshot` only when `need_full_snapshots=True`
    too (JUnit/`--bundle-facts-out`), since bundle analysis on its own
    never needed the full snapshot.
    """
    from abicheck.api_types import CompareResult
    from abicheck.checker import DiffResult, Verdict

    old_path = tmp_path / "libfoo.so.1"
    new_path = tmp_path / "libfoo.so.2"
    old_snap = _snap("1.0")
    new_snap = _snap("1.0")

    def _fake_run_compare_pair(*_args: object, **_kwargs: object) -> CompareResult:
        result = DiffResult(
            old_version="1.0",
            new_version="1.0",
            library="libfoo.so",
            changes=[],
            verdict=Verdict.COMPATIBLE,
        )
        return CompareResult(diff=result, old_snapshot=old_snap, new_snapshot=new_snap)

    monkeypatch.setattr(
        "abicheck.cli_compare_release_pairwise._run_compare_pair", _fake_run_compare_pair
    )
    common = (
        {"libfoo.so": old_path},
        {"libfoo.so": new_path},
        None,
        None,
        lambda _old, _dbg: None,
        [],
        [],
        [],
        [],
        "1.0",
        "1.0",
        "c",
        None,
        "strict_abi",
        None,
        None,
    )
    default_entry = _compare_one_library("libfoo.so", *common)
    assert "_old_snapshot" not in default_entry
    assert "_old_bundle_evidence" not in default_entry

    bundle_entry = _compare_one_library(
        "libfoo.so",
        *common,
        collect_diff_results=True,
    )
    assert "_old_snapshot" not in bundle_entry
    evidence = bundle_entry["_old_bundle_evidence"]
    assert isinstance(evidence, BundleSignatureEvidence)
    assert evidence.function_map is old_snap.function_map
    assert evidence.variable_map is old_snap.variable_map
    assert evidence.elf_only_mode == old_snap.elf_only_mode

    junit_entry = _compare_one_library(
        "libfoo.so",
        *common,
        collect_diff_results=True,
        need_full_snapshots=True,
    )
    assert junit_entry["_old_snapshot"] is old_snap
    assert "_old_bundle_evidence" not in junit_entry


def test_strip_diff_results_builds_annotations_only_when_requested() -> None:
    """Codex review, fresh evidence: the uncapped `annotations` array (only
    a JSON render -- primary `--format json` or a secondary `--write
    json=...` -- ever reads it) must not be built for every library
    unconditionally, the same class of gap the sibling `_old_snapshot`
    fix above closed for JUnit specifically -- every entry in
    `library_results` is held until the whole release finishes.
    """
    from abicheck.checker import Change, ChangeKind, DiffResult, Verdict

    def _entry() -> dict[str, object]:
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libfoo.so",
            changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
            verdict=Verdict.BREAKING,
        )
        return {"library": "libfoo.so", "verdict": "BREAKING", "_diff_result": result}

    markdown_entries = [_entry()]
    _strip_diff_results_and_adjust_verdict(
        markdown_entries,
        [],
        "BREAKING",
        needs_annotations=False,
    )
    assert "annotations" not in markdown_entries[0]

    json_entries = [_entry()]
    _strip_diff_results_and_adjust_verdict(
        json_entries,
        [],
        "BREAKING",
        needs_annotations=True,
    )
    assert json_entries[0]["annotations"]


def test_release_json_contract_coverage_failure_count_independent_of_warn() -> None:
    # CLI-audit P2 follow-up (Codex review): `contract.unresolved=warn`
    # zeroes the exit-code contribution while the failure count stays real
    # -- both fields must be independently readable from the JSON so a
    # consumer can tell "clean" apart from "accepted incomplete assurance".
    libs = [
        {
            "library": "libb.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_exit_contribution": 0,
            "contract_coverage_failure_count": 1,
        },
    ]
    out = _format_release_json(
        "NO_CHANGE",
        Path("/o"),
        Path("/n"),
        libs,
        [],
        [],
        {},
        {},
        [],
        None,
        None,
        contract_coverage_exit_contribution=0,
        contract_coverage_failure_count=1,
    )
    data = json.loads(out)
    assert data["contract_coverage_exit_contribution"] == 0
    assert data["contract_coverage_failure_count"] == 1


def test_release_stderr_announces_contract_coverage_for_non_json_format(capsys) -> None:
    # CLI-audit P1 (Codex review): action/run.sh's `_coverage_gated()` falls
    # back to grepping stderr for "Contract coverage incomplete" whenever no
    # JSON report exists -- exactly the shape of a directory/package
    # (release) `compare` outside a `pull_request` event, since the release
    # fan-out rejects `--write` and the Action's PR-comment JSON
    # rerun only fires on `pull_request`/`pull_request_target`. A markdown
    # (or any non-json) release format must announce the same notice
    # single-pair `compare` already does via `announce_coverage_floor`.
    libs = [
        {
            "library": "liba.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_failure_count": 0,
            "contract_coverage_exit_contribution": 0,
        },
        {
            "library": "libb.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_failure_count": 1,
            "contract_coverage_exit_contribution": 1,
        },
    ]
    with pytest.raises(SystemExit) as exc_info:
        _finalize_release_output(
            "markdown",
            "NO_CHANGE",
            Path("/o"),
            Path("/n"),
            libs,
            [],
            [],
            {},
            {},
            [],
            [],
            None,
            None,
            None,
            False,
            contract_coverage_exit_contribution=1,
            contract_coverage_failure_count=1,
        )
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Contract coverage incomplete" in err
    assert "libb.so" in err
    assert "liba.so" not in err
    assert "Contributes 1 to the release exit code" in err


def test_release_stderr_announces_warn_accepted_gap_as_advisory(capsys) -> None:
    # CLI-audit P2 follow-up (Codex review): `contract.unresolved=warn`
    # zeroes the exit-code contribution while the failures stay real --
    # the notice must still fire (worded as accepted, not as a blocking
    # contribution), mirroring single-pair `compare`'s own
    # `coverage_failure_diagnostic` wording for this same case.
    libs = [
        {
            "library": "libb.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_failure_count": 1,
            "contract_coverage_exit_contribution": 0,
        },
    ]
    _finalize_release_output(
        "markdown",
        "NO_CHANGE",
        Path("/o"),
        Path("/n"),
        libs,
        [],
        [],
        {},
        {},
        [],
        [],
        None,
        None,
        None,
        False,
        contract_coverage_exit_contribution=0,
        contract_coverage_failure_count=1,
    )
    err = capsys.readouterr().err
    assert "Contract coverage incomplete" in err
    assert "libb.so" in err
    assert "contract.unresolved=warn" in err
    assert "contributes 0" in err


def test_release_stderr_no_notice_when_no_library_carries_per_library_key(
    capsys,
) -> None:
    # A release-level count/contribution with no per-library entry carrying
    # the key at all (shouldn't normally happen -- the release CLI always
    # stamps all three together) must not fabricate an "in: " list with
    # nothing in it; the aggregate `_exit_compare_release` fold still
    # applies regardless (unaffected by this stderr-only notice).
    libs = [{"library": "liba.so", "verdict": "NO_CHANGE"}]
    with pytest.raises(SystemExit) as exc_info:
        _finalize_release_output(
            "markdown",
            "NO_CHANGE",
            Path("/o"),
            Path("/n"),
            libs,
            [],
            [],
            {},
            {},
            [],
            [],
            None,
            None,
            None,
            False,
            contract_coverage_exit_contribution=1,
            contract_coverage_failure_count=1,
        )
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "Contract coverage incomplete" not in err


def test_release_stderr_omits_contract_coverage_notice_for_json_format(capsys) -> None:
    # --format json already states contract_coverage_exit_contribution in the
    # rendered report -- a second stderr copy would be redundant, mirroring
    # single-pair compare's report_carries_the_ledger check.
    libs = [
        {
            "library": "libb.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_exit_contribution": 1,
        }
    ]
    with pytest.raises(SystemExit):
        _finalize_release_output(
            "json",
            "NO_CHANGE",
            Path("/o"),
            Path("/n"),
            libs,
            [],
            [],
            {},
            {},
            [],
            [],
            None,
            None,
            None,
            False,
            contract_coverage_exit_contribution=1,
        )
    err = capsys.readouterr().err
    assert "Contract coverage incomplete" not in err


def test_release_stderr_silent_when_contract_coverage_contribution_zero(capsys) -> None:
    # A NO_CHANGE verdict with a zero coverage contribution falls all the way
    # through `_exit_compare_release` without calling `sys.exit` at all (the
    # process exits 0 naturally) -- so, unlike the other cases here, no
    # SystemExit is raised.
    libs = [
        {
            "library": "liba.so",
            "verdict": "NO_CHANGE",
            "contract_coverage_exit_contribution": 0,
        }
    ]
    _finalize_release_output(
        "markdown",
        "NO_CHANGE",
        Path("/o"),
        Path("/n"),
        libs,
        [],
        [],
        {},
        {},
        [],
        [],
        None,
        None,
        None,
        False,
        contract_coverage_exit_contribution=0,
    )
    err = capsys.readouterr().err
    assert "Contract coverage incomplete" not in err


def test_release_json_omits_contract_coverage_block_by_default() -> None:
    libs = [{"library": "liba.so", "verdict": "NO_CHANGE"}]
    out = _format_release_json(
        "NO_CHANGE",
        Path("/o"),
        Path("/n"),
        libs,
        [],
        [],
        {},
        {},
        [],
        None,
        None,
    )
    assert "contract_coverage_exit_contribution" not in json.loads(out)

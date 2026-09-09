# SPDX-License-Identifier: Apache-2.0
"""End-to-end CLI contracts for ``compare --no-baseline``.

Everything here was verified by hand while the command was built, and
nothing pinned it. That is the same gap this PR's own history keeps
producing: a behaviour confirmed once, at a terminal, by the person who
just wrote it — and then free to regress silently, because no test names
it. These are the user-facing contracts the command makes, driven through
the real Click entry point rather than through the helpers underneath, so a
refactor that keeps the helpers working but breaks the wiring still fails.

Scope is deliberately the *boundary*: which invocations are refused and
what the message says, what reaches disk, and what the process exits with.
The finding set itself is covered by ``tests/parity/`` (against ``scan``)
and the report shapes by ``tests/test_no_baseline_report_formats.py``.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from abicheck.cli import main as abicheck_main  # noqa: E402
from abicheck.report.no_baseline import (  # noqa: E402
    NO_BASELINE_SUPPORTED_FORMATS,
    NO_BASELINE_UNSUPPORTED_FORMATS,
)

_EXIT_USAGE = 64


def invoke_cli(*args: str):
    """Invoke the real ``abicheck`` CLI in-process (the public entry point)."""
    return CliRunner().invoke(abicheck_main, list(args))


@pytest.fixture
def candidate() -> Path:
    """A committed G20 audit fixture that reports exactly one finding."""
    path = (
        example_catalog.case_dir("case143_audit_accidental_export")
        / "snapshot.abi.json"
    )
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


# ---------------------------------------------------------------------------
# --format / --write: which are rendered, which are refused, and how.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", sorted(NO_BASELINE_SUPPORTED_FORMATS))
def test_every_supported_format_renders_through_the_cli(
    candidate: Path, fmt: str
) -> None:
    """A supported format is not merely accepted -- it produces its format.

    Asserted structurally per format rather than by "exit code is 0": a
    command that printed nothing at all would pass the weaker check.
    """
    result = invoke_cli("compare", "--no-baseline", str(candidate), "--format", fmt)
    assert result.exit_code == 0, result.output
    if fmt in ("json", "sarif"):
        payload = json.loads(result.stdout)
        assert payload  # parses, and is not empty
    elif fmt == "junit":
        ET.fromstring(result.stdout)  # well-formed XML
    elif fmt == "markdown":
        assert result.stdout.startswith("# ABI audit:")
    else:  # oneline
        assert result.stdout.strip().count("\n") == 0


@pytest.mark.parametrize("fmt", sorted(NO_BASELINE_UNSUPPORTED_FORMATS))
def test_an_unsupported_format_is_a_usage_error_that_names_the_ruling(
    candidate: Path, fmt: str
) -> None:
    """``html``/``review`` are refused *with the reason*, not deferred.

    The message content is part of the contract here: the whole point of
    ruling on these rather than promising a later phase is that the user is
    told why, and pointed somewhere that works.
    """
    result = invoke_cli("compare", "--no-baseline", str(candidate), "--format", fmt)
    assert result.exit_code == _EXIT_USAGE, result.output
    assert f"--format {fmt}" in result.output
    assert "compatibility comparison" in result.output
    assert "oneline" in result.output, "the error must point at a usable alternative"


def test_an_unsupported_write_format_names_write_not_format(
    candidate: Path, tmp_path: Path
) -> None:
    """The error quotes the flag the user actually typed."""
    result = invoke_cli(
        "compare",
        "--no-baseline",
        str(candidate),
        "--write",
        f"html={tmp_path / 'x.html'}",
    )
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "--write html" in result.output
    assert "--format html" not in result.output


def test_write_emits_each_format_from_one_run(candidate: Path, tmp_path: Path) -> None:
    """ADR-068 D4: one analysis, several artifacts -- and the same analysis.

    Checks that both files are real renderings *and* that they describe the
    same run, which is what "without re-running" has to mean.
    """
    out_json = tmp_path / "audit.json"
    out_sarif = tmp_path / "audit.sarif"
    result = invoke_cli(
        "compare",
        "--no-baseline",
        str(candidate),
        "--write",
        f"json={out_json}",
        "--write",
        f"sarif={out_sarif}",
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out_json.read_text())
    sarif = json.loads(out_sarif.read_text())
    assert payload["findings"], "the secondary JSON must carry the audit's findings"
    assert sorted(f["kind"] for f in payload["findings"]) == sorted(
        r["ruleId"] for r in sarif["runs"][0]["results"]
    )


def test_write_creates_a_missing_parent_directory(
    candidate: Path, tmp_path: Path
) -> None:
    """The secondary writer behaves like ``-o/--output``.

    Regression guard: a raw ``Path.write_text`` raised ``FileNotFoundError``
    here *after* the analysis had already completed, turning a successful
    run into a traceback (Codex review, P2).
    """
    target = tmp_path / "does" / "not" / "exist" / "audit.json"
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--write", f"json={target}"
    )
    assert result.exit_code == 0, result.output
    assert json.loads(target.read_text())["no_baseline"] is True


# ---------------------------------------------------------------------------
# Refusals: an option this path does not honour must never be a silent no-op.
# ---------------------------------------------------------------------------


def test_an_old_scoped_evidence_input_is_refused(candidate: Path) -> None:
    """There is no OLD side for ``--sources old=`` to describe."""
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--sources", "old=."
    )
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "--sources old=" in result.output


def test_a_bare_sources_value_is_accepted(candidate: Path, tmp_path: Path) -> None:
    """The complement of the guard above, and the reason it must be narrow.

    A bare ``--sources`` populates *both* per-side dests, so a guard keyed on
    "the old dest is set" would reject the single most useful spelling on
    this path.
    """
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--sources", str(tmp_path)
    )
    assert result.exit_code == 0, result.output


def test_a_view_token_is_refused(candidate: Path) -> None:
    """An audit has no root-cause graph or findings filter for --view."""
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--view", "report-mode=leaf"
    )
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "--view" in result.output


def test_an_unimplemented_option_is_refused_with_its_reason(
    candidate: Path,
) -> None:
    """A declared-unsupported option fails loudly rather than doing nothing."""
    result = invoke_cli("compare", "--no-baseline", str(candidate), "--budget", "5m")
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "--budget" in result.output
    assert "silently ignored" in result.output


def test_two_operands_and_zero_flags_are_both_usage_errors(
    candidate: Path, tmp_path: Path
) -> None:
    """ADR-068 D2: audit mode is declared, never inferred from arity."""
    second = tmp_path / "second.abi.json"
    second.write_text(candidate.read_text())
    both = invoke_cli("compare", "--no-baseline", str(candidate), str(second))
    assert both.exit_code == _EXIT_USAGE, both.output
    alone = invoke_cli("compare", str(candidate))
    assert alone.exit_code == _EXIT_USAGE, alone.output


# ---------------------------------------------------------------------------
# --dry-run: previews the run it actually is.
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_describes_the_audit(candidate: Path) -> None:
    result = invoke_cli("compare", "--no-baseline", str(candidate), "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Command: compare --no-baseline" in result.output
    assert "baseline: (none" in result.output
    assert "no analysis performed, nothing written" in result.output


def test_dry_run_rejects_output_and_write(candidate: Path, tmp_path: Path) -> None:
    """A dry run promises no side effect, so both writers are refused."""
    with_output = invoke_cli(
        "compare",
        "--no-baseline",
        str(candidate),
        "--dry-run",
        "-o",
        str(tmp_path / "x.md"),
    )
    assert with_output.exit_code == _EXIT_USAGE, with_output.output
    with_write = invoke_cli(
        "compare",
        "--no-baseline",
        str(candidate),
        "--dry-run",
        "--write",
        f"json={tmp_path / 'x.json'}",
    )
    assert with_write.exit_code == _EXIT_USAGE, with_write.output


def test_dry_run_agrees_with_the_real_run_on_a_stored_candidate(
    candidate: Path,
) -> None:
    """A pinned depth cannot be outrun by a snapshot this run never extracted.

    Regression guard for the preview claiming a blocker (exit 1) where the
    real run exits 0 (Codex review, P2). Asserted as *agreement* rather than
    as two fixed numbers, so the pair cannot drift apart.
    """
    real = invoke_cli("compare", "--no-baseline", str(candidate), "--depth", "build")
    dry = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--depth", "build", "--dry-run"
    )
    assert real.exit_code == 0, real.output
    assert dry.exit_code == 0, dry.output
    assert "stored snapshot" in dry.output


# ---------------------------------------------------------------------------
# Exit codes: the orthogonal axes, through the real CLI.
# ---------------------------------------------------------------------------


def test_findings_alone_never_gate(candidate: Path) -> None:
    """The fixture reports a finding and the command still exits 0."""
    result = invoke_cli("compare", "--no-baseline", str(candidate), "--format", "json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["findings"], "fixture precondition"


def test_output_file_receives_the_report(candidate: Path, tmp_path: Path) -> None:
    target = tmp_path / "audit.md"
    result = invoke_cli("compare", "--no-baseline", str(candidate), "-o", str(target))
    assert result.exit_code == 0, result.output
    assert target.read_text().startswith("# ABI audit:")


# ---------------------------------------------------------------------------
# The dry-run depth preview, driven directly.
# ---------------------------------------------------------------------------


def _dry_run(*, depth, sources=None, candidate_is_live=True):
    from abicheck.frontends.cli.no_baseline_dry_run import (
        build_no_baseline_dry_run_result,
    )

    return build_no_baseline_dry_run_result(
        candidate=Path("libfoo.so"),
        depth=depth,
        headers=(),
        includes=(),
        public_header_dirs=(),
        sources=sources,
        build_info=None,
        fmt="markdown",
        contract_mode=None,
        candidate_is_live=candidate_is_live,
    )


def test_dry_run_blocks_a_pinned_depth_with_no_evidence_input() -> None:
    """The real run exits 7 here, so the preview must not call it fine."""
    result = _dry_run(depth="build")
    assert result.blockers, "a pinned depth with nothing to collect must block"
    assert result.exit_code == 1
    assert "--sources" in result.blockers[0]


def test_dry_run_warns_rather_than_blocks_once_evidence_is_supplied() -> None:
    """With an input present, only the real collection can decide.

    Predicting success here would be guessing, and a dry run that guesses
    wrong in either direction is worse than one that says so — hence a
    warning, which does not change the exit code.
    """
    result = _dry_run(depth="build", sources=Path("."))
    assert not result.blockers
    assert result.warnings
    assert result.exit_code == 0


def test_dry_run_never_blocks_a_stored_candidate() -> None:
    """The live/stored carve-out, at the unit level."""
    result = _dry_run(depth="source", candidate_is_live=False)
    assert not result.blockers
    assert result.exit_code == 0


def test_dry_run_does_not_block_without_a_pinned_depth() -> None:
    """Omitting ``--depth`` is never a contract, so there is nothing to miss."""
    assert not _dry_run(depth=None).blockers
    assert not _dry_run(depth="headers").blockers

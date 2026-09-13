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
    result = invoke_cli("compare", "--no-baseline", str(candidate), "-o", f"{fmt}=-")
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
    result = invoke_cli("compare", "--no-baseline", str(candidate), "-o", f"{fmt}=-")
    assert result.exit_code == _EXIT_USAGE, result.output
    assert f"-o {fmt}=..." in result.output
    assert "compatibility comparison" in result.output
    assert "oneline" in result.output, "the error must point at a usable alternative"


def test_every_export_is_checked_against_the_supported_set(
    candidate: Path, tmp_path: Path
) -> None:
    """Not just the first: `-o` is repeatable, and an unsupported format in
    *any* export must be refused rather than silently producing nothing at
    that destination.

    This replaces a test that checked the error named `--write` rather than
    `--format` -- a distinction plan slice 7m dissolved, since both spellings
    are now the one `-o` operand. What survives is the substantive half: the
    rejection covers every export, and the message names the export grammar.
    """
    unsupported = tmp_path / "second.html"
    result = invoke_cli(
        "compare",
        "--no-baseline",
        str(candidate),
        "-o",
        "json=-",
        "-o",
        f"html={unsupported}",
    )
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "-o html=..." in result.output
    assert not unsupported.exists()


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
        "-o",
        f"json={out_json}",
        "-o",
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
        "compare", "--no-baseline", str(candidate), "-o", f"json={target}"
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
        "-o",
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
    result = invoke_cli("compare", "--no-baseline", str(candidate), "-o", "json=-")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["findings"], "fixture precondition"


def test_output_file_receives_the_report(candidate: Path, tmp_path: Path) -> None:
    target = tmp_path / "audit.md"
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "-o", f"markdown={target}"
    )
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


# ---------------------------------------------------------------------------
# Depth, outcome and error-boundary contracts (Codex review round 2).
# ---------------------------------------------------------------------------


def test_binary_depth_does_not_run_the_header_frontend(tmp_path: Path) -> None:
    """``--depth binary`` is symbols-only, even when headers are supplied.

    Regression guard: the audit hand-built its ``SideEvidence`` instead of
    going through the shared resolver, so it skipped the binary-depth
    clearing rules -- headers were still parsed and could *manufacture* a
    header-derived finding at a depth documented as symbols-only, diverging
    from the two-sided `compare` on the identical invocation.

    Asserted on a stored snapshot's own resolution rather than a live build,
    so it needs no toolchain: the rule under test is which evidence the
    resolver is handed, not what an extractor then finds.
    """
    from abicheck.service import InputSpec
    from abicheck.service_compare_evidence import resolve_side_evidence

    side = InputSpec.of(tmp_path / "libfoo.so", headers=[tmp_path / "inc"])
    binary = resolve_side_evidence(
        side,
        depth="binary",
        collect_mode="off",
        pair_compile=None,
        frontend_context="host",
    )
    headers = resolve_side_evidence(
        side,
        depth="headers",
        collect_mode="off",
        pair_compile=None,
        frontend_context="host",
    )
    assert binary.headers == [], "--depth binary must clear headers"
    assert headers.headers, "--depth headers must keep them"


def test_evidence_contract_failure_is_visible_in_run_outcome(
    candidate: Path,
) -> None:
    """A non-zero exit must not read as operationally successful.

    ``compatibility``/``gate`` genuinely never apply to an audit, but
    *operational* is a different axis -- and reporting ``none`` beside exit 7
    told a structured consumer the run was fine when the process said
    otherwise.
    """
    from abicheck.report.no_baseline import compute_no_baseline_document
    from abicheck.workflows.no_baseline_compare import (
        resolve_no_baseline_candidate,
        run_no_baseline_compare,
    )

    result = run_no_baseline_compare(resolve_no_baseline_candidate(candidate))
    assert compute_no_baseline_document(result).run_outcome["operational"] == "none"

    result.diff.evidence_contract_error = True
    doc = compute_no_baseline_document(result)
    assert doc.exit_code != 0
    assert doc.run_outcome["operational"] == "evidence_contract_error", (
        "a failed evidence contract must be visible on the outcome, not only "
        "in the process exit code"
    )


def test_an_unreadable_candidate_is_a_clean_error_not_a_traceback(
    tmp_path: Path,
) -> None:
    """Extraction failure is translated at the CLI boundary.

    The two-sided path already converts ``SnapshotError`` into a concise
    ``Error: ...``; this path let it escape as a full traceback for the
    identical input.
    """
    broken = tmp_path / "broken.so"
    broken.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"garbage" * 4)
    result = invoke_cli("compare", "--no-baseline", str(broken))
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "Failed to dump" in result.output


def test_a_dump_manifest_is_refused_rather_than_ignored(
    candidate: Path, tmp_path: Path
) -> None:
    """The sharpest instance of the silently-dropped-option class.

    A bare ``--dump-manifest`` reached a *generated* destination the dispatch
    never read, so even an invalid manifest exited 0 while the audit analysed
    a different surface than the user asked for. The guard is keyed on the
    generated destination now, not the pre-normalization name.
    """
    manifest = tmp_path / "bogus.json"
    manifest.write_text('{"not": "a manifest"}')
    result = invoke_cli(
        "compare", "--no-baseline", str(candidate), "--dump-manifest", str(manifest)
    )
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "--dump-manifest" in result.output


def _a_live_binary_this_host_can_audit(tmp_path: Path) -> Path:
    """A copy of a live binary artifact `--no-baseline` can actually audit.

    Not "any binary on PATH", which is what this was and why it broke. The
    original copied `shutil.which("true")` to `libfoo.so`: fine on Linux
    and macOS, where an ELF or Mach-O executable exposes symbols, and
    broken on Windows, where `true.exe` is a PE *executable* with no export
    directory at all -- so abicheck correctly refused it ("has no exports
    (named or ordinal)") and the test read that refusal as a broken
    `--version`. The name was misleading too: the operand was called `.so`
    on every platform while abicheck sniffs *content*, so on Windows it was
    a PE file wearing an ELF extension. Hence: pick per platform, keep the
    source's real filename, and assert the choice is a binary abicheck
    recognises so a bad candidate fails here, in setup, rather than as a
    puzzling exit code in the assertion.

    **Why POSIX uses an executable and not a real `.so`.** It should use a
    real shared library, and an earlier version of this helper did --
    resolving `libm.so.6` through the standard library directories, since
    `ctypes.util.find_library` answers a soname rather than a path. That
    fixture immediately hit a *separate, pre-existing* crash: auditing any
    real ELF library raises an uncaught `NoBaselineInvariantError`, because
    `diff_platform_elf_dynamic._diff_visibility_leak` is a single-sided
    detector (`del new  # detector is intentionally old-library-only`) that
    emits its finding with neither candidate-side marker, so the ADR-068
    partition files it as an identity-diff finding. It reproduces on `main`
    from a bare CLI call and is recorded in `docs/contribute/known-gaps.md`
    -- it is not this test's bug to fix and not this pull request's to
    widen into. This helper deliberately steps around it rather than
    silently, and the moment it is fixed the POSIX branch should go back to
    a real library.

    macOS additionally rules out `.dylib`: since macOS 11 the system
    libraries live in the dyld shared cache, so the paths `find_library`
    reports are not files on disk at all.
    """
    import os
    import shutil

    from abicheck.binary_utils import detect_binary_format

    tried: list[str] = []
    candidates: list[Path] = []

    if sys.platform == "win32":
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        # Real DLLs, i.e. things with an export directory -- the property
        # the previous fixture lacked on this platform.
        candidates += [system32 / name for name in ("kernel32.dll", "ws2_32.dll")]
    else:
        which_true = shutil.which("true")
        if which_true:
            candidates.append(Path(which_true))

    for candidate in candidates:
        tried.append(str(candidate))
        if not candidate.is_file():
            continue
        # Keep the source's own filename: the operand *is* that artifact, and
        # a manufactured `.so` name is half of what made the original bug
        # hard to read.
        copy = tmp_path / candidate.name
        copy.write_bytes(candidate.read_bytes())
        detected = detect_binary_format(copy)
        assert detected is not None, (
            f"{candidate} is not a binary abicheck recognises (detected "
            f"{detected!r}) -- a bad candidate in this helper, not a bug in "
            "the command under test"
        )
        return copy

    pytest.skip(f"no auditable live binary on this host; tried: {tried}")


def test_a_candidate_version_label_is_honoured(candidate: Path, tmp_path: Path) -> None:
    """``--version new=`` was another silently-dropped generated destination.

    Checked against a *live* artifact, since a stored snapshot keeps its own
    recorded version on both the one- and two-sided paths -- asserting on the
    snapshot would pass whether or not the option was wired.
    """
    binary = _a_live_binary_this_host_can_audit(tmp_path)
    result = invoke_cli(
        "compare",
        "--no-baseline",
        str(binary),
        "--version",
        "new=9.9",
        "-o",
        "json=-",
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["new_version"] == "9.9"


def test_a_linker_script_operand_counts_as_live(tmp_path: Path) -> None:
    """A GNU ld script that resolves to a DSO is a live artifact.

    The depth floor's live/stored carve-out asked ``detect_binary_format``
    about the operand *as written*. A linker script is text, so it answered
    "stored" and exempted the run — while the resolver happily followed
    ``INPUT(...)`` and performed a real extraction. The same library named
    directly exited 7; named through its script, 0.

    Asserted as *agreement between the two spellings* rather than as a fixed
    exit code, so the pair cannot drift apart again.
    """
    from abicheck.workflows.no_baseline_compare import candidate_is_live_artifact

    real = tmp_path / "real.so"
    real.write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(56))
    script = tmp_path / "alias.so"
    script.write_text(f"INPUT({real})\n")

    assert candidate_is_live_artifact(real) is True
    assert candidate_is_live_artifact(script) is True, (
        "a linker script resolving to a native artifact is a live extraction, "
        "so it must not be exempted from the pinned-depth floor"
    )
    stored = tmp_path / "snap.abi.json"
    stored.write_text('{"library": "x"}')
    assert candidate_is_live_artifact(stored) is False


@pytest.mark.parametrize("evidence_flag", ["--sources", "--build-info"])
def test_raw_evidence_makes_even_a_stored_operand_live(
    tmp_path: Path, evidence_flag: str
) -> None:
    """Liveness is a property of the *run*, not only of the operand's path.

    A run given a raw ``--sources`` checkout or ``--build-info`` build dir
    collects L3-L5 evidence itself, so a pinned ``--depth build``/``--depth
    source`` is something it can genuinely fall short of — even when the
    artifact operand is an already-serialized snapshot. Asking
    ``detect_binary_format`` about the operand alone answered "stored" and
    exempted exactly that case, so the audit reported a clean exit 0 where
    the equivalent two-sided invocation exited 7 (Codex review, P1).

    Both evidence flags are exercised, not just the reported one: they reach
    the same predicate through the same parameter, and a fix wired for one
    would silently leave the other open. The oracle is the *two-sided*
    command's own exit code on the same inputs — the behavior the one-sided
    path is supposed to match — rather than a hard-coded 7, so the two
    cannot drift apart again.
    """
    from abicheck.workflows.no_baseline_compare import candidate_is_live_artifact

    stored = tmp_path / "snap.abi.json"
    stored.write_text('{"library": "x"}')
    raw = tmp_path / "checkout"
    raw.mkdir()
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text('{"build_source_pack_version": 1}')

    kwarg = "sources" if evidence_flag == "--sources" else "build_info"
    assert candidate_is_live_artifact(stored) is False
    assert candidate_is_live_artifact(stored, **{kwarg: raw}) is True, (
        "raw evidence means this run extracts, so the pinned-depth floor applies"
    )
    assert candidate_is_live_artifact(stored, **{kwarg: pack}) is False, (
        "a prebuilt pack is loaded, not collected from — it does not make the "
        "run live, and treating it as live would fire the floor on a run that "
        "never extracted anything"
    )


@pytest.mark.parametrize("depth", ["build", "source"])
def test_a_stored_operand_with_raw_evidence_gates_like_the_two_sided_run(
    tmp_path: Path, depth: str
) -> None:
    """End-to-end companion to the predicate test above, through the CLI.

    Both pinned depths are covered because both are in the floor's gated
    set; a fix keyed to one rung would leave the other silently exempt.
    """
    case = example_catalog.case_dir("case143_audit_accidental_export")
    snapshot = case / "snapshot.abi.json"
    empty = tmp_path / "src"
    empty.mkdir()

    audit = invoke_cli(
        "compare",
        "--no-baseline",
        str(snapshot),
        "--sources",
        str(empty),
        "--depth",
        depth,
    )
    two_sided = invoke_cli(
        "compare",
        str(snapshot),
        str(snapshot),
        "--sources",
        f"new={empty}",
        "--depth",
        depth,
    )
    assert audit.exit_code == two_sided.exit_code, (
        f"one-sided exited {audit.exit_code}, two-sided {two_sided.exit_code} "
        "on the same operand and the same unsatisfiable pinned depth"
    )


def _project_snapshot_package(tmp_path: Path, case: str) -> Path:
    """A real directory-backed storage-v2 ``ProjectSnapshot`` package holding
    the same single artifact *case*'s committed ``.abi.json`` file holds."""
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
    from abicheck.serialization import load_snapshot, snapshot_to_dict

    document = snapshot_to_dict(
        load_snapshot(example_catalog.case_dir(case) / "snapshot.abi.json")
    )
    package = tmp_path / "package"
    write_legacy_snapshot_package(
        document,
        package,
        artifact_id="libdemo.so",
        max_known_schema_version=document.get("schema_version", 1),
    )
    return package


@pytest.mark.parametrize(
    "operand",
    ["symvers", "unknown_text"],
)
def test_an_operand_this_run_parses_is_held_to_the_pinned_depth(
    tmp_path: Path, operand: str
) -> None:
    """Only an already-*serialized snapshot* is exempt from the depth floor.

    The carve-out asked ``detect_binary_format(path) is not None`` — "is this
    a native binary?" — which is a narrower question with a different answer
    for every operand that is neither a binary nor a serialized ABI
    description. Each of those has an ABI description *derived* from it by
    this run and structurally cannot carry L3-L5 evidence, so a pinned
    ``--depth source`` was silently unsatisfiable: ``Module.symvers --depth
    source`` reported no evidence tiers at all and exit 0 (Codex review, P1).

    Parametrized over the *class* rather than the reported input: the shared
    property is "raw evidence, not an ABI description someone already wrote
    down", and a fix keyed to symvers alone would leave the siblings exempt.
    The oracle is the predicate's own contract — anything not already an ABI
    description is live — checked here against operands chosen to be exactly
    the ones that used to slip through.
    """
    from abicheck.workflows.no_baseline_compare import candidate_is_live_artifact

    bodies = {
        "symvers": "0x00000000\tvfs_read\tvmlinux\tEXPORT_SYMBOL\n",
        "unknown_text": "not a snapshot, not a binary\n",
    }
    path = tmp_path / operand
    path.write_text(bodies[operand])
    assert candidate_is_live_artifact(path) is True, (
        f"a {operand} operand is parsed into a fresh snapshot by this run, so "
        "a pinned --depth build/source is something it can fall short of"
    )


def test_a_serialized_description_stays_exempt_in_every_one_of_its_shapes(
    tmp_path: Path,
) -> None:
    """The complement, so the fix above cannot be 'gate everything'.

    Every shape `resolve_input` accepts as an already-serialized ABI
    description is exempt — a single ``.abi.json`` file, a directory-backed
    `ProjectSnapshot` package, and an ABICC Perl dump — because only such a
    description can already *carry* the pinned evidence. A plain directory of
    libraries is none of them, and must not be swept into the exemption.
    """
    from abicheck.workflows.no_baseline_compare import candidate_is_live_artifact

    stored = (
        example_catalog.case_dir("case143_audit_accidental_export")
        / "snapshot.abi.json"
    )
    package = _project_snapshot_package(tmp_path, "case143_audit_accidental_export")
    perl_dump = tmp_path / "saved.dump"
    perl_dump.write_text("$VAR1 = {\n  'ABI' => {}\n};\n")
    plain = tmp_path / "release"
    plain.mkdir()
    (plain / "libfoo.so").write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(56))

    assert candidate_is_live_artifact(stored) is False
    assert candidate_is_live_artifact(package) is False, (
        "a ProjectSnapshot package is the repository's own storage-v2 form of "
        "the same stored snapshot, so it carries the same exemption"
    )
    assert candidate_is_live_artifact(perl_dump) is False, (
        "an ABICC Perl dump is a pre-built, tool-produced ABI description "
        "this run parses rather than extracts, exactly like an .abi.json; "
        "splitting the two on serialization format alone made --depth source "
        "exit 7 on one and 0 on the other (Codex review, P2)"
    )
    assert candidate_is_live_artifact(plain) is True, (
        "a plain directory of libraries is not a stored snapshot; exempting "
        "one would be the same silent-clean-result bug in reverse"
    )


def test_the_audit_accepts_a_project_snapshot_package_directory(
    tmp_path: Path,
) -> None:
    """A single artifact is a single artifact, whichever shape it is stored in.

    The dispatch rejected every directory outright, so the same snapshot was
    accepted as a `.abi.json` file and refused in the repository's own
    package form — which a two-sided `compare` accepts (Codex review, P2).
    Asserted as *agreement between the two shapes*' finding sets rather than
    a fixed exit code, so they cannot drift apart again.
    """
    case = "case143_audit_accidental_export"
    package = _project_snapshot_package(tmp_path, case)
    stored = example_catalog.case_dir(case) / "snapshot.abi.json"

    from_package = invoke_cli("compare", "--no-baseline", str(package), "-o", "json=-")
    from_file = invoke_cli("compare", "--no-baseline", str(stored), "-o", "json=-")
    assert from_package.exit_code == from_file.exit_code == 0, from_package.output

    kinds_of = lambda out: sorted(f["kind"] for f in json.loads(out)["findings"])  # noqa: E731
    assert kinds_of(from_package.output) == kinds_of(from_file.output)


def test_a_release_directory_is_still_a_usage_error(tmp_path: Path) -> None:
    """The narrowing must not become 'accept any directory'.

    A directory of several libraries needs the per-library fan-out this path
    does not have yet, so it stays a usage error naming that reason.
    """
    plain = tmp_path / "release"
    plain.mkdir()
    (plain / "libfoo.so").write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(56))

    result = invoke_cli("compare", "--no-baseline", str(plain))
    assert result.exit_code == 64, result.output
    assert "directory of libraries" in result.output


def _in_dir(tmp_path: Path, config_text: str | None):
    """A working directory with (or without) an auto-discovered project config."""
    work = tmp_path / "project"
    work.mkdir(exist_ok=True)
    snapshot = work / "snapshot.abi.json"
    if not snapshot.exists():
        snapshot.write_text(
            (
                example_catalog.case_dir("case143_audit_accidental_export")
                / "snapshot.abi.json"
            ).read_text()
        )
    cfg = work / ".abicheck.yml"
    if config_text is None:
        cfg.unlink(missing_ok=True)
    else:
        cfg.write_text(config_text)
    return work, snapshot


def test_a_malformed_discovered_config_fails_the_audit_as_it_fails_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auto-discovered `.abicheck.yml` is not invisible to the audit.

    The audit dispatches before the two-sided config resolution and never
    reached it, so a malformed discovered config exited 0 here while ordinary
    `compare` exits 64 (Codex review, P1). The oracle is the two-sided
    command's own exit code on the same directory — not a hard-coded 64 — so
    the two cannot drift apart again.
    """
    work, snapshot = _in_dir(tmp_path, "this is: [not valid yaml\n")
    monkeypatch.chdir(work)

    audit = invoke_cli("compare", "--no-baseline", str(snapshot))
    two_sided = invoke_cli("compare", str(snapshot), str(snapshot))
    assert audit.exit_code == two_sided.exit_code, (
        f"audit exited {audit.exit_code}, two-sided {two_sided.exit_code} "
        "on the same malformed auto-discovered config"
    )
    assert audit.exit_code != 0


@pytest.mark.parametrize("public", [True, False])
def test_a_discovered_configs_scope_reaches_the_audit_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public: bool
) -> None:
    """A valid config's `scope.public` must actually change what is audited.

    Asserted at the runner boundary rather than through a finding count:
    the value has to *arrive*, and a fixture whose findings happen not to be
    scope-sensitive would let a silently-dropped setting pass. Both values
    are exercised, since honoring only the default would satisfy one row.
    """
    import abicheck.frontends.cli.commands.compare_no_baseline as cmd

    work, snapshot = _in_dir(tmp_path, f"scope:\n  public: {str(public).lower()}\n")
    monkeypatch.chdir(work)

    seen: dict[str, object] = {}
    original = cmd.run_no_baseline_compare

    def spy(candidate, **kwargs):
        seen["scope"] = kwargs.get("scope_to_public_surface")
        return original(candidate, **kwargs)

    monkeypatch.setattr(cmd, "run_no_baseline_compare", spy)
    result = invoke_cli("compare", "--no-baseline", str(snapshot), "-o", "json=-")
    assert result.exit_code == 0, result.output
    assert seen["scope"] is public, (
        "the discovered config's scope.public must reach the audit runner; "
        "dropping it audits a different surface than the same directory's "
        "`compare` would"
    )


def test_a_discovered_configs_deployment_reaches_the_audit_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex review finding 4: a discovered `.abicheck.yml`'s `deployment:`
    (`resolved_cfg.deployment`, the config-only `EnvironmentMatrix` the
    two-sided `compare` path threads through too) must reach
    `run_no_baseline_compare` -- previously that function had no
    `env_matrix` parameter at all, so a declared `deployment.runtime_floors`
    was silently invisible to this audit mode."""
    import abicheck.frontends.cli.commands.compare_no_baseline as cmd
    from abicheck.environment_matrix import EnvironmentMatrix

    work, snapshot = _in_dir(
        tmp_path, 'deployment:\n  runtime_floors:\n    GLIBC: "2.28"\n'
    )
    monkeypatch.chdir(work)

    seen: dict[str, object] = {}
    original = cmd.run_no_baseline_compare

    def spy(candidate, **kwargs):
        seen["env_matrix"] = kwargs.get("env_matrix")
        return original(candidate, **kwargs)

    monkeypatch.setattr(cmd, "run_no_baseline_compare", spy)
    result = invoke_cli("compare", "--no-baseline", str(snapshot), "-o", "json=-")
    assert result.exit_code == 0, result.output
    matrix = seen["env_matrix"]
    assert isinstance(matrix, EnvironmentMatrix)
    assert matrix.runtime_floors == {"GLIBC": "2.28"}


def test_the_audit_sarif_names_why_it_exited(tmp_path: Path) -> None:
    """SARIF must carry the coverage ledger, not just `exitCode: 1`.

    A code-scanning consumer reading only the exit code cannot tell which
    provider fell short (Codex review, P2). The failures become
    `toolExecutionNotifications` — SARIF's shape for "the run itself was
    limited", which is what an incomplete evidence domain is, rather than a
    `result` about the code.
    """
    from abicheck.report.no_baseline import render_no_baseline
    from abicheck.workflows.no_baseline_compare import (
        resolve_no_baseline_candidate,
        run_no_baseline_compare,
    )

    # `case145` under `--contract public` is short of evidence and really
    # gates; `case143` closes the domain cleanly, so pointing this test at
    # it and guarding on `if exit_code:` left the whole gated assertion
    # unreachable (CodeRabbit review). The gate is asserted first, so the
    # test fails loudly if the fixture ever stops gating instead of
    # quietly passing on nothing.
    snapshot = (
        example_catalog.case_dir("case145_audit_unversioned_export")
        / "snapshot.abi.json"
    )
    result = run_no_baseline_compare(
        resolve_no_baseline_candidate(snapshot),
        contract_evaluation=True,
        contract_mode="public",
    )
    payload, exit_code = render_no_baseline(result, "sarif")
    run = json.loads(payload)["runs"][0]
    invocation = run["invocations"][0]

    assert exit_code != 0, "the gated fixture must actually gate"
    assert "contract coverage incomplete" in invocation["exitCodeDescription"]
    notifications = invocation.get("toolExecutionNotifications") or []
    assert notifications, "a gated audit must say which provider fell short"
    assert any("provider" in n["message"]["text"] for n in notifications)
    assert run["properties"]["contractCoverageFailures"]
    assert invocation["executionSuccessful"] is True, (
        "SARIF's executionSuccessful means the tool ran to completion, not "
        "that it found nothing"
    )


@pytest.mark.parametrize(
    ("config_text", "suppression_text"),
    [
        pytest.param(
            "suppression:\n  require_justification: true\n",
            'version: 1\nsuppressions:\n  - symbol_pattern: ".*"\n',
            id="require_justification",
        ),
        pytest.param(
            "suppression:\n  strict: true\n",
            (
                "version: 1\n"
                "suppressions:\n"
                '  - symbol_pattern: ".*"\n'
                '    reason: "audited"\n'
                '    expires: "2020-01-01"\n'
            ),
            id="strict_expiry",
        ),
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_configs_suppression_acceptance_rules_bind_the_audit_too(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_text: str,
    suppression_text: str,
    dry_run: bool,
) -> None:
    """`.abicheck.yml`'s `suppression:` acceptance settings gate the audit.

    These two settings decide whether a suppression *document* may be used at
    all, not which findings it matches. The audit resolved the project config
    but never read them, so a reasonless (or long-expired) rule that ordinary
    `compare` rejects was accepted here — and then suppressed the finding and
    exited 0, which is the worst possible direction for the failure to run in
    (Codex review, P1).

    Both settings are exercised, since honoring one would satisfy a
    single-row test, and `--dry-run` is exercised alongside the real run: a
    preview that accepts a document the run rejects approves a run that
    cannot start. The oracle is the two-sided command's own exit code on the
    same directory and the same document — not a hard-coded number — so the
    two paths cannot drift apart again.
    """
    work, snapshot = _in_dir(tmp_path, config_text)
    suppress = work / "sup.yaml"
    suppress.write_text(suppression_text)
    monkeypatch.chdir(work)

    real_audit = invoke_cli(
        "compare", "--no-baseline", str(snapshot), "--suppress", str(suppress)
    )
    two_sided = invoke_cli(
        "compare", str(snapshot), str(snapshot), "--suppress", str(suppress)
    )
    assert two_sided.exit_code != 0, (
        "the two-sided command is this test's oracle; if it stopped "
        "rejecting the document there is nothing left to compare against"
    )
    assert real_audit.exit_code == two_sided.exit_code, (
        f"audit exited {real_audit.exit_code}, two-sided "
        f"{two_sided.exit_code} on the same rejected suppression document"
    )

    if not dry_run:
        return

    # `--dry-run`'s oracle is the audit's own real run, not the two-sided
    # command's preview: two-sided `--dry-run` does not load the suppression
    # document at all, while this path deliberately does, so that a preview
    # cannot approve a run that then cannot start (an earlier Codex P2 on
    # this same command). Comparing against the looser preview would pin the
    # weaker behaviour, so the invariant asserted here is the one this path
    # actually promises.
    preview = invoke_cli(
        "compare",
        "--no-baseline",
        str(snapshot),
        "--suppress",
        str(suppress),
        "--dry-run",
    )
    assert preview.exit_code == real_audit.exit_code, (
        f"the preview exited {preview.exit_code} where the real audit exits "
        f"{real_audit.exit_code}; a preview that accepts a document the run "
        "rejects approves a run that cannot start"
    )

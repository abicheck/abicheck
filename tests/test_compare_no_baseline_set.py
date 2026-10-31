# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``abicheck compare --no-baseline DIR`` -- the N-library audit
(one-comparison-product F-23), through the public CLI.

The oracles here are deliberately *not* the implementation:

* a member's embedded report is checked against a **separate scalar run**
  of ``compare --no-baseline <that member>`` -- the documented contract is
  "each member is audited exactly as that file would be on its own", and a
  second process-level invocation is the only thing that states it without
  reusing the set path's own code;
* acquisition states and member sets are checked against what the test
  itself wrote to disk, never against a function of the implementation;
* exit codes are checked against the documented fold (max over the listed
  axes), recomputed here from the per-member scalar exit axes.

Volatile fields: none. Every comparison below is whole-document equality,
and it holds for stored-snapshot members, for members extracted out of an
archive (compared with a scalar run over the test's own independent
extraction of the same archive), and for gcc-built ELF members (the
``integration`` test at the bottom). Nothing in an audit document records a
path, a timestamp or a temporary directory.
"""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.cli import main  # noqa: E402

_EXIT_USAGE = 64

#: The four committed audit fixtures, each reporting at least one
#: candidate-side finding (`case145` is also short of `--contract public`
#: evidence, which the exit-fold test relies on).
_CASES = (
    "case143_audit_accidental_export",
    "case144_audit_private_header_leak",
    "case145_audit_unversioned_export",
    "case146_audit_rtti_for_internal",
)


def _invoke(*args: str, cwd: Path | None = None):
    runner = CliRunner()
    if cwd is None:
        return runner.invoke(main, list(args))
    with runner.isolated_filesystem(temp_dir=cwd):
        return runner.invoke(main, list(args))


def _case_text(case: str) -> str:
    """A committed fixture, re-serialized so its ``library`` key leads.

    The fixtures are sorted-key documents whose ``library`` key sits past
    the 4 KiB prefix ``classify.AbiJsonClassifier`` probes, so a directory
    scan would not recognize them as snapshots at all (a property of the
    two-sided discovery too, not of this feature). Content is unchanged.
    """
    data = json.loads(
        (example_catalog.case_dir(case) / "snapshot.abi.json").read_text()
    )
    return json.dumps({"library": data["library"], **data})


def _populate(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Write one member per name (cycling the four fixtures); return
    ``{filename: case}`` -- the test's own record of what it wrote."""
    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for i, name in enumerate(names):
        case = _CASES[i % len(_CASES)]
        (root / name).write_text(_case_text(case))
        written[name] = case
    return written


def _json(result) -> dict:
    assert result.exit_code != _EXIT_USAGE, result.output
    return json.loads(result.stdout)


def _scalar(path: Path, *extra: str) -> tuple[dict, int]:
    result = _invoke("compare", "--no-baseline", str(path), "-o", "json=-", *extra)
    doc = _json(result)
    assert "audit_set" not in doc, "a single file must stay a scalar audit"
    return doc, result.exit_code


def _by_name(doc: dict) -> dict[str, dict]:
    return {m["display_name"]: m for m in doc["members"]}


# ---------------------------------------------------------------------------
# The oracle: a member's report is the scalar audit of that member.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [(), ("--contract", "public")])
def test_each_member_report_equals_a_scalar_run_of_that_member(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    root = tmp_path / "release"
    written = _populate(
        root, ("liba.abi.json", "libb.abi.json", "libc.abi.json", "libd.abi.json")
    )
    result = _invoke("compare", "--no-baseline", str(root), "-o", "json=-", *extra)
    doc = _json(result)
    members = _by_name(doc)
    assert set(members) == set(written)
    worst = 0
    for name in written:
        scalar, scalar_exit = _scalar(root / name, *extra)
        assert members[name]["report"] == scalar, name
        worst = max(worst, scalar_exit)
    # Every member completed, so the set's exit is the worst member's own.
    assert result.exit_code == doc["exit_code"] == worst


def test_a_one_member_directory_matches_the_scalar_path(tmp_path: Path) -> None:
    root = tmp_path / "one"
    _populate(root, ("libonly.abi.json",))
    scalar, scalar_exit = _scalar(root / "libonly.abi.json", "--contract", "public")
    result = _invoke(
        "compare", "--no-baseline", str(root), "-o", "json=-", "--contract", "public"
    )
    doc = _json(result)
    assert result.exit_code == scalar_exit
    assert [{k: v for k, v in f.items() if k != "member"} for f in doc["findings"]] == (
        scalar["findings"]
    )
    assert doc["exit_code"] == scalar["exit_code"]


@pytest.mark.parametrize("mode", ["w:gz", "w:xz", "w:bz2", "w:"])
def test_an_archive_equals_scalar_runs_over_its_extracted_files(
    tmp_path: Path, mode: str
) -> None:
    src = tmp_path / "src"
    written = _populate(src, ("liba.abi.json", "libb.abi.json", "libc.abi.json"))
    archive = tmp_path / f"release.tar{'.' + mode[2:] if mode[2:] else ''}"
    with tarfile.open(archive, mode) as tar:
        for name in written:
            tar.add(src / name, arcname=f"usr/lib/{name}")
    # The test's own extraction, independent of abicheck's extractor.
    extracted = tmp_path / "extracted"
    with tarfile.open(archive) as tar:
        tar.extractall(extracted, filter="data")
    doc = _json(_invoke("compare", "--no-baseline", str(archive), "-o", "json=-"))
    assert doc["operand_kind"] == "package"
    members = _by_name(doc)
    assert set(members) == set(written)
    for name in written:
        scalar, _ = _scalar(extracted / "usr" / "lib" / name)
        assert members[name]["report"] == scalar, name
    # ADR-065 D2: a fully extracted archive proves its inventory.
    assert doc["comparison_scope"]["new_inventory"]["completeness"] == "proven"


# ---------------------------------------------------------------------------
# Routing: the bug class -- a set operand audited as if it were one artifact.
# ---------------------------------------------------------------------------


def _wheel(path: Path, src: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.write(src / name, arcname=f"demo/{name}")
        zf.writestr(
            "demo-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: t\nRoot-Is-Purelib: false\nTag: py3-none-any\n",
        )
        zf.writestr(
            "demo-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
        )


def _stored_package(root: Path, *, degraded: bool = False, n: int = 2) -> list[str]:
    """A stored ``ProjectSnapshot`` package of *n* libraries; returns the
    member keys a reader resolves them to (the test package helpers each
    stamp their own filename convention, so the key is read from the
    two-sided release resolution rather than guessed)."""
    from test_release_scope_bundle import _lib
    from test_release_scope_completeness import _write_stored_package

    from abicheck.workflows.release_package import resolve_release_package_map

    names = [f"lib{chr(ord('a') + i)}.so" for i in range(n)]
    libs = {name: _lib(name, exports=(f"f{i}",)) for i, name in enumerate(names)}
    _write_stored_package(root, libs, degraded={names[0]: "boom"} if degraded else None)
    unpacked = root.parent / f"{root.name}-unpacked"
    keys = resolve_release_package_map(root, variant_id=None, dest_root=unpacked)
    assert len(keys) == n
    return [path.name for path in keys.values()]


def _operand(tmp_path: Path, shape: str) -> tuple[Path, set[str]]:
    """Build one set-shaped operand; return it and the member names the test
    itself put in it (the oracle)."""
    src = tmp_path / "src"
    names = list(_populate(src, ("liba.abi.json", "libb.abi.json")))
    if shape == "directory":
        return src, set(names)
    if shape.startswith("tar"):
        suffix = {"tar": "", "tar.gz": ":gz", "tar.xz": ":xz", "tar.bz2": ":bz2"}[shape]
        path = tmp_path / f"release.{shape}"
        with tarfile.open(path, f"w{suffix}") as tar:
            for name in names:
                tar.add(src / name, arcname=name)
        return path, set(names)
    if shape == "wheel":
        path = tmp_path / "demo-1.0-py3-none-any.whl"
        _wheel(path, src, names)
        return path, set(names)
    if shape == "stored_multi":
        root = tmp_path / "pkg"
        return root, set(_stored_package(root))
    if shape == "stored_degraded_single":
        root = tmp_path / "pkg"
        return root, set(_stored_package(root, degraded=True, n=1))
    raise AssertionError(shape)


@pytest.mark.parametrize(
    "shape",
    [
        "directory",
        "tar",
        "tar.gz",
        "tar.xz",
        "tar.bz2",
        "wheel",
        "stored_multi",
        "stored_degraded_single",
    ],
)
def test_every_set_shaped_operand_is_audited_per_member(
    tmp_path: Path, shape: str
) -> None:
    """The routing bug class: before F-23 only a plain directory was told
    apart, so a package archive and a multi-artifact or degraded stored
    package reached the scalar resolver -- refused, or audited as one
    complete artifact. Every shape the two-sided ``compare`` fans out over
    must reach the per-member audit, and every member the test wrote must be
    listed (none dropped, none invented)."""
    operand, expected = _operand(tmp_path, shape)
    result = _invoke("compare", "--no-baseline", str(operand), "-o", "json=-")
    doc = _json(result)
    assert doc["audit_set"] is True
    assert set(_by_name(doc)) == expected
    if shape == "stored_degraded_single":
        (member,) = doc["members"]
        # The degraded capture is a failed acquisition, never an audit of
        # incomplete evidence -- and with nothing audited, exit 1 (D7).
        assert member["acquisition_state"] == "failed"
        assert member["report"] is None
        assert result.exit_code == 1
    else:
        assert {m["acquisition_state"] for m in doc["members"]} == {"declared_absent"}


@pytest.mark.parametrize("shape", ["file", "stored_single"])
def test_a_single_artifact_operand_stays_scalar(tmp_path: Path, shape: str) -> None:
    if shape == "file":
        operand = tmp_path / "liba.abi.json"
        operand.write_text(_case_text(_CASES[0]))
    else:
        operand = tmp_path / "pkg"
        _stored_package(operand, n=1)
    doc = _json(_invoke("compare", "--no-baseline", str(operand), "-o", "json=-"))
    assert "audit_set" not in doc
    assert doc["audit_report_schema_version"]


# ---------------------------------------------------------------------------
# Scope and completeness (ADR-065 D2/D6/D7, S1).
# ---------------------------------------------------------------------------


def _with_config(tmp_path: Path, text: str) -> Path:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / ".abicheck.yml").write_text(text)
    return work


@pytest.mark.parametrize(("policy", "expected_exit"), [("warn", 0), ("block", 1)])
def test_a_missing_required_member_is_expected_not_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, expected_exit: int
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json", "libb.abi.json"))
    monkeypatch.chdir(_with_config(tmp_path, f"scope:\n  on_incomplete: {policy}\n"))
    result = _invoke(
        "compare",
        "--no-baseline",
        str(root),
        "--select-required",
        "liba.abi.json",
        "--select-required",
        "libmissing.so",
        "-o",
        "json=-",
    )
    doc = _json(result)
    members = {m["member"]: m for m in doc["members"]}
    assert members["libmissing.so"]["acquisition_state"] == "expected_not_produced"
    assert members["libmissing.so"]["report"] is None
    assert members["liba.abi.json"]["acquisition_state"] == "declared_absent"
    # Discovered but undeclared: never audited, never a gap.
    assert members["libb.abi.json"]["acquisition_state"] == "out_of_scope"
    assert doc["comparison_scope"]["completeness"] == "incomplete"
    assert doc["comparison_scope"]["unchecked"] == ["libmissing.so"]
    assert result.exit_code == doc["exit_code"] == expected_exit
    assert doc["exit_axes"]["incomplete_scope"] == expected_exit


def test_an_optional_absent_member_keeps_the_scope_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    monkeypatch.chdir(_with_config(tmp_path, "scope:\n  on_incomplete: block\n"))
    result = _invoke(
        "compare",
        "--no-baseline",
        str(root),
        "--select-required",
        "liba.abi.json",
        "--select",
        "libopt.so",
        "-o",
        "json=-",
    )
    doc = _json(result)
    opt = {m["member"]: m for m in doc["members"]}["libopt.so"]
    assert opt["acquisition_state"] == "expected_not_produced"
    assert opt["required"] is False
    assert doc["comparison_scope"]["completeness"] == "complete"
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "select",
    [("--select-required", "libnope.so"), ("--select", "libnope.so")],
)
def test_a_selection_matching_nothing_exits_1_never_clean(
    tmp_path: Path, select: tuple[str, str]
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    result = _invoke("compare", "--no-baseline", str(root), *select, "-o", "json=-")
    doc = _json(result)
    assert doc["comparison_scope"]["no_comparison_completed"] is True
    assert doc["run_outcome"]["operational"] == "no_comparison_completed"
    assert result.exit_code == doc["exit_axes"]["no_comparison_completed"] == 1


def test_a_corrupt_member_is_listed_failed_with_the_operational_axis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    (root / "libbad.abi.json").write_text('{"library": "libbad.so", "functions": 7}')
    result = _invoke("compare", "--no-baseline", str(root), "-o", "json=-")
    doc = _json(result)
    bad = _by_name(doc)["libbad.abi.json"]
    assert bad["acquisition_state"] == "failed"
    assert bad["report"] is None and bad["reason"]
    assert _by_name(doc)["liba.abi.json"]["acquisition_state"] == "declared_absent"
    # The same contribution the two-sided release path folds for a failed
    # member, and the run reports itself as not cleanly completed.
    assert doc["exit_axes"]["operational_error"] == 4
    assert doc["run_outcome"]["operational"] == "extraction_error"
    assert result.exit_code == 4


def test_an_undetectable_member_fails_alone_like_the_two_sided_release(
    tmp_path: Path,
) -> None:
    """A member whose format cannot be detected is that member's failure,
    not the run's: the rest are still audited, and the exit matches what the
    two-sided release fan-out does with the very same member set."""
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json", "libb.abi.json"))
    (root / "libbad.so").write_bytes(b"not a binary at all\n")
    result = _invoke("compare", "--no-baseline", str(root), "-o", "json=-")
    doc = _json(result)
    members = _by_name(doc)
    assert members["libbad.so"]["acquisition_state"] == "failed"
    assert members["libbad.so"]["report"] is None
    for name in ("liba.abi.json", "libb.abi.json"):
        assert members[name]["report"] is not None, name

    # Oracle: the two-sided release over the same member set on both sides.
    old = tmp_path / "old"
    _populate(old, ("liba.abi.json", "libb.abi.json"))
    (old / "libbad.so").write_bytes(b"not a binary at all\n")
    two_sided = _invoke("compare", str(old), str(root), "-o", "json=-")
    assert two_sided.exit_code == result.exit_code == 4, two_sided.output


def test_an_unreadable_newer_snapshot_is_unsupported_not_failed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    data = json.loads(_case_text(_CASES[0]))
    data["schema_version"] = 10_000
    (root / "libnew.abi.json").write_text(json.dumps(data))
    result = _invoke("compare", "--no-baseline", str(root), "-o", "json=-")
    doc = _json(result)
    member = _by_name(doc)["libnew.abi.json"]
    assert member["acquisition_state"] == "unsupported", member
    # Incompleteness, not an operational failure: warn accepts it.
    assert doc["exit_axes"]["operational_error"] == 0
    assert doc["comparison_scope"]["completeness"] == "incomplete"
    assert result.exit_code == 0


def test_every_member_failing_exits_nonzero(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    for name in ("liba.abi.json", "libb.abi.json"):
        (root / name).write_text('{"library": "x.so", "functions": 7}')
    result = _invoke("compare", "--no-baseline", str(root), "-o", "json=-")
    doc = _json(result)
    assert {m["acquisition_state"] for m in doc["members"]} == {"failed"}
    assert doc["exit_axes"]["no_comparison_completed"] == 1
    assert result.exit_code == max(doc["exit_axes"].values()) == 4


def test_an_empty_directory_gives_the_two_sided_paths_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _invoke("compare", "--no-baseline", str(empty))
    two_sided = _invoke("compare", str(empty), str(empty))
    assert result.exit_code == two_sided.exit_code == 1
    assert "No supported ABI inputs found in directory" in result.output
    assert "No supported ABI inputs found in directory" in two_sided.output


@pytest.mark.parametrize("complete", [True, False])
def test_a_stored_package_proves_its_inventory_only_by_its_assertion(
    tmp_path: Path, complete: bool
) -> None:
    from test_release_scope_bundle import _lib
    from test_release_scope_completeness import _write_stored_package

    root = tmp_path / "pkg"
    _write_stored_package(
        root,
        {"liba.so": _lib("liba.so", exports=("a",)), "libb.so": _lib("libb.so")},
        inventory_complete=complete,
    )
    doc = _json(_invoke("compare", "--no-baseline", str(root), "-o", "json=-"))
    expected = "proven" if complete else "unproven"
    assert doc["comparison_scope"]["new_inventory"]["completeness"] == expected
    # Proven or not, an audit never reads absence as removal or addition.
    assert doc["comparison_scope"]["proven_removed"] == []
    assert doc["comparison_scope"]["proven_added"] == []


def test_a_directory_proves_nothing_about_its_inventory(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    doc = _json(_invoke("compare", "--no-baseline", str(root), "-o", "json=-"))
    assert doc["comparison_scope"]["new_inventory"]["completeness"] == "unproven"


# ---------------------------------------------------------------------------
# The exit fold: max over every contributing axis, each one listed.
# ---------------------------------------------------------------------------


def test_the_exit_code_is_the_max_and_every_contributing_axis_is_listed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    # case143 is promoted to BREAKING by the policy below (audit gate, 3);
    # case145 is short of `--contract public` evidence (coverage, 1); a
    # corrupt member adds the operational axis (4).
    (root).mkdir()
    (root / "liba.abi.json").write_text(_case_text(_CASES[0]))
    (root / "libc.abi.json").write_text(_case_text(_CASES[2]))
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "base_policy: strict_abi\noverrides:\n  exported_not_public: break\n"
    )
    args = (
        "--contract",
        "public",
        "--policy",
        str(policy),
        "--severity-preset",
        "default",
    )
    clean = _invoke("compare", "--no-baseline", str(root), "-o", "json=-", *args)
    doc = _json(clean)
    axes = doc["exit_axes"]
    # Oracle: each member's own scalar axes, folded here by the documented rule.
    scalar = {
        n: _scalar(root / n, *args)[0]["exit_axes"]
        for n in ("liba.abi.json", "libc.abi.json")
    }
    assert scalar["liba.abi.json"]["audit_gate"] == 3
    assert scalar["libc.abi.json"]["contract_coverage"] == 1
    for axis in (
        "audit_gate",
        "contract_coverage",
        "analysis_assurance",
        "evidence_contract",
    ):
        assert axes[axis] == max(s[axis] for s in scalar.values()), axis
    assert clean.exit_code == doc["exit_code"] == 3

    (root / "libbad.abi.json").write_text('{"library": "libbad.so", "functions": 7}')
    failing = _invoke("compare", "--no-baseline", str(root), "-o", "json=-", *args)
    doc = _json(failing)
    contributing = {k for k, v in doc["exit_axes"].items() if v}
    assert contributing == {"audit_gate", "contract_coverage", "operational_error"}
    assert failing.exit_code == doc["exit_code"] == 4
    markdown = _invoke("compare", "--no-baseline", str(root), *args).stdout
    for label in (
        "Audit-gate finding",
        "Contract coverage incomplete",
        "Member audit failed",
    ):
        assert label in markdown, label
    oneline = _invoke("compare", "--no-baseline", str(root), "-o", "oneline=-", *args)
    for label in (
        "audit-gate finding",
        "contract coverage incomplete",
        "member audit failed",
    ):
        assert label in oneline.stdout, label


# ---------------------------------------------------------------------------
# Formats, options, dry run.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["sarif", "junit", "html", "review", "terminal"])
def test_unsupported_formats_are_usage_errors(tmp_path: Path, fmt: str) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    result = _invoke("compare", "--no-baseline", str(root), "-o", f"{fmt}=-")
    assert result.exit_code == _EXIT_USAGE, result.output
    assert "directory or package of libraries" in result.output


def test_a_directory_export_is_a_usage_error(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json",))
    out = tmp_path / "out"
    result = _invoke("compare", "--no-baseline", str(root), "-o", f"json={out}/")
    assert result.exit_code == _EXIT_USAGE, result.output


def test_the_human_default_is_markdown_and_secondary_writes_share_one_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json", "libb.abi.json"))
    default = _invoke("compare", "--no-baseline", str(root))
    assert default.stdout.startswith("# ABI audit set: release (no baseline)")
    assert "## Member `liba.abi.json`" in default.stdout
    side = tmp_path / "audit.json"
    both = _invoke(
        "compare", "--no-baseline", str(root), "-o", "markdown=-", "-o", f"json={side}"
    )
    assert both.stdout == default.stdout
    assert json.loads(side.read_text())["audit_set"] is True


@pytest.mark.parametrize("flag", ["--select", "--select-required"])
def test_member_selection_is_refused_for_a_single_artifact(
    tmp_path: Path, flag: str
) -> None:
    single = tmp_path / "liba.abi.json"
    single.write_text(_case_text(_CASES[0]))
    result = _invoke("compare", "--no-baseline", str(single), flag, "liba")
    assert result.exit_code == _EXIT_USAGE
    assert flag in result.output


def test_variant_selects_the_candidate_variant_and_old_is_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "pkg"
    _stored_package(root)
    ok = _invoke(
        "compare",
        "--no-baseline",
        str(root),
        "--variant",
        "new=default",
        "-o",
        "json=-",
    )
    assert ok.exit_code == 0, ok.output
    unknown = _invoke("compare", "--no-baseline", str(root), "--variant", "nope")
    assert unknown.exit_code == _EXIT_USAGE and "nope" in unknown.output
    old = _invoke("compare", "--no-baseline", str(root), "--variant", "old=default")
    assert old.exit_code == _EXIT_USAGE and "--variant old=" in old.output


def test_dry_run_lists_members_without_auditing(tmp_path: Path) -> None:
    root = tmp_path / "release"
    _populate(root, ("liba.abi.json", "libb.abi.json"))
    result = _invoke(
        "compare",
        "--no-baseline",
        str(root),
        "--dry-run",
        "--select-required",
        "liba.abi.json",
        "--select-required",
        "libgone.so",
    )
    assert result.exit_code == 0, result.output
    assert "audit: liba.abi.json" in result.output
    assert "skip: libb.abi.json" in result.output
    assert "MISSING: libgone.so" in result.output
    assert "ABI audit set" not in result.output


def test_dry_run_of_an_archive_does_not_extract(tmp_path: Path) -> None:
    operand, _ = _operand(tmp_path, "tar.gz")
    result = _invoke("compare", "--no-baseline", str(operand), "--dry-run")
    assert result.exit_code == 0, result.output
    assert "preview skipped for a package operand" in result.output


# ---------------------------------------------------------------------------
# gcc-built ELF members (live extraction).
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc")
def test_gcc_built_members_equal_scalar_runs(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "lib"
    root.mkdir()
    for name, body in (
        ("liba.so", "int a(void){return 1;}"),
        ("libb.so", "int b(int x){return x;}"),
    ):
        src = tmp_path / f"{name}.c"
        src.write_text(body + "\n")
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(root / name), str(src)], check=True
        )
    doc = _json(_invoke("compare", "--no-baseline", str(root), "-o", "json=-"))
    for name, member in _by_name(doc).items():
        assert member["report"] == _scalar(root / name)[0], name

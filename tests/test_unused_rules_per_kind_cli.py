"""ADR-067 P1 addendum, end to end: an ELF-only comparison (gcc, no ``-g``, so
no DWARF on either side) through the CLI.

* A stale symbol-kind ``reclassify:`` rule under ``unused_rules: block`` reads
  ``unused`` and the run exits ``1`` although nothing else failed.
* A stale rule scoped to a DWARF-only kind reads ``not_evaluated`` and does not
  trip the gate, on its own or beside the symbol rule.

Needs gcc, so it carries the ``integration`` marker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    # The fixture is an ELF ``.so``: on Mach-O/PE the ELF detector that produces
    # ``symbol_size_changed`` never runs, so ``not_evaluated`` is the correct status there.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF-only fixture"),
]

_SOURCE = "int keep(void) { return 3; }\nint also(int a) { return a + 1; }\n"


def _build(tmp_path: Path) -> tuple[Path, Path]:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not found")
    old, new = tmp_path / "old.so", tmp_path / "new.so"
    for out in (old, new):
        src = out.with_suffix(".c")
        src.write_text(_SOURCE, encoding="utf-8")
        # No -g: the point is an ELF-only comparison with no DWARF.
        proc = subprocess.run(
            [gcc, "-shared", "-fPIC", "-o", str(out), str(src)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            pytest.skip(f"gcc failed: {proc.stderr[:200]}")
    return old, new


def _policy(tmp_path: Path, rules: str, *, block: bool) -> Path:
    head = "unused_rules: block\n" if block else ""
    path = tmp_path / "policy.yml"
    path.write_text(head + "reclassify:\n" + rules, encoding="utf-8")
    return path


_SYMBOL_RULE = (
    "  - symbol: gone_nope\n"
    "    kind: symbol_size_changed\n"
    "    to: ignore\n"
    "    reason: stale symbol waiver\n"
)
_DWARF_RULE = (
    "  - symbol: gone_nope2\n"
    "    kind: struct_size_changed\n"
    "    to: ignore\n"
    "    reason: stale layout waiver\n"
)


def _run(tmp_path: Path, old: Path, new: Path, policy: Path, *extra: str):
    out = tmp_path / "report.json"
    args = [
        "compare",
        str(old),
        str(new),
        "--policy",
        str(policy),
        *extra,
        "-o",
        f"json={out}",
    ]
    result = CliRunner().invoke(main, args)
    report = json.loads(out.read_text(encoding="utf-8"))
    return result.exit_code, report


def _rows_by_index(report: dict) -> dict[int, dict]:
    return {
        row["entry_index"]: row
        for row in report["disposition_audit"]["reclassifications"]
    }


def test_a_stale_symbol_rule_is_unused_and_blocks_an_otherwise_clean_run(
    tmp_path: Path,
) -> None:
    old, new = _build(tmp_path)
    policy = _policy(tmp_path, _SYMBOL_RULE, block=True)
    code, report = _run(tmp_path, old, new, policy)
    assert code == 1
    assert _rows_by_index(report)[0]["status"] == "unused"
    review = report["disposition_audit"]["unused_rules_review"]
    assert review["gate_contribution"] == 1
    assert len(review["unused"]) == 1


def test_a_stale_dwarf_only_rule_stays_not_evaluated_and_never_blocks(
    tmp_path: Path,
) -> None:
    old, new = _build(tmp_path)
    policy = _policy(tmp_path, _DWARF_RULE, block=True)
    code, report = _run(tmp_path, old, new, policy)
    assert code == 0
    assert _rows_by_index(report)[0]["status"] == "not_evaluated"
    assert report["disposition_audit"]["unused_rules_review"]["unused"] == []


def test_the_dwarf_rule_beside_the_symbol_rule_is_not_evaluated_and_the_gate_is_the_symbol_rule(
    tmp_path: Path,
) -> None:
    old, new = _build(tmp_path)
    policy = _policy(tmp_path, _SYMBOL_RULE + _DWARF_RULE, block=True)
    code, report = _run(tmp_path, old, new, policy)
    rows = _rows_by_index(report)
    assert code == 1
    assert rows[0]["status"] == "unused"
    assert rows[1]["status"] == "not_evaluated"
    unused = report["disposition_audit"]["unused_rules_review"]["unused"]
    assert [row["entry_index"] for row in unused] == [0]


def test_without_the_policy_key_the_stale_rule_is_reported_but_never_blocks(
    tmp_path: Path,
) -> None:
    """The gate is the policy key alone: ``allow`` (the default) lists the row
    as unused and leaves the exit code where it was."""
    old, new = _build(tmp_path)
    policy = _policy(tmp_path, _SYMBOL_RULE, block=False)
    code, report = _run(tmp_path, old, new, policy)
    assert code == 0
    assert _rows_by_index(report)[0]["status"] == "unused"


def test_the_removed_fail_on_unused_rules_flag_is_rejected(tmp_path: Path) -> None:
    old, new = _build(tmp_path)
    policy = _policy(tmp_path, _SYMBOL_RULE, block=False)
    out = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old),
            str(new),
            "--policy",
            str(policy),
            "--fail-on-unused-rules",
            "-o",
            f"json={out}",
        ],
    )
    assert result.exit_code != 0
    assert not out.exists()


# --- ADR-067 P1 addendum 2: not_evaluated is a warning, an observed kind is proof


def _policy_mode(tmp_path: Path, rules: str, mode: str) -> Path:
    path = tmp_path / "policy.yml"
    path.write_text(f"unused_rules: {mode}\nreclassify:\n" + rules, encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["warn", "block"])
def test_a_not_evaluated_rule_is_a_warning_naming_the_missing_evidence(
    tmp_path: Path, mode: str
) -> None:
    old, new = _build(tmp_path)
    policy = _policy_mode(tmp_path, _DWARF_RULE, mode)
    md = tmp_path / "report.md"
    code, report = _run(tmp_path, old, new, policy, "-o", f"markdown={md}")
    assert code == 0  # never gating, even under block
    review = report["disposition_audit"]["unused_rules_review"]
    assert review["gate_contribution"] == 0
    (entry,) = review["not_evaluated"]
    assert entry["entry_index"] == 0
    assert "dwarf" in entry["missing_evidence"]
    assert _rows_by_index(report)[0]["missing_evidence"] == entry["missing_evidence"]
    text = md.read_text(encoding="utf-8")
    assert "rules not evaluated" in text
    assert "dwarf" in text


def test_under_allow_the_not_evaluated_rule_stays_quiet(tmp_path: Path) -> None:
    old, new = _build(tmp_path)
    policy = _policy_mode(tmp_path, _DWARF_RULE, "allow")
    code, report = _run(tmp_path, old, new, policy)
    assert code == 0
    assert "not_evaluated" not in report["disposition_audit"]["unused_rules_review"]


def test_a_pipeline_kind_observed_in_the_run_makes_its_stale_rule_unused(
    tmp_path: Path,
) -> None:
    """``public_surface_shrank`` has no registered detector; before the
    observed-kind proof a stale rule on it could only read ``not_evaluated``."""
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not found")
    old, new = tmp_path / "old.so", tmp_path / "new.so"
    for out, src_text in ((old, _SOURCE), (new, "int keep(void) { return 3; }\n")):
        src = out.with_suffix(".c")
        src.write_text(src_text, encoding="utf-8")
        subprocess.run(
            [gcc, "-shared", "-fPIC", "-o", str(out), str(src)], check=True, timeout=60
        )
    rule = (
        "  - symbol: not_a_real_symbol\n"
        "    kind: public_surface_shrank\n"
        "    to: ignore\n"
        "    reason: stale\n"
    )
    _code, report = _run(tmp_path, old, new, _policy_mode(tmp_path, rule, "warn"))
    kinds = {c["kind"] for c in report["changes"]}
    assert "public_surface_shrank" in kinds
    assert _rows_by_index(report)[0]["status"] == "unused"

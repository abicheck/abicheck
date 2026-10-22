"""``--suppress`` is repeatable, and every file's rules reach the audit.

Regression: the option was single-valued, so ``--suppress a.yml --suppress
b.yml`` silently used only ``b.yml`` (AGENTS.md "Record before disposing": a
user's suppression document vanished without a message). The oracle in every
case is the union of the rules read *independently* from the YAML files
here, in command-line order, each at ``(file, in-file index)`` -- not
abicheck's own loader.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

_REMOVED = ("b", "c", "d")


def _fn(name: str) -> Function:
    return Function(
        name=f"api_{name}",
        mangled=f"_Z5api_{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1",
        functions=[_fn(n) for n in ("a", *_REMOVED)],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1", version="2", functions=[_fn("a")], from_headers=True
    )
    old_p, new_p = tmp_path / "old.json", tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _rule(sym: str, *, expires: str | None = None) -> str:
    text = f"  - symbol: _Z5api_{sym}v\n    reason: r-{sym}\n"
    return text + (f'    expires: "{expires}"\n' if expires else "")


def _write(tmp_path: Path, files: dict[str, list[str]]) -> list[Path]:
    paths = []
    for name, rules in files.items():
        p = tmp_path / name
        p.write_text("version: 1\nsuppressions:\n" + "".join(rules), encoding="utf-8")
        paths.append(p)
    return paths


def _oracle(paths: list[Path]) -> list[tuple[str, int, str]]:
    out = []
    for p in paths:
        for i, entry in enumerate(yaml.safe_load(p.read_text())["suppressions"]):
            out.append((p.name, i, entry["symbol"]))
    return out


def _run(*args: str) -> tuple[int, dict, str]:
    result = CliRunner().invoke(main, [*args, "-o", "json=-"])
    try:
        return result.exit_code, json.loads(result.stdout), result.output
    except json.JSONDecodeError:
        return result.exit_code, {}, result.output


def _flags(paths: list[Path]) -> list[str]:
    return [a for p in paths for a in ("--suppress", str(p))]


def _rows(report: dict) -> list[dict]:
    return report["disposition_audit"]["rules"]


_CASES = {
    "one_file": {"a.yml": [_rule("b")]},
    "two_files": {"a.yml": [_rule("b")], "b.yml": [_rule("c")]},
    "three_files": {
        "a.yml": [_rule("b")],
        "b.yml": [_rule("c")],
        "c.yml": [_rule("d"), _rule("zz")],
    },
    "duplicate_rule": {"a.yml": [_rule("b"), _rule("c")], "dup.yml": [_rule("b")]},
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_every_files_rules_are_audited_with_their_own_file(
    tmp_path: Path, case: str
) -> None:
    old, new = _pair(tmp_path)
    paths = _write(tmp_path, _CASES[case])
    oracle = _oracle(paths)

    code, report, output = _run("compare", str(old), str(new), *_flags(paths))
    rows = _rows(report)
    assert [(Path(r["source_file"]).name, r["entry_index"]) for r in rows] == [
        (f, i) for f, i, _ in oracle
    ], output
    # Every removed function some file names is suppressed; any one left
    # over keeps the run breaking.
    named = {sym for _, _, sym in oracle}
    covered = all(f"_Z5api_{n}v" in named for n in _REMOVED)
    assert (code == 0) == covered, output
    # First-match wins: an earlier file's rule disposes; a later duplicate is
    # still reported, never dropped, and never silently read as "active".
    first_owner: dict[str, tuple[str, int]] = {}
    for f, i, sym in oracle:
        first_owner.setdefault(sym, (f, i))
    for row, (f, i, sym) in zip(rows, oracle, strict=True):
        if sym == "_Z5api_zzv":
            assert row["status"] == "unused"
        elif first_owner[sym] == (f, i):
            assert row["status"] == "active", row
        else:
            assert row["status"] != "active", row


@pytest.mark.parametrize("case", sorted(_CASES))
def test_no_baseline_audits_every_file(tmp_path: Path, case: str) -> None:
    snapshot = example_catalog.case_dir("case143_audit_accidental_export")
    paths = _write(tmp_path, _CASES[case])
    _, report, output = _run(
        "compare", "--no-baseline", str(snapshot / "snapshot.abi.json"), *_flags(paths)
    )
    assert [(Path(r["source_file"]).name, r["entry_index"]) for r in _rows(report)] == [
        (f, i) for f, i, _ in _oracle(paths)
    ], output


def test_unused_rules_block_names_the_unused_file_only(tmp_path: Path) -> None:
    old, new = _pair(tmp_path)
    paths = _write(
        tmp_path,
        {
            "shared.yml": [_rule("b"), _rule("c"), _rule("d")],
            "variant.yml": [_rule("zz")],
        },
    )
    policy = tmp_path / "policy.yml"
    policy.write_text("unused_rules: block\n", encoding="utf-8")
    code, report, output = _run(
        "compare", str(old), str(new), "--policy", str(policy), *_flags(paths)
    )
    assert code == 1, output
    status = {
        (Path(r["source_file"]).name, r["entry_index"]): r["status"]
        for r in _rows(report)
    }
    assert status == {
        ("shared.yml", 0): "active",
        ("shared.yml", 1): "active",
        ("shared.yml", 2): "active",
        ("variant.yml", 0): "unused",
    }
    # Dropping the unused variant file clears the gate: the shared file's
    # rules are judged on their own, not blamed for another file's.
    code, _, output = _run(
        "compare", str(old), str(new), "--policy", str(policy), *_flags(paths[:1])
    )
    assert code == 0, output


@pytest.mark.parametrize("no_baseline", [False, True])
def test_strict_expiry_names_the_second_file_and_its_own_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_baseline: bool
) -> None:
    old, new = _pair(tmp_path)
    paths = _write(
        tmp_path,
        {
            "shared.yml": [_rule("b")],
            "variant.yml": [_rule("c"), _rule("d", expires="2020-01-01")],
        },
    )
    (tmp_path / ".abicheck.yml").write_text("suppression:\n  strict: true\n")
    monkeypatch.chdir(tmp_path)
    operands = ["--no-baseline", str(new)] if no_baseline else [str(old), str(new)]
    result = CliRunner().invoke(main, ["compare", *operands, *_flags(paths)])
    assert result.exit_code not in (0, 4), result.output
    assert f"found in {paths[1]}" in result.output
    assert str(paths[0]) not in result.output
    assert 'Rule 2: symbol="_Z5api_dv"' in result.output

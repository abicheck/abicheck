"""ADR-067 P2: the optional ``id:`` key on reclassify and suppression entries.

``id`` is accepted in both grammars, must be a non-empty string, and must be
unique within its document (a repeat is a hard error). It becomes the audit's
``rule_id``; without one, ``rule_id`` is the rule's canonical identity. ``label``
and ``reason`` are never the identifier -- they are surfaced as their own fields,
so two rules that share a label or a reason stay two rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.errors import PolicyError
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.suppression.rules import SuppressionList
from abicheck.policy_file import PolicyFile
from abicheck.report.disposition_audit import compute_disposition_audit

_MANGLED = "_ZN2ns5gone0Ev"


def _snapshots() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(library="libid", version="1.0")
    new = AbiSnapshot(library="libid", version="2.0")
    old.declarations.functions.append(
        Function(
            name="ns::gone0",
            mangled=_MANGLED,
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    return old, new


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_suppression_id_becomes_the_audit_rule_id(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "suppress.yml",
        "version: 1\nsuppressions:\n"
        "  - id: waive-gone0\n    symbol: " + _MANGLED + "\n"
        "    label: grp\n    reason: shared prose\n",
    )
    old, new = _snapshots()
    result = compare(old, new, SuppressionList.load(path))
    (row,) = compute_disposition_audit(result).rules
    assert row.rule_id == "waive-gone0"
    assert row.label == "grp"
    assert row.reason == "shared prose"
    assert row.matched_count == 1


def test_a_reclassify_id_becomes_the_audit_rule_id(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "policy.yml",
        "reclassify:\n"
        "  - id: demote-gone0\n    kind: func_removed\n    symbol: " + _MANGLED + "\n"
        "    to: ignore\n    label: grp\n    reason: shared prose\n",
    )
    old, new = _snapshots()
    result = compare(old, new, policy_file=PolicyFile.load(path))
    (row,) = compute_disposition_audit(result).reclassifications
    assert row.rule_id == "demote-gone0"
    assert row.label == "grp"
    assert row.reason == "shared prose"
    assert row.matched_count == 1


def test_a_rule_without_an_id_is_identified_by_its_canonical_identity(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "policy.yml",
        "reclassify:\n  - kind: func_removed\n    symbol: " + _MANGLED + "\n"
        "    to: ignore\n    label: grp\n",
    )
    old, new = _snapshots()
    result = compare(old, new, policy_file=PolicyFile.load(path))
    (row,) = compute_disposition_audit(result).reclassifications
    assert "symbol=" + repr(_MANGLED) in row.rule_id
    assert row.rule_id != "grp", "a label is never the identifier"


def test_a_duplicate_suppression_id_is_a_hard_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "s.yml",
        "version: 1\nsuppressions:\n"
        "  - id: dup\n    symbol: " + _MANGLED + "\n    reason: a\n"
        "  - id: dup\n    symbol: other\n    reason: b\n",
    )
    with pytest.raises(PolicyError, match="duplicate id 'dup'"):
        SuppressionList.load(path)


def test_a_duplicate_reclassify_id_is_a_hard_error(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "p.yml",
        "reclassify:\n"
        "  - id: dup\n    kind: func_removed\n    symbol: "
        + _MANGLED
        + "\n    to: ignore\n"
        "  - id: dup\n    kind: func_removed\n    symbol: other\n    to: warn\n",
    )
    with pytest.raises(PolicyError, match="duplicate id 'dup'"):
        PolicyFile.load(path)


@pytest.mark.parametrize("bad", ["", "   ", 7, ["x"]])
def test_an_id_must_be_a_non_empty_string(tmp_path: Path, bad: object) -> None:
    text = f"reclassify:\n  - id: {bad!r}\n    kind: func_removed\n    symbol: x\n    to: ignore\n"
    path = _write(tmp_path, "p.yml", text.replace("'", '"'))
    with pytest.raises(PolicyError, match=r"\.id in .*non-empty string"):
        PolicyFile.load(path)


def test_identical_unlabelled_rules_are_two_rows_with_one_identity(
    tmp_path: Path,
) -> None:
    """Two byte-identical entries are two configured rules (P1), even though
    their canonical identity is the same string; the row keeps their position."""
    entry = "  - kind: func_removed\n    symbol: " + _MANGLED + "\n    to: ignore\n"
    path = _write(tmp_path, "p.yml", "reclassify:\n" + entry + entry)
    old, new = _snapshots()
    result = compare(old, new, policy_file=PolicyFile.load(path))
    rows = compute_disposition_audit(result).reclassifications
    assert [row.entry_index for row in rows] == [0, 1]
    assert rows[0].rule_id == rows[1].rule_id
    assert [row.matched_count for row in rows] == [1, 0]


def test_the_verdict_a_reclassify_id_names_is_unchanged_by_the_id(
    tmp_path: Path,
) -> None:
    """``id`` is identification, never matching: the same rule with and without
    an id must move the same verdict."""
    plain = (
        "reclassify:\n  - kind: func_removed\n    symbol: "
        + _MANGLED
        + "\n    to: ignore\n"
    )
    named = plain.replace("  - kind", "  - id: x\n    kind")
    old, new = _snapshots()
    a = compare(old, new, policy_file=PolicyFile.load(_write(tmp_path, "a.yml", plain)))
    b = compare(old, new, policy_file=PolicyFile.load(_write(tmp_path, "b.yml", named)))
    assert a.verdict == b.verdict
    assert [c.kind for c in a.changes] == [c.kind for c in b.changes]


#: What each key that either grammar accepts means to the audit (P2). A key
#: accepted by a grammar but absent here, or classified here but no longer
#: accepted, fails the test: a new key cannot reach the audit unclassified.
_KEY_CLASSES: dict[str, str] = {
    "id": "identity",
    "label": "grouping",
    "reason": "prose",
    "expires": "expiry",
    "to": "target",
    "kind": "selector",
    "change_kind": "selector",
    "symbol": "selector",
    "symbol_pattern": "selector",
    "type_pattern": "selector",
    "member_name": "selector",
    "namespace": "selector",
    "entity_namespace": "selector",
    "cause_namespace": "selector",
    "source_location": "selector",
    "binding": "selector",
    "finding_id": "selector",
    "reachability": "selector",
    "allow_public_break": "gate",
    "allow_unknown_reachability": "gate",
}


def test_every_accepted_key_is_classified_for_the_audit() -> None:
    from abicheck.policy.reclassify import RECLASSIFY_KNOWN_KEYS
    from abicheck.policy.suppression.rules import _KNOWN_ENTRY_KEYS

    accepted = set(RECLASSIFY_KNOWN_KEYS) | set(_KNOWN_ENTRY_KEYS)
    assert accepted - set(_KEY_CLASSES) == set()
    assert set(_KEY_CLASSES) - accepted == set()


def test_only_id_names_the_rule_in_the_audit() -> None:
    assert {key for key, cls in _KEY_CLASSES.items() if cls == "identity"} == {"id"}


def test_an_id_on_a_policy_file_is_accepted_by_the_cli(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    old, new = _snapshots()
    save_snapshot(old, tmp_path / "old.json")
    save_snapshot(new, tmp_path / "new.json")
    policy = _write(
        tmp_path,
        "policy.yml",
        "reclassify:\n"
        "  - id: demote-gone0\n    kind: func_removed\n    symbol: " + _MANGLED + "\n"
        "    to: ignore\n",
    )
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "--policy",
            str(policy),
        ],
    )
    # Exit 64 was the pre-P2 answer ("unknown key 'id'"); the rule is now valid.
    assert res.exit_code == 0, res.output

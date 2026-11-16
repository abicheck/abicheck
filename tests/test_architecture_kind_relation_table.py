"""The ``kind-relation-table`` rule sees relation tables at every nesting level.

A hand-written ChangeKind-to-ChangeKind dict is a kind relation (ADR-093 D5),
wherever it is written. The rule must flag it at module level, inside a
function, and inside a conditional block, and must still honour the one
baselined module-level table.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.check_architecture import (
    KIND_RELATION_TABLE_BASELINE,
    Finding,
    _check_kind_relation_tables,
)

_TABLE = "{ChangeKind.STRUCT_SIZE_CHANGED: ChangeKind.TYPE_SIZE_CHANGED}"


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


def _rules(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    _check_kind_relation_tables(root, findings)
    return findings


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(f"partners = {_TABLE}\n", id="module_level"),
        pytest.param(
            f"def build():\n    partners = {_TABLE}\n    return partners\n",
            id="function_local",
        ),
        pytest.param(
            f"import os\nif os.name:\n    partners = {_TABLE}\n",
            id="conditional_block",
        ),
        pytest.param(
            f"class Holder:\n    partners: dict = {_TABLE}\n",
            id="class_body_annotated",
        ),
    ],
)
def test_relation_table_is_flagged_at_every_nesting_level(
    tmp_path: Path, source: str
) -> None:
    _write(
        tmp_path, "abicheck/compare/somewhere.py", f"from x import ChangeKind\n{source}"
    )
    findings = _rules(tmp_path)
    assert [f.rule for f in findings] == ["kind-relation-table"]
    assert "abicheck/compare/somewhere.py:partners:" in findings[0].message


def test_relation_table_inside_allowed_catalog_is_not_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "abicheck/model/change_catalog/types_9.py",
        f"from x import ChangeKind\ndef f():\n    partners = {_TABLE}\n",
    )
    assert _rules(tmp_path) == []


def test_baselined_module_level_table_is_not_flagged(tmp_path: Path) -> None:
    key = next(iter(KIND_RELATION_TABLE_BASELINE))
    relative, name = key.split(":", 1)
    _write(tmp_path, relative, f"from x import ChangeKind\n{name} = {_TABLE}\n")
    assert _rules(tmp_path) == []


def test_non_kind_dict_is_not_flagged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "abicheck/compare/plain.py",
        "def f():\n    return {'a': 'b'}\n",
    )
    assert _rules(tmp_path) == []

"""Conformance tests linking the Lean model of TYPE_FIELD_OFFSET_CHANGED
(``formal/lean/AbiLean/FieldOffset.lean``) to abicheck's implementation.

These are *conformance* tests, not a formal proof of the Python code:

* the rule's truth table is checked exhaustively over a small domain against
  the table the Lean model itself computed (``formal/lean/fires_truth_table.json``);
* the castxml extractor must not turn an absent ``offset`` attribute into 0
  (the model's negative check N1: "unknown treated as 0" makes the rule fire
  on absent evidence);
* the C fixtures in ``formal/fixtures/`` are compiled and run (integration):
  an old consumer reading the moved field observes a different value, the
  unaffected consumers do not, abicheck reports the offset change for the
  exposed record and nothing for the opaque one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from abicheck.diff_types import _diff_type_field_pair
from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.records import expand_anonymous_field
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.entities import TypeField

ROOT = Path(__file__).resolve().parent.parent
FORMAL = ROOT / "formal"
_TABLE = json.loads(
    (FORMAL / "lean" / "fires_truth_table.json").read_text(encoding="utf-8")
)["rows"]


@pytest.mark.parametrize(("old", "new", "lean_fires"), _TABLE)
def test_rule_matches_lean_truth_table(
    old: int | None, new: int | None, lean_fires: bool
) -> None:
    changes = _diff_type_field_pair(
        "S",
        "f",
        TypeField(name="f", type="int", offset_bits=old),
        TypeField(name="f", type="int", offset_bits=new),
    )
    offset = [c for c in changes if c.kind == ChangeKind.TYPE_FIELD_OFFSET_CHANGED]
    assert bool(offset) is lean_fires
    if lean_fires:
        assert (offset[0].old_value, offset[0].new_value) == (str(old), str(new))


def test_truth_table_is_the_full_domain() -> None:
    domain = [None, 0, 32, 64]
    assert sorted(((o, n) for o, n, _ in _TABLE), key=str) == sorted(
        ((o, n) for o in domain for n in domain), key=str
    )


def _anon_xml(
    outer_offset: str | None, inner_offset: str | None
) -> tuple[CastxmlParserContext, ET.Element]:
    def attr(name: str, value: str | None) -> str:
        return "" if value is None else f' {name}="{value}"'

    xml = (
        "<GCC_XML>"
        '<FundamentalType id="_int" name="int" size="32" align="32"/>'
        '<Union id="_u" name="" members="_m"/>'
        f'<Field id="_m" name="a" type="_int" context="_u"{attr("offset", inner_offset)}/>'
        f'<Field id="_anon" name="" type="_u" context="_s"{attr("offset", outer_offset)}/>'
        "</GCC_XML>"
    )
    root = ET.fromstring(xml)  # nosec B314 - fixture XML built in this test
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    anon = root.find("Field[@id='_anon']")
    assert anon is not None
    return ctx, anon


@pytest.mark.parametrize(
    ("outer", "inner", "expected"),
    [
        ("64", "0", 64),
        ("0", "32", 32),
        ("32", "32", 64),
        (None, "0", None),
        ("64", None, None),
        (None, None, None),
    ],
)
def test_anonymous_member_unknown_offset_stays_unknown(
    outer: str | None, inner: str | None, expected: int | None
) -> None:
    ctx, el = _anon_xml(outer, inner)
    (field,) = expand_anonymous_field(ctx, el)
    assert field.name == "a"
    assert field.offset_bits == expected


def test_unknown_anonymous_offset_cannot_fire_rule() -> None:
    """Old side lacks the offset attribute: whatever the new side reports, the
    old offset is unknown and the rule must stay silent (Lean N1: before the
    fix the absent offset became 0 and the rule fired against 32)."""
    ctx_old, el_old = _anon_xml(None, None)
    ctx_new, el_new = _anon_xml("0", "0")
    (f_old,) = expand_anonymous_field(ctx_old, el_old)
    (f_new,) = expand_anonymous_field(ctx_new, el_new)
    f_new_moved = TypeField(name="a", type="int", offset_bits=32)
    assert not [
        c
        for c in _diff_type_field_pair("S", "a", f_old, f_new_moved)
        if c.kind == ChangeKind.TYPE_FIELD_OFFSET_CHANGED
    ]
    assert f_new.offset_bits == 0


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux ELF experiment (.so, -soname, LD_LIBRARY_PATH)",
)
def test_fixture_experiment(tmp_path: Path) -> None:
    from tests._workflow_exec import require_bash

    require_bash()
    from tests._workflow_exec import bash_executable

    bash = bash_executable()
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml required")
    proc = subprocess.run(  # nosec B603 - fixed in-repo script
        [bash, str(FORMAL / "fixtures" / "run_experiment.sh"), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = {
        line.split()[0]: line.split()[1:]
        for line in proc.stdout.splitlines()
        if line[:1].islower()
    }
    assert rows["reader_b"] == ["42", "7"]  # contract of this consumer violated
    assert rows["reader_a"] == ["1", "1"]  # reads an unmoved field
    assert rows["accessor_user"] == ["42", "42"]  # never dereferences
    assert rows["opaque_user"] == ["42", "42"]
    exposed = json.loads((tmp_path / "exposed.json").read_text(encoding="utf-8"))
    offs = [c for c in exposed["changes"] if c["kind"] == "type_field_offset_changed"]
    assert [
        (c["symbol"], c["old_value"], c["new_value"], c["severity"]) for c in offs
    ] == [("rec", "32", "64", "breaking")]
    assert "rec::b" in offs[0]["description"]
    assert exposed["verdict"] == "BREAKING"
    opaque = json.loads((tmp_path / "opaque.json").read_text(encoding="utf-8"))
    assert not [
        c for c in opaque["changes"] if c["kind"] == "type_field_offset_changed"
    ]
    assert opaque["verdict"] in ("NO_CHANGE", "COMPATIBLE")

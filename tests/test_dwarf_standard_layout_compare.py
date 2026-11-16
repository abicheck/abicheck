"""End-to-end: a record edit that loses standard layout is reported from DWARF.

Each case builds two shared libraries with g++ -g (DWARF only, no headers in the
comparison), runs the public ``compare`` command, and checks the kinds and the
verdict against values written from the edit's ABI meaning, not from the
checker's output:

* ``mixed_access_added``: a protected member joins public ones. No byte moves,
  so the record only loses standard layout and its access changes:
  COMPATIBLE_WITH_RISK.
* ``virtual_added``: a virtual method gives the record a vptr. Its size and
  offsets move: BREAKING.
* ``base_with_data_added``: the record now derives from a base that has data
  members, so the data is no longer in one class of the hierarchy and the
  layout moves: BREAKING.
* ``reference_member_added``: a data member becomes a reference, which breaks
  the record's standard layout and changes its member type: BREAKING.
* ``unchanged_control``: identical sources. Nothing is lost, so no standard
  layout kind appears.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command
from tests._elf_capability import elf_capability, skip_reason

pytestmark = pytest.mark.integration

#: The oracles read ELF DWARF (readelf, pyelftools) and build ELF shared objects.

_TAKE = 'extern "C" int take_s(S s) { return 0; }\n'
_USE = 'extern "C" int use_s(S* s) { return s != nullptr; }\n'

#: name -> (old declarations, new declarations, extra definitions, expected kinds
#: (all must appear), absent kinds (none may appear), expected verdict)
_CASES: dict[str, tuple[str, str, str, frozenset[str], frozenset[str], str]] = {
    "mixed_access_added": (
        "struct S { int a; int b; };",
        "struct S { int a; protected: int b; };",
        "",
        frozenset({"standard_layout_lost", "field_access_changed"}),
        frozenset(),
        "COMPATIBLE_WITH_RISK",
    ),
    "virtual_added": (
        "struct S { int a; int b; };",
        "struct S { virtual void g() {} int a; int b; };",
        'extern "C" S make_s() { return S(); }\n',
        frozenset({"standard_layout_lost", "vptr_introduced"}),
        frozenset(),
        "BREAKING",
    ),
    "base_with_data_added": (
        "struct S { int a; int b; };",
        "struct B { int x; };\nstruct S : B { int a; int b; };",
        "",
        frozenset({"standard_layout_lost", "type_size_changed"}),
        frozenset(),
        "BREAKING",
    ),
    "reference_member_added": (
        "struct S { void* p; };",
        "struct S { int& p; };",
        "",
        frozenset({"standard_layout_lost", "type_field_type_changed"}),
        frozenset(),
        "BREAKING",
    ),
    "unchanged_control": (
        "struct S { int a; int b; };",
        "struct S { int a; int b; };",
        "",
        frozenset(),
        frozenset({"standard_layout_lost"}),
        "NO_CHANGE",
    ),
}


def _build(
    prefix: list[str], tmp: Path, name: str, side: str, decls: str, extra: str
) -> Path:
    src = tmp / f"{name}_{side}.cpp"
    out = tmp / f"lib{name}_{side}.so"
    src.write_text(
        f"{decls}\n{_USE}{_TAKE}{extra}",
        encoding="utf-8",
    )
    subprocess.run(
        [
            *prefix,
            "-std=c++17",
            "-g",
            "-O0",
            "-shared",
            "-fPIC",
            str(src),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out


def _compare(old: Path, new: Path, report: Path) -> dict:
    subprocess.run(
        abicheck_command("compare", str(old), str(new), "-o", f"json={report}"),
        check=False,
        capture_output=True,
        timeout=600,
    )
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(_CASES))
def test_record_edit_is_reported_from_dwarf(name: str, tmp_path: Path) -> None:
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    old_decls, new_decls, extra, expected, absent, verdict = _CASES[name]
    old = _build(prefix, tmp_path, name, "old", old_decls, extra)
    new = _build(prefix, tmp_path, name, "new", new_decls, extra)
    report = _compare(old, new, tmp_path / "report.json")
    kinds = {c["kind"] for c in report.get("changes", [])}
    assert expected <= kinds, (sorted(kinds), sorted(expected))
    assert not (absent & kinds), (sorted(kinds), sorted(absent))
    assert report["verdict"] == verdict, (report["verdict"], sorted(kinds))

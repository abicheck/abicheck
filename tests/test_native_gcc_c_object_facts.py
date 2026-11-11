"""The native GCC header backend spells C object types and record completeness as castxml does.

Bug class: a header backend records a fact in a form the detectors cannot
read. The GCC C front end has no type printer, and ``print_generic_expr``
spells ``_Atomic int`` as ``atomic int``, a flexible array member as
``char[0:]`` and an unknown bound as ``[<unknown>]``; the plugin read
``const`` off an array type (whose qualifiers sit on the element) and
recorded no completeness. So ``atomic_qualifier_changed``,
``flexible_array_member_changed``, ``symbol_size_changed_const_object`` and
``type_became_opaque`` were all missed under the gcc backend (edit corpus
``field_became_atomic``, ``flexible_array_member``,
``const_string_object_grew``, ``record_became_opaque``).

Oracle: castxml's record of the same header (field type spellings,
``Variable.type``/``is_const``, ``RecordType.is_opaque``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.model.snapshot import AbiSnapshot
from tests.test_native_gcc_workflow import native_plugins  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

# Each header is paired with the definitions its library needs.
_CASES = {
    "atomic_fields": (
        "struct S { int n; _Atomic int a; _Atomic(long) b; _Atomic unsigned char c; };",
        "",
    ),
    "flexible_and_zero_length": (
        "struct F { int n; char data[]; };\nstruct Z { int n; int d[0]; };",
        "",
    ),
    "fixed_arrays": ("struct A { int m[3]; double one[1]; };", ""),
    "opaque_and_complete": (
        "struct Opaque;\nunion OpaqueU;\nstruct Done { int v; };",
        "",
    ),
    "const_objects": (
        "extern const char banner[];\nextern const char fixed[6];\n"
        "extern const int nums[];\nextern char plain[];\nextern const double pi;",
        'const char banner[] = "hi";\nconst char fixed[6] = "x";\n'
        'const int nums[] = {1};\nchar plain[] = "p";\nconst double pi = 3.0;',
    ),
}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("gcc", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


def _lib(root: Path, name: str, header: str, source: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    # Name every record in an exported signature so each backend keeps it.
    tags = [
        w
        for w in header.replace("{", " ").replace(";", " ").split()
        if w.isidentifier()
    ]
    records = [f"{k} {n}" for k, n in zip(tags, tags[1:]) if k in ("struct", "union")]
    params = (
        ", ".join(f"{r} *p{i}" for i, r in enumerate(dict.fromkeys(records))) or "void"
    )
    (d / "a.h").write_text(f"{header}\nint use({params});\n")
    (d / "a.c").write_text(
        f'#include "a.h"\n{source}\nint use({params}) {{ return 0; }}\n'
    )
    so = d / "liba.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(d / "a.c")], check=True
    )
    return so, d / "a.h"


def _dump(so: Path, hdr: Path, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="cc",
        lang="c",
        header_backend=backend,
    )


def _facts(snap: AbiSnapshot) -> dict[str, object]:
    out: dict[str, object] = {}
    for t in snap.declarations.types:
        if t.name in ("S", "F", "Z", "A", "Opaque", "OpaqueU", "Done"):
            out[t.name] = (bool(t.is_opaque), [(f.name, f.type) for f in t.fields])
    for v in snap.declarations.variables:
        out[v.name] = (v.type, bool(v.is_const))
    return out


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_c_object_facts_match_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    so, hdr = _lib(tmp_path, case, *_CASES[case])
    want = _facts(_dump(so, hdr, "castxml"))
    assert want, "the oracle sees the declarations"
    assert _facts(_dump(so, hdr, "gcc")) == want


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        (
            "struct R { int a; int b; };",
            "struct R { int a; _Atomic int b; };",
            "atomic_qualifier_changed",
        ),
        (
            "struct R { int n; int d[1]; };",
            "struct R { int n; int d[]; };",
            "flexible_array_member_changed",
        ),
        ("struct R { char a; int b; };", "struct R;", "type_became_opaque"),
    ],
)
def test_gcc_reports_c_object_transitions(
    old: str, new: str, kind: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snaps = [
        _dump(*_lib(tmp_path, f"v{i}", h, ""), "gcc") for i, h in enumerate((old, new))
    ]
    assert kind in {c.kind.value for c in compare(*snaps).changes}


def test_grown_unbounded_const_object_is_a_const_object_change_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    _require()
    hdr = "extern const char banner[];"
    snaps = [
        _dump(*_lib(tmp_path, f"v{i}", hdr, f'const char banner[] = "{text}";'), "gcc")
        for i, text in enumerate(("hi", "much-longer"))
    ]
    assert "symbol_size_changed_const_object" in {
        c.kind.value for c in compare(*snaps).changes
    }


def test_gcc_spells_a_nested_array_in_declaration_order(
    tmp_path: Path, gcc_env: None
) -> None:
    """C spells ``char grid[2][4]``'s type ``char[2][4]`` (castxml 0.7 writes
    ``char[4][2]``, so the language rule is the oracle here)."""
    _require()
    so, hdr = _lib(tmp_path, "nested", "struct A { char grid[2][4]; };", "")
    assert _facts(_dump(so, hdr, "gcc"))["A"] == (False, [("grid", "char[2][4]")])


def test_unchanged_c_objects_report_nothing_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: atomic, flexible and opaque declarations on both sides."""
    _require()
    hdr = "struct S { _Atomic int a; char d[]; };\nstruct O;"
    snaps = [_dump(*_lib(tmp_path, f"v{i}", hdr, ""), "gcc") for i in (1, 2)]
    assert not compare(*snaps).changes

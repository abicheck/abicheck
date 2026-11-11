"""The native GCC header backend records record, enum and field deprecation as castxml does.

Bug class: a header backend leaves a fact family empty that the reference
backend records. The GCC plugin emitted ``deprecated`` only for function
and variable declarations, so ``[[deprecated]]``/``__attribute__((deprecated))``
on a struct, an enum or a data member was never recorded and
``type_deprecated_added``/``enum_deprecated_added``/``field_deprecated_*``
read NO_CHANGE under the gcc backend (edit corpus ``api_deprecated``,
``api_undeprecated``, ``field_deprecated_removed``).

Oracle: castxml's deprecation marker for the same header (its message, ""
for a bare attribute, ``None`` when absent).
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

# (header, language): each declares struct R { int f; }; enum E; and uses them.
_CASES = {
    "c_attribute_bare": (
        "struct __attribute__((deprecated)) R { int f; int __attribute__((deprecated)) g; };\n"
        "enum __attribute__((deprecated)) E { E1 };",
        "c",
    ),
    "c_attribute_message": (
        'struct __attribute__((deprecated("r"))) R { int f; int __attribute__((deprecated("g"))) g; };\n'
        'enum __attribute__((deprecated("e"))) E { E1 };',
        "c",
    ),
    "c_none": ("struct R { int f; int g; };\nenum E { E1 };", "c"),
    "cxx_std_message": (
        'struct [[deprecated("r")]] R { int f; [[deprecated("g")]] int g; };\n'
        'enum class [[deprecated("e")]] E { E1 };',
        "c++",
    ),
    "cxx_std_bare_ns": (
        "namespace n { struct [[deprecated]] R { int f; [[deprecated]] int g; };\n"
        "enum [[deprecated]] E { E1 }; }\nusing n::R; using n::E;",
        "c++",
    ),
    "cxx_gnu_on_field_only": (
        "struct R { int f; __attribute__((deprecated)) int g; };\nenum E { E1 };",
        "c++",
    ),
}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("gcc", "g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


def _lib(root: Path, name: str, header: str, lang: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    hdr = d / "a.h"
    # Use both types in an exported function so every backend keeps them.
    rtag, etag = ("struct R", "enum E") if lang == "c" else ("R", "E")
    hdr.write_text(
        '#pragma GCC diagnostic ignored "-Wdeprecated-declarations"\n'
        f"{header}\nint use({rtag} *r, {etag} e);\n"
    )
    ext, cc = ("c", "gcc") if lang == "c" else ("cpp", "g++")
    src = d / f"a.{ext}"
    src.write_text(
        f'#include "a.h"\nint use({rtag} *r, {etag} e) {{ return (int)e; }}\n'
    )
    so = d / "liba.so"
    subprocess.run([cc, "-shared", "-fPIC", "-o", str(so), str(src)], check=True)
    return so, hdr


def _dump(so: Path, hdr: Path, lang: str, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="cc" if lang == "c" else "c++",
        lang=lang,
        header_backend=backend,
    )


def _markers(snap: AbiSnapshot) -> dict[str, str | None]:
    rec = next(t for t in snap.declarations.types if t.name.split("::")[-1] == "R")
    enum = next(e for e in snap.declarations.enums if e.name.split("::")[-1] == "E")
    fields = {f.name: f.deprecated for f in rec.fields}
    return {
        "R": rec.deprecated,
        "E": enum.deprecated,
        "R::f": fields["f"],
        "R::g": fields["g"],
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_deprecation_markers_match_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    header, lang = _CASES[case]
    so, hdr = _lib(tmp_path, case, header, lang)
    want = _markers(_dump(so, hdr, lang, "castxml"))
    assert _markers(_dump(so, hdr, lang, "gcc")) == want


@pytest.mark.parametrize(
    ("old", "new", "kinds"),
    [
        (
            "c_none",
            "c_attribute_bare",
            {
                "type_deprecated_added",
                "enum_deprecated_added",
                "field_deprecated_added",
            },
        ),
        (
            "c_attribute_message",
            "c_none",
            {
                "type_deprecated_removed",
                "enum_deprecated_removed",
                "field_deprecated_removed",
            },
        ),
    ],
)
def test_gcc_reports_deprecation_transitions(
    old: str, new: str, kinds: set[str], tmp_path: Path, gcc_env: None
) -> None:
    _require()
    snaps = [
        _dump(*_lib(tmp_path, c, _CASES[c][0], "c"), "c", "gcc") for c in (old, new)
    ]
    assert kinds <= {c.kind.value for c in compare(*snaps).changes}


def test_unchanged_deprecation_reports_nothing_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: the same deprecated declarations on both sides."""
    _require()
    header, lang = _CASES["c_attribute_message"]
    snaps = [_dump(*_lib(tmp_path, f"v{i}", header, lang), lang, "gcc") for i in (1, 2)]
    assert not {
        c.kind.value for c in compare(*snaps).changes if "deprecated" in c.kind.value
    }

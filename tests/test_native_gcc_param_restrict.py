"""The native GCC header backend records a parameter's top-level restrict as castxml does.

Bug class: a header backend leaves a fact empty that the reference backend
records. The GCC plugin read ``restrict`` from the function type's
parameter list, but C++ drops a parameter's top-level cv/restrict
qualifiers from the function type ([dcl.fct]/5), so every C++
``T* __restrict p`` read unrestricted and ``param_restrict_changed`` was
missed under the gcc backend (edit corpus ``param_restrict_removed``).
The ``PARM_DECL`` keeps the declared qualifier.

Oracle: castxml's ``Param.is_restrict`` for the same header.
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

# (declaration of fill, language)
_CASES = {
    "cxx_first": ("int fill(int* __restrict p, int n);", "c++"),
    "cxx_second": ("int fill(int n, char* __restrict__ p);", "c++"),
    "cxx_both": ("int fill(int* __restrict a, const int* __restrict b);", "c++"),
    "cxx_pointee_only": ("int fill(int* __restrict* pp, int n);", "c++"),
    "cxx_none": ("int fill(int* p, int n);", "c++"),
    "c_first": ("int fill(int* restrict p, int n);", "c"),
    "c_none": ("int fill(int* p, int n);", "c"),
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


def _lib(root: Path, name: str, decl: str, lang: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    (d / "a.h").write_text(decl + "\n")
    ext, cc = ("c", "gcc") if lang == "c" else ("cpp", "g++")
    src = d / f"a.{ext}"
    src.write_text(f'#include "a.h"\n{decl[:-1]} {{ return 0; }}\n')
    so = d / "liba.so"
    subprocess.run([cc, "-shared", "-fPIC", "-o", str(so), str(src)], check=True)
    return so, d / "a.h"


def _dump(so: Path, hdr: Path, lang: str, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="cc" if lang == "c" else "c++",
        lang=lang,
        header_backend=backend,
    )


def _restrict(snap: AbiSnapshot) -> list[bool]:
    (fn,) = [f for f in snap.declarations.functions if f.name.endswith("fill")]
    return [bool(p.is_restrict) for p in fn.params]


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_param_restrict_matches_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    decl, lang = _CASES[case]
    so, hdr = _lib(tmp_path, case, decl, lang)
    assert _restrict(_dump(so, hdr, lang, "gcc")) == _restrict(
        _dump(so, hdr, lang, "castxml")
    )


@pytest.mark.parametrize(
    ("old", "new"), [("cxx_first", "cxx_none"), ("c_first", "c_none")]
)
def test_gcc_reports_a_removed_restrict(
    old: str, new: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    lang = _CASES[old][1]
    snaps = [
        _dump(*_lib(tmp_path, c, _CASES[c][0], lang), lang, "gcc") for c in (old, new)
    ]
    assert "param_restrict_changed" in {c.kind.value for c in compare(*snaps).changes}


def test_unchanged_restrict_reports_nothing_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: the same restrict-qualified parameter on both sides."""
    _require()
    snaps = [
        _dump(*_lib(tmp_path, f"v{i}", _CASES["cxx_first"][0], "c++"), "c++", "gcc")
        for i in (1, 2)
    ]
    assert "param_restrict_changed" not in {
        c.kind.value for c in compare(*snaps).changes
    }

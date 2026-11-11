"""The clang header backend records a typedef'd return type's ABI fact as castxml does.

Bug class: a header-AST backend drops a declaration fact the reference
backend records. clang's ``-ast-dump=json`` gives a ``FunctionDecl``'s type
no ``desugaredQualType``, so a return type spelled through a typedef or
alias (``count_t f();``) recorded no ``return_type_abi_fact``. Removing the
typedef (``count_t f()`` -> ``int f()``) then read ``func_return_changed``
(BREAKING) under clang, where castxml proves the two spellings ABI-equal
(edit corpus operator ``typedef_removed``).

Oracle: castxml's ``return_type_abi_fact`` for the same header, and the
language rule that a typedef names the same type.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.dumper import dump
from abicheck.extract.headers.clang.return_type import desugar_return_alias
from abicheck.model.availability import FactStatus
from abicheck.model.snapshot import AbiSnapshot

pytestmark = pytest.mark.integration

# (header declarations, C or C++) -- every one returns through an alias.
_CASES = {
    "c_typedef_int": ("typedef int count_t;\ncount_t f(int c);", "c"),
    "c_typedef_chain": (
        "typedef unsigned long base_t;\ntypedef base_t size2_t;\nsize2_t f(int c);",
        "c",
    ),
    "cxx_using": ("using count_t = unsigned int;\ncount_t f(int c);", "c++"),
    "cxx_ns_typedef": (
        "namespace n { typedef long long h_t; }\nn::h_t f(int c);",
        "c++",
    ),
    "cxx_member_scope": (
        "namespace n { typedef char c_t; c_t f(int c); }",
        "c++",
    ),
}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("gcc", "g++", "castxml", "clang"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


def _lib(root: Path, name: str, decls: str, lang: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    hdr = d / "a.h"
    hdr.write_text(decls + "\n")
    ext, cc = ("c", "gcc") if lang == "c" else ("cpp", "g++")
    # `f` is defined in the header's own scope, so its declared return type
    # is reused verbatim (``namespace n { ... }`` wraps the definition too).
    body = "f(int c) { return c; }"
    if decls.endswith("c_t f(int c); }"):
        defn = "namespace n { c_t " + body + " }"
    else:
        defn = decls.splitlines()[-1].replace("f(int c);", body)
    src = d / f"a.{ext}"
    src.write_text(f'#include "a.h"\n{defn}\n')
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


def _f(snap: AbiSnapshot):  # noqa: ANN202 - a model Function
    return next(fn for fn in snap.declarations.functions if fn.name.endswith("f"))


@pytest.mark.parametrize("case", sorted(_CASES))
def test_clang_return_alias_fact_matches_castxml(case: str, tmp_path: Path) -> None:
    _require()
    decls, lang = _CASES[case]
    so, hdr = _lib(tmp_path, case, decls, lang)
    want = _f(_dump(so, hdr, lang, "castxml")).return_type_abi_fact
    got = _f(_dump(so, hdr, lang, "clang")).return_type_abi_fact
    assert want is not None and want.status is FactStatus.PRESENT
    assert got is not None and got.status is FactStatus.PRESENT, got
    assert got.value == want.value


@pytest.mark.parametrize("case", ["c_typedef_int", "c_typedef_chain", "cxx_using"])
def test_removing_the_return_typedef_is_not_a_return_change_on_clang(
    case: str, tmp_path: Path
) -> None:
    _require()
    decls, lang = _CASES[case]
    target = {"c_typedef_int": "int", "c_typedef_chain": "unsigned long"}.get(
        case, "unsigned int"
    )
    new_decls = f"{target} f(int c);"
    old = _dump(*_lib(tmp_path, "old", decls, lang), lang, "clang")
    new = _dump(*_lib(tmp_path, "new", new_decls, lang), lang, "clang")
    kinds = {c.kind.value for c in compare(old, new).changes}
    assert "func_return_changed" not in kinds


def test_a_real_return_retype_through_an_alias_is_still_reported(
    tmp_path: Path,
) -> None:
    """Negative control: int -> long through the same alias name breaks."""
    _require()
    old = _dump(
        *_lib(tmp_path, "old", "typedef int r_t;\nr_t f(int c);", "c"), "c", "clang"
    )
    new = _dump(
        *_lib(tmp_path, "new", "typedef long r_t;\nr_t f(int c);", "c"), "c", "clang"
    )
    assert _f(old).return_type_abi_fact.value != _f(new).return_type_abi_fact.value


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("count_t", "int"),
        ("const count_t", "const int"),
        ("::count_t", "int"),
        ("count_t *", "count_t *"),  # a declarator stays as written
        ("other_t", "other_t"),
        ("int", "int"),
    ],
)
def test_desugar_return_alias_replaces_only_a_whole_top_level_alias(
    spelling: str, expected: str
) -> None:
    assert desugar_return_alias(spelling, {"count_t": "int"}) == expected

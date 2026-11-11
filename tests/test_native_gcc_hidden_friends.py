"""The native GCC header backend marks befriended functions as castxml does.

Bug class: a header backend leaves a fact family empty that the reference
backend records. The GCC plugin emitted an in-class ``friend`` function
like any namespace function and recorded no befriending class, so
``Function.is_hidden_friend``/``hidden_friend_owner`` stayed unset and
adding or removing a hidden friend (found only by ADL, never exported)
read NO_CHANGE under the gcc backend (edit corpus ``hidden_friend_added``/
``hidden_friend_removed``).

Oracle: castxml's ``befriending`` record for the same header
(``extract/headers/castxml/functions.build_hidden_friend_ids``).
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

_CASES = {
    "inline_operator": (
        "namespace n { struct Cmp { int a; "
        "friend bool operator<(Cmp x, Cmp y) { return x.a < y.a; } }; }"
    ),
    "declared_friend_and_namespace_decl": (
        "namespace n { struct Two { int b; friend int peek(const Two &t); };\n"
        "int peek(const Two &t); }"
    ),
    "two_owners": (
        "struct A; struct B;\n"
        "struct A { int x; friend int both(const A &, const B &); };\n"
        "struct B { int y; friend int both(const A &, const B &); };\n"
        "int both(const A &, const B &);"
    ),
    "global_inline_friend": (
        "struct G { int v; friend int twice(G g) { return 2 * g.v; } };"
    ),
    "no_friend": "namespace n { struct P { int a; }; int plain(P p); }",
}
_SOURCES = {
    "declared_friend_and_namespace_decl": "int n::peek(const n::Two &t) { return t.b; }",
    "two_owners": "int both(const A &a, const B &b) { return a.x + b.y; }",
    "no_friend": "int n::plain(n::P p) { return p.a; }",
}


def _require() -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("ELF shared library")
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def gcc_env(native_plugins: tuple[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setenv("ABICHECK_GCC_PLUGIN", str(native_plugins[1]))


def _lib(root: Path, name: str, header: str, source: str) -> tuple[Path, Path]:
    d = root / name
    d.mkdir()
    (d / "a.hpp").write_text(header + "\n")
    (d / "a.cpp").write_text(
        f'#include "a.hpp"\nint keep_alive() {{ return 0; }}\n{source}\n'
    )
    so = d / "liba.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(d / "a.cpp")], check=True
    )
    return so, d / "a.hpp"


def _dump(so: Path, hdr: Path, backend: str) -> AbiSnapshot:
    return dump(
        so,
        [hdr],
        version=so.parent.name,
        compiler="c++",
        lang="c++",
        header_backend=backend,
    )


def _friends(snap: AbiSnapshot) -> dict[str, tuple[bool, str | None]]:
    return {
        # Keyed by leaf: castxml spells a namespace function's name bare.
        f.name.split("::")[-1]: (bool(f.is_hidden_friend), f.hidden_friend_owner)
        for f in snap.declarations.functions
        if f.name.split("::")[-1] in ("operator<", "peek", "both", "twice", "plain")
    }


@pytest.mark.parametrize("case", sorted(_CASES))
def test_gcc_hidden_friend_facts_match_castxml(
    case: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    so, hdr = _lib(tmp_path, case, _CASES[case], _SOURCES.get(case, ""))
    want = _friends(_dump(so, hdr, "castxml"))
    assert want, "the oracle sees the function"
    assert _friends(_dump(so, hdr, "gcc")) == want


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("no_friend", "inline_operator", "hidden_friend_added"),
        ("inline_operator", "no_friend", "hidden_friend_removed"),
    ],
)
def test_gcc_reports_hidden_friend_transitions(
    old: str, new: str, kind: str, tmp_path: Path, gcc_env: None
) -> None:
    _require()
    base = "namespace n { struct Cmp { int a; }; }"
    headers = {"no_friend": base, "inline_operator": _CASES["inline_operator"]}
    snaps = [_dump(*_lib(tmp_path, c, headers[c], ""), "gcc") for c in (old, new)]
    assert kind in {c.kind.value for c in compare(*snaps).changes}


def test_unchanged_hidden_friend_reports_nothing_on_gcc(
    tmp_path: Path, gcc_env: None
) -> None:
    """Negative control: the same hidden friend on both sides."""
    _require()
    snaps = [
        _dump(*_lib(tmp_path, f"v{i}", _CASES["inline_operator"], ""), "gcc")
        for i in (1, 2)
    ]
    assert not {
        c.kind.value for c in compare(*snaps).changes if "friend" in c.kind.value
    }

"""Comparing a library against a byte-identical copy of itself, extracted
under a different directory, must report nothing -- from *any* detector.

clang and castxml embed the absolute declaring path in every lambda-closure
and unnamed-tag type spelling (``foldable<(lambda at /w/old/kumi.hpp:18:20)>``).
Unpacking two release tarballs into two directories is the ordinary way to
compare two builds, so any detector that keyed identity on that raw spelling
turned the directory name into phantom removed+added pairs (a real report: a
false BREAKING on byte-identical binaries). This drives the public CLI end to
end over real headers and a real compiled library, rather than hand-built
snapshots, and asserts on the whole finding set so a detector that was never
audited for this is still covered.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = pytest.mark.integration

# Each shape puts a lambda or unnamed type somewhere a declaration's identity
# is spelled from: a hidden friend of a lambda-parameterised specialization
# (the reported shape, modelled on eve's kumi.hpp), an ordinary member of
# one, a lambda-typed alias, an unnamed struct member, and a closure type in
# a function-template argument.
_KUMI = """\
#pragma once
namespace kumi { namespace detail {
template <class F, class T> struct foldable {
  F func; T value;
  template <class W>
  friend constexpr auto operator<<(foldable&& x, foldable<F, W>&& y) {
    return foldable<F, decltype(x.func(x.value, y.value))>{x.func, x.func(x.value, y.value)};
  }
  int apply() const { return int(func(value, value)); }
};
template <class F, class T> foldable(F, T) -> foldable<F, T>;
template <class F> int call(F f) { return f(1); }
}
template <class F, class V, class... T>
constexpr auto fold_left(F f, V init, T const&... xs) {
  auto step = [&](auto&&... e) { return (detail::foldable{f, init} << ... << detail::foldable{f, e}).value; };
  return step(xs...);
}
template <class... T> constexpr int sum(T const&... xs) {
  return fold_left([](auto a, auto b) { return a + b; }, 0, xs...);
}
inline auto make_add() { return [](int a, int b) { return a + b; }; }
}
"""

_API = """\
#pragma once
#include <eve/detail/kumi.hpp>
namespace lib {
using Adder = decltype(kumi::make_add());
using Fold = kumi::detail::foldable<Adder, unsigned char>;
struct Holder { Fold fold; struct { int x, y; } point; int n; };
inline int total(unsigned char const& a, unsigned char const& b) { return kumi::sum(a, b); }
inline int poke() { return kumi::detail::call([](int v) { return v * 2; }); }
int exported_total(unsigned char a, unsigned char b);
int hold(Holder const& h);
}
"""

_SRC = """\
#include <lib/api.hpp>
namespace lib {
int exported_total(unsigned char a, unsigned char b) { return total(a, b) + poke(); }
int hold(Holder const& h) { return h.n + h.fold.apply() + h.point.x; }
}
"""

# (old dir, new dir) relative to the test's working directory. Sibling dirs
# are the reported case; the others vary depth and include a space, which a
# ``\\S+``-style path pattern cannot span.
_LAYOUTS = [
    ("work/old", "work/new"),
    ("a/old", "b/deeper/tree/new"),
    ("with space/old", "other space/new"),
]


def _frontends() -> list[str]:
    out = []
    if shutil.which("castxml"):
        out.append("castxml")
    if shutil.which("clang"):
        out.append("clang")
    return out


@pytest.fixture(scope="module")
def built_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
        pytest.skip("no C++ compiler")
    root = tmp_path_factory.mktemp("lambda_src")
    (root / "include/eve-1/eve/detail").mkdir(parents=True)
    (root / "include/lib").mkdir(parents=True)
    (root / "include/eve-1/eve/detail/kumi.hpp").write_text(_KUMI)
    (root / "include/lib/api.hpp").write_text(_API)
    (root / "api.cpp").write_text(_SRC)
    subprocess.run(
        [
            cxx,
            "-std=c++20",
            "-shared",
            "-fPIC",
            "-g",
            "-Iinclude",
            "-Iinclude/eve-1",
            "api.cpp",
            "-o",
            "libapi.so",
        ],
        cwd=root,
        check=True,
    )
    return root


def _compare(old: str, new: str, extra: list[str]) -> tuple[int, dict]:
    args = [
        "compare",
        f"{old}/libapi.so",
        f"{new}/libapi.so",
        "--header",
        f"old={old}/include/lib/api.hpp",
        "--header",
        f"new={new}/include/lib/api.hpp",
        "-I",
        f"old={old}/include",
        "-I",
        f"old={old}/include/eve-1",
        "-I",
        f"new={new}/include",
        "-I",
        f"new={new}/include/eve-1",
        "-o",
        "json=report.json",
        *extra,
    ]
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    return result.exit_code, json.loads(Path("report.json").read_text())


@pytest.mark.parametrize("frontend", _frontends() or ["castxml"])
@pytest.mark.parametrize(("old", "new"), _LAYOUTS)
def test_identical_tree_under_two_roots_reports_nothing(
    built_tree: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontend: str,
    old: str,
    new: str,
) -> None:
    if frontend not in _frontends():
        pytest.skip(f"{frontend} not found in PATH")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
    for side in (old, new):
        shutil.copytree(built_tree, tmp_path / side)

    # Oracle: the same tree compared against itself at one path. Per-side
    # observations (e.g. ``exported_not_public``) legitimately appear there
    # too; what must not appear is anything the directory name adds.
    ref_code, reference = _compare(old, old, [])
    code, report = _compare(old, new, [])

    def findings(r: dict) -> list[tuple[str, str]]:
        return sorted((c["kind"], c.get("symbol") or "") for c in r.get("changes", []))

    assert findings(report) == findings(reference)
    assert code == ref_code
    assert report.get("verdict") == reference.get("verdict")
    assert not [k for k, _ in findings(report) if k.endswith(("_removed", "_added"))]
    # The checkout directories must not leak into any reported identity
    # (``source_location`` legitimately says where the finding lives).
    identity = ("symbol", "old_value", "new_value", "description")
    text = json.dumps([[c.get(k) for k in identity] for c in report.get("changes", [])])
    for side in (old, new):
        assert side not in text


_VT_HEADER = """\
#pragma once
namespace lib {
inline auto tag() { return [](int v) { return v; }; }
template <class F> struct Base { F f; virtual ~Base() {} virtual int %s(); virtual int %s(); };
template <class F> int Base<F>::a() { return 1; }
template <class F> int Base<F>::b() { return 2; }
using L = decltype(tag());  // desugars to Base<(lambda at ...)>
struct Derived : Base<L> { Derived(); int c; };
}
"""


@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not found in PATH")
def test_vtable_of_class_derived_from_lambda_specialization_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Swapping two virtual methods of a lambda-parameterised base must be
    seen through a derived public class: the base lookup and the
    specialization index have to spell the lambda argument the same way."""
    cxx = shutil.which("g++") or shutil.which("clang++")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    for side, order in (("old", ("a", "b")), ("new", ("b", "a"))):
        inc = tmp_path / side / "include/lib"
        inc.mkdir(parents=True)
        (inc / "api.hpp").write_text(_VT_HEADER % order)
        (tmp_path / side / "api.cpp").write_text(
            "#include <lib/api.hpp>\nnamespace lib { Derived::Derived() : c(0) {} }\n"
        )
        subprocess.run(
            [
                cxx,
                "-std=c++20",
                "-shared",
                "-fPIC",
                "-g",
                "-Iinclude",
                "api.cpp",
                "-o",
                "libapi.so",
            ],
            cwd=tmp_path / side,
            check=True,
        )
    args = [
        "compare",
        "old/libapi.so",
        "new/libapi.so",
        "--header",
        "old=old/include/lib/api.hpp",
        "--header",
        "new=new/include/lib/api.hpp",
        "-I",
        "old=old/include",
        "-I",
        "new=new/include",
        "-o",
        "json=report.json",
    ]
    CliRunner().invoke(main, args, catch_exceptions=False)
    kinds = {c["kind"] for c in json.loads(Path("report.json").read_text())["changes"]}
    vt = {
        c.get("symbol")
        for c in json.loads(Path("report.json").read_text())["changes"]
        if c["kind"] == "type_vtable_changed"
    }
    assert any("Derived" in s for s in vt), (kinds, vt)

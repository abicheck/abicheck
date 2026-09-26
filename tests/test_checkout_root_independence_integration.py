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
import re
import shutil
import subprocess
import sys
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


def _std_config() -> list[str]:
    """Parse headers as C++20, the standard the library is built with.

    clang's default standard differs by target (C++14 for an MSVC-targeted
    clang on Windows), and the fixture's deduction guide needs C++17, so an
    unpinned parse fails there and silently falls back to export-table mode.
    """
    cfg = Path(".abicheck.yml")
    cfg.write_text("compile:\n  options: [-std=c++20]\n")
    return ["--config", str(cfg)]


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
        *_std_config(),
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
    if sys.platform == "win32":
        # A MinGW g++ DLL exports Itanium-mangled names while clang parses
        # the header for the MSVC target, so no header declaration matches an
        # export and header scoping falls back to the export table (which
        # carries no vtable layout). Needs a clang built for the MinGW target.
        pytest.skip(
            "header/export mangling differs between MinGW g++ and MSVC-target clang"
        )
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None or not shutil.which("clang"):
        pytest.skip("needs a C++ compiler and clang")
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
        *_std_config(),
    ]
    CliRunner().invoke(main, args, catch_exceptions=False)
    kinds = {c["kind"] for c in json.loads(Path("report.json").read_text())["changes"]}
    vt = {
        c.get("symbol")
        for c in json.loads(Path("report.json").read_text())["changes"]
        if c["kind"] == "type_vtable_changed"
    }
    assert any("Derived" in s for s in vt), (kinds, vt)


@pytest.mark.parametrize("frontend", _frontends() or ["castxml"])
def test_exclude_header_scopes_out_what_only_the_excluded_header_provides(
    built_tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frontend: str
) -> None:
    """``--exclude-header`` treats a matched header like a toolchain header
    even when it is only reached through another header's ``#include``:
    what it alone declares is not observed, while a type the library's own
    public API uses keeps being checked."""
    if frontend not in _frontends():
        pytest.skip(f"{frontend} not found in PATH")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", frontend)
    shutil.copytree(built_tree, tmp_path / "old")
    shutil.copytree(built_tree, tmp_path / "new")
    # NEW's excluded header gains unreferenced internals, and `foldable` --
    # which the public `lib::Holder` embeds -- gains a field.
    (tmp_path / "new/include/eve-1/eve/detail/kumi.hpp").write_text(
        _KUMI.replace(
            "  F func; T value;", "  F func; T value; long extra_field;"
        ).replace(
            "inline auto make_add()",
            "struct ExtraInternal { int a; };\ninline int extra_helper() { return 7; }\n"
            "int extra_decl(ExtraInternal const&);\n"
            "inline auto make_add()",
        )
    )

    def names(report: dict) -> str:
        return json.dumps(
            [
                [c["kind"], c.get("symbol"), c.get("description")]
                for c in report["changes"]
            ]
        )

    _, plain = _compare("old", "new", [])
    _, scoped = _compare("old", "new", ["--exclude-header", "*/eve-*/*"])

    unreferenced = ("ExtraInternal", "extra_helper", "extra_decl")
    seen_without_flag = [n for n in unreferenced if n in names(plain)]
    assert not any(n in names(scoped) for n in unreferenced), names(scoped)
    # Not vacuous: castxml reports these additions without the flag. (clang
    # already leaves unreferenced non-public additions out on its own.)
    if frontend == "castxml":
        assert seen_without_flag, names(plain)
    # A layout change to an excluded-header type the public `lib::Holder`
    # embeds is still reported. That layout comes from the ELF/DWARF path; a
    # MinGW DLL on Windows is read as PE, whose clang header parse targets
    # MSVC and matches no export, so no record layout is compared there.
    if sys.platform == "win32":
        return
    assert "extra_field" in names(scoped) or "Holder" in names(scoped), names(scoped)


# The L5 shape of the same defect: a body fingerprint and a signature key
# hashed `(lambda at /abs/checkout/...)` verbatim, so `std::make_shared<Lambda>`
# read as `declaration_renamed` and inline bodies as `inline_body_changed`.
_L5_HEADER = """\
#pragma once
#include <memory>
#include <svs/detail/impl.hpp>
namespace svs {
inline auto lam = [](int x) { return x + 1; };
struct Anon { struct { int a; } inner; };
inline auto share_lam() { return std::make_shared<decltype(lam)>(lam); }
inline int anon_sum(Anon const& a) { decltype(a.inner) c = a.inner; return c.a; }
template <class F> auto wrap(F f) { return std::make_shared<F>(f); }
inline auto wrapped() { return wrap([](int y) { return y * 2; }); }
int run(Anon const& a);
}
"""


@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not found in PATH")
@pytest.mark.parametrize(("old", "new"), _LAYOUTS[:2])
def test_source_depth_dumps_of_relocated_checkouts_compare_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: str, new: str
) -> None:
    """Dump each checkout separately at `--depth source` (relative `-I`, as a
    user types it), then compare the two stored snapshots: the pair must
    report exactly what one checkout reports against itself, every
    declaring header must be spelled one way, and the extraction scope must
    not claim the rules differ."""
    cxx = shutil.which("g++") or shutil.which("clang++")
    if cxx is None or shutil.which("git") is None or sys.platform == "win32":
        pytest.skip("needs a POSIX C++ compiler and git")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    src = tmp_path / "src_tree"
    (src / "include/svs").mkdir(parents=True)
    (src / "include/svs/api.hpp").write_text(_L5_HEADER)
    # Reached only through the relative `-I include`, never named on the
    # umbrella: the header the parser used to spell relative.
    (src / "include/svs/detail").mkdir()
    (src / "include/svs/detail/impl.hpp").write_text(
        "#pragma once\nnamespace svs { int helper(int); inline int twice(int v) { return 2 * v; } }\n"
    )
    (src / "a.cpp").write_text(
        "#include <svs/api.hpp>\nnamespace svs { int helper(int v) { return twice(v); }"
        " int run(Anon const& a) { (void)share_lam(); (void)wrapped(); return anon_sum(a); } }\n"
    )
    subprocess.run(
        [
            cxx,
            "-std=c++17",
            "-shared",
            "-fPIC",
            "-g",
            "-Iinclude",
            "a.cpp",
            "-o",
            "lib.so",
        ],
        cwd=src,
        check=True,
    )
    snaps = {}
    for side in (old, new):
        root = tmp_path / side
        shutil.copytree(src, root)
        (root / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(root),
                        "file": "a.cpp",
                        "command": f"{cxx} -std=c++17 -fPIC -Iinclude -c a.cpp",
                    }
                ]
            )
        )
        # A checkout marker is all the ownership anchor looks for; no commit
        # (which a signing or identity config on the host could refuse).
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        out = f"{side.replace('/', '_')}.json"
        result = CliRunner().invoke(
            main,
            [
                "dump",
                f"{side}/lib.so",
                "-H",
                f"{side}/include",
                "--include",
                f"{side}/include",
                "--depth",
                "source",
                "--sources",
                side,
                "--build-info",
                f"{side}/compile_commands.json",
                "-o",
                out,
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        snaps[side] = out

    headers = {
        h
        for h in re.findall(r'"source_header": "([^"]*)"', Path(snaps[old]).read_text())
        if h
    }
    assert any(h.endswith("impl.hpp") for h in headers), headers
    assert all(Path(h).is_absolute() for h in headers), headers

    def compare(a: str, b: str) -> dict:
        CliRunner().invoke(
            main, ["compare", a, b, "-o", "json=r.json"], catch_exceptions=False
        )
        return json.loads(Path("r.json").read_text())

    def findings(r: dict) -> list[tuple[str, str]]:
        return sorted((c["kind"], c.get("symbol") or "") for c in r.get("changes", []))

    reference, report = compare(snaps[old], snaps[old]), compare(snaps[old], snaps[new])
    assert findings(report) == findings(reference)
    assert report.get("verdict") == reference.get("verdict")
    assert not any("ownership rules" in w for w in report.get("coverage_warnings", []))

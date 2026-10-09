"""Real-compiler probes from the 2026-10-08 GCC/Clang catalog re-audit.

Each scenario is compiled with gcc/g++, dumped through both header backends
(CastXML and the clang AST frontend) and compared through the CLI. The
oracle is the compiler's own semantics, stated per scenario, never the
detector's output on another backend:

* a calling convention spelled through a macro is whatever the macro
  *expands to* in that translation unit -- a switch made through the macro
  is a break, an equivalent respelling (literal <-> macro, macro <-> macro,
  explicit target default <-> none) is not;
* ``char *f()`` -> ``const char *f()`` breaks a caller binding the result to
  ``char *`` (API break); the reverse direction is only a risk;
* a struct gaining a user-provided destructor stops being trivially
  copyable (by-value passing switches to a hidden pointer) even when the
  binaries carry no debug information; gaining a ``= default`` destructor
  does not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF + x86-64 ABIs"
    ),
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKENDS = ("castxml", "clang")

_MS = "__attribute__((ms_abi))"
_SYSV = "__attribute__((sysv_abi))"


def _require(*tools: str) -> None:
    for tool in tools:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    machine = subprocess.run(["gcc", "-dumpmachine"], capture_output=True, text=True)
    if not machine.stdout.startswith("x86_64"):
        pytest.skip("ms_abi/sysv_abi need an x86-64 target")


def _build(
    root: Path, name: str, files: dict[str, str], *, cxx: bool, strip: bool
) -> Path:
    d = root / name
    d.mkdir(parents=True)
    for fname, text in files.items():
        (d / fname).write_text(text)
    src = "lib.cpp" if cxx else "lib.c"
    cc = "g++" if cxx else "gcc"
    r = subprocess.run(
        [cc, "-shared", "-fPIC", "-g", "-O0", "-I.", "-o", "libx.so", src],
        cwd=d,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    if strip:
        subprocess.run(["strip", "--strip-debug", "libx.so"], cwd=d, check=True)
    return d


def _compare(old: Path, new: Path, backend: str, cache: Path) -> tuple[int, set[str]]:
    out = new.parent / f"{old.name}-{new.name}-{backend}.json"
    r = subprocess.run(
        [
            sys.executable, "-m", "abicheck", "compare",
            str(old / "libx.so"), str(new / "libx.so"),
            "--header", f"old={old / 'lib.h'}", "--header", f"new={new / 'lib.h'}",
            "-o", f"json={out}",
        ],
        env={
            **os.environ,
            "ABICHECK_AST_FRONTEND": backend,
            # Private caches: a hit from another build would skip this code.
            "ABICHECK_CACHE_DIR": str(cache),
            "XDG_CACHE_HOME": str(cache),
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
            ),
        },
        capture_output=True,
        text=True,
    )  # fmt: skip
    assert out.exists(), r.stderr
    kinds = {c["kind"] for c in json.loads(out.read_text())["changes"]}
    return r.returncode, kinds


def _c_lib(header: str, *, cc_def: str = "", impl_cc: str = "") -> dict[str, str]:
    files = {
        "lib.h": header,
        "lib.c": f'#include "lib.h"\n{impl_cc} int add(int a, int b) {{ return a + b; }}\n',
    }
    if cc_def:
        files["cc.h"] = cc_def
    return files


# (old files, new files, expect a calling_convention_changed)
_CC_SCENARIOS = {
    "macro-switch-in-included-header": (
        _c_lib('#include "cc.h"\nint CALL add(int a, int b);\n',
               cc_def=f"#define CALL {_SYSV}\n", impl_cc=_SYSV),
        _c_lib('#include "cc.h"\nint CALL add(int a, int b);\n',
               cc_def=f"#define CALL {_MS}\n", impl_cc=_MS),
        True,
    ),
    "literal-to-equivalent-macro": (
        _c_lib(f"{_MS} int add(int a, int b);\n", impl_cc=_MS),
        _c_lib(f"#define CALL {_MS}\nCALL int add(int a, int b);\n", impl_cc=_MS),
        False,
    ),
    "macro-to-nested-equivalent-macro": (
        _c_lib(f"#define API {_MS}\nAPI int add(int a, int b);\n", impl_cc=_MS),
        _c_lib(f"#define MS {_MS}\n#define CALL2 MS\nCALL2 int add(int a, int b);\n",
               impl_cc=_MS),
        False,
    ),
    "explicit-target-default": (
        _c_lib("int add(int a, int b);\n"),
        _c_lib(f"{_SYSV} int add(int a, int b);\n"),
        False,
    ),
}  # fmt: skip


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("scenario", sorted(_CC_SCENARIOS))
def test_calling_convention_through_macros(
    scenario: str, backend: str, tmp_path: Path
) -> None:
    _require("gcc", "castxml", "clang")
    old_files, new_files, breaks = _CC_SCENARIOS[scenario]
    old = _build(tmp_path, "v1", old_files, cxx=False, strip=False)
    new = _build(tmp_path, "v2", new_files, cxx=False, strip=False)
    rc, kinds = _compare(old, new, backend, tmp_path / "cache")
    assert ("calling_convention_changed" in kinds) is breaks, kinds
    assert "func_return_changed" not in kinds, kinds
    assert rc == (4 if breaks else 0), kinds


def _ret_lib(ret: str) -> dict[str, str]:
    return {
        "lib.h": f"{ret} get_name(void);\n",
        "lib.c": f'#include "lib.h"\nstatic char n[] = "x";\n{ret} get_name(void) {{ return n; }}\n',
    }


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize(
    ("old_ret", "new_ret", "kind", "rc"),
    [
        ("char *", "const char *", "func_return_pointee_qualifier_added", 2),
        ("const char *", "char *", "func_return_pointee_qualifier_removed", 0),
    ],
)
def test_return_pointee_qualifier(
    old_ret: str, new_ret: str, kind: str, rc: int, backend: str, tmp_path: Path
) -> None:
    _require("gcc", "castxml", "clang")
    old = _build(tmp_path, "v1", _ret_lib(old_ret), cxx=False, strip=False)
    new = _build(tmp_path, "v2", _ret_lib(new_ret), cxx=False, strip=False)
    got_rc, kinds = _compare(old, new, backend, tmp_path / "cache")
    assert kind in kinds and "func_return_changed" not in kinds, kinds
    assert got_rc == rc, kinds


def _point_lib(member: str) -> dict[str, str]:
    return {
        "lib.h": f"struct Point {{ double x; double y; {member} }};\n"
        "double distance(struct Point a, struct Point b);\n",
        "lib.cpp": '#include "lib.h"\n'
        "double distance(Point a, Point b) { return a.x - b.x + a.y - b.y; }\n",
    }


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize(
    ("new_member", "lost"),
    [("~Point() {}", True), ("~Point() = default;", False)],
)
def test_triviality_without_debug_info(
    new_member: str, lost: bool, backend: str, tmp_path: Path
) -> None:
    _require("g++", "castxml", "clang", "strip")
    old = _build(tmp_path, "v1", _point_lib(""), cxx=True, strip=True)
    new = _build(tmp_path, "v2", _point_lib(new_member), cxx=True, strip=True)
    rc, kinds = _compare(old, new, backend, tmp_path / "cache")
    assert ("trivially_copyable_lost" in kinds) is lost, kinds
    if lost:
        assert rc == 4, kinds

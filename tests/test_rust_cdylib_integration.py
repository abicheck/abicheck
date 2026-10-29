# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Rust cdylibs through the public ``abicheck compare`` workflow.

The supported Rust surface is the C ABI a cdylib exports (``#[no_mangle]``
+ ``extern "C"`` + ``#[repr(C)]``). These tests build real cdylibs with
``cargo`` and compare them with the CLI, the way a Rust maintainer would:

* the C surface with a cbindgen-style header (``-H``);
* the same surface with DWARF only (``debug = 2`` in the release profile);
* a Rust ``extern "C" fn`` callback whose arity changed, which used to be
  reported as a C++-template arity finding (``mandatory_template_param_added``)
  because the argument-counter read the ``->`` return arrow and the commas of
  the function type as template arguments;
* a pure Rust-native ``pub fn`` change, which is not exported from a cdylib
  and therefore is outside the compared surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="the cdylib checked here is an ELF .so; Mach-O and PE hosts build .dylib/.dll",
    ),
]

_CARGO = shutil.which("cargo")
requires_cargo = pytest.mark.skipif(_CARGO is None, reason="cargo not installed")

_CARGO_TOML = """\
[package]
name = "{name}"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[profile.release]
debug = 2
"""

_BASE_SRC = """\
#[repr(C)]
#[derive(Clone, Copy)]
pub struct Point {{ pub x: f64, pub y: f64 }}

#[no_mangle]
pub extern "C" fn rl_add(a: i32, b: i32) -> {ret} {{ a.wrapping_add(b) as {ret} }}

#[no_mangle]
pub extern "C" fn rl_point_len2(p: *const Point) -> f64 {{
    unsafe {{ (*p).x * (*p).x + (*p).y * (*p).y }}
}}

#[no_mangle]
pub extern "C" fn rl_set_callback(cb: Option<extern "C" fn({cb_args}) -> i32>) -> i32 {{
    match cb {{ Some(f) => f({cb_call}), None => 0 }}
}}

pub fn rust_scale(x: u64, level: u32) -> u64 {{ x.wrapping_mul(level as u64) }}
"""

_HEADER = """\
#include <stdint.h>
typedef struct Point {{ double x; double y; }} Point;
int{bits}_t rl_add(int32_t a, int32_t b);
double rl_point_len2(const Point *p);
int32_t rl_set_callback(int32_t (*cb)({cb_decl}));
"""


def _variant(ret: str = "i32", cb_arity: int = 1) -> str:
    cb_args = ", ".join(["i32"] * cb_arity)
    cb_call = ", ".join(str(i + 1) for i in range(cb_arity))
    return _BASE_SRC.format(ret=ret, cb_args=cb_args, cb_call=cb_call)


def _header(bits: int = 32, cb_arity: int = 1) -> str:
    cb_decl = ", ".join(["int32_t"] * cb_arity)
    return _HEADER.format(bits=bits, cb_decl=cb_decl)


def _build_cdylib(root: Path, name: str, src: str) -> Path:
    crate = root / name
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(_CARGO_TOML.format(name=name))
    (crate / "src" / "lib.rs").write_text(src)
    subprocess.run(
        [_CARGO, "build", "--release", "--offline", "-q"],
        cwd=crate,
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    so = crate / "target" / "release" / f"lib{name}.so"
    assert so.is_file(), so
    return so


def _compare(old: Path, new: Path, out: Path, *headers: str) -> dict:
    cmd = abicheck_command("compare", str(old), str(new), "-o", f"json={out}")
    for h in headers:
        cmd += ["-H", h]
    subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=600)
    return json.loads(out.read_text())


def _kinds(report: dict) -> set[str]:
    return {c["kind"] for c in report.get("changes", [])}


@pytest.fixture(scope="module")
def rust_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("rust_cdylib")


@pytest.fixture(scope="module")
def base_so(rust_root: Path) -> Path:
    if _CARGO is None:
        pytest.skip("cargo not installed")
    return _build_cdylib(rust_root / "base", "rlbase", _variant())


@requires_cargo
def test_c_surface_return_change_detected_with_header(
    rust_root: Path, base_so: Path
) -> None:
    new = _build_cdylib(rust_root / "ret", "rlbase", _variant(ret="i64"))
    base_h = rust_root / "base.h"
    base_h.write_text(_header(bits=32))
    new_h = rust_root / "ret.h"
    new_h.write_text(_header(bits=64))
    cmd = abicheck_command("compare",
        str(base_so),
        str(new),
        "-H",
        f"old={base_h}",
        "-H",
        f"new={new_h}",
        "-o",
        f"json={rust_root / 'ret.json'}",
    )
    subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=600)
    report = json.loads((rust_root / "ret.json").read_text())
    assert report["verdict"] == "BREAKING"
    assert "func_return_changed" in _kinds(report)


@requires_cargo
def test_dwarf_only_reports_repr_c_layout_and_return_change(
    rust_root: Path, base_so: Path
) -> None:
    new = _build_cdylib(rust_root / "ret_dwarf", "rlbase", _variant(ret="i64"))
    report = _compare(base_so, new, rust_root / "dwarf_ret.json")
    assert report["verdict"] == "BREAKING"
    assert "func_return_changed" in _kinds(report)


@requires_cargo
def test_fn_pointer_arity_change_is_not_a_template_arity_finding(
    rust_root: Path, base_so: Path
) -> None:
    new = _build_cdylib(rust_root / "cb", "rlbase", _variant(cb_arity=2))
    report = _compare(base_so, new, rust_root / "cb.json")
    kinds = _kinds(report)
    assert "mandatory_template_param_added" not in kinds
    assert "func_params_changed" in kinds


@requires_cargo
def test_rust_native_api_is_outside_the_cdylib_surface(
    rust_root: Path, base_so: Path
) -> None:
    # ``rust_scale`` is ``pub`` but not ``#[no_mangle] extern "C"``, so rustc
    # does not export it from the cdylib. A change to it is not a binary
    # contract a C consumer can see, and abicheck reports nothing for it.
    changed = _variant().replace(
        "pub fn rust_scale(x: u64, level: u32) -> u64 { x.wrapping_mul(level as u64) }",
        "pub fn rust_scale(x: u32, level: u32) -> u64 { x as u64 * level as u64 }",
    )
    new = _build_cdylib(rust_root / "native", "rlbase", changed)
    report = _compare(base_so, new, rust_root / "native.json")
    assert report["verdict"] == "NO_CHANGE"

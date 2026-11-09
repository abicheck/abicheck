"""End-to-end (real g++ build, real clang L4 replay, public CLI) for the
L4 source kinds whose grading this branch changed:

* a relaxed concept is ``concept_constraint_changed`` (RISK), the forward
  tightening stays ``concept_tightened`` (API_BREAK);
* a body-only template edit is ``template_body_changed`` (RISK), a
  parameter-list edit is ``header_only_function_params_changed`` (API_BREAK).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command
from tests._unconfirmed import observed_verdict

pytestmark = pytest.mark.integration

CONCEPT_H = """#pragma once
namespace mylib {{
template <typename T>
concept Addable = {body};
template <Addable T> T sum(T a, T b);
}}
"""
CONCEPT_CPP = """#include "api.h"
namespace mylib {
template <Addable T> T sum(T a, T b) { return a + b; }
template int sum<int>(int, int);
}
"""
TEMPLATE_H = """#pragma once
template <typename T>
T clamp(T value, {p} lo, {p} hi) {{ {body} }}
int library_version();
"""
TEMPLATE_CPP = '#include "api.h"\nint library_version() { return 1; }\n'


def _tools() -> str:
    gxx, clang = shutil.which("g++"), shutil.which("clang++")
    if gxx is None or clang is None:
        pytest.skip("g++ and clang++ are both required")
    return gxx


def _side(root: Path, name: str, header: str, source: str, gxx: str) -> Path:
    d = root / name
    d.mkdir()
    (d / "api.h").write_text(header)
    (d / "api.cpp").write_text(source)
    subprocess.run(
        [gxx, "-std=c++20", "-shared", "-fPIC", "-g", "api.cpp", "-o", "libapi.so"],
        cwd=d,
        check=True,
    )
    (d / "cc.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(d),
                    "command": "c++ -std=c++20 -c api.cpp -o api.o",
                    "file": str(d / "api.cpp"),
                }
            ]
        )
    )
    return d


def _compare(root: Path, old: Path, new: Path) -> tuple[str, set[str]]:
    cfg = root / "cfg.yml"
    cfg.write_text("compile:\n  frontend: clang\n")
    out = root / f"{old.name}-{new.name}.json"
    subprocess.run(
        abicheck_command(
            "compare",
            old / "libapi.so",
            new / "libapi.so",
            "-H",
            f"old={old / 'api.h'}",
            "-H",
            f"new={new / 'api.h'}",
            "--build-info",
            f"old={old / 'cc.json'}",
            "--build-info",
            f"new={new / 'cc.json'}",
            "--sources",
            f"old={old}",
            "--sources",
            f"new={new}",
            "--depth",
            "source",
            "--config",
            cfg,
            "--contract",
            "all",
            "-o",
            f"json={out}",
        ),
        capture_output=True,
    )
    doc = json.loads(out.read_text())
    # The observed verdict: on PE/Mach-O, header scoping falls back to the
    # export table when no declaration matches an export (`mangling-fallback`),
    # so the side has no type-level evidence and ADR-090 labels a claim
    # UNCONFIRMED. The concept direction under test is the observed answer.
    return observed_verdict(doc), {c["kind"] for c in doc["changes"]}


def test_concept_direction_end_to_end(tmp_path):
    gxx = _tools()
    base = _side(
        tmp_path,
        "base",
        CONCEPT_H.format(body="requires(T a, T b) { a + b; }"),
        CONCEPT_CPP,
        gxx,
    )
    tight = _side(
        tmp_path,
        "tight",
        CONCEPT_H.format(body="requires(T a, T b) { a + b; T(); }"),
        CONCEPT_CPP,
        gxx,
    )

    verdict, kinds = _compare(tmp_path, base, tight)
    assert "concept_tightened" in kinds and verdict == "API_BREAK"

    verdict, kinds = _compare(tmp_path, tight, base)
    assert "concept_constraint_changed" in kinds
    assert "concept_tightened" not in kinds
    assert verdict == "COMPATIBLE_WITH_RISK"


def test_template_body_vs_signature_end_to_end(tmp_path):
    gxx = _tools()
    body = "return value < lo ? T(lo) : value > hi ? T(hi) : value;"
    v1 = _side(tmp_path, "v1", TEMPLATE_H.format(p="int", body=body), TEMPLATE_CPP, gxx)
    body_only = _side(
        tmp_path,
        "body",
        TEMPLATE_H.format(p="int", body="return value;"),
        TEMPLATE_CPP,
        gxx,
    )
    params = _side(
        tmp_path, "params", TEMPLATE_H.format(p="long", body=body), TEMPLATE_CPP, gxx
    )

    verdict, kinds = _compare(tmp_path, v1, body_only)
    assert "template_body_changed" in kinds
    assert "header_only_function_params_changed" not in kinds
    assert verdict == "COMPATIBLE_WITH_RISK"

    verdict, kinds = _compare(tmp_path, v1, params)
    assert "header_only_function_params_changed" in kinds
    assert verdict == "API_BREAK"

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.source_smoke import SourceSmokeSide, SourceSmokeSpec, run_source_smoke


def _cxx() -> str:
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no C++ compiler")
    return compiler


def test_source_smoke_syntax_detects_expected_consumer_failure(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "v1.h").write_text("template<class T> concept C = true;\n", encoding="utf-8")
    (case / "v2.h").write_text("template<class T> concept C = requires { typename T::missing; };\n", encoding="utf-8")

    spec = SourceSmokeSpec(
        standard="c++20",
        proof="concept tightening",
        v1=SourceSmokeSide(code='#include "v1.h"\nstatic_assert(C<int>);\n', expect="success"),
        v2=SourceSmokeSide(code='#include "v2.h"\nstatic_assert(C<int>);\n', expect="failure"),
    )

    result = run_source_smoke(spec, case_dir=case, work_dir=tmp_path / "work", compiler=_cxx())

    assert result.ok
    assert result.proof == "concept tightening"


def test_source_smoke_link_mode_detects_missing_export(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "v1.cpp").write_text("void api() {}\n", encoding="utf-8")
    (case / "v2.cpp").write_text("void other() {}\n", encoding="utf-8")
    app = 'void api();\nint main() { api(); }\n'

    spec = SourceSmokeSpec(
        mode="link",
        proof="missing export",
        v1=SourceSmokeSide(code=app, lib_source="v1.cpp", expect="success"),
        v2=SourceSmokeSide(code=app, lib_source="v2.cpp", expect="failure"),
    )

    result = run_source_smoke(spec, case_dir=case, work_dir=tmp_path / "work", compiler=_cxx())

    assert result.ok


def test_source_smoke_fails_when_expected_failure_compiles(tmp_path):
    spec = SourceSmokeSpec(
        source="app.cpp",
        standard="c++17",
        proof="expected failure must actually fail",
        v1=SourceSmokeSide(expect="success", code="int main() { return 0; }\n"),
        v2=SourceSmokeSide(expect="failure", code="int main() { return 0; }\n"),
    )

    result = run_source_smoke(spec, case_dir=tmp_path, work_dir=tmp_path / "work", compiler=_cxx())

    assert not result.ok


def test_source_smoke_fails_when_expected_success_does_not_compile(tmp_path):
    spec = SourceSmokeSpec(
        source="app.cpp",
        standard="c++17",
        proof="expected success must compile",
        v1=SourceSmokeSide(expect="success", code="int main( { return 0; }\n"),
        v2=SourceSmokeSide(expect="failure", code="int main( { return 0; }\n"),
    )

    result = run_source_smoke(spec, case_dir=tmp_path, work_dir=tmp_path / "work", compiler=_cxx())

    assert not result.ok

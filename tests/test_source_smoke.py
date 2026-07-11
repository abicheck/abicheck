from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from abicheck.source_smoke import (
    SourceSmokeResult,
    SourceSmokeSide,
    SourceSmokeSpec,
    run_source_smoke,
)


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


def test_from_dict_parses_every_field() -> None:
    # from_dict is the JSON/YAML → spec adapter used by the examples runner; it
    # must faithfully carry each field (including the nested side objects) and
    # coerce the ``replace`` pairs into hashable tuples for the frozen dataclass.
    raw = {
        "source": "app.cpp",
        "standard": "c++20",
        "mode": "link",
        "proof": "custom proof",
        "v1": {
            "expect": "success",
            "code": "int main() {}",
            "replace": [["OLD", "NEW"], ["A", "B"]],
            "lib_source": "libv1.cpp",
            "mode": "run",
        },
        "v2": {"expect": "failure"},
    }

    spec = SourceSmokeSpec.from_dict(raw)

    assert spec.source == "app.cpp"
    assert spec.standard == "c++20"
    assert spec.mode == "link"
    assert spec.proof == "custom proof"
    assert spec.v1.expect == "success"
    assert spec.v1.code == "int main() {}"
    assert spec.v1.replace == (("OLD", "NEW"), ("A", "B"))
    assert spec.v1.lib_source == "libv1.cpp"
    assert spec.v1.mode == "run"
    # Absent side keys fall back to the SourceSmokeSide defaults.
    assert spec.v2.expect == "failure"
    assert spec.v2.code is None
    assert spec.v2.replace == ()
    assert spec.v2.lib_source is None
    assert spec.v2.mode is None


def test_from_dict_applies_defaults_for_empty_mapping() -> None:
    spec = SourceSmokeSpec.from_dict({})

    assert spec.source is None
    assert spec.standard == "c++17"
    assert spec.mode == "syntax"
    assert spec.proof == "source smoke"
    # Missing "v1"/"v2" keys still yield default success sides.
    assert spec.v1.expect == "success"
    assert spec.v2.expect == "success"


def test_result_passed_is_ok_alias() -> None:
    assert SourceSmokeResult(ok=True, proof="p").passed is True
    assert SourceSmokeResult(ok=False, proof="p", failures=("boom",)).passed is False


def test_top_level_source_is_read_and_replacements_applied(tmp_path: Path) -> None:
    # When a side has no inline ``code``, the top-level ``source`` file is read
    # from the case dir and the per-side ``replace`` pairs are applied — this is
    # how one shared consumer file drives both a compiling and a failing variant.
    case = tmp_path / "case"
    case.mkdir()
    (case / "app.cpp").write_text("int main() { return MARKER; }\n", encoding="utf-8")

    spec = SourceSmokeSpec(
        source="app.cpp",
        proof="marker substitution",
        v1=SourceSmokeSide(expect="success", replace=(("MARKER", "0"),)),
        v2=SourceSmokeSide(expect="failure", replace=(("MARKER", "undeclared_ident"),)),
    )

    result = run_source_smoke(spec, case_dir=case, work_dir=tmp_path / "work", compiler=_cxx())

    assert result.ok
    assert result.proof == "marker substitution"


def test_side_without_code_or_source_is_a_fixture_bug(tmp_path: Path) -> None:
    # Neither inline code nor a top-level source: this is invalid smoke metadata
    # (a fixture bug), so it must raise rather than be reported as a compat
    # outcome. The compiler is never reached, so a stub name is fine.
    spec = SourceSmokeSpec(source=None, v1=SourceSmokeSide(code=None))

    with pytest.raises(ValueError, match="either code or top-level source"):
        run_source_smoke(spec, case_dir=tmp_path, work_dir=tmp_path / "work", compiler="true")


def test_unsupported_mode_raises(tmp_path: Path) -> None:
    spec = SourceSmokeSpec(
        mode="teleport",  # type: ignore[arg-type]
        v1=SourceSmokeSide(code="int main() {}\n"),
    )

    with pytest.raises(ValueError, match="unsupported source_smoke mode"):
        run_source_smoke(spec, case_dir=tmp_path, work_dir=tmp_path / "work", compiler="true")


def test_run_mode_checks_runtime_exit_code(tmp_path: Path) -> None:
    # "run" mode links and then *executes*: a non-zero process exit is a failure
    # even though both sides compile+link cleanly. v1 exits 0 (success expected),
    # v2 exits non-zero (failure expected) — both predictions must hold.
    case = tmp_path / "case"
    case.mkdir()
    (case / "v1.cpp").write_text("int payload() { return 0; }\n", encoding="utf-8")
    (case / "v2.cpp").write_text("int payload() { return 3; }\n", encoding="utf-8")
    app = "int payload();\nint main() { return payload(); }\n"

    spec = SourceSmokeSpec(
        mode="run",
        proof="runtime exit code",
        v1=SourceSmokeSide(code=app, lib_source="v1.cpp", expect="success"),
        v2=SourceSmokeSide(code=app, lib_source="v2.cpp", expect="failure"),
    )

    result = run_source_smoke(spec, case_dir=case, work_dir=tmp_path / "work", compiler=_cxx())

    assert result.ok


def test_timeout_is_treated_as_a_failure(tmp_path: Path) -> None:
    # A hanging program under "run" mode must surface as a (timed-out) failure,
    # not hang the whole check. v1 loops forever, so its expected-success
    # prediction is violated and the failure records the timeout.
    case = tmp_path / "case"
    case.mkdir()
    (case / "v1.cpp").write_text(
        "int payload() { for (;;) {} return 0; }\n", encoding="utf-8"
    )
    app = "int payload();\nint main() { return payload(); }\n"

    spec = SourceSmokeSpec(
        mode="run",
        proof="timeout",
        v1=SourceSmokeSide(code=app, lib_source="v1.cpp", expect="success"),
        v2=SourceSmokeSide(code=app, lib_source="v1.cpp", expect="failure"),
    )

    result = run_source_smoke(
        spec, case_dir=case, work_dir=tmp_path / "work", compiler=_cxx(), timeout=2.0
    )

    assert not result.ok
    assert any("timed out" in failure for failure in result.failures)


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

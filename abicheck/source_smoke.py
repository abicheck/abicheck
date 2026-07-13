# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Consumer source-smoke proof helpers for source-only API hazards.

Some compatibility risks are deliberately outside a producer binary/header
snapshot: macro-conditioned consumer API, C++20 concept constraint tightening,
and overload-resolution ambiguity at downstream call sites.  This module keeps
that proof lane in the library (not only in the examples runner) so callers can
exercise the same compile/link checks from tests, validation, or future CLI
wiring.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

SmokeMode = Literal["syntax", "link", "run"]
SmokeExpectation = Literal["success", "failure"]


@dataclass(frozen=True)
class SourceSmokeSide:
    """One side of a consumer source smoke check."""

    expect: SmokeExpectation = "success"
    code: str | None = None
    replace: tuple[tuple[str, str], ...] = ()
    lib_source: str | None = None
    mode: SmokeMode | None = None


@dataclass(frozen=True)
class SourceSmokeSpec:
    """Declarative compile/link proof for a source-only compatibility hazard."""

    source: str | None = None
    standard: str = "c++17"
    mode: SmokeMode = "syntax"
    proof: str = "source smoke"
    v1: SourceSmokeSide = field(default_factory=SourceSmokeSide)
    v2: SourceSmokeSide = field(default_factory=SourceSmokeSide)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceSmokeSpec:
        def side(name: str) -> SourceSmokeSide:
            data: dict[str, Any] = raw.get(name) or {}
            return SourceSmokeSide(
                expect=cast(SmokeExpectation, data.get("expect", "success")),
                code=data.get("code"),
                replace=tuple(tuple(pair) for pair in data.get("replace", ())),
                lib_source=data.get("lib_source"),
                mode=cast(SmokeMode | None, data.get("mode")),
            )

        return cls(
            source=raw.get("source"),
            standard=raw.get("standard", "c++17"),
            mode=cast(SmokeMode, raw.get("mode", "syntax")),
            proof=raw.get("proof", "source smoke"),
            v1=side("v1"),
            v2=side("v2"),
        )


@dataclass(frozen=True)
class SourceSmokeResult:
    ok: bool
    proof: str
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Backward-compatible alias for callers that used the old result field."""
        return self.ok


def _side_code(spec: SourceSmokeSpec, side: SourceSmokeSide, case_dir: Path) -> str:
    if side.code is not None:
        return side.code
    if spec.source is None:
        raise ValueError("source_smoke side needs either code or top-level source")
    code = (case_dir / spec.source).read_text(encoding="utf-8")
    for old, new in side.replace:
        code = code.replace(old, new)
    return code


def _process_error_detail(proc: subprocess.CompletedProcess[str]) -> list[str]:
    """Extract diagnostic lines from a finished compile/link/run process.

    ``stderr or stdout`` (picking exactly one stream) can silently drop the
    real error: cl.exe often puts its banner on stdout and the actual
    diagnostic on stderr, or vice versa depending on the failure stage, and a
    4-line cap can cut off the message before the useful part (CMake's own
    "not able to compile a simple test program" continues well past that).
    Combine both streams and keep more lines.
    """
    combined = "\n".join(s for s in (proc.stderr, proc.stdout) if s).strip()
    return combined.splitlines()[:20]


def run_source_smoke(
    spec: SourceSmokeSpec,
    *,
    case_dir: Path,
    work_dir: Path,
    compiler: str,
    timeout: float = 60.0,
) -> SourceSmokeResult:
    """Run a two-sided consumer compile/link smoke check.

    Returns a structured result instead of raising for expected compiler
    failures.  Invalid smoke metadata raises ``ValueError`` because that is a
    test/fixture bug, not a compatibility outcome.
    """

    work_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    source_suffix = ".cpp" if spec.standard.startswith("c++") else ".c"
    for label, side in (("v1", spec.v1), ("v2", spec.v2)):
        code = _side_code(spec, side, case_dir)
        # Named distinctly from lib_source (conventionally also "v1"/"v2" +
        # extension, e.g. "v1.cpp"): cl.exe writes each input's .obj to the
        # CWD using the source's own basename, with no per-invocation
        # disambiguation the way GCC/Clang's internal temp-object naming
        # provides — compiling case_dir/v1.cpp and work_dir/v1.cpp together
        # collides on "v1.obj", silently dropping one (LNK4042 "object
        # specified more than once"), which then surfaces as a baffling
        # LNK1561 "entry point must be defined" once the survivor isn't the
        # one with main().
        src = work_dir / f"{label}_consumer{source_suffix}"
        src.write_text(code, encoding="utf-8")
        mode = side.mode or spec.mode
        exe = work_dir / f"{label}.out"
        # MSVC's cl.exe doesn't understand GCC/Clang flag syntax at all (it
        # warns D9002/D9035 and ignores -std=/-o rather than acting on them,
        # so the smoke compile silently doesn't do what's expected).
        is_msvc = Path(compiler).stem.lower() == "cl"
        # /EHsc: standard C++ exception-unwind semantics. Without it cl.exe
        # still compiles try/catch code (just a C4530 warning), but the
        # generated unwind behavior is unreliable — not what "the same code
        # compiles the same way under a different compiler" is meant to prove.
        msvc_cxx_flags = ["/EHsc"] if spec.standard.startswith("c++") else []
        if mode == "syntax":
            if is_msvc:
                cmd = [
                    compiler, f"/std:{spec.standard}", *msvc_cxx_flags,
                    f"/I{case_dir}", "/Zs", str(src),
                ]
            else:
                cmd = [compiler, f"-std={spec.standard}", "-I", str(case_dir), "-fsyntax-only", str(src)]
        elif mode in {"link", "run"}:
            lib_source = case_dir / (side.lib_source or f"{label}.cpp")
            if is_msvc:
                cmd = [
                    compiler, f"/std:{spec.standard}", *msvc_cxx_flags, f"/I{case_dir}",
                    str(lib_source), str(src), f"/Fe:{exe}",
                ]
            else:
                cmd = [compiler, f"-std={spec.standard}", "-I", str(case_dir), str(lib_source), str(src), "-o", str(exe)]
        else:
            raise ValueError(f"unsupported source_smoke mode: {mode}")

        try:
            # cwd=work_dir keeps cl.exe's default-named .obj droppings (it has
            # no -fsyntax-only equivalent that skips them for /Zs, and link
            # mode always writes one per source) inside the per-test tmp dir
            # instead of the shared process CWD, where parallel (-n auto)
            # workers running this smoke check concurrently could collide.
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, cwd=str(work_dir),
            )
            compiled = proc.returncode == 0
            detail = _process_error_detail(proc)
            if compiled and mode == "run":
                run_proc = subprocess.run(
                    [str(exe)], capture_output=True, text=True, timeout=timeout,
                    cwd=str(work_dir),
                )
                compiled = run_proc.returncode == 0
                detail = _process_error_detail(run_proc)
        except subprocess.TimeoutExpired as exc:
            compiled = False
            detail = [f"timed out after {exc.timeout}s"]

        want_success = side.expect == "success"
        if compiled != want_success:
            expected_word = "compile/link" if want_success else "fail"
            got_word = "compiled/linked" if compiled else "failed"
            failures.append(f"{label}: expected {expected_word}, got {got_word}: {' | '.join(detail)}")

    return SourceSmokeResult(ok=not failures, proof=spec.proof, failures=tuple(failures))

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
        src = work_dir / f"{label}{source_suffix}"
        src.write_text(code, encoding="utf-8")
        mode = side.mode or spec.mode
        exe = work_dir / f"{label}.out"
        if mode == "syntax":
            cmd = [compiler, f"-std={spec.standard}", "-I", str(case_dir), "-fsyntax-only", str(src)]
        elif mode in {"link", "run"}:
            lib_source = case_dir / (side.lib_source or f"{label}.cpp")
            cmd = [compiler, f"-std={spec.standard}", "-I", str(case_dir), str(lib_source), str(src), "-o", str(exe)]
        else:
            raise ValueError(f"unsupported source_smoke mode: {mode}")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            compiled = proc.returncode == 0
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[:4]
            if compiled and mode == "run":
                run_proc = subprocess.run([str(exe)], capture_output=True, text=True, timeout=timeout)
                compiled = run_proc.returncode == 0
                detail = (run_proc.stderr or run_proc.stdout or "").strip().splitlines()[:4]
        except subprocess.TimeoutExpired as exc:
            compiled = False
            detail = [f"timed out after {exc.timeout}s"]

        want_success = side.expect == "success"
        if compiled != want_success:
            expected_word = "compile/link" if want_success else "fail"
            got_word = "compiled/linked" if compiled else "failed"
            failures.append(f"{label}: expected {expected_word}, got {got_word}: {' | '.join(detail)}")

    return SourceSmokeResult(ok=not failures, proof=spec.proof, failures=tuple(failures))

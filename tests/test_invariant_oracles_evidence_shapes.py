"""Invariant A across evidence shapes: absent evidence must not read as clean.

``tests/test_invariant_oracles_reduced_evidence.py`` pins the stripped pair's
JSON verdict and its terminal and Markdown wording. This module covers the
rest of the evidence axis on the same small C library pair (``add`` gains a
parameter), through the public CLI:

* **DWARF, no headers** (controls): a break is seen (``complete``, exit 4),
  and a pair with no ABI change reads ``NO_CHANGE`` with exit 0 and
  ``complete`` assurance. The oracles must stay silent on a clean claim the
  evidence supports.
* **failed extractor**: the header parser rejects the header, or castxml is
  not on ``PATH``. The run must stop with an error, not compare an empty
  surface.
* **mixed**: old side with DWARF, new side stripped (``partial``).
* **requested depth unmet**: stripped pair, ``--depth headers``, no headers
  (``failed``).
* **stripped**: the stripped pair's exit code, version bump, and review and
  HTML wording, which the reduced-evidence module does not check.

Known gap: the mixed, unmet-depth and stripped runs read ``NO_CHANGE`` or
``COMPATIBLE`` with exit 0. The strict xfails state the ADR-090 contract
(verdict ``UNCONFIRMED``, version bump ``undetermined``, exit 1) as separate
checks, because ADR-090 ships the label before the exit change; each flips
when its stage lands.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env
from tests.oracles.absence import (
    absence_violations,
    gate_violations,
    rendered_absence_violations,
    verdict_violations,
    version_bump_violations,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or shutil.which("gcc") is None,
        reason="builds ELF libraries with gcc on Linux",
    ),
    pytest.mark.skipif(
        shutil.which("strip") is None,
        reason="strip is required to build the stripped library",
    ),
]

_V1 = "int add(int a, int b) { return a + b; }\nint keep(void) { return 1; }\n"
_V2 = (
    "int add(int a, int b, int c) { return a + b + c; }\nint keep(void) { return 1; }\n"
)
#: Same ABI as ``_V1``; only a function body changes.
_V1_REBUILT = "int add(int a, int b) { return b + a; }\nint keep(void) { return 1; }\n"
_BAD_HEADER = "int add(int a, int b\n"

_GAP = (
    "known gap: a compare without type-level evidence on a side reads NO_CHANGE or "
    "COMPATIBLE with exit 0 and an accepted headline (ADR-090 proposes UNCONFIRMED, exit 1)"
)

#: Reduced-evidence runs whose JSON verdict this module checks. The stripped
#: pair's verdict is the reduced-evidence module's.
_NEW_SHAPES = ("mixed", "depth_unmet")
#: Every reduced-evidence run that currently reads as clean.
_GAPPED = ("stripped", *_NEW_SHAPES)

_ALL_RENDERERS = frozenset({"terminal", "markdown", "review", "html"})
#: The renderers each gapped run is judged on here. The stripped pair's
#: terminal and Markdown wording is checked by the reduced-evidence module.
_RENDERERS = {
    "stripped": frozenset({"review", "html"}),
    "mixed": _ALL_RENDERERS,
    "depth_unmet": _ALL_RENDERERS,
}

#: Rendered formats each run writes as files; the terminal is stdout.
_FILE_FORMATS = {"json": "json", "markdown": "md", "review": "md", "html": "html"}


@dataclass(frozen=True)
class _Run:
    exit_code: int
    stdout: str
    stderr: str
    files: dict[str, Path]

    def report(self) -> dict | None:
        path = self.files["json"]
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def rendered(self) -> dict[str, str]:
        texts = {"terminal": self.stdout}
        for fmt in ("markdown", "review", "html"):
            if self.files[fmt].exists():
                texts[fmt] = self.files[fmt].read_text(encoding="utf-8")
        return texts


def _gcc(tmp: Path, name: str, src: str, *, strip: bool) -> Path:
    (tmp / f"{name}.c").write_text(src)
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-g", "-o", str(lib), str(tmp / f"{name}.c")],
        check=True,
        capture_output=True,
    )
    if strip:
        subprocess.run(["strip", str(lib)], check=True)
    return lib


def _compare(
    tmp: Path, tag: str, *args: str, env: dict[str, str] | None = None
) -> _Run:
    out = tmp / tag
    out.mkdir()
    files = {fmt: out / f"{fmt}.{ext}" for fmt, ext in _FILE_FORMATS.items()}
    outputs = [x for fmt, path in files.items() for x in ("-o", f"{fmt}={path}")]
    proc = subprocess.run(
        abicheck_command("compare", *args, *outputs, "-o", "terminal=-"),
        capture_output=True,
        text=True,
        timeout=600,
        env=env or abicheck_env(),
        # A scratch directory, so no .abicheck.yml (e.g. compile.frontend:
        # clang) from the invoking checkout changes which extractor runs.
        cwd=out,
    )
    return _Run(proc.returncode, proc.stdout, proc.stderr, files)


def _without_castxml() -> dict[str, str]:
    """``abicheck_env()`` with every ``PATH`` entry holding castxml removed."""
    env = abicheck_env()
    env.pop("ABICHECK_AST_FRONTEND", None)
    env["PATH"] = os.pathsep.join(
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if not (Path(entry) / "castxml").exists()
    )
    return env


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, _Run]:
    tmp = tmp_path_factory.mktemp("evidence_shapes")
    v1 = _gcc(tmp, "v1", _V1, strip=False)
    v2 = _gcc(tmp, "v2", _V2, strip=False)
    v1r = _gcc(tmp, "v1r", _V1_REBUILT, strip=False)
    s1 = _gcc(tmp, "v1s", _V1, strip=True)
    s2 = _gcc(tmp, "v2s", _V2, strip=True)
    (tmp / "bad_old.h").write_text(_BAD_HEADER)
    (tmp / "bad_new.h").write_text(_BAD_HEADER)
    (tmp / "v1.h").write_text("int add(int a, int b);\nint keep(void);\n")
    (tmp / "v2.h").write_text("int add(int a, int b, int c);\nint keep(void);\n")
    return {
        "dwarf_break": _compare(tmp, "dwarf_break", str(v1), str(v2)),
        "dwarf_no_change": _compare(tmp, "dwarf_no_change", str(v1), str(v1r)),
        "stripped": _compare(tmp, "stripped", str(s1), str(s2)),
        "mixed": _compare(tmp, "mixed", str(v1), str(s2)),
        "depth_unmet": _compare(
            tmp, "depth_unmet", str(s1), str(s2), "--depth", "headers"
        ),
        "header_parse_failed": _compare(
            tmp,
            "header_parse_failed",
            str(s1),
            str(s2),
            "-H",
            f"old={tmp / 'bad_old.h'}",
            "-H",
            f"new={tmp / 'bad_new.h'}",
        ),
        "castxml_missing": _compare(
            tmp,
            "castxml_missing",
            str(s1),
            str(s2),
            "-H",
            f"old={tmp / 'v1.h'}",
            "-H",
            f"new={tmp / 'v2.h'}",
            env=_without_castxml(),
        ),
    }


# -- controls: type-level evidence present -------------------------------------


def test_dwarf_without_headers_reaches_a_complete_breaking_result(
    runs: dict[str, _Run],
) -> None:
    """Control: DWARF alone is type-level evidence, so the break is seen and gated."""
    run = runs["dwarf_break"]
    report = run.report()
    assert report is not None
    assert report["verdict"] == "BREAKING"
    assert report["analysis_assurance"]["status"] == "complete"
    assert "func_params_changed" in {c["kind"] for c in report["changes"]}
    assert run.exit_code == 4


def test_dwarf_without_headers_may_state_a_supported_clean_result(
    runs: dict[str, _Run],
) -> None:
    """Control: a real no-change pair with DWARF reads clean, and the oracles accept it."""
    run = runs["dwarf_no_change"]
    report = run.report()
    assert report is not None
    status = report["analysis_assurance"]["status"]
    assert (report["verdict"], status, run.exit_code) == ("NO_CHANGE", "complete", 0)
    assert absence_violations(report) == []
    assert gate_violations(run.exit_code, status) == []
    rendered = run.rendered()
    assert set(rendered) == _ALL_RENDERERS
    assert rendered_absence_violations(rendered, status) == []


# -- failed extractor: an error, never an empty surface ------------------------


@pytest.mark.parametrize(
    ("tag", "cause"),
    [
        ("header_parse_failed", "header AST parse failed"),
        ("castxml_missing", "castxml not found"),
    ],
)
def test_failed_extractor_stops_the_run_instead_of_comparing_an_empty_surface(
    tag: str, cause: str, runs: dict[str, _Run]
) -> None:
    """A header extractor that fails or is unavailable is an error, not a clean pass.

    The run exits with an error naming the cause and states no clean claim.
    If it writes a JSON report at all, that report must carry the failure
    (``failed`` assurance) and satisfy invariant A.
    """
    run = runs[tag]
    assert run.exit_code not in (0, 2, 4), (tag, run.exit_code, run.stderr[-1500:])
    assert cause in run.stderr, (tag, run.stderr[-1500:])
    report = run.report()
    if report is not None:
        assert report["analysis_assurance"]["status"] == "failed", tag
        assert absence_violations(report) == [], tag
    assert rendered_absence_violations(run.rendered(), "failed") == [], tag


# -- the gap: reduced evidence reads as clean ----------------------------------


@pytest.mark.known_gap_canary
@pytest.mark.parametrize(
    ("tag", "verdict", "status", "bump"),
    [
        ("mixed", "COMPATIBLE", "partial", "patch"),
        ("depth_unmet", "NO_CHANGE", "failed", "none"),
    ],
)
def test_one_sided_and_unmet_depth_gap_is_still_the_current_answer(
    tag: str, verdict: str, status: str, bump: str, runs: dict[str, _Run]
) -> None:
    """Canary: each run's gapped answer (a clean verdict, exit 0) while the gap is open."""
    run = runs[tag]
    report = run.report()
    assert report is not None
    assert report["verdict"] == verdict
    assert report["analysis_assurance"]["status"] == status
    assert report["release_recommendation"]["version_bump"] == bump
    assert "func_params_changed" not in {c["kind"] for c in report["changes"]}
    assert run.exit_code == 0


@pytest.mark.parametrize("tag", _NEW_SHAPES)
@pytest.mark.xfail(strict=True, reason=_GAP)
def test_reduced_evidence_verdict_does_not_claim_compatibility(
    tag: str, runs: dict[str, _Run]
) -> None:
    """Invariant A on the JSON verdict (ADR-090: UNCONFIRMED)."""
    report = runs[tag].report()
    assert report is not None
    assert verdict_violations(report) == []


@pytest.mark.parametrize("tag", _GAPPED)
@pytest.mark.xfail(strict=True, reason=_GAP)
def test_reduced_evidence_version_bump_does_not_presume_compatibility(
    tag: str, runs: dict[str, _Run]
) -> None:
    """Invariant A on the release recommendation (ADR-090: undetermined)."""
    report = runs[tag].report()
    assert report is not None
    assert version_bump_violations(report) == []


@pytest.mark.parametrize("tag", _GAPPED)
@pytest.mark.xfail(strict=True, reason=_GAP)
def test_reduced_evidence_run_does_not_pass_the_gate_with_exit_0(
    tag: str, runs: dict[str, _Run]
) -> None:
    """Invariant A on the exit code: partial or failed evidence never exits 0 (ADR-090: exit 1)."""
    run = runs[tag]
    report = run.report()
    assert report is not None
    assert gate_violations(run.exit_code, report["analysis_assurance"]["status"]) == []


@pytest.mark.parametrize("tag", _GAPPED)
@pytest.mark.xfail(strict=True, reason=_GAP)
def test_reduced_evidence_renderers_do_not_state_acceptance(
    tag: str, runs: dict[str, _Run]
) -> None:
    """Invariant A on the rendered wording (review and HTML for every run, all four for the new shapes)."""
    run = runs[tag]
    report = run.report()
    assert report is not None
    rendered = {
        name: text for name, text in run.rendered().items() if name in _RENDERERS[tag]
    }
    assert set(rendered) == _RENDERERS[tag]
    assert (
        rendered_absence_violations(rendered, report["analysis_assurance"]["status"])
        == []
    )

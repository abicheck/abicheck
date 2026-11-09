"""Invariant A on reduced-evidence runs: absent evidence must not read as clean.

The catalog runs in the other invariant modules all carry ``complete`` analysis
assurance, so invariant A cannot fire on them. This module builds a pair whose
evidence is genuinely reduced (the new library stripped, compared with no
headers) and checks the public CLI output of that run.

The unstripped pair is the control: it must reach a complete, breaking result,
so the reduced pair's assertions are not vacuous.

Fixed by ADR-090: the stripped pair used to compare as ``NO_CHANGE`` with
``partial`` assurance, exit 0 and ``Gate: ACCEPTED``. It now reads
``UNCONFIRMED`` with exit 1 on every surface. The former canary is the
regression test of that answer, and the two invariant tests are no longer
expected to fail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env
from tests.oracles.absence import absence_violations, rendered_absence_violations

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


def _build(tmp: Path) -> tuple[Path, Path, Path, Path]:
    """Return the unstripped pair and a stripped copy of each."""
    for name, src in (("v1", _V1), ("v2", _V2)):
        (tmp / f"{name}.c").write_text(src)
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-g",
                "-o",
                str(tmp / f"lib{name}.so"),
                str(tmp / f"{name}.c"),
            ],
            check=True,
            capture_output=True,
        )
        shutil.copy(tmp / f"lib{name}.so", tmp / f"lib{name}_stripped.so")
        subprocess.run(["strip", str(tmp / f"lib{name}_stripped.so")], check=True)
    return (
        tmp / "libv1.so",
        tmp / "libv2.so",
        tmp / "libv1_stripped.so",
        tmp / "libv2_stripped.so",
    )


def _compare(tmp: Path, old: Path, new: Path, tag: str) -> tuple[int, dict, str, str]:
    """Run the public CLI compare; return (exit code, JSON report, terminal, Markdown)."""
    out_json = tmp / f"{tag}.json"
    out_md = tmp / f"{tag}.md"
    proc = subprocess.run(
        abicheck_command(
            "compare",
            str(old),
            str(new),
            "-o",
            f"json={out_json}",
            "-o",
            f"markdown={out_md}",
            "-o",
            "terminal=-",
        ),
        capture_output=True,
        text=True,
        timeout=600,
        env=abicheck_env(),
    )
    report = json.loads(out_json.read_text(encoding="utf-8"))
    return proc.returncode, report, proc.stdout, out_md.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pairs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[int, dict, str, str]]:
    tmp = tmp_path_factory.mktemp("reduced_evidence")
    v1, v2, s1, s2 = _build(tmp)
    return {
        "control": _compare(tmp, v1, v2, "control"),
        "stripped": _compare(tmp, s1, s2, "stripped"),
    }


def test_unstripped_control_reaches_a_complete_breaking_result(
    pairs: dict[str, tuple[int, dict, str, str]],
) -> None:
    """Control: the scenario is sensitive, so the reduced pair's answer means something."""
    exit_code, report, _, _ = pairs["control"]
    assert report["verdict"] == "BREAKING"
    assert report["analysis_assurance"]["status"] == "complete"
    assert exit_code == 4


def test_stripped_pair_reads_unconfirmed_and_exits_one(
    pairs: dict[str, tuple[int, dict, str, str]],
) -> None:
    """ADR-090: the reduced run is UNCONFIRMED, exit 1, with the observed
    verdict kept and both sides named as lacking type-level evidence."""
    exit_code, report, terminal, _ = pairs["stripped"]
    assert report["verdict"] == "UNCONFIRMED"
    assert report["analysis_assurance"]["status"] == "partial"
    assert report["unconfirmed"]["observed_verdict"] == "NO_CHANGE"
    assert report["unconfirmed"]["sides_without_type_evidence"] == ["old", "new"]
    assert report["release_recommendation"]["version_bump"] == "undetermined"
    assert exit_code == 1
    assert "Gate: REJECTED (exit 1)" in terminal


def test_stripped_pair_does_not_claim_compatibility_on_partial_evidence(
    pairs: dict[str, tuple[int, dict, str, str]],
) -> None:
    """Invariant A on the JSON verdict."""
    _, report, _, _ = pairs["stripped"]
    assert absence_violations(report) == []


def test_stripped_pair_renderers_do_not_state_acceptance(
    pairs: dict[str, tuple[int, dict, str, str]],
) -> None:
    """Invariant A on the rendered wording, in the terminal and the Markdown report."""
    _, report, terminal, markdown = pairs["stripped"]
    status = report["analysis_assurance"]["status"]
    assert (
        rendered_absence_violations(
            {"terminal": terminal, "markdown": markdown}, status
        )
        == []
    )

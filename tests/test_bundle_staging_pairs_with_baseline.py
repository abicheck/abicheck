"""A bundle cell's staged candidates pair with the accepted-main baseline set.

Bug class: the two halves of a bundle comparison are laid out by two
different producers -- ``actions/baseline/run.sh`` stores every member binary
as ``binaries/<member name>``, and ``check-project.yml``'s "Resolve candidate
binary/binaries" step stages the candidate side into ``bundle-staging/`` --
and ``compare DIR DIR`` pairs members by file name. When the candidate step
staged by artifact basename (``libcore.so``) against a baseline ``core``,
nothing paired: every member read ``not_supplied`` and every bundle cell
failed SCOPE_INCOMPLETE (exit 1). Reported from abicheck/integration-lab#34.

Oracle: the real CLI's own ``comparison_scope`` -- the baseline side is laid
out exactly as ``actions/baseline/run.sh`` stages it (its ``binaries/$name``
copy line is asserted to still be the contract), the candidate side is
produced by executing the workflow step itself, and ``abicheck compare``
must pair every member with nothing unchecked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _abicheck_subprocess import abicheck_command
from _workflow_exec import bash_executable, require_bash

from tests.test_reusable_workflows import CHECK_PROJECT, _load, _steps

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects"),
]

REPO = Path(__file__).resolve().parent.parent
BASELINE_RUN_SH = REPO / "actions" / "baseline" / "run.sh"

#: Member name -> artifact basename. Deliberately different, as in every
#: real project (target ``core`` builds ``libcore.so``).
MEMBERS = {"core": "libcore.so", "math": "libmath.so", "strings": "libstrings.so"}


def _resolver_script() -> str:
    steps = _steps(_load(CHECK_PROJECT)["jobs"]["check"])
    return next(
        s for s in steps if s.get("name") == "Resolve candidate binary/binaries"
    )["run"]


def _build(cc: str, out: Path, symbol: str) -> None:
    src = out.with_suffix(".c")
    src.write_text(f"int {symbol}(int x) {{ return x + 1; }}\n", encoding="utf-8")
    subprocess.run([cc, "-shared", "-fPIC", str(src), "-o", str(out)], check=True)


def test_baseline_run_sh_still_stages_binaries_by_member_name() -> None:
    assert 'cp "$artifact" "$OUTPUT_DIR/binaries/$name"' in BASELINE_RUN_SH.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("member_count", [1, 2, 3])
def test_staged_bundle_pairs_every_member_with_the_baseline(
    tmp_path: Path, member_count: int
) -> None:
    cc = shutil.which("gcc") or shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")  # marker-level: integration lane has gcc
    require_bash()
    members = dict(list(MEMBERS.items())[:member_count])

    candidate = tmp_path / "candidate" / "lib"
    candidate.mkdir(parents=True)
    baseline = tmp_path / "baseline" / "binaries"
    baseline.mkdir(parents=True)
    for name, artifact in members.items():
        _build(cc, candidate / artifact, f"{name}_api")
        shutil.copy2(
            candidate / artifact, baseline / name
        )  # actions/baseline/run.sh layout

    matrix = {
        "kind": "bundle",
        "name": "sdk",
        "member_binary_patterns": {n: f"lib/{a}" for n, a in members.items()},
    }
    github_output = tmp_path / "github_output"
    github_output.write_text("", encoding="utf-8")
    staged = subprocess.run(
        [bash_executable(), "-c", _resolver_script()],
        cwd=tmp_path,
        env={
            **os.environ,
            "MATRIX_JSON": json.dumps(matrix),
            "GITHUB_OUTPUT": str(github_output),
        },
        capture_output=True,
        text=True,
    )
    assert staged.returncode == 0, staged.stderr
    assert "new-library=bundle-staging" in github_output.read_text(encoding="utf-8")

    report = tmp_path / "report.json"
    result = subprocess.run(
        abicheck_command(
            "compare",
            str(baseline),
            str(tmp_path / "bundle-staging"),
            "--depth",
            "binary",
            "-o",
            f"json={report}",
        ),
        capture_output=True,
        text=True,
    )
    scope = json.loads(report.read_text(encoding="utf-8"))["comparison_scope"]
    assert scope["unchecked"] == [], (scope["unchecked"], result.stderr)
    assert scope["completeness"] == "complete"
    assert sorted(
        m["member"] for m in scope["members"] if m["state"] == "available"
    ) == sorted(members)
    assert result.returncode == 0, result.stderr

"""``action/run.sh``'s own annotation renderer (CLI cleanup phase two, PR E).

The Action now emits GitHub Actions workflow-command annotations
(``::error``/``::warning``/``::notice``) itself, reading the persisted
``annotations`` array (report schema 2.43/2.44) off whichever JSON report
the run produced -- instead of asking ``abicheck compare --annotate`` to
render them to its own stderr. Driven through the real ``run.sh``: once
against a fake ``abicheck`` on ``$PATH`` whose own stdout is a
hand-crafted JSON report (proving the renderer's own filtering/gating
logic in isolation from the real CLI), and once against the real
``abicheck`` binary with a genuine breaking-change pair (the end-to-end
proof that a real persisted report's annotations actually reach the
Action's own log output).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_REAL_ABICHECK = shutil.which("abicheck")


def _run_compare(
    tmp_path: Path,
    env_extra: dict[str, str],
    *,
    stub_report: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run compare mode; *stub_report* becomes the fake abicheck's stdout."""
    old_json = tmp_path / "old.json"
    new_json = tmp_path / "new.json"
    old_json.write_text("{}", encoding="utf-8")
    new_json.write_text("{}", encoding="utf-8")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    stub = fake_bin / "abicheck"
    payload = json.dumps(stub_report if stub_report is not None else {"verdict": "COMPATIBLE"})
    stub.write_text(
        "#!/usr/bin/env bash\n" f"cat <<'STUBJSON'\n{payload}\nSTUBJSON\n" "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old_json),
        "INPUT_NEW_LIBRARY": str(new_json),
        "INPUT_FORMAT": "json",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **env_extra,
    }
    return subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
    )


_REPORT_WITH_ANNOTATIONS = {
    "verdict": "BREAKING",
    "annotations": [
        {
            "level": "error",
            "annotation": "::error title=ABI Break::function foo removed",
            "always_visible": True,
        },
        {
            "level": "notice",
            "annotation": "::notice title=ABI Addition::function bar added",
            "always_visible": False,
        },
        {
            "level": "notice",
            "annotation": "::notice title=Not evaluated (contract)::baz",
            "always_visible": True,
        },
    ],
}


def _emitted_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Every exact stdout/stderr line starting with a workflow-command
    sigil (``::error``/``::warning``/``::notice``) -- what the renderer
    itself printed, as opposed to a byte range of the raw JSON dump that
    happens to contain the same text."""
    lines = (result.stdout + result.stderr).splitlines()
    return [ln for ln in lines if ln.startswith(("::error ", "::warning ", "::notice "))]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAnnotateRendererReadsThePersistedReport:
    def test_annotate_true_emits_the_always_visible_entries(
        self, tmp_path: Path
    ) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # Exact-line membership, not substring containment: FORMAT=json's
        # own primary output is the raw report text, which already contains
        # every annotation string embedded inside its JSON encoding --
        # a substring check can't tell "the renderer echoed this as its own
        # workflow-command line" apart from "this text happens to appear
        # somewhere in the JSON dump".
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::function foo removed" in lines
        assert "::notice title=Not evaluated (contract)::baz" in lines
        # The opt-in addition notice must NOT appear without annotate-additions.
        assert "::notice title=ABI Addition::function bar added" not in lines

    def test_annotate_additions_includes_the_opt_in_notice(
        self, tmp_path: Path
    ) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true", "INPUT_ANNOTATE_ADDITIONS": "true"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "::notice title=ABI Addition::function bar added"
            in _emitted_lines(result)
        )

    def test_annotate_false_emits_nothing(self, tmp_path: Path) -> None:
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "false"},
            stub_report=_REPORT_WITH_ANNOTATIONS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _emitted_lines(result) == []

    def test_release_shape_flattens_across_libraries(self, tmp_path: Path) -> None:
        release_report = {
            "verdict": "BREAKING",
            "libraries": [
                {
                    "library": "liba.so",
                    "annotations": [
                        {
                            "level": "error",
                            "annotation": "::error title=ABI Break::liba broke",
                            "always_visible": True,
                        }
                    ],
                },
                {
                    "library": "libb.so",
                    "annotations": [
                        {
                            "level": "warning",
                            "annotation": "::warning title=API Break::libb warned",
                            "always_visible": True,
                        }
                    ],
                },
            ],
        }
        result = _run_compare(
            tmp_path, {"INPUT_ANNOTATE": "true"}, stub_report=release_report
        )
        assert result.returncode == 0, result.stdout + result.stderr
        lines = _emitted_lines(result)
        assert "::error title=ABI Break::liba broke" in lines
        assert "::warning title=API Break::libb warned" in lines

    def test_no_annotations_key_is_a_quiet_no_op(self, tmp_path: Path) -> None:
        # A scan-shaped (or pre-2.43) report carries no `annotations` at
        # all -- must not error, just print nothing.
        result = _run_compare(
            tmp_path,
            {"INPUT_ANNOTATE": "true"},
            stub_report={"verdict": "COMPATIBLE"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _emitted_lines(result) == []


@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or _REAL_ABICHECK is None
    or not RUN_SH.is_file(),
    reason="Linux-only (matches test_action_baseline.py's own real-abicheck "
    "end-to-end precedent) with a real abicheck binary and action/run.sh",
)
class TestRealAbicheckAnnotationsReachTheActionLog:
    """The genuinely end-to-end proof: real `abicheck`, a real breaking
    change, real persisted annotations, actually echoed by the real
    `run.sh` -- not a stub that could pass regardless of what the
    renderer's own logic does wrong.
    """

    def test_a_real_breaking_change_is_annotated(self, tmp_path: Path) -> None:
        from abicheck.model import AbiSnapshot, Function, Visibility
        from abicheck.serialization import snapshot_to_json

        def _fn(name: str, mangled: str) -> Function:
            return Function(
                name=name, mangled=mangled, return_type="void",
                visibility=Visibility.PUBLIC,
            )

        old_snap = AbiSnapshot(
            library="libfoo.so", version="1.0",
            functions=[_fn("foo", "_Z3foov"), _fn("bar", "_Z3barv")],
            from_headers=True,
        )
        new_snap = AbiSnapshot(
            library="libfoo.so", version="2.0",
            functions=[_fn("foo", "_Z3foov")],
            from_headers=True,
        )
        old_json = tmp_path / "old.json"
        new_json = tmp_path / "new.json"
        old_json.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_json.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env = {
            **base_env,
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": str(old_json),
            "INPUT_NEW_LIBRARY": str(new_json),
            "INPUT_FORMAT": "markdown",
            "INPUT_ADD_JOB_SUMMARY": "false",
            "INPUT_PR_COMMENT": "false",
            "INPUT_ANNOTATE": "true",
            "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        }
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True, text=True, env=env, cwd=tmp_path, check=False,
        )
        combined = result.stdout + result.stderr
        assert "::error" in combined, combined
        assert "bar" in combined, combined

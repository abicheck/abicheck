"""Issue #1714: a caller export in `extra-args` must not drop the Action's own
report (the one the step summary, PR comment and `report-path` read).

Oracle: the CLI receives the Action's own `FORMAT=DEST` export (unless the
caller claims that exact destination) plus every caller export, each exactly
once -- never two exports to one destination.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_action_run_sh_compare_pr_json_write import RUN_SH, _compare_argv


def _exports(argv: str) -> list[str]:
    """Every user-visible `-o FORMAT=DEST` operand (the internal PR-JSON
    sidecar, a fresh mktemp path, is excluded)."""
    toks = argv.split()
    out: list[str] = []
    for i, tok in enumerate(toks):
        if tok == "-o" and i + 1 < len(toks):
            out.append(toks[i + 1])
        elif tok.startswith("--output="):
            out.append(tok.removeprefix("--output="))
    return [e for e in out if "abicheck-pr-json" not in e]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestOwnReportSurvivesCallerExports:
    @pytest.mark.parametrize(
        ("format_", "output_file", "extra", "expected"),
        [
            # no extra output
            ("markdown", "", "", ["markdown=-"]),
            ("markdown", "r.md", "--verbose", ["markdown=r.md"]),
            # extra JSON (both spellings, stdout and file primaries)
            ("markdown", "", "-o json=x.json", ["markdown=-", "json=x.json"]),
            ("markdown", "", "--output=json=x.json", ["markdown=-", "json=x.json"]),
            ("markdown", "r.md", "-o json=x.json", ["markdown=r.md", "json=x.json"]),
            ("html", "", "-o json=x.json", ["html=-", "json=x.json"]),
            # extra markdown to a file
            ("markdown", "", "-o markdown=mine.md", ["markdown=-", "markdown=mine.md"]),
            # both
            (
                "markdown",
                "",
                "-o json=x.json -o markdown=mine.md",
                ["markdown=-", "json=x.json", "markdown=mine.md"],
            ),
            # caller claims our exact destination: theirs wins, no duplicate
            ("markdown", "", "-o json=-", ["json=-"]),
            ("markdown", "r.md", "-o json=r.md", ["json=r.md"]),
        ],
    )
    def test_exports(
        self,
        tmp_path: Path,
        format_: str,
        output_file: str,
        extra: str,
        expected: list[str],
    ) -> None:
        argv = _compare_argv(
            tmp_path,
            {
                "INPUT_FORMAT": format_,
                "INPUT_OUTPUT_FILE": output_file,
                "INPUT_EXTRA_ARGS": extra,
            },
        )
        got = _exports(argv)
        assert sorted(got) == sorted(expected), argv
        dests = [e.split("=", 1)[1] for e in got]
        assert len(dests) == len(set(dests)), argv

    def test_sarif_keeps_its_default_file_beside_a_caller_stdout_json(
        self, tmp_path: Path
    ) -> None:
        argv = _compare_argv(
            tmp_path, {"INPUT_FORMAT": "sarif", "INPUT_EXTRA_ARGS": "-o json=-"}
        )
        assert sorted(_exports(argv)) == ["json=-", "sarif=abicheck-results.sarif"]

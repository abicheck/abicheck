# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Behavioral tests for ``action/run.sh``'s sticky-PR-comment JSON acquisition.

``compare`` mode now renders its PR-comment JSON as a second format from the
*same* comparison run (``--secondary-format json --secondary-output``,
abicheck's own ``--secondary-format`` CLI feature) instead of re-invoking
abicheck a second time. This exercises the acquisition decision in
``_maybe_post_pr_comment`` (extracted verbatim from run.sh, the same "parse
the real file, don't hand-copy it" discipline as
``test_action_run_sh_helpers.py``):

- If ``PR_JSON`` was already populated by the primary run (compare mode, a
  non-json primary format), it's used as-is — no copy, no rerun.
- Otherwise (compare-release/appcompat, which don't build CMD with
  --secondary-format), the original reuse-if-json-else-rerun logic applies
  unchanged.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_FUNCS_START_MARKER = "_can_reuse_primary_json() {"
_FUNCS_END_MARKER = "_maybe_post_pr_comment() {"
_FRAGMENT_START_MARKER = 'echo "::group::abicheck PR comment"'
_FRAGMENT_END_MARKER = '"${PR_CMD_JSON[@]}" >/dev/null 2>/dev/null || true\n  fi'


def _funcs_region() -> str:
    """``_can_reuse_primary_json``/``_build_json_cmd`` — self-contained,
    balanced function defs, extracted verbatim from run.sh."""
    text = RUN_SH.read_text(encoding="utf-8")
    return text[text.index(_FUNCS_START_MARKER):text.index(_FUNCS_END_MARKER)]


def _fragment_region() -> str:
    """The JSON-acquisition if/elif/else from inside ``_maybe_post_pr_comment``
    (balanced on its own) — extracted verbatim from run.sh, without the
    enclosing function's opening brace so it parses as standalone top-level
    statements instead of an unclosed function body."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_FRAGMENT_START_MARKER)
    end = text.index(_FRAGMENT_END_MARKER, start) + len(_FRAGMENT_END_MARKER)
    return text[start:end]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale (GitHub windows-latest runners resolve a bare "bash" to a
    non-functional WSL stub ahead of Git for Windows' real bash).
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run(harness: str) -> None:
    """Run the extracted function defs, then *harness* (which sets up
    PR_JSON/FORMAT/OUTPUT_FILE/CMD), then the extracted acquisition fragment
    that consumes them — harness must execute before the fragment runs."""
    script = _funcs_region() + "\n" + harness + "\n" + _fragment_region()
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )


class TestPrJsonAcquisition:
    def test_prepopulated_pr_json_is_left_untouched(self, tmp_path):
        # Simulates compare mode with a non-json primary format: PR_JSON was
        # already written by the primary run's --secondary-format, so the
        # acquisition block must not overwrite it via cp or a rerun.
        pr_json = tmp_path / "pr.json"
        pr_json.write_text('{"source": "secondary-format"}', encoding="utf-8")
        output_file = tmp_path / "primary.json"
        output_file.write_text('{"source": "primary-output-file"}', encoding="utf-8")
        harness = f"""
PR_JSON={pr_json}
FORMAT=json
OUTPUT_FILE={output_file}
CMD=(abicheck compare old.json new.json --format json -o {output_file})
"""
        _run(harness)
        assert pr_json.read_text(encoding="utf-8") == '{"source": "secondary-format"}'

    def test_falls_back_to_reuse_when_pr_json_empty_and_format_json(self, tmp_path):
        # Simulates compare-release/appcompat (which never populate PR_JSON
        # up front): FORMAT is json, OUTPUT_FILE is a faithful unfiltered
        # report, and no --show-only/--stat is present, so the primary
        # output file is reused instead of rerunning.
        pr_json = tmp_path / "pr.json"
        pr_json.write_text("", encoding="utf-8")
        output_file = tmp_path / "primary.json"
        output_file.write_text('{"source": "primary-output-file"}', encoding="utf-8")
        harness = f"""
PR_JSON={pr_json}
FORMAT=json
OUTPUT_FILE={output_file}
CMD=(abicheck compare old.json new.json --format json -o {output_file})
"""
        _run(harness)
        assert pr_json.read_text(encoding="utf-8") == '{"source": "primary-output-file"}'

    def test_falls_back_to_rerun_when_pr_json_empty_and_not_reusable(self, tmp_path):
        # FORMAT isn't json (or --show-only/--stat is present) and PR_JSON
        # wasn't pre-populated — falls all the way through to _build_json_cmd
        # and a rerun. Stub CMD[0] as a script that writes a sentinel to its
        # last argument (where _build_json_cmd appends "-o $PR_JSON") so the
        # rerun's execution is directly observable.
        pr_json = tmp_path / "pr.json"
        pr_json.write_text("", encoding="utf-8")
        stub = tmp_path / "stub.sh"
        stub.write_text('#!/bin/bash\necho rerun-sentinel > "${@: -1}"\n',
                         encoding="utf-8")
        stub.chmod(0o755)
        harness = f"""
PR_JSON={pr_json}
FORMAT=markdown
OUTPUT_FILE=
CMD=({stub} compare old.json new.json --show-only added --format markdown)
"""
        _run(harness)
        assert pr_json.read_text(encoding="utf-8").strip() == "rerun-sentinel"

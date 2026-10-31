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

"""Pins where the Action writes each report (ADR-091 R11, kept as-is).

ADR-091 R11 originally proposed folding the Action's ``format`` +
``output-file`` pair into one ``output`` list; the dated amendment keeps both
inputs. This module pins the destination of every ``format`` value, with and
without ``output-file``, so a bare ``format: sarif`` keeps writing the file it
writes today. Each cell is observed from the real ``run.sh`` with a recording
``abicheck`` stub: the exact ``-o`` values of the caller's own CLI call, the
``report-path`` output, and the files that exist afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _run_action,
)

pytestmark = REQUIRES_POSIX_SHELL

#: The `-o FORMAT=DEST` values run.sh adds for its own PR-comment JSON, which
#: are not the caller's report and are excluded from the pinned sets below.
_PR_JSON_MARKER = "abicheck-pr-json"


def _recording_stub(tmp_path: Path) -> tuple[Path, Path]:
    """An ``abicheck`` that records each call's argv and writes a minimal
    compatible JSON report at every ``-o FORMAT=PATH`` destination, and to
    stdout for ``-o json=-``, so ``run.sh`` reads a valid report."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "argv.log"
    stub = bindir / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'for arg in "$@"; do printf \'%s\\n\' "$arg"; done >> "{log}"\n'
        f"printf -- '---\\n' >> \"{log}\"\n"
        "report='{\"verdict\": \"COMPATIBLE\", \"changes\": []}'\n"
        "prev=''\n"
        'for arg in "$@"; do\n'
        '  if [[ "$prev" == "-o" ]]; then\n'
        '    dest="$arg"; [[ "$arg" == *=* ]] && dest="${arg#*=}"\n'
        '    if [[ "$dest" != "-" ]]; then mkdir -p "$(dirname "$dest")"; printf \'%s\' "$report" > "$dest";\n'
        '    elif [[ "$arg" == json=- ]]; then printf \'%s\' "$report"; fi\n'
        "  fi\n"
        '  prev="$arg"\n'
        "done\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir, log


def _first_call(log: Path) -> list[str]:
    """The caller's own CLI call: run.sh's PR-comment rerun comes after it."""
    text = log.read_text(encoding="utf-8")
    return text.split("---\n")[0].splitlines()


def _caller_outputs(argv: list[str], tmp_path: Path) -> list[str]:
    """The ``-o`` values of the caller's call, as ``run.sh`` relative paths.

    Absolute destinations are rewritten relative to ``tmp_path`` so the pins
    read the same on every machine.
    """
    values = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "-o"]
    values = [v for v in values if _PR_JSON_MARKER not in v]
    return [v.replace(str(tmp_path) + "/", "") for v in values]


def _run(tmp_path: Path, env: dict[str, str]) -> tuple[dict, list[str]]:
    bindir, log = _recording_stub(tmp_path)
    outputs = _run_action(tmp_path, env, bindir)
    assert outputs["_exit"] == 0, outputs["_stdout"][-400:]
    return outputs, _first_call(log)


def _libs(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(exist_ok=True)
    new.mkdir(exist_ok=True)
    return old, new


# --- compare, one pair -------------------------------------------------------

#: format -> (bare `-o` value, bare report-path, file a bare format writes).
_SINGLE_PAIR_BARE = {
    "json": ("json=-", "", None),
    "markdown": ("markdown=-", "", None),
    "sarif": (
        "sarif=abicheck-results.sarif",
        "abicheck-results.sarif",
        "abicheck-results.sarif",
    ),
    "html": ("html=-", "", None),
    "junit": ("junit=-", "", None),
    "review": ("review=-", "", None),
    "oneline": ("oneline=-", "", None),
}


@pytest.mark.parametrize("fmt", sorted(_SINGLE_PAIR_BARE))
def test_compare_bare_format_writes_where_it_always_has(
    tmp_path: Path, fmt: str
) -> None:
    """Without `output-file`, the format alone names its one destination:
    stdout for every format except sarif, which writes its default file."""
    dest, report, written = _SINGLE_PAIR_BARE[fmt]
    env = _compare_env(tmp_path)
    env.pop("INPUT_OUTPUT_FILE")
    env["INPUT_FORMAT"] = fmt
    outputs, argv = _run(tmp_path, env)
    assert _caller_outputs(argv, tmp_path) == [dest]
    assert outputs.get("report-path", "") == report
    if written is not None:
        assert (tmp_path / written).is_file()


@pytest.mark.parametrize("fmt", sorted(_SINGLE_PAIR_BARE))
def test_compare_format_with_output_file_writes_that_path(
    tmp_path: Path, fmt: str
) -> None:
    """With `output-file`, the same format writes to that path and publishes
    it as `report-path`."""
    target = tmp_path / "out" / f"report.{fmt}"
    env = _compare_env(tmp_path)
    env.pop("INPUT_OUTPUT_FILE")
    env["INPUT_FORMAT"] = fmt
    env["INPUT_OUTPUT_FILE"] = str(target)
    outputs, argv = _run(tmp_path, env)
    assert _caller_outputs(argv, tmp_path) == [f"{fmt}=out/report.{fmt}"]
    assert outputs.get("report-path") == str(target)
    assert target.is_file()


# --- compare, directory / package fan-out ------------------------------------

#: The same destination rule holds on the release fan-out, where html is also
#: a renderable format.
_RELEASE_BARE = {
    "markdown": ("markdown=-", ""),
    "sarif": ("sarif=abicheck-results.sarif", "abicheck-results.sarif"),
    "junit": ("junit=-", ""),
    "oneline": ("oneline=-", ""),
    "html": ("html=-", ""),
    "review": ("review=-", ""),
}


@pytest.mark.parametrize("fmt", sorted(_RELEASE_BARE))
def test_directory_bare_format_writes_where_it_always_has(
    tmp_path: Path, fmt: str
) -> None:
    old, new = _libs(tmp_path)
    env = _compare_env(tmp_path)
    env.pop("INPUT_OUTPUT_FILE")
    env["INPUT_OLD_LIBRARY"] = str(old)
    env["INPUT_NEW_LIBRARY"] = str(new)
    env["INPUT_FORMAT"] = fmt
    outputs, argv = _run(tmp_path, env)
    dest, report = _RELEASE_BARE[fmt]
    assert _caller_outputs(argv, tmp_path) == [dest]
    assert outputs.get("report-path", "") == report


def test_directory_junit_with_output_file_writes_that_path(tmp_path: Path) -> None:
    old, new = _libs(tmp_path)
    target = tmp_path / "OUT" / "j.xml"
    env = _compare_env(tmp_path)
    env.pop("INPUT_OUTPUT_FILE")
    env["INPUT_OLD_LIBRARY"] = str(old)
    env["INPUT_NEW_LIBRARY"] = str(new)
    env["INPUT_FORMAT"] = "junit"
    env["INPUT_OUTPUT_FILE"] = str(target)
    outputs, argv = _run(tmp_path, env)
    assert _caller_outputs(argv, tmp_path) == ["junit=OUT/j.xml"]
    assert outputs.get("report-path") == str(target)


# --- deps tree / deps compare ------------------------------------------------


def _deps_env(
    tmp_path: Path, mode: str, fmt: str, output: Path | None
) -> dict[str, str]:
    lib = tmp_path / "libnew.so"
    lib.write_bytes(b"\x7fELF")
    env = {"INPUT_MODE": mode, "INPUT_NEW_LIBRARY": str(lib), "INPUT_FORMAT": fmt}
    if mode == "deps-compare":
        env["INPUT_OLD_ROOT"] = str(tmp_path)
        env["INPUT_NEW_ROOT"] = str(tmp_path)
    if output is not None:
        env["INPUT_OUTPUT_FILE"] = str(output)
    return env


@pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
def test_deps_bare_markdown_writes_stdout(tmp_path: Path, mode: str) -> None:
    outputs, argv = _run(tmp_path, _deps_env(tmp_path, mode, "markdown", None))
    assert _caller_outputs(argv, tmp_path) == ["markdown=-"]
    assert outputs.get("report-path", "") == ""


def test_deps_tree_bare_html_writes_stdout(tmp_path: Path) -> None:
    outputs, argv = _run(tmp_path, _deps_env(tmp_path, "deps-tree", "html", None))
    assert _caller_outputs(argv, tmp_path) == ["html=-"]
    assert outputs.get("report-path", "") == ""


def test_deps_tree_json_with_output_file_writes_that_path(tmp_path: Path) -> None:
    target = tmp_path / "OUT" / "tree.json"
    outputs, argv = _run(tmp_path, _deps_env(tmp_path, "deps-tree", "json", target))
    assert _caller_outputs(argv, tmp_path) == ["json=OUT/tree.json"]
    assert outputs.get("report-path") == str(target)


# --- dump --------------------------------------------------------------------


def test_dump_without_output_file_writes_the_default_snapshot(tmp_path: Path) -> None:
    """Dump takes no `format`; its one output is the snapshot file, which
    defaults to `abicheck-baseline.json` and is published as `report-path`
    once written."""
    lib = tmp_path / "libnew.so"
    lib.write_bytes(b"\x7fELF")
    env = {"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(lib)}
    outputs, argv = _run(tmp_path, env)
    assert _caller_outputs(argv, tmp_path) == ["abicheck-baseline.json"]
    assert (tmp_path / "abicheck-baseline.json").is_file()
    assert outputs.get("report-path") == "abicheck-baseline.json"


def test_dump_with_output_file_writes_that_snapshot(tmp_path: Path) -> None:
    lib = tmp_path / "libnew.so"
    lib.write_bytes(b"\x7fELF")
    target = tmp_path / "OUT" / "snap.json"
    env = {
        "INPUT_MODE": "dump",
        "INPUT_NEW_LIBRARY": str(lib),
        "INPUT_OUTPUT_FILE": str(target),
    }
    outputs, argv = _run(tmp_path, env)
    assert argv[-2:] == ["-o", str(target)]
    assert target.is_file()
    assert outputs.get("report-path") == str(target)

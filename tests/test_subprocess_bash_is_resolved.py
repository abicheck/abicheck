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

"""A test that shells out must resolve bash, not spell it as a bare literal.

Bug class `tests.bare_bash_resolves_to_wsl_stub`. On GitHub's `windows-latest`
runners `%SystemRoot%\\System32\\bash.exe` is the WSL *launcher stub* — present
even with no distribution installed. A bare ``["bash", ...]`` argv resolves
through PATH and can find it ahead of Git for Windows' real bash, whereupon it
prints WSL's own "no installed distributions" text (UTF-16LE, so it does not
even match a substring assertion) and exits 1. Every test in the calling module
then fails at once with no bash-level diagnostic.

`_workflow_exec.bash_executable()` is the fix, and it has existed for a while.
What kept the lane red is that it was a *convention*: roughly two dozen modules
carried their own private `_bash_executable` clone, and a module written without
noticing the convention simply spelled `"bash"` and reddened the lane again
(`test_action_run_sh_build_info_conflict` did exactly that, and so did
`test_extra_args_is_value_option_completeness` and
`test_action_cli_surface_generated`, which is what this module was written for).

A per-module fix forecloses only the modules that exist today, so this states
the class in two halves:

* a **structural** half — no test module may pass a bare ``"bash"`` literal as
  the program of a subprocess argv, and no module may define a private
  `_bash_executable` clone: there is one resolver and every caller imports it;
  and
* a **behavioural** half — the resolver really does return something other than
  ``"bash"`` on an `nt` host that has a Git bash, so the structural rule is
  defending a live behaviour rather than enforcing a no-op alias. Without this
  the rule could pass vacuously the day `bash_executable()` was reduced to
  ``return "bash"``.

The structural half is a real AST scan, not a text grep: a ``"bash"`` inside a
docstring, a comment, or a `shutil.which("bash")` availability check is not a
call site, and only the *first* element of an argv list is the program.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from _workflow_exec import bash_executable

TESTS_DIR = Path(__file__).resolve().parent

#: The one module allowed to name the program literally: it *is* the resolver.
_RESOLVER_MODULE = "_workflow_exec.py"

#: Callables whose first positional argument is an argv sequence.
_SUBPROCESS_ENTRY_POINTS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output"}
)


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("*.py") if p.name != _RESOLVER_MODULE)


def _argv_program(node: ast.Call) -> ast.expr | None:
    """The expression in `node`'s argv position, if this is a subprocess call."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in _SUBPROCESS_ENTRY_POINTS or not node.args:
        return None
    argv = node.args[0]
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return None
    return argv.elts[0]


def _bare_bash_call_sites(source: str) -> list[int]:
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        program = _argv_program(node)
        if isinstance(program, ast.Constant) and program.value == "bash":
            hits.append(node.lineno)
    return hits


def _private_clone_lines(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_bash_executable"
    ]


class TestNoModuleSpellsBashItself:
    """The structural half: one resolver, imported everywhere."""

    def test_no_test_module_passes_a_bare_bash_program(self) -> None:
        offenders = {}
        for path in _test_modules():
            hits = _bare_bash_call_sites(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.relative_to(TESTS_DIR).as_posix()] = hits
        assert offenders == {}, (
            'these modules pass a bare "bash" as a subprocess program, which '
            "resolves to the WSL launcher stub on windows-latest; import "
            "`bash_executable` from `_workflow_exec` and call it instead: "
            f"{offenders}"
        )

    def test_no_test_module_clones_the_resolver(self) -> None:
        offenders = {}
        for path in _test_modules():
            hits = _private_clone_lines(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.relative_to(TESTS_DIR).as_posix()] = hits
        assert offenders == {}, (
            "these modules define a private `_bash_executable` clone; the "
            "canonical one lives in `tests/_workflow_exec.py` and a clone is "
            "how a module drifts out of a fix applied to the original: "
            f"{offenders}"
        )

    def test_the_scan_would_catch_a_reintroduced_call_site(self) -> None:
        """The structural rule is not vacuous: it fires on the shape it bans."""
        assert _bare_bash_call_sites('subprocess.run(["bash", str(p)])') == [1]
        assert _bare_bash_call_sites('subprocess.Popen(("bash", "-c", s))') == [1]
        assert _private_clone_lines(
            "def _bash_executable() -> str:\n    return 'x'\n"
        ) == [1]

    @pytest.mark.parametrize(
        "benign",
        [
            pytest.param('shutil.which("bash")', id="availability-probe"),
            pytest.param('subprocess.run([exe, "bash", "-n"])', id="not-the-program"),
            pytest.param('x = "bash"', id="plain-assignment"),
            pytest.param('"""a docstring mentioning bash"""', id="prose"),
            pytest.param("subprocess.run(cmd)", id="argv-is-a-name"),
        ],
    )
    def test_the_scan_does_not_fire_on_a_non_call_site(self, benign: str) -> None:
        assert _bare_bash_call_sites(benign) == []


class TestResolverIsNotAnAlias:
    """The behavioural half: resolving actually changes the program on `nt`."""

    def test_resolves_to_git_bash_on_a_windows_host(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        git_bash = tmp_path / "bash.exe"
        git_bash.write_text("", encoding="utf-8")
        # Only the resolver's own view of `os` is swapped: patching the real
        # `os.name` would also flip `pathlib.Path()` to `WindowsPath`, whose
        # `is_file()` cannot see a POSIX path, and the test would then pass for
        # the wrong reason.
        monkeypatch.setattr(
            "_workflow_exec.os",
            SimpleNamespace(name="nt", environ={"GIT_BASH_PATH": str(git_bash)}),
        )
        resolved = bash_executable()
        assert resolved == str(git_bash)
        assert resolved != "bash", (
            "the resolver has been reduced to an alias for the bare program, "
            "which makes the structural rule above enforce nothing"
        )

    def test_non_windows_hosts_keep_the_path_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "_workflow_exec.os",
            SimpleNamespace(
                name="posix", environ={"GIT_BASH_PATH": r"C:\nonsense\bash.exe"}
            ),
        )
        assert bash_executable() == "bash"

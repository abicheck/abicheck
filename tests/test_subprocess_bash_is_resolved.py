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

Bug class `guard.platform_convention_without_a_gate`
(`tests/regressions/manifest_guards.py`). On GitHub's `windows-latest`
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

import _workflow_exec
import pytest
from _pytest.outcomes import Skipped

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
    """The expression in `node`'s argv position, if this is a subprocess call.

    `args` is read as a keyword as well as positionally: every one of these
    entry points names its first parameter `args`, so
    `subprocess.run(args=["bash", ...])` is the same call with the same
    exposure, and a scan that only inspected `node.args[0]` would pass it
    (Codex review, P1). A call that somehow supplies both is not a shape to
    reason about -- Python rejects it -- so the positional wins and the
    keyword is consulted only in its absence.
    """
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in _SUBPROCESS_ENTRY_POINTS:
        return None
    argv: ast.expr | None = node.args[0] if node.args else None
    if argv is None:
        argv = next((kw.value for kw in node.keywords if kw.arg == "args"), None)
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


def _unguarded_resolutions(source: str) -> list[str]:
    """Names of functions that resolve bash, shell out, and never guard.

    Reported per enclosing function rather than per call, since the guard is a
    property of the function that shells out: two resolutions in one guarded
    helper are one guarded call site, not two offences.

    Resolving is only an offence when the function also *runs* something: a
    test that reasons about `bash_executable` itself -- the behavioural half
    below, or any future test of the resolver -- never reaches a stub and has
    nothing to skip for. Pairing the resolution with a subprocess entry point
    is deliberately looser than checking the argv position, because a call
    site that builds `cmd = [bash_executable(), ...]` and passes the name on
    is just as exposed as one that inlines it.
    """
    offenders: list[str] = []

    def _names_called(fn: ast.AST) -> set[str]:
        found = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = (
                    f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                )
                if name:
                    found.add(name)
        return found

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called = _names_called(node)
        shells_out = bool(called & _SUBPROCESS_ENTRY_POINTS)
        if "bash_executable" in called and shells_out and "require_bash" not in called:
            offenders.append(node.name)
    return offenders


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
        # The same call with argv passed by keyword — a distinct shape, not a
        # rewording of the one above, and the one the first revision missed.
        assert _bare_bash_call_sites('subprocess.run(args=["bash", "-c", s])') == [1]
        assert _bare_bash_call_sites('subprocess.run(args=["bash"], check=True)') == [1]
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
            pytest.param("subprocess.run(args=cmd)", id="keyword-argv-is-a-name"),
            pytest.param(
                'subprocess.run(args=[exe, "bash"])', id="keyword-not-the-program"
            ),
        ],
    )
    def test_the_scan_does_not_fire_on_a_non_call_site(self, benign: str) -> None:
        assert _bare_bash_call_sites(benign) == []


class TestEveryResolvedCallSiteGuardsFirst:
    """The third rule: resolving without asking reaches the same stub.

    `bash_executable()` deliberately still answers `"bash"` on a machine with
    no real bash, because every call site hands it straight to `subprocess`.
    So the resolver alone does not protect a caller on a stub-only runner --
    `require_bash()` is the half that skips, and a function that resolves
    without it runs the WSL launcher exactly as a bare argv would. Banning the
    literal while allowing the unguarded call would move the defect, not
    remove it.
    """

    def test_every_function_that_resolves_also_guards(self) -> None:
        offenders = {}
        for path in _test_modules():
            hits = _unguarded_resolutions(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.relative_to(TESTS_DIR).as_posix()] = hits
        assert offenders == {}, (
            "these functions call `bash_executable()` without calling "
            "`require_bash()` first, so on a runner whose only bash is the "
            "WSL launcher stub they execute it instead of skipping: "
            f"{offenders}"
        )

    def test_the_scan_would_catch_an_unguarded_resolution(self) -> None:
        unguarded = "def f():\n    subprocess.run([bash_executable(), p])\n"
        guarded = (
            "def f():\n    require_bash()\n    subprocess.run([bash_executable(), p])\n"
        )
        indirect = (
            "def f():\n    cmd = [bash_executable(), p]\n    subprocess.run(cmd)\n"
        )
        assert _unguarded_resolutions(unguarded) == ["f"]
        assert _unguarded_resolutions(indirect) == ["f"]
        assert _unguarded_resolutions(guarded) == []

    def test_reasoning_about_the_resolver_is_not_shelling_out(self) -> None:
        """The rule follows the subprocess, not the import or the mention."""
        assert (
            _unguarded_resolutions("from _workflow_exec import bash_executable\n") == []
        )
        assert _unguarded_resolutions("def f():\n    assert bash_executable()\n") == []


class TestTheGuardedPairIsNotAnAlias:
    """The behavioural half: both helpers do something other than the ban.

    Without this the three structural rules could all pass on the day
    `bash_executable()` became `return "bash"` and `require_bash()` became a
    no-op -- every call site would be spelled correctly and every one of them
    would run the stub.
    """

    def test_the_resolver_returns_the_bash_it_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_workflow_exec, "_real_bash", lambda: r"C:\Git\bash.exe")
        resolved = _workflow_exec.bash_executable()
        assert resolved == r"C:\Git\bash.exe"
        assert resolved != "bash", (
            "the resolver has been reduced to an alias for the bare program, "
            "which makes the structural rules above enforce nothing"
        )

    def test_the_guard_really_skips_when_no_real_bash_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_workflow_exec, "_real_bash", lambda: None)
        with pytest.raises(Skipped):
            _workflow_exec.require_bash()

    def test_the_guard_is_silent_when_a_real_bash_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_workflow_exec, "_real_bash", lambda: "/usr/bin/bash")
        _workflow_exec.require_bash()  # does not raise
        assert _workflow_exec.have_bash() is True

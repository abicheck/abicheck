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
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import _workflow_exec
import pytest
from _pytest.outcomes import Skipped

TESTS_DIR = Path(__file__).resolve().parent

#: The retired private clone. Banned as a name, not merely as a definition.
_CLONE_NAME = "_bash_executable"

T = TypeVar("T")

#: The availability guard, and the module a qualified call must name.
_GUARD_NAME = "require_bash"
_RESOLVER_MODULE_NAME = "_workflow_exec"


#: Callables whose first positional argument is an argv sequence.
_SUBPROCESS_ENTRY_POINTS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output"}
)


def _test_modules() -> list[Path]:
    """Every module under `tests/`, the resolver's own included.

    An earlier revision exempted `_workflow_exec.py` wholesale on the grounds
    that it *is* the resolver. That hid `run_step`, which shells out through
    `bash_executable()` with no guard of its own, so every consumer that never
    marked itself reached the WSL launcher (Codex review, P1). The resolver's
    own literal `"bash"` spellings are returns, not subprocess argv, so no
    exemption is needed for them: the rules are narrow enough to include the
    module that defines them.
    """
    return sorted(TESTS_DIR.rglob("*.py"))


def _scan(rule: Callable[[str], list[T]], path: Path) -> list[T]:
    """Apply one scan to one file, naming the file if it will not parse.

    `ast.parse` reports a `SyntaxError` against `"<unknown>"`, so a malformed
    module added under `tests/` would fail all three rules at once without
    saying which file it was (CodeRabbit review). Re-raised rather than
    skipped: an unparseable module is a real problem, and one this scan is
    well placed to notice.
    """
    try:
        return rule(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise AssertionError(
            f"{_display(path)} does not parse, so it cannot be scanned: {exc}"
        ) from exc


def _display(path: Path) -> str:
    """`path` relative to `tests/` when it lives there, else in full."""
    try:
        return path.relative_to(TESTS_DIR).as_posix()
    except ValueError:
        return str(path)


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
    if isinstance(argv, ast.Name):
        # Resolved against the enclosing scope's literal bindings by
        # `_bare_bash_call_sites`; returned as-is so the tracing lives in one
        # place rather than being re-derived per caller.
        return argv
    if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
        return None
    return argv.elts[0]


def _literal_argvs(scope: ast.AST) -> dict[str, ast.expr]:
    """Names bound to a list/tuple literal inside *scope*.

    `cmd = ["bash", ...]` followed by `subprocess.run(cmd)` runs the same
    program as the inline form, so declaring the indirection benign would
    leave the gate open to the reported shape's most natural sibling (Codex
    review, P1 — the first revision did exactly that).

    A name assigned more than once keeps every binding: this is a ban, so a
    name that is a bare-bash argv on *any* path is reported rather than
    excused by a later rebinding. Only literal right-hand sides are traced;
    a name from a parameter, a call, or a comprehension is genuinely opaque
    to a structural scan and is left to the resolver-and-guard rules.
    """
    bound: dict[str, ast.expr] = {}
    for node in ast.walk(scope):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node is not scope
        ):
            continue
        # An annotated binding (`cmd: list[str] = ["bash", ...]`) is the same
        # binding with a type on it, and reading only `ast.Assign` left that
        # especially natural form untraced (Codex review, P1).
        if isinstance(node, ast.AnnAssign):
            targets = [node.target] if node.target else []
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        else:
            continue
        if not isinstance(value, ast.List | ast.Tuple) or not value.elts:
            continue
        if not _is_bare_bash(value.elts[0]):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bound[target.id] = value
    return bound


def _is_bare_bash(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "bash"


def _bare_bash_call_sites(source: str) -> list[int]:
    tree = ast.parse(source)
    scopes: list[ast.AST] = [tree] + [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    bare_names = {name for scope in scopes for name in _literal_argvs(scope)}
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        program = _argv_program(node)
        if _is_bare_bash(program):
            hits.append(node.lineno)
        elif isinstance(program, ast.Name) and program.id in bare_names:
            hits.append(node.lineno)
    return hits


def _is_guard_call(call: ast.Call) -> bool:
    """Is *call* the real `require_bash()`, bare or qualified by its module?

    A bare name is trusted: the module-level import is what binds it, and no
    rule here can see past a deliberately shadowed import without a full
    symbol table. A qualified call must name `_workflow_exec`, so an
    unrelated object's same-named method cannot pose as the guard.
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == _GUARD_NAME
    if isinstance(func, ast.Attribute) and func.attr == _GUARD_NAME:
        return (
            isinstance(func.value, ast.Name) and func.value.id == _RESOLVER_MODULE_NAME
        )
    return False


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

    def _own_nodes(scope: ast.AST) -> list[ast.AST]:
        """Every node in *scope* except those owned by a nested function.

        A nested helper is its own call site and carries its own guard --
        `test_resolver_reports_build_output_end_to_end` guards inside the
        closure that actually shells out, which is correct, and which a walk
        that descended into it reported as an unguarded parent.
        """
        out: list[ast.AST] = []
        stack = [scope]
        while stack:
            node = stack.pop()
            out.append(node)
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
                ):
                    continue
                stack.append(child)
        return out

    def _names_called(fn: ast.AST) -> set[str]:
        found = set()
        for node in _own_nodes(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = (
                    f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                )
                if name:
                    found.add(name)
        return found

    def _dominating_guard_line(fn: ast.AST) -> int | None:
        """Line of a `require_bash()` that runs on every path into the body.

        Only an unconditional statement in the function's own body counts. A
        guard nested in an `if`/`try`/loop is skipped on the paths that do not
        take it, and a guard *after* the call has already run the stub -- both
        satisfy a set-membership test and neither satisfies the rule (Codex
        review, P1). Structural dominance rather than a real CFG analysis: it
        is the conservative side, so the worst it does is ask a caller to
        hoist a guard it already has.
        """
        for stmt in getattr(fn, "body", []):
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
                continue
            # Both call forms, matching how `_names_called` reads a
            # resolution: reading only `func.id` here while accepting
            # `func.attr` there made the two halves disagree, so a function
            # guarding with `_workflow_exec.require_bash()` was reported
            # unguarded (CodeRabbit review).
            #
            # The receiver is checked, unlike on the resolution side, because
            # the two directions fail differently: a stricter resolution rule
            # only asks for a guard that is already there, while a looser
            # guard rule lets an unrelated `helper.require_bash()` stand in
            # for the real one and silently permits the subprocess (Codex
            # review). So a qualified guard must name `_workflow_exec`.
            if _is_guard_call(stmt.value):
                return stmt.lineno
        return None

    tree = ast.parse(source)
    # The module body is a scope like any other, and a stricter one: a
    # resolved subprocess at module level runs during *collection*, so a guard
    # placed in some function cannot help it and the whole module dies on a
    # stub-only runner (Codex review, P2). Reported under "<module>".
    for node in [tree] + [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]:
        called = _names_called(node)
        if "bash_executable" not in called:
            continue
        runs = [
            call.lineno
            for call in _own_nodes(node)
            if isinstance(call, ast.Call)
            and _names_called(call) & _SUBPROCESS_ENTRY_POINTS
        ]
        if not runs:
            continue
        guard = _dominating_guard_line(node)
        if guard is None or guard > min(runs):
            offenders.append(getattr(node, "name", "<module>"))
    return offenders


def _private_clone_lines(source: str) -> list[int]:
    """Every *mention* of the private clone name, not only its definition.

    A definitions-only scan is blind to the migration's own wreckage: deleting
    a clone changes the defining module's exported surface, and a sibling that
    imported the name (`from test_action_run_sh_helpers import
    _bash_executable`) then fails at *collection*, before any test runs. Two
    modules did exactly that here and no rule in this file objected, because
    each held a reference and no definition (Codex review, P1).

    So the rule is the name itself: it may not appear as a definition, an
    import, or a call anywhere under `tests/`. This walks the AST rather than
    grepping, because a docstring or comment discussing the retired clones --
    this module's own included -- is not a mention.
    """
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == _CLONE_NAME
        ):
            hits.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == _CLONE_NAME:
            hits.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == _CLONE_NAME:
            hits.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            hits.extend(node.lineno for a in node.names if a.name == _CLONE_NAME)
    return sorted(set(hits))


class TestNoModuleSpellsBashItself:
    """The structural half: one resolver, imported everywhere."""

    def test_no_test_module_passes_a_bare_bash_program(self) -> None:
        offenders = {}
        for path in _test_modules():
            hits = _scan(_bare_bash_call_sites, path)
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
            hits = _scan(_private_clone_lines, path)
            if hits:
                offenders[path.relative_to(TESTS_DIR).as_posix()] = hits
        assert offenders == {}, (
            "these modules define a private `_bash_executable` clone; the "
            "canonical one lives in `tests/_workflow_exec.py` and a clone is "
            "how a module drifts out of a fix applied to the original: "
            f"{offenders}"
        )

    @pytest.mark.parametrize(
        "reference",
        [
            pytest.param(
                "def _bash_executable() -> str:\n    return 'x'\n", id="definition"
            ),
            pytest.param(
                "from test_action_run_sh_helpers import _bash_executable",
                id="import-of-a-deleted-clone",
            ),
            pytest.param("subprocess.run([_bash_executable()])", id="call"),
            pytest.param("helpers._bash_executable()", id="qualified-call"),
        ],
    )
    def test_the_ban_is_on_the_name_not_only_the_definition(
        self, reference: str
    ) -> None:
        """Deleting a clone changes its module's exported surface.

        The definitions-only rule was blind to the sibling that *imported* the
        deleted name, which fails at collection before any test runs — two
        modules in this very migration (Codex review, P1).
        """
        assert _private_clone_lines(reference) == [1]

    def test_an_unparseable_module_names_itself(self, tmp_path: Path) -> None:
        """A `SyntaxError` from `ast.parse` blames `"<unknown>"` otherwise."""
        broken = tmp_path / "broken_module.py"
        broken.write_text("def f(\n", encoding="utf-8")
        with pytest.raises(AssertionError, match="does not parse"):
            _scan(_bare_bash_call_sites, broken)

    def test_prose_about_the_retired_clones_is_not_a_mention(self) -> None:
        """Otherwise this module, whose subject is that name, bans itself."""
        assert _private_clone_lines('"""a docstring about _bash_executable"""') == []
        assert _private_clone_lines("# a comment about _bash_executable\n") == []

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
        "traced",
        [
            pytest.param(
                'cmd = ["bash", "-c", s]\nsubprocess.run(cmd)', id="module-scope"
            ),
            pytest.param(
                'def f():\n    cmd = ["bash", p]\n    subprocess.run(cmd)',
                id="function-scope",
            ),
            pytest.param(
                'def f():\n    cmd = ["bash", p]\n    subprocess.run(args=cmd)',
                id="traced-through-the-keyword",
            ),
            pytest.param(
                'def f():\n    cmd = [exe, p]\n    cmd = ["bash", p]\n'
                "    subprocess.run(cmd)",
                id="bare-on-one-path-only",
            ),
            pytest.param(
                'def f():\n    cmd: list[str] = ["bash", "-c", s]\n'
                "    subprocess.run(cmd)",
                id="annotated-binding",
            ),
            pytest.param(
                'cmd: list[str] = ["bash"]\nsubprocess.run(cmd)',
                id="annotated-at-module-scope",
            ),
        ],
    )
    def test_the_scan_follows_a_name_bound_to_the_argv(self, traced: str) -> None:
        """`cmd = ["bash", ...]; run(cmd)` runs exactly what the inline form runs.

        The first revision declared this shape benign in so many words, which
        left the ban open to the reported shape's most natural sibling (Codex
        review, P1).
        """
        assert _bare_bash_call_sites(traced) != []

    @pytest.mark.parametrize(
        "benign",
        [
            pytest.param('shutil.which("bash")', id="availability-probe"),
            pytest.param('subprocess.run([exe, "bash", "-n"])', id="not-the-program"),
            pytest.param('x = "bash"', id="plain-assignment"),
            pytest.param('"""a docstring mentioning bash"""', id="prose"),
            pytest.param("subprocess.run(cmd)", id="untraceable-name"),
            pytest.param("subprocess.run(args=cmd)", id="untraceable-keyword-name"),
            pytest.param(
                'subprocess.run(args=[exe, "bash"])', id="keyword-not-the-program"
            ),
            pytest.param(
                'cmd = [exe, "bash"]\nsubprocess.run(cmd)', id="traced-not-the-program"
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
            hits = _scan(_unguarded_resolutions, path)
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
        qualified = (
            "def f():\n    _workflow_exec.require_bash()\n"
            "    subprocess.run([_workflow_exec.bash_executable(), p])\n"
        )
        assert _unguarded_resolutions(unguarded) == ["f"]
        assert _unguarded_resolutions(indirect) == ["f"]
        assert _unguarded_resolutions(guarded) == []
        assert _unguarded_resolutions(qualified) == []
        # A same-named method on some other object is not the guard: unlike
        # the resolution side, a false negative here permits the subprocess.
        wrong_receiver = (
            "def f():\n    helper.require_bash()\n"
            "    subprocess.run([_workflow_exec.bash_executable(), p])\n"
        )
        assert _unguarded_resolutions(wrong_receiver) == ["f"]

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(
                "def f():\n    subprocess.run([bash_executable(), p])\n"
                "    require_bash()\n",
                id="guard-after-the-call",
            ),
            pytest.param(
                "def f():\n    if slow:\n        require_bash()\n"
                "    subprocess.run([bash_executable(), p])\n",
                id="guard-in-one-branch",
            ),
            pytest.param(
                "def f():\n    try:\n        require_bash()\n    except E:\n"
                "        pass\n    subprocess.run([bash_executable(), p])\n",
                id="guard-inside-try",
            ),
            pytest.param(
                "def f():\n    for _ in xs:\n        require_bash()\n"
                "    subprocess.run([bash_executable(), p])\n",
                id="guard-inside-loop",
            ),
        ],
    )
    def test_a_guard_that_does_not_dominate_is_not_a_guard(self, source: str) -> None:
        """Set membership is not the rule; running first on every path is.

        Each of these satisfies "the function mentions `require_bash`" and
        none of them protects the call (Codex review, P1).
        """
        assert _unguarded_resolutions(source) == ["f"]

    def test_a_module_level_resolution_is_a_scope_of_its_own(self) -> None:
        """A resolved subprocess at import time runs during collection.

        No function's guard can protect it, so the module body is scanned as
        its own scope and reported under `<module>` (Codex review, P2).
        """
        unguarded = 'subprocess.run([bash_executable(), "-c", s])\n'
        guarded = 'require_bash()\nsubprocess.run([bash_executable(), "-c", s])\n'
        assert _unguarded_resolutions(unguarded) == ["<module>"]
        assert _unguarded_resolutions(guarded) == []

    def test_a_nested_helper_carries_its_own_guard(self) -> None:
        """A closure that guards is not its parent's offence.

        `test_resolver_reports_build_output_end_to_end` guards inside the
        closure that shells out, which is correct — and which the first
        dominance revision reported as an unguarded parent, because the walk
        descended into the nested body.
        """
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        require_bash()\n"
            "        subprocess.run([bash_executable(), p])\n"
            "    return inner\n"
        )
        assert _unguarded_resolutions(source) == []

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

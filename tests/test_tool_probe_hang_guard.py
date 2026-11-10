"""An external-tool call gives up only on a hung tool, never on a slow one.

What a probe of a compiler, castxml, ``c++filt`` or ``git`` answers is
recorded: a target platform, a compiler family or version, an include search
path, a demangled name, a commit. A bound tighter than a hang guard turns a
loaded host (a cold compiler start under antivirus, a saturated CI runner)
into a different recorded fact, or into a "could not be verified" error, for
the same tool and the same input. ``deadline.PROBE_HANG_TIMEOUT_S`` is the one
hang guard; an active ``--budget`` still caps every call.

Two checks:

* ``test_no_tool_call_gives_up_before_the_hang_guard`` reads the AST of every
  module under ``abicheck/`` and fails on any external-tool call (or the local
  probe bound it is handed) whose timeout is a number below the hang guard.
  The oracle is the source text, not the helpers under test.
* ``test_slow_tools_still_answer`` runs real probes against a stand-in tool
  that answers correctly but only after the bound the probes used to have.
"""

from __future__ import annotations

import ast
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from abicheck import deadline

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "abicheck"

#: Callees that start an external tool, or bound one already started.
_TOOL_CALLS = frozenset(
    {
        "run_bounded",
        "run_bounded_for_extraction",
        "run",
        "check_output",
        "check_call",
        "call",
        "run_ast",
        "communicate",
        "wait",
        "deadline_scope",
    }
)

#: Names a probe's local bound is assigned to before it is passed on.
_BOUND_NAMES = ("timeout", "TIMEOUT")

#: (file, function) pairs whose short bound is not a probe's answer: each one
#: waits for a child that was already told to exit, or drains its pipes.
_NOT_A_PROBE = {
    ("abicheck/deadline.py", "_kill_process_tree"): "reaps a child already signalled",
    ("abicheck/deadline.py", "run_bounded"): "drains pipes after the kill",
    ("abicheck/deadline.py", "<module>"): "spawn-drain bound of the SIGTERM handler",
    (
        "abicheck/extract/packages.py",
        "_rpm_extract",
    ): "reaps cpio/rpm2cpio after a failure",
    (
        "abicheck/debug_resolver.py",
        "<module>",
    ): "an HTTP request to debuginfod, not a tool",
    (
        "abicheck/workflows/dump/header_graph_prefetch.py",
        "<module>",
    ): "waits for an in-process prefetch thread, not a tool",
    (
        "abicheck/buildsource/include_graph.py",
        "<module>",
    ): "the advisory include map's whole-pass cost budget; each unit gets 120 s",
}


def _numeric(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if not isinstance(node.value, bool):
            return float(node.value)
    return None


def _callee(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


class _Scan(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope = ["<module>"]
        self.found: list[tuple[str, str, int, float]] = []

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def _hit(self, line: int, value: float) -> None:
        if value < deadline.PROBE_HANG_TIMEOUT_S:
            self.found.append((self.rel, self.scope[-1], line, value))

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node)
        if name in _TOOL_CALLS:
            for keyword in node.keywords:
                if (
                    keyword.arg == "timeout"
                    and (v := _numeric(keyword.value)) is not None
                ):
                    self._hit(node.lineno, v)
            if name == "deadline_scope" and node.args:
                if (v := _numeric(node.args[0])) is not None:
                    self._hit(node.lineno, v)
        self.generic_visit(node)

    def _assign(
        self, targets: list[ast.expr], value: ast.AST | None, line: int
    ) -> None:
        v = _numeric(value)
        if v is None:
            return
        for target in targets:
            if isinstance(target, ast.Name) and any(
                n in target.id for n in _BOUND_NAMES
            ):
                self._hit(line, v)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._assign(node.targets, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assign([node.target], node.value, node.lineno)
        self.generic_visit(node)


def _short_bounds(root: Path) -> list[tuple[str, str, int, float]]:
    found = []
    for path in sorted((root / "abicheck").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        scan = _Scan(rel)
        scan.visit(ast.parse(path.read_text(encoding="utf-8"), filename=rel))
        found.extend(scan.found)
    return found


@pytest.mark.repo_scan
def test_no_tool_call_gives_up_before_the_hang_guard() -> None:
    offenders = [
        f"{rel}:{line} ({function}): timeout {value:g}s"
        for rel, function, line, value in _short_bounds(ROOT)
        if (rel, function) not in _NOT_A_PROBE
    ]
    assert offenders == [], (
        "an external-tool call bounded below deadline.PROBE_HANG_TIMEOUT_S "
        "records a different answer on a loaded host; use the shared hang guard:\n"
        + "\n".join(offenders)
    )


@pytest.mark.repo_scan
def test_every_exemption_still_names_a_short_bound() -> None:
    seen = {(rel, function) for rel, function, _line, _value in _short_bounds(ROOT)}
    assert sorted(set(_NOT_A_PROBE) - seen) == []


def test_the_scan_sees_each_shape_of_a_short_bound(tmp_path: Path) -> None:
    """Negative control: each spelling the gate guards is reported, and a call
    bounded by the shared constant, or by a number at the guard, is not."""
    module = tmp_path / "abicheck" / "probe.py"
    module.parent.mkdir()
    module.write_text(
        textwrap.dedent(
            """
            import subprocess
            from abicheck import deadline
            from abicheck.deadline import PROBE_HANG_TIMEOUT_S, run_bounded

            def kwarg():
                return run_bounded(["cc", "-v"], timeout=10)

            def plain_run():
                return subprocess.run(["git", "rev-parse"], timeout=5.0)

            def scoped():
                with deadline.deadline_scope(15.0):
                    pass

            def local_bound():
                probe_timeout = 30
                return probe_timeout

            def waited(proc):
                proc.wait(timeout=1)

            CONSTANT_TIMEOUT_S = 60

            def guarded():
                return run_bounded(["cc", "-v"], timeout=PROBE_HANG_TIMEOUT_S)

            def at_the_guard():
                return run_bounded(["cc", "-v"], timeout=120)

            def not_a_tool(queue):
                return queue.get(timeout=0.1)
            """
        ),
        encoding="utf-8",
    )
    found = {
        (function, value) for _rel, function, _line, value in _short_bounds(tmp_path)
    }
    assert found == {
        ("kwarg", 10.0),
        ("plain_run", 5.0),
        ("scoped", 15.0),
        ("local_bound", 30.0),
        ("waited", 1.0),
        ("<module>", 60.0),
    }


#: Longer than every bound the probes had before the shared hang guard (5,
#: 10, 15 and 30 s), far below the guard itself.
_SLOW_S = 31

_SLOW_TOOL = """\
#!{python}
import sys, time
time.sleep({delay})
args = sys.argv[1:]
if "-dumpmachine" in args:
    print("x86_64-pc-linux-gnu")
elif "-dumpversion" in args:
    print("18.1.8")
elif "--version" in args:
    print("clang version 18.1.8")
elif "-dM" in args:
    sys.stdin.read()
    print("#define __SIZEOF_POINTER__ 8")
    print("#define __BYTE_ORDER__ __ORDER_LITTLE_ENDIAN__")
    print("#define __ORDER_LITTLE_ENDIAN__ 1234")
    print("#define __cplusplus 201703L")
elif "-fsyntax-only" in args:
    sys.stdin.read()
    sys.exit(0 if "--target=aarch64-linux-gnu" in args else 1)
"""


@pytest.fixture
def slow_tool(tmp_path: Path) -> str:
    tool = tmp_path / "slow-cc"
    tool.write_text(_SLOW_TOOL.format(python=sys.executable, delay=_SLOW_S))
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    return str(tool)


@pytest.mark.slow
@pytest.mark.skipif(os.name == "nt", reason="the stand-in tool is a POSIX script")
def test_slow_tools_still_answer(slow_tool: str) -> None:
    """Every probe answers from a tool that is slow but working; the oracle is
    what the stand-in tool prints, so an unanswered probe reads as None."""
    from concurrent.futures import ThreadPoolExecutor

    from abicheck.buildsource import toolchain_probe
    from abicheck.buildsource.source_extractors import clang
    from abicheck.extract import target_platform_probe
    from abicheck.extract.headers import toolchain

    probes = {
        "accepts a real target": lambda: (
            toolchain_probe._clang_accepts_target.__wrapped__(
                slow_tool, "d", "aarch64-linux-gnu"
            )
        ),
        "rejects a bogus target": lambda: (
            toolchain_probe._clang_accepts_target.__wrapped__(
                slow_tool, "d", "not-a-real-target"
            )
        ),
        "-dumpmachine": lambda: toolchain._tool_target_triple.__wrapped__(
            slow_tool, "d"
        ),
        "target platform": lambda: (
            target_platform_probe._probe_target_platform.__wrapped__(
                slow_tool, (), False
            )
        ),
    }
    probes["compiler family"] = lambda: clang._clang_compiler_family.__wrapped__(
        slow_tool
    )
    probes["compiler version"] = lambda: clang._clang_compiler_version.__wrapped__(
        slow_tool
    )
    probes["language standard"] = lambda: (
        toolchain._probe_default_language_standard.__wrapped__(slow_tool, "c++")
    )
    expected = {
        "accepts a real target": True,
        "rejects a bogus target": False,
        "-dumpmachine": "x86_64-pc-linux-gnu",
        "target platform": (64, "little"),
        "compiler family": "clang",
        "compiler version": "18.1.8",
        "language standard": "probed:__cplusplus=201703L",
    }
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {label: pool.submit(probe) for label, probe in probes.items()}
        answers = {label: future.result() for label, future in futures.items()}
    assert answers == expected

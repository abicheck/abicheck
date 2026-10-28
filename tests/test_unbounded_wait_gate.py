# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""No new unbounded wait under ``abicheck/`` outside a reasoned allowlist.

Bug class ``concurrency.forked_child_unbounded_reap``: a parent that waits on
a child process, a thread, a future or a queue with no timeout hangs the whole
run when that child never finishes (a SIGTERM-proof child, a deadlocked fork,
a pipe that never reaches EOF). The bounded forms already in the tree are
``join(timeout)``, ``wait(timeout)``, ``communicate(timeout)``,
``run_bounded``/``supervised_popen``, and the ``reap_child`` grace period.

The gate flags a call site that waits with no bound:

* ``x.join()``, ``x.wait()``, ``x.communicate()``, ``x.result()``,
  ``x.exception()``, ``x.acquire()``, ``x.recv()`` with no positional argument
  and no ``timeout=``;
* ``x.get()`` with no argument (a queue read), excluding names bound to a
  ``ContextVar``, whose ``get()`` is a non-blocking read;
* ``subprocess.run``/``call``/``check_call``/``check_output`` with no
  ``timeout=``;
* ``os.wait()``/``os.waitpid()``, ``as_completed(...)`` and
  ``wait_ready(...)`` (``multiprocessing.connection.wait``) with no
  ``timeout=``.

Each allowlist entry is keyed by ``(file, call text)`` and pins the exact
number of such sites in that file, with the reason it is bounded by design or
by its caller. A new site, or an extra site with the same text, fails the
gate. An entry whose file or call no longer exists also fails it, so the
allowlist cannot quietly outlive the code it excuses.

"""

from __future__ import annotations

import ast
import functools
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCAN_ROOT = REPO / "abicheck"

_CHILD_PIPE_GAP = (
    "KNOWN GAP, docs/contribute/known-gaps.md: a child that is alive but never "
    "answers or exits blocks this read indefinitely. Bounding it needs a time "
    "policy for unbudgeted runs (a maintainer decision); under --budget the "
    "deadline could bound it, but workflows may not import the unclassified "
    "deadline module yet (architecture/dispositions.yaml)."
)

#: ``(relative path, call text) -> (exact site count, reason)``.
ALLOWED_UNBOUNDED: dict[tuple[str, str], tuple[int, str]] = {
    ("abicheck/buildsource/callback_graph.py", "parse.result()"): (
        1,
        "CallbackParse.result(): a parser's own result, not a Future",
    ),
    ("abicheck/buildsource/template_graph.py", "parse.result()"): (
        1,
        "TemplateParse.result(): a parser's own result, not a Future",
    ),
    ("abicheck/buildsource/semantic_fact_folds.py", "templates.result()"): (
        1,
        "TemplateParse/CallbackParse.result(): parser results, not Futures",
    ),
    ("abicheck/buildsource/semantic_fact_folds.py", "callbacks.result()"): (
        1,
        "CallbackParse.result(): a parser's own result, not a Future",
    ),
    ("abicheck/buildsource/semantic_fact_folds.py", "self._templates.result()"): (
        1,
        "TemplateParse.result(): a parser's own result, not a Future",
    ),
    ("abicheck/buildsource/semantic_fact_folds.py", "self._callbacks.result()"): (
        1,
        "CallbackParse.result(): a parser's own result, not a Future",
    ),
    ("abicheck/buildsource/include_graph_workers.py", "f.result()"): (
        1,
        "probe futures: each probe runs under run_bounded with a per-unit "
        "timeout and the aggregate deadline, so every future completes",
    ),
    ("abicheck/buildsource/include_graph_workers.py", "done.result()"): (
        1,
        "inside a done-callback: the future is already complete",
    ),
    ("abicheck/buildsource/include_graph_workers.py", "done.exception()"): (
        1,
        "inside a done-callback: the future is already complete",
    ),
    ("abicheck/cc_wrapper.py", "subprocess.run"): (
        1,
        "transparent compiler passthrough: runs the user's own compiler command, "
        "which must inherit the terminal and never be time-bounded (also pinned "
        "in tests/test_subprocess_supervisor.py)",
    ),
    ("abicheck/extract/headers/manifest.py", "as_completed()"): (
        1,
        "TU fan-out: every worker's subprocesses run under run_bounded, whose "
        "timeout argument is required, so each future completes",
    ),
    ("abicheck/extract/headers/manifest.py", "fut.result()"): (
        1,
        "as_completed yields only finished futures",
    ),
    ("abicheck/model/execution_cache.py", "pending.result()"): (
        1,
        "waiter on an in-flight computation: bounded by its owner's work, which "
        "spawns children only through run_bounded",
    ),
    ("abicheck/service_header_graph_attach.py", "prefetched.result()"): (
        1,
        "header-AST prefetch future: bounded by its worker's run_bounded subprocess",
    ),
    ("abicheck/storage/header_ast_cache.py", "future.exception()"): (
        1,
        "guarded by future.done() on the line above",
    ),
    ("abicheck/storage/header_ast_cache.py", "future.result()"): (
        2,
        "guarded by future.done(), or after result(timeout=) has returned",
    ),
    ("abicheck/workflows/compare/resolve.py", "old_future.result()"): (
        1,
        "side resolution on a BudgetedExecutor worker: bounded by its run_bounded subprocesses",
    ),
    ("abicheck/workflows/compare/resolve.py", "new_future.result()"): (
        1,
        "side resolution on a BudgetedExecutor worker: bounded by its run_bounded subprocesses",
    ),
    ("abicheck/workflows/keyed_thread_pool.py", "as_completed()"): (
        1,
        "member fan-out: each member's work is bounded as its callee's work is",
    ),
    ("abicheck/workflows/keyed_thread_pool.py", "future.result()"): (
        1,
        "as_completed yields only finished futures",
    ),
    ("abicheck/workflows/release_admission.py", "self._cond.wait()"): (
        1,
        "waits for an in-flight member to release its budget; ends when that "
        "member settles (see the child-pipe gap)",
    ),
    ("abicheck/workflows/keyed_process_pool.py", "recv.recv()"): (1, _CHILD_PIPE_GAP),
    ("abicheck/workflows/keyed_process_pool.py", "wait_ready()"): (1, _CHILD_PIPE_GAP),
    ("abicheck/workflows/side_isolation.py", "recv.recv()"): (1, _CHILD_PIPE_GAP),
}

_WAIT_METHODS = frozenset(
    {"join", "wait", "communicate", "result", "exception", "acquire", "recv"}
)
_SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output"})
_FREE_WAITS = frozenset({"as_completed", "wait_ready"})


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _contextvar_names(tree: ast.AST) -> set[str]:
    """Names bound to a ``ContextVar(...)``: their ``get()`` never blocks."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        if _receiver_name(value.func) != "ContextVar":
            continue
        names.update(n for t in targets if (n := _receiver_name(t)) is not None)
    return names


def unbounded_wait_sites(
    tree: ast.AST, source: str, contextvars: set[str]
) -> list[tuple[int, str]]:
    """``(line, call text)`` for every wait in *tree* with no bound."""
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {k.arg for k in node.keywords}
        if None in keywords or "timeout" in keywords:
            continue  # **kwargs may carry a timeout; an explicit timeout is a bound
        npos = len(node.args)
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
            recv = func.value
            recv_text = ast.get_source_segment(source, recv) or "?"
            if name in _WAIT_METHODS and npos == 0:
                if name == "acquire" and keywords:
                    continue
                sites.append((node.lineno, f"{recv_text}.{name}()"))
            elif name == "get" and npos == 0 and not keywords:
                if _receiver_name(recv) not in contextvars:
                    sites.append((node.lineno, f"{recv_text}.get()"))
            elif (
                name in _SUBPROCESS_CALLS
                and isinstance(recv, ast.Name)
                and recv.id == "subprocess"
            ):
                sites.append((node.lineno, f"subprocess.{name}"))
            elif (
                name in {"wait", "waitpid"}
                and isinstance(recv, ast.Name)
                and recv.id == "os"
            ):
                sites.append((node.lineno, f"os.{name}"))
            elif name in _FREE_WAITS and npos <= 1:
                sites.append((node.lineno, f"{name}()"))
        elif isinstance(func, ast.Name) and func.id in _FREE_WAITS and npos <= 1:
            sites.append((node.lineno, f"{func.id}()"))
    return sites


@functools.cache
def _scan_tree() -> Counter[tuple[str, str]]:
    sites: Counter[tuple[str, str]] = Counter()
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = path.relative_to(REPO).as_posix()
        for _line, text in unbounded_wait_sites(tree, source, _contextvar_names(tree)):
            sites[(rel, text)] += 1
    return sites


@pytest.mark.repo_scan
def test_no_unbounded_wait_outside_the_reasoned_allowlist() -> None:
    actual = _scan_tree()
    offenders = {
        key: (count, ALLOWED_UNBOUNDED.get(key, (0, ""))[0])
        for key, count in actual.items()
        if count != ALLOWED_UNBOUNDED.get(key, (0, ""))[0]
    }
    assert offenders == {}, (
        "unbounded wait(s) under abicheck/ (key -> (found, allowed)): "
        f"{offenders}. Bound the wait with join/wait/communicate(timeout=), "
        "deadline.run_bounded, or reap_child's grace period; or, if the wait "
        "is bounded by design or by its caller, add a reasoned "
        "ALLOWED_UNBOUNDED entry."
    )


@pytest.mark.repo_scan
def test_allowlist_entries_are_live() -> None:
    actual = _scan_tree()
    stale = []
    for (rel, text), (count, reason) in ALLOWED_UNBOUNDED.items():
        assert reason.strip(), f"{rel} {text!r}: an allowlist entry needs a reason"
        if not (REPO / rel).is_file():
            stale.append(f"{rel} (file gone)")
        elif actual.get((rel, text), 0) != count:
            stale.append(
                f"{rel} {text!r} (now {actual.get((rel, text), 0)}, pinned {count})"
            )
    assert stale == [], f"stale ALLOWED_UNBOUNDED entries: {stale}"


# -- the scanner itself ------------------------------------------------------


def _sites_of(snippet: str) -> list[str]:
    tree = ast.parse(snippet)
    return [
        text for _, text in unbounded_wait_sites(tree, snippet, _contextvar_names(tree))
    ]


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("p.join()", ["p.join()"]),
        ("p.wait()", ["p.wait()"]),
        ("self._proc.communicate()", ["self._proc.communicate()"]),
        ("fut.result()", ["fut.result()"]),
        ("fut.exception()", ["fut.exception()"]),
        ("lock.acquire()", ["lock.acquire()"]),
        ("q.get()", ["q.get()"]),
        ("subprocess.run(cmd)", ["subprocess.run"]),
        ("subprocess.check_output(cmd)", ["subprocess.check_output"]),
        ("os.waitpid(pid, 0)", ["os.waitpid"]),
        ("as_completed(futs)", ["as_completed()"]),
        ("wait_ready(conns)", ["wait_ready()"]),
    ],
)
def test_scanner_flags_each_unbounded_form(snippet: str, expected: list[str]) -> None:
    assert _sites_of(snippet) == expected


@pytest.mark.parametrize(
    "snippet",
    [
        "p.join(5)",
        "p.join(timeout=5)",
        "p.wait(timeout=5)",
        "p.wait(5)",
        "p.communicate(timeout=5)",
        "fut.result(timeout=left)",
        "fut.result(5)",
        "subprocess.run(cmd, timeout=5)",
        "subprocess.run(cmd, **opts)",
        "', '.join(parts)",
        "os.path.join(a, b)",
        "d.get('key')",
        "d.get('key', None)",
        "lock.acquire(timeout=1)",
        "lock.acquire(False)",
        "wait_ready(conns, timeout=1)",
        "wait_ready(conns, 1.0)",
    ],
)
def test_scanner_passes_each_bounded_or_non_wait_form(snippet: str) -> None:
    assert _sites_of(snippet) == []


def test_scanner_ignores_get_on_a_contextvar_but_not_on_a_queue() -> None:
    snippet = "_CURRENT = ContextVar('c')\n_CURRENT.get()\nself._q.get()\n"
    assert _sites_of(snippet) == ["self._q.get()"]

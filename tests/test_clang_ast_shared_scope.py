"""Contract of ``clang_ast_run.parse_clang_ast`` / ``shared_ast_scope``.

The six clang-backed L5 graph passes used to dump every TU six times. The
shared scope makes them share one dump per TU; these tests state what must
stay true for that to be invisible to the passes: every pass observes exactly
what an independent dump of the same argv would have given it (result,
diagnostics, and exception), and each distinct dump input is dumped once.

The oracle is always a *direct* ``parser(fake_ast(key))`` / an independent
fake dump — never the memo under test.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import abicheck.buildsource.clang_ast_run as car
from abicheck.buildsource.clang_ast_run import NoAst, parse_clang_ast, shared_ast_scope


def _ast_for(argv: list[str], cwd: str | None) -> dict[str, Any]:
    return {"kind": "TranslationUnitDecl", "argv": list(argv), "cwd": cwd}


class _FakeClang:
    """Deterministic stand-in for ``run_clang_ast_dump``; counts every dump."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.calls: list[tuple[str, tuple[str, ...], str | None]] = []
        self.failing = failing
        self._lock = threading.Lock()

    def __call__(self, clang_bin, argv, *, cwd, diagnostics):
        with self._lock:
            self.calls.append((clang_bin, tuple(argv), cwd))
        diagnostics.append(f"dumped {argv[-1]}")
        if argv[-1] in self.failing:
            diagnostics.append(f"clang produced no AST for {argv[-1]}")
            return None
        return _ast_for(argv, cwd)


def _p_len(ast: dict[str, Any]) -> int:
    return len(ast["argv"])


def _p_last(ast: dict[str, Any]) -> str:
    return ast["argv"][-1]


def _p_cwd(ast: dict[str, Any]) -> str | None:
    return ast["cwd"]


def _p_raises_on_b(ast: dict[str, Any]) -> str:
    if ast["argv"][-1].startswith("b"):
        raise ValueError(f"bad {ast['argv'][-1]}")
    return "fine"


PARSERS = (_p_len, _p_last, _p_cwd, _p_raises_on_b)


def _direct(parser, argv, cwd, failing):
    """Oracle: what one independent dump + parse would yield."""
    diags = [f"dumped {argv[-1]}"]
    if argv[-1] in failing:
        diags.append(f"clang produced no AST for {argv[-1]}")
        return ("noast", None), diags
    try:
        return ("ok", parser(_ast_for(argv, cwd))), diags
    except ValueError as exc:
        return ("err", str(exc)), diags


def _observe(parser, argv, cwd):
    diags: list[str] = []
    try:
        r = parse_clang_ast("clang++", argv, cwd=cwd, diagnostics=diags, parser=parser)
    except ValueError as exc:
        return ("err", str(exc)), diags
    return (("noast", None) if isinstance(r, NoAst) else ("ok", r)), diags


_units = st.lists(
    st.tuples(
        st.lists(st.sampled_from(["-O2", "-DX", "-Ifoo"]), max_size=3),
        st.sampled_from(["a.cpp", "b.cpp", "c.cpp", "d.cpp"]),
        st.sampled_from([None, "/w", "/v"]),
    ),
    min_size=1,
    max_size=8,
)


@settings(max_examples=150, deadline=None)
@given(
    units=_units,
    failing=st.frozensets(st.sampled_from(["a.cpp", "b.cpp", "c.cpp", "d.cpp"])),
    order=st.permutations(list(range(len(PARSERS)))),
)
def test_scope_is_observationally_identical_and_dumps_each_input_once(
    units, failing, order
) -> None:
    fake = _FakeClang(failing)
    original = car.run_clang_ast_dump
    car.run_clang_ast_dump = fake
    try:
        with shared_ast_scope(PARSERS):
            # Pass-major order, as fold_semantic_graphs runs them: every pass
            # walks every unit; the pass order is generated.
            for i in order:
                parser = PARSERS[i]
                for flags, src, cwd in units:
                    argv = [*flags, "--", src]
                    assert _observe(parser, argv, cwd) == _direct(
                        parser, argv, cwd, failing
                    )
    finally:
        car.run_clang_ast_dump = original
    distinct = {("clang++", tuple([*f, "--", s]), c) for f, s, c in units}
    assert sorted(fake.calls, key=repr) == sorted(distinct, key=repr)


def test_outside_a_scope_every_call_dumps(monkeypatch) -> None:
    fake = _FakeClang()
    monkeypatch.setattr(car, "run_clang_ast_dump", fake)
    for _ in range(3):
        _observe(_p_len, ["--", "a.cpp"], None)
    assert len(fake.calls) == 3


def test_records_do_not_outlive_the_scope(monkeypatch) -> None:
    fake = _FakeClang()
    monkeypatch.setattr(car, "run_clang_ast_dump", fake)
    with shared_ast_scope(PARSERS):
        _observe(_p_len, ["--", "a.cpp"], None)
    with shared_ast_scope(PARSERS):
        _observe(_p_len, ["--", "a.cpp"], None)
    assert len(fake.calls) == 2


def test_unregistered_parser_inside_a_scope_dumps_for_itself(monkeypatch) -> None:
    fake = _FakeClang()
    monkeypatch.setattr(car, "run_clang_ast_dump", fake)
    with shared_ast_scope([_p_len]):
        assert _observe(_p_len, ["--", "a.cpp"], None)[0] == ("ok", 2)
        assert _observe(_p_last, ["--", "a.cpp"], None) == _direct(
            _p_last, ["--", "a.cpp"], None, frozenset()
        )
    assert len(fake.calls) == 2


def test_overlapping_scopes_keep_records_until_the_last_closes(monkeypatch) -> None:
    fake = _FakeClang()
    monkeypatch.setattr(car, "run_clang_ast_dump", fake)
    outer = shared_ast_scope(PARSERS)
    outer.__enter__()
    try:
        with shared_ast_scope(PARSERS):
            _observe(_p_len, ["--", "a.cpp"], None)
        # inner closed; the outer scope still shares the record
        _observe(_p_last, ["--", "a.cpp"], None)
        assert len(fake.calls) == 1
    finally:
        outer.__exit__(None, None, None)


def test_concurrent_requests_for_one_input_dump_once(monkeypatch) -> None:
    gate = threading.Event()
    fake = _FakeClang()

    def slow(*a, **k):
        gate.wait(5)
        return fake(*a, **k)

    monkeypatch.setattr(car, "run_clang_ast_dump", slow)
    results: list[Any] = []
    with shared_ast_scope(PARSERS):
        threads = [
            threading.Thread(
                target=lambda p=p: results.append(_observe(p, ["--", "a.cpp"], "/w"))
            )
            for p in PARSERS * 4
        ]
        for t in threads:
            t.start()
        gate.set()
        for t in threads:
            t.join(10)
    assert len(fake.calls) == 1
    assert len(results) == len(PARSERS) * 4
    for (outcome, diags) in results:
        assert diags == ["dumped a.cpp"]
        assert outcome[0] in {"ok", "err"}


@pytest.mark.parametrize("n_units", [1, 3, 7])
def test_fold_semantic_graphs_dumps_each_tu_once(monkeypatch, n_units) -> None:
    """End to end: the six clang-backed passes share one dump per TU."""
    from abicheck.buildsource import inline_graph_fold
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.source_graph import SourceGraphSummary

    dumps: list[tuple[str, ...]] = []
    lock = threading.Lock()

    def fake(clang_bin, argv, *, cwd, diagnostics):
        with lock:
            dumps.append(tuple(argv))
        return {"kind": "TranslationUnitDecl", "inner": []}

    monkeypatch.setattr(car, "run_clang_ast_dump", fake)
    monkeypatch.setattr("shutil.which", lambda _b: "/usr/bin/clang++")
    monkeypatch.setattr(inline_graph_fold, "fold_include_graph", lambda *a, **k: None)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(
                id=f"cu://{i}",
                source=f"/p/src/f{i}.cpp",
                directory="/p/build",
                argv=["/usr/bin/g++", "-c", f"/p/src/f{i}.cpp"],
                language="CXX",
                standard="c++17",
            )
            for i in range(n_units)
        ]
    )
    inline_graph_fold.fold_semantic_graphs(
        SourceGraphSummary(), merged, "clang++", extractors=[]
    )
    assert len(dumps) == n_units
    assert len(set(dumps)) == n_units

"""The type-graph family's cross-TU merge and its share of the L5 AST pass.

Split out of ``test_type_graph.py`` (which keeps the pure-parser and graph
fold tests, and sits past the file-size cap) when ``ClangTypeGraphExtractor``
was replaced by ``buildsource/l5_ast_pass``: what used to be that class's
merge, deadline and degradation behaviour is now ``merge_type_edges`` plus the
shared runner, and is tested against those owners here.
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.buildsource.type_graph import CONF_HIGH, TypeEdge, parse_clang_ast_types


def _field(name: str, qual_type: str) -> dict:
    return {"kind": "FieldDecl", "name": name, "type": {"qualType": qual_type}}


def _record(name: str, *, inner: list[dict] | None = None) -> dict:
    return {"kind": "CXXRecordDecl", "name": name, "inner": inner or []}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


def _type_pass():
    from abicheck.buildsource.l5_ast_pass import L5_AST_PASSES

    return [p for p in L5_AST_PASSES if p.name == "type_graph"]


def _run_type_pass(build: BuildEvidence):
    """Run only the type-graph family of the shared L5 AST pass."""
    from abicheck.buildsource.l5_ast_pass import run_ast_passes

    return run_ast_passes(build, "clang++", passes=_type_pass())["type_graph"]


def _one_unit() -> BuildEvidence:
    return BuildEvidence(compile_units=[CompileUnit(id="cu://f", source="foo.cpp")])


def test_merge_type_edges_merges_richer_edge_across_compile_units() -> None:
    from abicheck.buildsource.type_graph import merge_type_edges

    # TU a doesn't see detail::Impl's declaration; TU b includes the private
    # header and resolves the file. Order must not matter.
    no_file = TypeEdge(
        "Widget", "detail::Impl", "TYPE_HAS_FIELD_TYPE", CONF_HIGH, "field", ""
    )
    with_file = TypeEdge(
        "Widget",
        "detail::Impl",
        "TYPE_HAS_FIELD_TYPE",
        CONF_HIGH,
        "field",
        "src/detail/impl.h",
    )
    for per_unit in ([[no_file], [with_file]], [[with_file], [no_file]]):
        edges = merge_type_edges(per_unit)
        assert len(edges) == 1
        assert edges[0].dst_file == "src/detail/impl.h"


def test_merge_type_edges_keeps_distinct_roles_apart() -> None:
    from abicheck.buildsource.type_graph import merge_type_edges

    ret = TypeEdge("_Zf", "detail::Impl", "DECL_USES_TYPE", CONF_HIGH, "return")
    par = TypeEdge("_Zg", "detail::Impl", "DECL_USES_TYPE", CONF_HIGH, "param")
    par_f = TypeEdge("_Zf", "detail::Impl", "DECL_USES_TYPE", CONF_HIGH, "param")
    assert merge_type_edges([[ret], [par, par_f]]) == [ret, par, par_f]


def test_run_ast_passes_merges_richer_edge_across_compile_units(
    monkeypatch,
) -> None:
    import abicheck.buildsource.l5_ast_pass as l5

    def fake_dump(_clang, argv, *, cwd, diagnostics):
        del cwd, diagnostics
        return {"source": argv[-1]}

    monkeypatch.setattr(l5, "run_clang_ast_dump", fake_dump)

    def _parse(ast, _cu) -> list[TypeEdge]:
        dst_file = "" if ast["source"] == "src/a.cpp" else "src/detail/impl.h"
        return [
            TypeEdge(
                "Widget",
                "detail::Impl",
                "TYPE_HAS_FIELD_TYPE",
                CONF_HIGH,
                "field",
                dst_file,
            )
        ]

    real = _type_pass()[0]
    type_pass = l5.AstPass(real.name, _parse, real.merge)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    edges = l5.run_ast_passes(build, "clang++", passes=[type_pass])["type_graph"]
    assert len(edges.result) == 1
    assert edges.result[0].dst_file == "src/detail/impl.h"


def test_run_ast_passes_propagates_deadline_into_pool_workers(monkeypatch) -> None:
    """Codex review (PR #591): contextvars don't cross a ThreadPoolExecutor
    boundary, so a worker submitted from inside deadline.deadline_scope()
    used to see no active deadline at all — each clang subprocess call
    inside it would run to its full fixed 120s regardless of --budget. The
    shared pass carries it through parallel_probe.run_parallel_probes."""
    import abicheck.buildsource.l5_ast_pass as l5
    from abicheck import deadline

    # Force a real pool (l5_ast_pass imports _call_graph_jobs by name).
    monkeypatch.setattr(
        "abicheck.buildsource.l5_ast_pass._call_graph_jobs", lambda _n: 2
    )
    seen_remaining: list[float | None] = []

    def fake_dump(_clang, _argv, *, cwd, diagnostics):
        del cwd, diagnostics
        seen_remaining.append(deadline.remaining())
        return None

    monkeypatch.setattr(l5, "run_clang_ast_dump", fake_dump)
    build = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://a", source="a.cpp"),
            CompileUnit(id="cu://b", source="b.cpp"),
            CompileUnit(id="cu://c", source="c.cpp"),
        ]
    )
    with deadline.deadline_scope(30.0):
        _run_type_pass(build)
    assert len(seen_remaining) == 3
    assert all(r is not None for r in seen_remaining), (
        "pool worker saw no active deadline (remaining()=None) — the scan "
        "deadline did not cross the executor boundary"
    )
    assert all(0 < r <= 30.0 for r in seen_remaining)


def test_run_ast_passes_deadline_exceeded_degrades_to_diagnostic(
    monkeypatch,
) -> None:
    # Codex review (PR #591): this pass is advisory (ADR-028 D3) — a
    # DeadlineExceeded from the now-bounded clang subprocess must degrade to
    # the same diagnostic+[] contract as any other probe failure, not
    # propagate and abort the whole L5 type-graph fold.
    from abicheck import deadline

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr("abicheck.deadline.run_bounded", _raise)
    outcome = _run_type_pass(_one_unit())
    assert outcome.result == []
    assert any("clang invocation failed" in d for d in outcome.diagnostics)


def test_run_ast_passes_bounded_by_local_cap_not_full_scan_budget(
    monkeypatch,
) -> None:
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=120 on this L5 clang call alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this pass's own 120s local cap. Assert the ContextVar
    deadline observed inside run_bounded is capped near the local cap, not
    the much larger outer scan budget."""
    from abicheck import deadline

    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run_bounded)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        _run_type_pass(_one_unit())

    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 120.5


def test_run_ast_passes_rechecks_deadline_before_parsing_ast(
    monkeypatch,
) -> None:
    """Codex review (PR #591): clang can exit successfully right as the
    budget expires, but json.loads()+parse_clang_ast_types() used to run
    unbounded. Must degrade to the advisory diagnostic+[] contract
    (ADR-028 D3), not raise."""
    import json as _json
    import time

    from abicheck import deadline

    ast = _tu(_record("Widget", inner=[_field("x", "int")]))

    def fake_run(*_a, **_k):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
    with deadline.deadline_scope(0.03):
        outcome = _run_type_pass(_one_unit())
    assert outcome.result == []
    assert any(
        "scan deadline exceeded before parsing clang AST" in d
        for d in outcome.diagnostics
    )


def test_run_ast_passes_rechecks_deadline_before_walking_ast(
    monkeypatch,
) -> None:
    """Codex review (PR #591, round 4): json.loads() on a huge L5 type-graph
    AST can itself consume the rest of the budget -- the existing pre-load
    deadline.check() doesn't catch that; must re-check again after the load,
    before the recursive parse_clang_ast_types() walk."""
    import json as _json
    import time

    from abicheck import deadline

    ast = _tu(_record("Widget", inner=[_field("x", "int")]))

    def fake_run(*_a, **_k):
        return _FakeProc(_json.dumps(ast))

    monkeypatch.setattr("abicheck.deadline.run_bounded", fake_run)
    from abicheck.buildsource import clang_ast_run

    real_loads = clang_ast_run.json.loads

    def _slow_loads(text: str) -> object:
        time.sleep(0.05)
        return real_loads(text)

    monkeypatch.setattr(clang_ast_run.json, "loads", _slow_loads)
    with deadline.deadline_scope(0.03):
        outcome = _run_type_pass(_one_unit())
    assert outcome.result == []
    assert any(
        "scan deadline exceeded before walking clang AST" in d
        for d in outcome.diagnostics
    )


# ── shared L5 AST pass: graceful degrade ─────────────────────────────────────


def test_extractor_missing_clang_is_graceful() -> None:
    from abicheck.buildsource.inline_graph_fold import fold_type_graph
    from abicheck.buildsource.l5_ast_pass import run_l5_ast_pass

    build = _one_unit()
    run = run_l5_ast_pass(build, "definitely-not-a-real-clang-binary")
    assert run.clang_available is False
    graph = SourceGraphSummary()
    rows: list = []
    fold_type_graph(graph, build, run, rows)
    assert graph.edges == []
    assert [r.status for r in rows] == ["failed"]


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _patch_clang(monkeypatch, *, proc: _FakeProc) -> None:

    monkeypatch.setattr("abicheck.deadline.run_bounded", lambda *_a, **_k: proc)


def test_run_ast_passes_nonzero_exit_records_diagnostic_but_salvages_edges(
    monkeypatch,
) -> None:
    # Ninth Codex review: clang can exit non-zero (real compile errors in the
    # necessarily-approximate replayed flags) while still printing a partial,
    # error-recovered AST dump. Edges are still salvaged (best effort), but a
    # diagnostic must be recorded regardless — extractor_pass_fully_covered
    # relies on `diagnostics` being non-empty to disqualify confirmed pass
    # coverage for this TU.
    import json as _json

    ast = _tu(
        _record("Impl"),
        _record("Widget", inner=[_field("impl", "Impl")]),
    )
    _patch_clang(
        monkeypatch,
        proc=_FakeProc(_json.dumps(ast), stderr="error: bad thing", returncode=1),
    )
    outcome = _run_type_pass(_one_unit())
    assert outcome.result == parse_clang_ast_types(ast)
    assert outcome.result  # the salvaged edge survived
    assert any("exited 1" in d for d in outcome.diagnostics)


def test_run_ast_passes_zero_exit_records_no_diagnostic(monkeypatch) -> None:
    import json as _json

    ast = _tu(_record("Widget"))
    _patch_clang(monkeypatch, proc=_FakeProc(_json.dumps(ast), returncode=0))
    outcome = _run_type_pass(_one_unit())
    assert outcome.diagnostics == []

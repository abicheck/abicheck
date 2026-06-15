"""Tests for stack HTML report generator."""
from __future__ import annotations

from types import SimpleNamespace

from abicheck.stack_checker import StackCheckResult, StackVerdict
from abicheck.stack_html import stack_to_html, write_stack_html


def _node(soname: str, depth: int = 0, path: str = "", reason: str = "") -> object:
    return SimpleNamespace(
        soname=soname, depth=depth, path=path or f"/lib/{soname}",
        needed=[], resolution_reason=reason or ("root" if depth == 0 else "DT_NEEDED"),
    )


def _graph(root: str = "/bin/app", nodes: dict | None = None) -> object:
    ns = nodes or {root: _node("app", 0, root)}
    return SimpleNamespace(
        root=root,
        nodes=ns,
        node_count=len(ns),
        edges=[],
        unresolved=[],
    )


def _binding(consumer: str, symbol: str, status: str, version: str = "", explanation: str = "") -> object:
    return SimpleNamespace(
        consumer=consumer, symbol=symbol, version=version,
        status=SimpleNamespace(value=status), explanation=explanation,
    )


def _stack_result(
    loadability: StackVerdict = StackVerdict.PASS,
    abi_risk: StackVerdict = StackVerdict.PASS,
    risk_score: str = "low",
    missing_symbols: list | None = None,
    stack_changes: list | None = None,
) -> StackCheckResult:
    return StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline",
        candidate_env="/candidate",
        loadability=loadability,
        abi_risk=abi_risk,
        baseline_graph=_graph(),
        candidate_graph=_graph(),
        bindings_baseline=[],
        bindings_candidate=[
            _binding("/bin/myapp", "main", "bound"),
        ],
        missing_symbols=missing_symbols or [],
        stack_changes=stack_changes or [],
        risk_score=risk_score,
    )


def test_html_is_valid_document() -> None:
    out = stack_to_html(_stack_result())
    assert out.startswith("<!DOCTYPE html>")
    assert "</html>" in out


def test_html_contains_root_binary() -> None:
    out = stack_to_html(_stack_result())
    assert "/bin/myapp" in out


def test_html_shows_pass_verdict() -> None:
    out = stack_to_html(_stack_result())
    assert "PASS" in out


def test_html_shows_fail_verdict() -> None:
    out = stack_to_html(_stack_result(loadability=StackVerdict.FAIL))
    assert "FAIL" in out


def test_html_shows_binding_summary() -> None:
    out = stack_to_html(_stack_result())
    assert "Symbol Binding Summary" in out
    assert "bound" in out


def test_html_shows_missing_symbols() -> None:
    missing = [_binding("/bin/myapp", "missing_func", "missing", explanation="not found")]
    out = stack_to_html(_stack_result(missing_symbols=missing))
    assert "Missing Symbols" in out
    assert "missing_func" in out


def test_html_shows_dependency_tree() -> None:
    out = stack_to_html(_stack_result())
    assert "Dependency Tree" in out


def test_html_shows_risk_score() -> None:
    out = stack_to_html(_stack_result(risk_score="high"))
    assert "HIGH" in out


def test_html_shows_warn_verdict() -> None:
    out = stack_to_html(_stack_result(abi_risk=StackVerdict.WARN))
    assert "WARN" in out


def test_html_shows_unresolved_libraries() -> None:
    graph = _graph()
    graph.unresolved = [("/bin/myapp", "libmissing.so.1")]
    r = _stack_result()
    r = StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline",
        candidate_env="/candidate",
        loadability=StackVerdict.FAIL,
        abi_risk=StackVerdict.PASS,
        baseline_graph=_graph(),
        candidate_graph=graph,
        bindings_baseline=[],
        bindings_candidate=[],
        missing_symbols=[],
        stack_changes=[],
        risk_score="high",
    )
    out = stack_to_html(r)
    assert "Unresolved Libraries" in out
    assert "libmissing.so.1" in out
    assert "NOT FOUND" in out


def test_html_shows_stack_changes_removed() -> None:
    sc = SimpleNamespace(library="libold.so", change_type="removed", abi_diff=None)
    out = stack_to_html(_stack_result(stack_changes=[sc]))
    assert "Stack Changes" in out
    assert "libold.so" in out
    assert "Removed from candidate" in out


def test_html_shows_stack_changes_added() -> None:
    sc = SimpleNamespace(library="libnew.so", change_type="added", abi_diff=None)
    out = stack_to_html(_stack_result(stack_changes=[sc]))
    assert "libnew.so" in out
    assert "New in candidate" in out


def test_html_shows_stack_changes_content_changed() -> None:
    from abicheck.checker import Verdict

    abi_diff = SimpleNamespace(
        verdict=Verdict.BREAKING,
        breaking=[SimpleNamespace(kind=SimpleNamespace(value="func_removed"), description="foo removed")],
        changes=[SimpleNamespace()],
    )
    sc = SimpleNamespace(library="libchanged.so", change_type="content_changed", abi_diff=abi_diff)
    out = stack_to_html(_stack_result(stack_changes=[sc]))
    assert "libchanged.so" in out
    assert "BREAKING" in out
    assert "Content changed" in out


def test_html_shows_environments() -> None:
    out = stack_to_html(_stack_result())
    assert "/baseline" in out
    assert "/candidate" in out


def test_html_tree_with_edges() -> None:
    """Tree rendering with parent-child edges."""
    root_key = "/bin/app"
    child_key = "/lib/libfoo.so"
    nodes = {
        root_key: _node("app", 0, root_key),
        child_key: _node("libfoo.so", 1, child_key, "DT_NEEDED"),
    }
    graph = SimpleNamespace(
        root=root_key,
        nodes=nodes,
        node_count=2,
        edges=[(root_key, child_key)],
        unresolved=[],
    )
    r = StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline",
        candidate_env="/candidate",
        loadability=StackVerdict.PASS,
        abi_risk=StackVerdict.PASS,
        baseline_graph=_graph(),
        candidate_graph=graph,
        bindings_baseline=[],
        bindings_candidate=[],
        missing_symbols=[],
        stack_changes=[],
        risk_score="low",
    )
    out = stack_to_html(r)
    assert "libfoo.so" in out
    assert "DT_NEEDED" in out


def test_html_tree_node_with_none_reason() -> None:
    """Node with depth > 0 but None resolution_reason should not show (None)."""
    root_key = "/bin/app"
    child_key = "/lib/libfoo.so"
    nodes = {
        root_key: _node("app", 0, root_key),
        child_key: SimpleNamespace(
            soname="libfoo.so", depth=1, path=child_key,
            needed=[], resolution_reason=None,
        ),
    }
    graph = SimpleNamespace(
        root=root_key, nodes=nodes, node_count=2,
        edges=[(root_key, child_key)], unresolved=[],
    )
    r = StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline", candidate_env="/candidate",
        loadability=StackVerdict.PASS, abi_risk=StackVerdict.PASS,
        baseline_graph=_graph(), candidate_graph=graph,
        bindings_baseline=[], bindings_candidate=[],
        missing_symbols=[], stack_changes=[], risk_score="low",
    )
    out = stack_to_html(r)
    assert "(None)" not in out
    assert "libfoo.so" in out


def test_html_escapes_xss_in_root_binary() -> None:
    """Malicious root_binary must be escaped."""
    r = _stack_result()
    r = StackCheckResult(
        root_binary="<script>alert(1)</script>",
        baseline_env="/baseline", candidate_env="/candidate",
        loadability=StackVerdict.PASS, abi_risk=StackVerdict.PASS,
        baseline_graph=_graph(), candidate_graph=_graph(),
        bindings_baseline=[], bindings_candidate=[],
        missing_symbols=[], stack_changes=[], risk_score="low",
    )
    out = stack_to_html(r)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_html_escapes_xss_in_missing_symbol() -> None:
    """Missing symbol names with HTML must be escaped."""
    evil = _binding("/bin/app", "<img src=x>", "missing", explanation="<b>bad</b>")
    out = stack_to_html(_stack_result(missing_symbols=[evil]))
    assert "<img " not in out
    assert "&lt;img " in out
    assert "<b>bad</b>" not in out


def test_html_medium_risk_score() -> None:
    out = stack_to_html(_stack_result(risk_score="medium"))
    assert "MEDIUM" in out


def _result_with_graph(graph: object) -> StackCheckResult:
    return StackCheckResult(
        root_binary="/bin/myapp",
        baseline_env="/baseline", candidate_env="/candidate",
        loadability=StackVerdict.PASS, abi_risk=StackVerdict.PASS,
        baseline_graph=_graph(), candidate_graph=graph,
        bindings_baseline=[], bindings_candidate=[],
        missing_symbols=[], stack_changes=[], risk_score="low",
    )


def test_html_truncates_missing_symbols_over_50() -> None:
    # Only the first 50 missing symbols are tabulated; the rest collapse into a
    # single "+N more" row so a huge break doesn't render a multi-thousand-row
    # table.
    missing = [_binding("/bin/app", f"sym{i}", "missing") for i in range(63)]
    out = stack_to_html(_stack_result(missing_symbols=missing))
    assert "+13 more" in out
    assert "sym0" in out
    assert "sym62" not in out  # beyond the 50-row cap


def test_html_tree_empty_when_no_root_node() -> None:
    # A graph with nodes but no depth-0 root can't be rendered as a tree; the
    # renderer must fall back to a placeholder, not crash.
    graph = SimpleNamespace(
        root="/bin/app",
        nodes={"/lib/orphan.so": _node("orphan.so", depth=1)},
        node_count=1, edges=[], unresolved=[],
    )
    out = stack_to_html(_result_with_graph(graph))
    assert "(empty graph)" in out


def test_html_tree_breaks_cycles() -> None:
    # A -> B -> A. The renderer must detect the back-edge and stop, not recurse
    # forever.
    a, b = "/bin/app", "/lib/libb.so"
    graph = SimpleNamespace(
        root=a,
        nodes={a: _node("app", 0, a), b: _node("libb.so", 1, b)},
        node_count=2, edges=[(a, b), (b, a)], unresolved=[],
    )
    out = stack_to_html(_result_with_graph(graph))
    assert "(cycle)" in out


def test_html_tree_dedupes_diamond_dependency() -> None:
    # A -> B -> D and A -> C -> D. D is reachable two ways but must be expanded
    # once; the second visit is marked "(already shown)" rather than duplicated.
    a, b, c, d = "/bin/app", "/lib/b.so", "/lib/c.so", "/lib/d.so"
    graph = SimpleNamespace(
        root=a,
        nodes={
            a: _node("app", 0, a),
            b: _node("b.so", 1, b),
            c: _node("c.so", 1, c),
            d: _node("d.so", 2, d),
        },
        node_count=4,
        edges=[(a, b), (a, c), (b, d), (c, d)],
        unresolved=[],
    )
    out = stack_to_html(_result_with_graph(graph))
    assert "(already shown)" in out
    assert out.count("d.so") == 2  # one real row + one "already shown" row


def test_html_tree_skips_dangling_edge_target() -> None:
    # An edge can point at a key with no node entry (a half-resolved graph); the
    # renderer must skip it silently instead of dereferencing None.
    a = "/bin/app"
    graph = SimpleNamespace(
        root=a,
        nodes={a: _node("app", 0, a)},
        node_count=1,
        edges=[(a, "/lib/ghost.so")],
        unresolved=[],
    )
    out = stack_to_html(_result_with_graph(graph))
    assert "app" in out
    assert "ghost.so" not in out


def test_write_stack_html_writes_file(tmp_path) -> None:
    out_path = tmp_path / "stack.html"
    write_stack_html(_stack_result(), out_path)
    written = out_path.read_text(encoding="utf-8")
    assert written.startswith("<!DOCTYPE html>")
    assert "/bin/myapp" in written

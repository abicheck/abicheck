# Copyright 2026 Nikolay Petrov
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

"""Tests for abicheck.stack_report — JSON and Markdown output."""
from __future__ import annotations

import json
from pathlib import Path

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import StackChange, StackCheckResult, StackVerdict
from abicheck.stack_report import stack_to_json, stack_to_markdown


def _binding(status: BindingStatus, symbol: str = "sym", provider: str | None = "/lib/libfoo.so") -> SymbolBinding:
    return SymbolBinding(consumer="/app", symbol=symbol, version="", provider=provider, status=status, explanation="test")


def _graph_with_nodes(**sonames: str) -> DependencyGraph:
    g = DependencyGraph(root="/app")
    for i, (key, soname) in enumerate(sonames.items()):
        g.nodes[key] = ResolvedDSO(
            path=Path(key), soname=soname, needed=[], rpath="", runpath="",
            resolution_reason="root" if i == 0 else "default", depth=i,
        )
    return g


def _make_result(
    loadability: StackVerdict = StackVerdict.PASS,
    abi_risk: StackVerdict = StackVerdict.PASS,
    missing: list[SymbolBinding] | None = None,
    stack_changes: list[StackChange] | None = None,
    unresolved: list[tuple[str, str]] | None = None,
    bindings: list[SymbolBinding] | None = None,
    baseline_env: str = "/base",
    candidate_env: str = "/cand",
    binding_changes: list | None = None,
) -> StackCheckResult:
    graph = _graph_with_nodes(**{"/app": "app", "/lib/libfoo.so": "libfoo.so"})
    graph.edges = [("/app", "/lib/libfoo.so")]
    if unresolved:
        graph.unresolved = list(unresolved)
    return StackCheckResult(
        root_binary="/app",
        baseline_env=baseline_env,
        candidate_env=candidate_env,
        loadability=loadability,
        abi_risk=abi_risk,
        baseline_graph=graph,
        candidate_graph=graph,
        bindings_baseline=bindings or [_binding(BindingStatus.RESOLVED_OK)],
        bindings_candidate=bindings or [_binding(BindingStatus.RESOLVED_OK)],
        missing_symbols=missing or [],
        stack_changes=stack_changes or [],
        binding_changes=binding_changes or [],
        risk_score="low",
    )


class TestStackToJson:
    def test_basic_json_parses(self):
        result = _make_result()
        data = json.loads(stack_to_json(result))
        assert data["root_binary"] == "/app"
        assert data["verdict"]["loadability"] == "pass"
        assert data["verdict"]["abi_risk"] == "pass"

    def test_missing_symbols_in_json(self):
        result = _make_result(
            missing=[_binding(BindingStatus.MISSING, symbol="missing_func", provider=None)],
        )
        data = json.loads(stack_to_json(result))
        assert "missing_symbols" in data
        assert data["missing_symbols"][0]["symbol"] == "missing_func"

    def test_unresolved_libraries_in_json(self):
        result = _make_result(unresolved=[("/app", "libmissing.so")])
        data = json.loads(stack_to_json(result))
        assert "unresolved_libraries" in data
        assert data["unresolved_libraries"][0]["soname"] == "libmissing.so"

    def test_stack_changes_in_json(self):
        result = _make_result(
            stack_changes=[StackChange(library="libfoo.so", change_type="added")],
        )
        data = json.loads(stack_to_json(result))
        assert "stack_changes" in data
        assert data["stack_changes"][0]["change_type"] == "added"

    def test_stack_change_findings_embedded_not_just_counts(self):
        """A content-changed library must embed which symbols broke, not just
        a bare count — verified defect: JSON only had `abi_breaking: N`, with
        no way to identify the actual findings without a separate `compare`
        run (mirroring the `scan --baseline` fix)."""
        diff = DiffResult(
            old_version="1.0", new_version="2.0", library="libfoo.so",
            changes=[
                Change(ChangeKind.FUNC_REMOVED, "foo_init", "removed: foo_init"),
            ],
            verdict=Verdict.BREAKING,
        )
        result = _make_result(
            stack_changes=[
                StackChange(library="libfoo.so", change_type="content_changed", abi_diff=diff),
            ],
        )
        data = json.loads(stack_to_json(result))
        sc = data["stack_changes"][0]
        assert sc["abi_breaking"] == 1
        assert "findings" in sc
        assert sc["findings"][0]["symbol"] == "foo_init"
        assert sc["findings"][0]["kind"] == "func_removed"
        assert sc["findings"][0]["bucket"] == "breaking"
        assert "findings_truncated" not in sc

    def test_stack_change_findings_capped_and_flagged_truncated(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, f"foo_{i}", f"removed: foo_{i}")
            for i in range(15)
        ]
        diff = DiffResult(
            old_version="1.0", new_version="2.0", library="libfoo.so",
            changes=changes, verdict=Verdict.BREAKING,
        )
        result = _make_result(
            stack_changes=[
                StackChange(library="libfoo.so", change_type="content_changed", abi_diff=diff),
            ],
        )
        data = json.loads(stack_to_json(result))
        sc = data["stack_changes"][0]
        assert len(sc["findings"]) == 10
        assert sc["findings_truncated"] is True

    def test_bindings_summary_in_json(self):
        result = _make_result()
        data = json.loads(stack_to_json(result))
        assert "bindings_summary" in data
        assert data["bindings_summary"]["resolved_ok"] == 1

    def test_binding_changes_in_json(self):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.RUNTIME_SYMBOL_PROVIDER_CHANGED,
            symbol="process", description="moved provider",
            old_value="liba.so.1", new_value="libb.so.1",
        )
        result = _make_result(binding_changes=[change])
        data = json.loads(stack_to_json(result))
        assert "binding_changes" in data
        assert data["binding_changes"][0]["kind"] == "runtime_symbol_provider_changed"
        assert data["binding_changes"][0]["old_value"] == "liba.so.1"

    def test_no_binding_changes_key_when_empty(self):
        data = json.loads(stack_to_json(_make_result()))
        assert "binding_changes" not in data


class TestStackToMarkdown:
    def test_basic_markdown_structure(self):
        md = stack_to_markdown(_make_result())
        assert "# Stack Report:" in md
        assert "PASS" in md
        assert "Dependency Tree" in md
        assert "Symbol Binding Summary" in md

    def test_environments_shown(self):
        md = stack_to_markdown(_make_result(baseline_env="/base", candidate_env="/cand"))
        assert "Baseline" in md
        assert "Candidate" in md

    def test_environments_hidden_when_same(self):
        md = stack_to_markdown(_make_result(baseline_env="/same", candidate_env="/same"))
        assert "## Environments" not in md

    def test_binding_changes_section(self):
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.RUNTIME_WEAK_RESOLUTION_CHANGED,
            symbol="opt_feature", description="weak reference now resolves",
        )
        md = stack_to_markdown(_make_result(binding_changes=[change]))
        assert "## Runtime Binding Changes" in md
        assert "runtime_weak_resolution_changed" in md
        assert "weak reference now resolves" in md

    def test_no_binding_changes_section_when_empty(self):
        md = stack_to_markdown(_make_result())
        assert "Runtime Binding Changes" not in md

    def test_unresolved_section(self):
        md = stack_to_markdown(_make_result(unresolved=[("/app", "libmissing.so")]))
        assert "Unresolved Libraries" in md
        assert "libmissing.so" in md

    def test_missing_symbols_section(self):
        md = stack_to_markdown(_make_result(
            missing=[_binding(BindingStatus.MISSING, symbol="foo_init", provider=None)],
        ))
        assert "Missing Symbols" in md
        assert "foo_init" in md

    def test_stack_changes_section_removed(self):
        md = stack_to_markdown(_make_result(
            stack_changes=[StackChange(library="libfoo.so", change_type="removed")],
        ))
        assert "Stack Changes" in md
        assert "removed" in md

    def test_stack_changes_section_added(self):
        md = stack_to_markdown(_make_result(
            stack_changes=[StackChange(library="libfoo.so", change_type="added")],
        ))
        assert "Stack Changes" in md
        assert "new in candidate" in md.lower() or "added" in md.lower()

    def test_footer_present(self):
        md = stack_to_markdown(_make_result())
        assert "abicheck" in md
        assert "---" in md

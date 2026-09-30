"""Contract of ``l5_ast_pass``: one clang AST dump per TU for every L5 family.

The six clang-backed graph families used to each own an extractor that
dumped every TU itself. ``run_ast_passes`` replaced them; these tests state
what it owes each family, as invariants over generated inputs:

- every TU with a source is dumped exactly once, whatever the family count;
- each family's result and diagnostics equal an *independent* derivation --
  ``merge([parse(ast(cu)) ...])`` and the per-TU dump + own-parse-error
  diagnostics, computed here directly from the family's own functions, never
  through the runner;
- a family's parse failure degrades only that family, only that TU;
- the answer does not depend on worker completion order.

The dump is faked (no compiler), deterministically per TU.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource import l5_ast_pass
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.l5_ast_pass import (
    L5_AST_PASSES,
    AstPass,
    run_ast_passes,
    run_l5_ast_pass,
)


def _ast(cu: CompileUnit) -> dict[str, Any]:
    return {"kind": "TranslationUnitDecl", "unit": cu.source}


@dataclass
class _FakeDump:
    """Deterministic ``run_clang_ast_dump`` stand-in that counts every dump."""

    failing: frozenset[str] = frozenset()
    jitter: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def source_of(self, argv: list[str]) -> str:
        return argv[-1]

    def __call__(self, clang_bin, argv, *, cwd, diagnostics):
        if self.jitter:
            time.sleep(random.random() / 200)
        src = self.source_of(argv)
        with self._lock:
            self.calls.append(src)
        diagnostics.append(f"dumped {src}")
        if any(src.endswith(f) for f in self.failing):
            diagnostics.append(f"clang produced no AST for {src}")
            return None
        return {"kind": "TranslationUnitDecl", "unit": src}


class _ParseError(ValueError):
    pass


def _family(name: str, raising: frozenset[str]) -> AstPass:
    def parse(ast: dict[str, Any], cu: CompileUnit) -> list[str]:
        if any(ast["unit"].endswith(r) for r in raising):
            raise _ParseError(f"{name} cannot read {ast['unit']}")
        return [f"{name}:{ast['unit']}"]

    def merge(per_unit: list[list[str]]) -> list[str]:
        return sorted({x for unit in per_unit for x in unit})

    return AstPass(name, parse, merge)


def _oracle(p: AstPass, units: list[CompileUnit], dump: _FakeDump):
    """What one family should get, derived from its own parse/merge directly."""
    per_unit, diags = [], []
    for cu in units:
        src = dump.source_of(_argv(cu))
        diags.append(f"dumped {src}")
        if any(src.endswith(f) for f in dump.failing):
            diags.append(f"clang produced no AST for {src}")
            continue
        try:
            per_unit.append(p.parse({"kind": "TranslationUnitDecl", "unit": src}, cu))
        except p.parse_errors as exc:
            diags.append(f"could not parse clang AST JSON: {exc}")
    return p.merge(per_unit), diags


def _argv(cu: CompileUnit) -> list[str]:
    return l5_ast_pass._safe_clang_args_from_compile_unit(cu)


_SOURCES = ["a.cpp", "b.cpp", "c.cpp", "d.cpp", "e.cpp"]


@settings(max_examples=120, deadline=None)
@given(
    sources=st.lists(st.sampled_from(_SOURCES + [""]), max_size=6),
    failing=st.frozensets(st.sampled_from(_SOURCES)),
    raising=st.lists(st.frozensets(st.sampled_from(_SOURCES)), min_size=1, max_size=4),
    jobs=st.sampled_from([1, 3]),
)
def test_each_family_gets_what_an_independent_derivation_gives(
    sources, failing, raising, jobs
) -> None:
    units = [
        CompileUnit(id=f"cu://{i}/{s}", source=f"src/{i}/{s}" if s else "")
        for i, s in enumerate(sources)
    ]
    passes = [_family(f"fam{i}", r) for i, r in enumerate(raising)]
    dump = _FakeDump(failing=failing, jitter=jobs > 1)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(l5_ast_pass, "run_clang_ast_dump", dump)
        mp.setattr(l5_ast_pass, "_call_graph_jobs", lambda n: min(n, jobs))
        outcomes = run_ast_passes(BuildEvidence(compile_units=units), "clang++", passes)

    with_source = [cu for cu in units if cu.source]
    # One dump per TU with a source, however many families read it.
    assert sorted(dump.calls) == sorted(_argv(cu)[-1] for cu in with_source)
    assert set(outcomes) == {p.name for p in passes}
    for p in passes:
        result, diagnostics = _oracle(p, with_source, dump)
        assert outcomes[p.name].result == result
        assert outcomes[p.name].diagnostics == diagnostics


def test_a_parse_error_outside_the_family_catch_list_is_not_swallowed(
    monkeypatch,
) -> None:
    """Only the declared exceptions degrade a TU; anything else is a bug."""

    def parse(ast, cu):
        raise KeyError("programming error")

    monkeypatch.setattr(l5_ast_pass, "run_clang_ast_dump", _FakeDump())
    target = BuildEvidence(compile_units=[CompileUnit(id="cu://a", source="a.cpp")])
    with pytest.raises(KeyError):
        run_ast_passes(target, "clang++", [AstPass("f", parse, list)])


def test_macro_family_degrades_a_type_error_like_any_malformed_ast(monkeypatch) -> None:
    """The macro parser int()s a line value unchecked; its family declares
    TypeError so a malformed line degrades the TU instead of aborting."""
    macro = next(p for p in L5_AST_PASSES if p.name == "macro_graph")
    assert TypeError in macro.parse_errors
    malformed = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "f",
                "mangledName": "_Zf",
                "loc": {"file": "a.c", "line": None},
                "range": {
                    "begin": {"file": "a.c", "line": None},
                    "end": {"file": "a.c", "line": None},
                },
            }
        ],
    }
    monkeypatch.setattr(l5_ast_pass, "run_clang_ast_dump", lambda *a, **k: malformed)
    target = BuildEvidence(compile_units=[CompileUnit(id="cu://a", source="a.c")])
    out = run_ast_passes(target, "clang++", [macro])["macro_graph"]
    assert out.result == []
    assert any("could not parse clang AST JSON" in d for d in out.diagnostics)


def test_every_real_family_reads_the_same_single_dump(monkeypatch) -> None:
    """The production family list, end to end: N TUs -> N dumps, not 6N."""
    dump = _FakeDump()
    monkeypatch.setattr(l5_ast_pass, "run_clang_ast_dump", dump)
    units = [CompileUnit(id=f"cu://{i}", source=f"/p/src/f{i}.cpp") for i in range(5)]
    outcomes = run_ast_passes(BuildEvidence(compile_units=units), "clang++")
    assert len(dump.calls) == 5
    assert set(outcomes) == {p.name for p in L5_AST_PASSES}
    for out in outcomes.values():
        assert out.diagnostics == [f"dumped {cu.source}" for cu in units]


def test_argv_and_cwd_are_the_replay_context_of_each_unit(monkeypatch) -> None:
    import os

    captured: list[tuple[list[str], str | None]] = []

    def dump(clang_bin, argv, *, cwd, diagnostics):
        captured.append((argv, cwd))
        return {"kind": "TranslationUnitDecl", "inner": []}

    monkeypatch.setattr(l5_ast_pass, "run_clang_ast_dump", dump)
    home = os.path.expanduser("~")
    cu = CompileUnit(
        id="cu://x",
        source="~/AppData/Local/Temp/t.cpp",
        directory="~/AppData/Local/Temp",
        argv=["/usr/bin/g++", "victim.cpp"],
        language="CXX",
        standard="c++17",
    )
    run_ast_passes(BuildEvidence(compile_units=[cu]), "clang++")
    assert len(captured) == 1
    argv, cwd = captured[0]
    assert argv[-2:] == ["--", f"{home}/AppData/Local/Temp/t.cpp"]
    assert cwd == f"{home}/AppData/Local/Temp"


class TestRunL5AstPass:
    def _merged(self) -> BuildEvidence:
        return BuildEvidence(
            compile_units=[
                CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
                CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
            ]
        )

    def test_missing_clang_runs_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(l5_ast_pass, "_clang_available", lambda _b: False)
        monkeypatch.setattr(
            l5_ast_pass,
            "run_ast_passes",
            lambda *a, **k: pytest.fail("must not run without clang"),
        )
        run = run_l5_ast_pass(self._merged(), "clang")
        assert run.clang_available is False
        assert run.outcomes == {}

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("clang", "clang++"),
            ("clang++", "clang++"),
            ("/opt/clang-18", "/opt/clang-18"),
        ],
    )
    def test_plain_clang_becomes_a_cxx_driver(
        self, monkeypatch, given, expected
    ) -> None:
        seen: list[str] = []
        monkeypatch.setattr(
            l5_ast_pass, "_clang_available", lambda b: seen.append(b) or True
        )
        monkeypatch.setattr(l5_ast_pass, "run_ast_passes", lambda t, b: {"x": b})
        run = run_l5_ast_pass(self._merged(), given)
        assert seen == [expected]
        assert run.clang_bin == expected

    @pytest.mark.parametrize(
        ("changed", "scoped", "sources", "narrowed"),
        [
            (("src/a.cpp",), None, ["src/a.cpp"], True),
            (("include/x.h",), None, ["src/a.cpp", "src/b.cpp"], False),
            (
                (),
                [CompileUnit(id="cu://src/b.cpp", source="src/b.cpp")],
                ["src/b.cpp"],
                True,
            ),
            ((), None, ["src/a.cpp", "src/b.cpp"], False),
        ],
    )
    def test_scope_is_decided_once_for_every_family(
        self, monkeypatch, changed, scoped, sources, narrowed
    ) -> None:
        targets: list[BuildEvidence] = []
        monkeypatch.setattr(l5_ast_pass, "_clang_available", lambda _b: True)
        monkeypatch.setattr(
            l5_ast_pass, "run_ast_passes", lambda t, b: targets.append(t) or {"x": 1}
        )
        run = run_l5_ast_pass(self._merged(), "clang++", changed, scoped)
        assert len(targets) == 1
        assert [cu.source for cu in run.target.compile_units] == sources
        assert targets[0] is run.target
        assert run.narrowed is narrowed


def test_inline_and_collect_paths_share_one_orchestrator(monkeypatch) -> None:
    """The out-of-band ``collect`` path used to keep its own copy of the pass
    list and fell behind it three times; it now calls the same function."""
    from abicheck import cli_buildsource_helpers
    from abicheck.buildsource import inline
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.workflows import extraction

    # The collect path reaches it through the workflows layer's re-export,
    # which must be the very same function object.
    assert extraction.fold_semantic_graphs is l5_ast_pass.fold_semantic_graphs
    calls: list[str] = []
    monkeypatch.setattr(
        extraction, "fold_semantic_graphs", lambda *a, **k: calls.append("collect")
    )
    cli_buildsource_helpers._collect_source_graph(
        BuildEvidence(compile_units=[CompileUnit(id="cu://a", source="a.cpp")]),
        [],
        source_graph="summary",
        changed_paths=(),
        kythe_entries=None,
        codeql_results=None,
        codeql_extends_results=None,
        surface=SourceAbiSurface(),
        clang_bin="clang++",
    )
    monkeypatch.setattr(
        l5_ast_pass, "fold_semantic_graphs", lambda *a, **k: calls.append("inline")
    )
    inline._build_inline_graph(
        BuildEvidence(compile_units=[CompileUnit(id="cu://a", source="a.cpp")]),
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
    )
    assert calls == ["collect", "inline"]

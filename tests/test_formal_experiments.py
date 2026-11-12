"""Reproducibility tests for the formal-methods experiments in ``formal/``.

These pin the *results* the experiment report (``formal/EXPERIMENTS.md``)
states, so a change that invalidates one turns a test red instead of leaving
the report silently stale. They are conformance and model-checking runs, not
proofs about the Python implementation.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FORMAL = Path(__file__).resolve().parent.parent / "formal"


def _load(path: Path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)
    return mod


def test_e7_count_trigger_sound_without_same_call_additions() -> None:
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    states, trace = chk.check(5, 3, adds=False)
    assert trace is None
    assert states > 1000  # the space was actually explored


def test_e7_count_trigger_misses_drop_plus_bucket_addition() -> None:
    """Shortest counterexample: a step drops finding 1 and puts a *new*
    object 2 into a bucket in the same call; the counts balance, so the
    trigger does not fire and finding 1 is neither visible, bucketed nor
    recorded."""
    chk = _load(FORMAL / "tla" / "check_disposition.py")
    _, trace = chk.check(4, 2, adds=True)
    assert trace is not None and len(trace) == 2
    (c0, b0, l0, s0, *_), (c1, b1, l1, s1, *_) = trace
    vanished = s1 - (c1 | b1 | l1)
    assert vanished and vanished <= c0
    assert len(c1) + len(b1 - b0) == len(c0)  # why the count trigger stays silent


def test_lean_layout_table_covers_declared_domain() -> None:
    rows = json.loads(
        (FORMAL / "lean" / "layout_table.json").read_text(encoding="utf-8")
    )["rows"]
    types = {"char", "short", "int", "double"}
    bases = {tuple(r["base"]) for r in rows}
    assert len(bases) == 4 + 16 + 64
    for r in rows:
        assert set(r["base"]) <= types and r["inserted"] in types
        assert 0 <= r["insert_at"] <= len(r["base"])
        assert len(r["new_offsets"]) == len(r["old_offsets"]) + 1
    assert len(rows) == sum((len(b) + 1) * 4 for b in bases)


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux")
    or platform.machine() not in ("x86_64", "aarch64")
    or sys.maxsize <= 2**32,
    reason="Linux ELF, LP64 experiment: the Lean table models x86-64/AArch64 alignment",
)
def test_e5_e6_layout_model_extraction_and_compare_agree(tmp_path: Path) -> None:
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("gcc and castxml required")
    exp = _load(FORMAL / "experiments" / "layout_experiments.py")
    assert exp.main(tmp_path) == 0
    s = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    n = 2 * s["cases"]
    assert s["E6a_model_vs_gcc"] == {"agree": n}
    assert s["E6b_extraction_vs_gcc"] == {
        "castxml_headers": {"agree": n},
        "dwarf_only": {"agree": n},
    }
    mismatches = {
        k: v
        for k, v in s["E5_compare_vs_model"].items()
        if "pred=True:found=False" in k or "pred=False:found=True" in k
    }
    assert mismatches == {}
    # E4: the edit class alone does not determine the outcome
    assert s["E4_class_outcomes"]["insert_before_field:NOT moved"] > 0
    assert s["E4_class_outcomes"]["append:size SAME"] > 0


# --- E8: scope completeness (ADR-065), formal/tla/ScopeCompleteness.tla ----


def test_e8_scope_spec_invariants_hold_for_three_members() -> None:
    chk = _load(FORMAL / "tla" / "check_scope.py")
    states, found = chk.check(3)
    assert found is None
    assert states > 1000  # the space was actually explored


@pytest.mark.parametrize(
    ("mutant", "invariant"),
    [
        ("classify-unguarded", "I2 RemovalNeedsProof"),
        ("warn-clean-zero", "I1 NoCleanZeroComparison"),
        ("failed-ops-dropped", "I3 FailedNeverClean"),
    ],
)
def test_e8_checker_detects_seeded_spec_faults(mutant: str, invariant: str) -> None:
    """A checker that cannot fail proves nothing: each seeded fault must
    produce a counterexample for the invariant it breaks."""
    chk = _load(FORMAL / "tla" / "check_scope.py")
    _, found = chk.check(3, chk.Spec(mutant))
    assert found is not None
    bad, trace = found
    assert invariant in bad
    assert len(trace) >= 2  # a real path, not an initial state


def test_e8_real_completeness_code_satisfies_i1_and_i2() -> None:
    """Conformance on the real aggregation (up to 2 members, all evidence and
    policy settings): zero comparisons never exit 0, and no removal exit or
    retired member without NEW's inventory proven."""
    audit = _load(FORMAL / "experiments" / "scope_completeness_audit.py")
    res = audit.audit(2)
    assert res.zero_comparison_runs > 0
    assert [v for v in res.violations if v[0].startswith("I1")] == []
    assert [v for v in res.violations if v[0].startswith("I2")] == []


def test_e8_failed_member_never_reads_clean_on_real_code() -> None:
    audit = _load(FORMAL / "experiments" / "scope_completeness_audit.py")
    res = audit.audit(2)
    assert [v for v in res.violations if v[0].startswith("I3")] == []


# --- TLC / Python correspondence (formal/tla/compare_tlc.py) ---------------
# TLC itself runs in the `tlc` job of .github/workflows/formal-lean.yml. These
# tests pin the parts that do not need Java: the TLA+ value and DOT parsers,
# and that each Python checker's full graph agrees with its own BFS.


def test_tlc_value_parser_reads_sets_functions_and_scalars() -> None:
    cmp = _load(FORMAL / "tla" / "compare_tlc.py")
    assert cmp.parse_value("{}") == frozenset()
    assert cmp.parse_value("{f1, f2}") == frozenset({("mv", "f1"), ("mv", "f2")})
    assert cmp.parse_value('(m1 :> "absent" @@ m2 :> "failed")') == (
        "fn",
        frozenset({(("mv", "m1"), "absent"), (("mv", "m2"), "failed")}),
    )
    assert cmp.parse_value("TRUE") is True
    assert cmp.parse_value("<<1, -2>>") == (1, -2)
    with pytest.raises(ValueError):
        cmp.parse_value("{f1, f2")


def test_tlc_dot_parser_maps_states_edges_and_stutter() -> None:
    """A two-node TLC dump: an initial node (style=filled), a Step edge and a
    self-loop. The self-loop is stuttering and is reported separately."""
    cmp = _load(FORMAL / "tla" / "compare_tlc.py")
    a = r"/\\ x = {f1}\n/\\ n = 0"
    b = r"/\\ x = {}\n/\\ n = 1"
    dot = "\n".join(
        [
            "strict digraph DiskGraph {",
            f'1 [label="{a}",style = filled]',
            '1 -> -2 [label="Step",color="black",fontcolor="black"];',
            f'-2 [label="{b}"];',
            '-2 -> -2 [label="Next",color="black",fontcolor="black"];',
            "}",
        ]
    )

    def to_state(v: dict) -> tuple:
        return (frozenset(x[1] for x in v["x"]), v["n"])

    g = cmp.parse_dot(dot, to_state)
    sa, sb = (frozenset({"f1"}), 0), (frozenset(), 1)
    assert g.init == {sa}
    assert g.states == {sa, sb}
    assert g.edges == {(sa, "Step", sb)}
    assert g.stutter == {sb}


@pytest.mark.parametrize("cfg", ["MC_noadds.cfg", "MC_adds.cfg", "MC_scope.cfg"])
def test_python_graph_matches_its_bfs_for_each_tlc_config(cfg: str) -> None:
    """The graph compare_tlc.py checks against TLC is the one the BFS
    explores: same reachable set where the invariant holds, and every edge
    stays inside the reachable set and starts the step counter/acquisition
    one step further."""
    cmp = _load(FORMAL / "tla" / "compare_tlc.py")
    model = next(m for m in cmp.MODELS if m.cfg == cfg)
    consts = cmp.read_constants(FORMAL / "tla" / cfg)
    (init, states, edges), n_check, violated, ok, _ = model.python(consts)
    assert init <= states
    assert all(s in states and d in states and s != d for s, _l, d in edges)
    assert {d for _s, _l, d in edges} | init == states
    assert violated == (model.expected != "holds")
    if not violated:
        assert n_check == len(states)
        assert all(ok(s) for s in states)
    else:
        assert any(not ok(s) for s in states)


@pytest.mark.skipif(
    not (os.environ.get("TLA2TOOLS_JAR") and shutil.which("java")),
    reason="needs java and TLA2TOOLS_JAR (the CI `tlc` job runs this directly)",
)
def test_tlc_and_python_state_graphs_agree() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(FORMAL / "tla" / "compare_tlc.py"),
            "--jar",
            os.environ["TLA2TOOLS_JAR"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count(" AGREE") == 3

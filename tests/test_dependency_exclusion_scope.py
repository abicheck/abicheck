"""`extract.dependency_exclusion`: the scope installs the predicate, and a
hybrid dump turns it off for both legs (Codex review: skipping only in the
clang leg handed `merge_snapshots` two legs that disagree)."""

from __future__ import annotations

from abicheck import dumper_hybrid
from abicheck.extract.dependency_exclusion import (
    active_dependency_predicate,
    dependency_exclusion_scope,
    suppress_dependency_exclusion,
)


def test_scope_installs_and_restores():
    assert active_dependency_predicate() is None
    with dependency_exclusion_scope(["/proj/include"]):
        pred = active_dependency_predicate()
        assert pred is not None
        assert pred("/usr/include/stdio.h") and not pred("/proj/include/a.h")
        with suppress_dependency_exclusion():
            assert active_dependency_predicate() is None
        assert active_dependency_predicate() is pred
    assert active_dependency_predicate() is None


def test_hybrid_dump_runs_both_legs_without_the_skip(monkeypatch, tmp_path):
    seen: list[tuple[str, object]] = []

    def fake_dump(so_path, headers, *, header_backend, **kwargs):
        seen.append((header_backend, active_dependency_predicate()))
        return object()

    monkeypatch.setattr(dumper_hybrid, "merge_snapshots", lambda a, b: a)
    monkeypatch.setattr(
        dumper_hybrid.closure_identity,
        "renumber_anonymous_closure_identities",
        lambda s: s,
    )
    with dependency_exclusion_scope(["/proj/include"]):
        dumper_hybrid.run_hybrid_dump(fake_dump, tmp_path / "x.so", [])
        assert active_dependency_predicate() is not None  # restored after
    assert seen == [("castxml", None), ("clang", None)]

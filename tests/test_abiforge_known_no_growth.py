"""The no-growth ratchet on ``abiforge/known.py`` (plan §7.2, WS-7 PR 2).

``check_known_no_growth`` in ``scripts/check_ai_readiness.py`` compares the
multiset of ``Known`` entry keys in ``abiforge/known.py`` with the committed
``ABIFORGE_KNOWN_BASELINE``. The gate must fail when an entry is added, and when
an entry is removed but the baseline still records it; it passes only when the
two multisets are equal. The cases below state each outcome directly, so the
oracle is the rule in the brief, not the gate's own helper.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location(
        "check_ai_readiness_known_gate", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# A committed baseline of three keys, one of them recorded twice.
BASELINE = ["k1", "k2", "k2"]


def test_unchanged_known_passes(gate):
    assert gate.check_known_no_growth(["k1", "k2", "k2"], BASELINE) == []


def test_added_entry_fails(gate):
    problems = gate.check_known_no_growth(["k1", "k2", "k2", "k3"], BASELINE)
    assert len(problems) == 1
    assert "k3" in problems[0]
    assert "does not record" in problems[0]


def test_added_second_copy_of_a_recorded_key_fails(gate):
    # Multiplicity counts: a third ``k2`` is growth even though ``k2`` is recorded.
    problems = gate.check_known_no_growth(["k1", "k2", "k2", "k2"], BASELINE)
    assert len(problems) == 1
    assert "k2" in problems[0]


def test_removed_entry_without_shrinking_baseline_fails(gate):
    problems = gate.check_known_no_growth(["k1", "k2"], BASELINE)
    assert len(problems) == 1
    assert "k2" in problems[0]
    assert "remove it from the baseline" in problems[0]


def test_removed_entry_and_shrunk_baseline_passes(gate):
    assert gate.check_known_no_growth(["k1", "k2"], ["k1", "k2"]) == []


def test_removing_one_of_two_copies_needs_one_baseline_removal(gate):
    # Two recorded ``k2`` entries, one removed: exactly one baseline line goes.
    assert gate.check_known_no_growth(["k1", "k2"], ["k1", "k2"]) == []
    problems = gate.check_known_no_growth(["k1", "k2"], ["k1", "k2", "k2"])
    assert len(problems) == 1


def test_real_known_py_matches_the_committed_baseline(gate):
    """The shipped state passes, and the baseline records every entry exactly once.

    The count oracle is ``len(KNOWN)``: a baseline that dropped an entry without
    the entry being removed from ``known.py`` fails here as well as in the gate.
    """
    from abiforge.known import KNOWN

    assert len(gate.ABIFORGE_KNOWN_BASELINE) == len(KNOWN)
    current = [gate.known_entry_key(entry) for entry in KNOWN]
    assert gate.check_known_no_growth(current, gate.ABIFORGE_KNOWN_BASELINE) == []


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_with_base_baseline(tmp_path: Path, keys: list[str]) -> Path:
    """A repository whose ``origin/main`` commits a script with this baseline."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    body = (
        "ABIFORGE_KNOWN_BASELINE: tuple[str, ...] = (\n"
        + "".join(f"    {k!r},\n" for k in keys)
        + ")\n"
    )
    (repo / "scripts" / "check_ai_readiness.py").write_text(body, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "scripts/check_ai_readiness.py")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def test_parse_known_baseline_reads_the_constant_or_none(gate):
    source = "x = 1\nABIFORGE_KNOWN_BASELINE: tuple[str, ...] = ('a', 'b', 'a')\n"
    assert gate.parse_known_baseline(source) == {"a": 2, "b": 1}
    assert gate.parse_known_baseline("x = 1\n") is None


def test_growth_against_the_base_fails_only_when_a_count_rises(gate):
    base = gate.Counter({"a": 1, "b": 1})
    (problem,) = gate.check_known_baseline_growth(gate.Counter({"a": 1, "b": 2}), base)
    assert "for b (1 -> 2)" in problem
    (problem,) = gate.check_known_baseline_growth(
        gate.Counter({"a": 1, "b": 1, "c": 1}), base
    )
    assert "for c (0 -> 1)" in problem
    assert gate.check_known_baseline_growth(gate.Counter({"a": 1, "b": 1}), base) == []
    assert gate.check_known_baseline_growth(gate.Counter({"a": 1}), base) == []


def test_base_leg_fails_when_the_branch_adds_a_recorded_key(gate, tmp_path):
    repo = _repo_with_base_baseline(tmp_path, ["a", "b"])
    problems, note = gate.check_known_baseline_vs_base(
        repo, {}, gate.Counter(["a", "b", "c"])
    )
    assert note is None
    assert len(problems) == 1 and "for c (0 -> 1)" in problems[0]


def test_base_leg_passes_when_unchanged_or_shrunk(gate, tmp_path):
    repo = _repo_with_base_baseline(tmp_path, ["a", "b"])
    assert gate.check_known_baseline_vs_base(repo, {}, gate.Counter(["a", "b"])) == (
        [],
        None,
    )
    assert gate.check_known_baseline_vs_base(repo, {}, gate.Counter(["a"])) == (
        [],
        None,
    )


def test_base_leg_is_reported_not_evaluated_without_a_base_ref(gate, tmp_path):
    repo = tmp_path / "bare"
    repo.mkdir()
    _git(repo, "init", "-q")
    problems, note = gate.check_known_baseline_vs_base(repo, {}, gate.Counter(["a"]))
    assert problems == []
    assert note == "no base ref resolved (GITHUB_BASE_REF, origin/main)"


def test_base_leg_uses_github_base_ref_when_set(gate, tmp_path):
    repo = _repo_with_base_baseline(tmp_path, ["a"])
    _git(repo, "update-ref", "refs/remotes/origin/release", "HEAD")
    assert gate.base_ref_for_known_gate(repo, {"GITHUB_BASE_REF": "release"}) == (
        "origin/release"
    )
    assert gate.base_ref_for_known_gate(repo, {}) == "origin/main"


def test_base_leg_is_reported_when_the_base_has_no_baseline(gate, tmp_path):
    repo = tmp_path / "old"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check_ai_readiness.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "scripts/check_ai_readiness.py")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    problems, note = gate.check_known_baseline_vs_base(repo, {}, gate.Counter(["a"]))
    assert problems == []
    assert note is not None and "has no ABIFORGE_KNOWN_BASELINE" in note

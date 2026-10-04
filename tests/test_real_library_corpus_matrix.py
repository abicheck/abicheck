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

"""The real-library corpus lane runs one job per corpus library.

It used to be one serial job over every pair, and its first header-aware run
spent 93 minutes on a single protobuf pair before being cancelled with six
pairs never started. Splitting it only keeps the gate whole if three things
hold, each pinned here:

* the workflow's matrix names exactly the corpus file's libraries -- a library
  added to ``compat_corpus.json`` but not to the matrix would never run;
* ``--library`` selects exactly that library's pairs, so the union of the jobs
  is the whole corpus and no pair runs twice;
* a compare that exceeds ``--compare-timeout`` is recorded as that pair's
  error, which the gate already treats as a failure ("not evaluated").
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import _yaml_fast
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills-src" / "evaluation" / "validation" / "scripts"
CORPUS = (
    REPO / "skills-src" / "evaluation" / "validation" / "data" / "compat_corpus.json"
)
WORKFLOW = REPO / ".github" / "workflows" / "real-library-corpus.yml"

_spec = importlib.util.spec_from_file_location(
    "run_compat_corpus", SCRIPTS / "run_compat_corpus.py"
)
assert _spec and _spec.loader
rcc = importlib.util.module_from_spec(_spec)
sys.modules["run_compat_corpus"] = rcc
_spec.loader.exec_module(rcc)


def _corpus_libraries() -> set[str]:
    return {
        p["library"] for p in json.loads(CORPUS.read_text(encoding="utf-8"))["pairs"]
    }


def test_matrix_names_exactly_the_corpus_libraries() -> None:
    job = _yaml_fast.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["corpus"]
    matrix = job["strategy"]["matrix"]["library"]
    assert sorted(matrix) == sorted(_corpus_libraries())
    assert len(matrix) == len(set(matrix)), "a library listed twice runs twice"
    assert job["strategy"]["fail-fast"] is False, (
        "one red library must not cancel the rest"
    )
    run = next(
        s["run"] for s in job["steps"] if "run_compat_corpus.py" in s.get("run", "")
    )
    assert '--library "$CORPUS_LIBRARY"' in run and "--compare-timeout" in run


def _entry(pair: str, library: str) -> dict:
    return {
        "pair": pair,
        "library": library,
        "pkg": library,
        "old_ver": "1.0.0",
        "new_ver": "1.0.1",
        "old_file": "a.conda",
        "new_file": "b.conda",
        "expected": "COMPATIBLE",
        "expected_break_kinds": [],
        "ground_truth_source": "test",
    }


@pytest.mark.parametrize("selected", [["a"], ["b"], ["a", "b"], ["c"]])
def test_library_selects_exactly_its_pairs(tmp_path: Path, selected: list[str]) -> None:
    pairs = [_entry("a1", "a"), _entry("a2", "a"), _entry("b1", "b"), _entry("c1", "c")]
    corpus = tmp_path / "c.json"
    corpus.write_text(json.dumps({"schema": rcc.CORPUS_SCHEMA, "pairs": pairs}))
    rdir = tmp_path / "r"
    rdir.mkdir()
    for p in pairs:
        (rdir / f"{p['pair']}.json").write_text(json.dumps({"libraries": {}}))
    out = tmp_path / "o"
    argv = [
        "--corpus",
        str(corpus),
        "--baseline",
        str(tmp_path / "none.json"),
        "--out-dir",
        str(out),
        "--results-dir",
        str(rdir),
    ]
    for lib in selected:
        argv += ["--library", lib]
    rcc.main(argv)
    written = {p.stem for p in out.glob("*.json") if p.stem != "gate"}
    assert written == {p["pair"] for p in pairs if p["library"] in selected}


def test_unknown_library_is_a_usage_error(tmp_path: Path) -> None:
    corpus = tmp_path / "c.json"
    corpus.write_text(
        json.dumps({"schema": rcc.CORPUS_SCHEMA, "pairs": [_entry("a1", "a")]})
    )
    with pytest.raises(SystemExit) as exc:
        rcc.main(
            [
                "--corpus",
                str(corpus),
                "--out-dir",
                str(tmp_path / "o"),
                "--library",
                "nope",
            ]
        )
    assert exc.value.code == 2


def test_a_timed_out_compare_makes_the_pair_not_evaluated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import conda_harness as ch

    monkeypatch.setattr(
        ch, "fetch_file", lambda url, dest, timeout: dest.write_bytes(b"x")
    )
    monkeypatch.setattr(
        ch, "extract_sos", lambda pkg, dest: {"libslow.so": str(dest / "libslow.so")}
    )

    def _slow(*args: object, timeout: float | None = None, **kw: object) -> dict:
        raise subprocess.TimeoutExpired(cmd="abicheck compare", timeout=timeout or 0)

    monkeypatch.setattr(ch, "run_abicheck", _slow)
    result = rcc.run_pair(_entry("p", "lib"), tmp_path, compare_timeout=5)
    assert result["libraries"]["libslow.so"]["error"] == "compare timed out after 5s"
    assert result["error"], "a timed-out library must leave the pair not evaluated"
    gate = rcc.evaluate_gate([_entry("p", "lib")], {"p": result}, None)
    assert not gate.passed


# Each status the report can give a finding; only NOT_EVALUATED is unscored.
_STATUSES = ("EVALUATED", "NOT_EVALUATED", None)
_KINDS = ("func_removed_elf_only", "func_removed", "param_renamed", "enum_member_added")


@pytest.mark.parametrize("status", _STATUSES)
@pytest.mark.parametrize("kind", _KINDS)
def test_only_a_scored_finding_counts_as_a_break(kind: str, status: str | None) -> None:
    """The gate's break count is what policy scored, never the kind alone.

    Oracle: a finding counts as a break iff policy evaluated it (status is not
    ``NOT_EVALUATED``) and its kind is intrinsically breaking/API-breaking.
    The zstd 1.5.5->1.5.7 report (verdict COMPATIBLE_WITH_RISK, summary
    ``breaking: 0``) carried three unscored ``func_removed_elf_only`` findings
    that the gate used to count as 3 BREAKING.
    """
    from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind

    change = {"kind": kind}
    if status is not None:
        change["compatibility_evaluation_status"] = status
    lib = rcc.summarize_report({"verdict": "X", "changes": [change, dict(change)]})
    tot = rcc.pair_totals({"libraries": {"lib.so": lib}})
    intrinsic = ChangeKind(kind) in BREAKING_KINDS | API_BREAK_KINDS
    scored = status != "NOT_EVALUATED"
    assert tot["breaking"] + tot["api_break"] == (2 if intrinsic and scored else 0)
    # Recorded either way: an unscored finding stays visible as a count.
    assert sum(tot["non_breaking"].values()) + tot["breaking"] + tot["api_break"] == 2
    if not scored:
        assert tot["non_breaking"] == {kind + rcc.NOT_EVALUATED_SUFFIX: 2}


def test_scoring_matrix_is_not_vacuous() -> None:
    from abicheck.checker_policy import BREAKING_KINDS, ChangeKind

    assert any(ChangeKind(k) in BREAKING_KINDS for k in _KINDS)
    assert any(ChangeKind(k) not in BREAKING_KINDS for k in _KINDS)


_REFUSED = {
    "report_schema_version": "5.13",
    "verdict": None,
    "reason": {"kind": "scope_mismatch", "message": "not comparable"},
    "run_outcome": {"operational": "not_comparable", "compatibility": None},
}


@pytest.mark.parametrize("expected", ["COMPATIBLE", "BREAKING"])
@pytest.mark.parametrize(
    "report",
    [
        _REFUSED,
        {"verdict": None, "run_outcome": {"operational": "not_comparable"}},
        {"verdict": None},
        {},
    ],
    ids=["scope-mismatch", "no-reason", "bare-null-verdict", "empty-document"],
)
def test_a_report_without_a_verdict_leaves_the_pair_not_evaluated(
    expected: str, report: dict
) -> None:
    """A comparison abicheck refused (protobuf 6->7: ``scope_mismatch``)
    writes a report with no changes. The gate must not read "no changes" as
    "compared, nothing found" -- a clean pass for a known-compatible pair on
    a run that compared nothing. Oracle: whether the report carries a
    verdict, independent of how the gate counts findings."""
    lib = rcc.summarize_report(report)
    assert lib["error"].startswith("abicheck reached no verdict")
    entry = _entry("p", "lib") | {"expected": expected}
    result = {"pair": "p", "libraries": {"lib.so": lib}, "error": lib["error"]}
    gate = rcc.evaluate_gate([entry], {"p": result}, None)
    assert not gate.passed
    assert any("not evaluated" in f for f in gate.failures), gate.failures


def test_a_report_with_a_verdict_is_evaluated() -> None:
    """Negative control: a real verdict with zero findings is a result."""
    lib = rcc.summarize_report({"verdict": "NO_CHANGE", "changes": []})
    assert "error" not in lib
    result = {"pair": "p", "libraries": {"lib.so": lib}}
    assert rcc.evaluate_gate([_entry("p", "lib")], {"p": result}, None).passed

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

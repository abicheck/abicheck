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

"""Tests for the ADR-035 D3 path-glob risk scoring (G19.3, scan orchestrator).

The score is the *strongest single signal present* and only ever drives the
opt-in ``--source-method auto`` escalation, so these tests pin the signal
*ordering* (the normative part) and the score→method mapping, not exact weights.
Pure-Python, default lane.
"""

from __future__ import annotations

from abicheck.buildsource.risk import (
    RiskRule,
    RiskRules,
    RiskScore,
    recommend_source_method,
    score_changed_paths,
)


def test_empty_changed_set_scores_zero_and_recommends_s0():
    score = score_changed_paths([])
    assert score.total == 0
    assert score.n_paths == 0
    assert score.recommended_method == "s0"


def test_docs_only_change_is_negative_and_de_escalates():
    score = score_changed_paths(["docs/guide.md", "README.md", "tests/test_x.py"])
    assert score.total < 0
    assert score.recommended_method == "s0"


def test_public_header_dominates_a_docs_change():
    # The strongest signal wins: a header touch escalates regardless of how many
    # docs files ride along (ADR-035 D3 ordering, not additive).
    score = score_changed_paths(["include/foo.h", "docs/a.md", "docs/b.md"])
    assert score.total == 50
    assert score.matched.get("public_headers") == 1
    assert score.matched.get("docs_tests") == 2
    assert score.recommended_method == "s5"


def test_signal_ordering_header_beats_build_beats_internal():
    header = score_changed_paths(["include/foo.h"]).total
    export = score_changed_paths(["libfoo.map"]).total
    build = score_changed_paths(["CMakeLists.txt"]).total
    internal = score_changed_paths(["src/foo.cpp"]).total
    docs = score_changed_paths(["docs/x.md"]).total
    # ADR-035 D3 normative ordering: public header > export map > ABI flag >
    # internal source > docs/tests.
    assert header > export > build > internal > docs


def test_internal_source_only_uses_lexical_method():
    score = score_changed_paths(["src/impl.cpp", "src/util.cc"])
    assert score.total == 20
    assert score.recommended_method == "s3"


def test_cpp_test_only_change_stays_on_docs_floor():
    # A C/C++ test file matches the generic internal_source suffix glob AND the
    # docs_tests rule; the test/docs floor must win so auto stays at s0 (Codex).
    score = score_changed_paths(["tests/foo_test.cpp", "test/bar_test.cc"])
    assert score.total < 0
    assert score.matched.get("docs_tests")
    assert score.recommended_method == "s0"


def test_strong_signal_beats_co_matched_docs_rule():
    # A public header that also trips the *_test.* pattern is still public API —
    # the strong signal wins over the de-escalation floor.
    score = score_changed_paths(["include/widget_test.h"])
    assert score.total == 50
    assert score.recommended_method == "s5"


def test_build_flag_change_escalates_to_semantic():
    score = score_changed_paths(["cmake/flags.cmake"])
    assert score.total == 40
    assert score.recommended_method == "s5"


def test_basename_glob_matches_anywhere_in_tree():
    # ``CMakeLists.txt`` is a slash-free glob; it must match in a subdirectory.
    score = score_changed_paths(["libs/foo/CMakeLists.txt"])
    assert score.matched.get("build_abi_flags") == 1


def test_unmatched_path_is_neutral_not_negative():
    score = score_changed_paths(["some/random/file.bin"])
    assert score.total == 0
    assert score.matched == {}
    assert score.recommended_method == "s0"


def test_custom_rules_from_dict_override_defaults():
    rules = RiskRules.from_dict(
        {
            "api": {"paths": ["api/**"], "weight": 99},
            "noise": {"paths": ["vendor/**"], "weight": -50},
        }
    )
    assert score_changed_paths(["api/v1.h"], rules).total == 99
    assert score_changed_paths(["vendor/x.cpp"], rules).total == -50
    # A path matching none of the *custom* rules is neutral (default profile not used).
    assert score_changed_paths(["include/foo.h"], rules).total == 0


def test_from_dict_empty_falls_back_to_default_profile():
    assert RiskRules.from_dict(None).rules == RiskRules.default().rules
    assert RiskRules.from_dict({}).rules == RiskRules.default().rules
    # A block with no usable rules also falls back rather than dropping all signal.
    assert RiskRules.from_dict({"bad": "notadict"}).rules == RiskRules.default().rules


def test_from_dict_tolerates_string_paths_and_bad_weight():
    rules = RiskRules.from_dict({"one": {"paths": "include/**", "weight": "notanint"}})
    rule = next(r for r in rules.rules if r.name == "one")
    assert rule.paths == ("include/**",)
    assert rule.weight == 0


def test_recommend_source_method_is_monotonic_and_capped_at_s5():
    methods = [
        recommend_source_method(RiskScore(total=t))
        for t in (-100, 0, 10, 20, 30, 40, 50, 1000)
    ]
    order = {"s0": 0, "s3": 1, "s5": 2, "s6": 3}
    ranks = [order[m] for m in methods]
    assert ranks == sorted(ranks)  # non-decreasing in the score
    assert "s6" not in methods  # auto never picks full s6 (baseline-only)


def test_rule_matches_normalizes_backslashes():
    rule = RiskRule("h", ("include/**",), 50)
    assert rule.matches("include\\foo\\bar.h")


def test_score_to_dict_is_serializable():
    d = score_changed_paths(["include/x.h"]).to_dict()
    assert d["total"] == 50
    assert d["recommended_method"] == "s5"
    assert d["matched"]["public_headers"] == 1

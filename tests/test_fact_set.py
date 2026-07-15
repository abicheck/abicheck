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

"""Tests for ADR-038 C.8: canonical fact-set identity, per-family coverage,
comparison-compatibility rules, and the SOURCE_FACT_COVERAGE_INCOMPLETE
source-replay finding."""

from __future__ import annotations

import pytest

from abicheck.buildsource import (
    SourceAbiSurface,
    SourceAbiTu,
    diff_source_abi,
    link_source_abi,
)
from abicheck.buildsource.fact_set import (
    FactCompatibility,
    FactSetIssue,
    check_fact_compatibility,
    check_fact_set_compatibility,
    hash_recipe_id,
    incomplete_families,
    rollup_coverage,
    rollup_fact_set,
)
from abicheck.buildsource.source_abi import (
    COVERAGE_STATES,
    SOURCE_ABI_FACT_SET_NAME,
    SOURCE_ABI_FACT_SET_VERSION,
    coverage_state_for_family,
    default_fact_set,
)
from abicheck.checker_policy import ChangeKind

# -- coverage_state_for_family decision table --------------------------------


@pytest.mark.parametrize(
    ("entities_present", "family_diagnostics_seen", "expected"),
    [
        (True, False, "complete"),
        (False, False, "empty-confirmed"),
        (True, True, "partial"),
        (False, True, "failed"),
    ],
)
def test_coverage_state_for_family(
    entities_present: bool, family_diagnostics_seen: bool, expected: str
) -> None:
    assert (
        coverage_state_for_family(
            entities_present=entities_present,
            family_diagnostics_seen=family_diagnostics_seen,
        )
        == expected
    )


def test_coverage_state_unsupported_overrides_everything() -> None:
    assert (
        coverage_state_for_family(
            entities_present=True, family_diagnostics_seen=True, unsupported=True
        )
        == "unsupported"
    )


def test_coverage_states_are_the_documented_five() -> None:
    assert COVERAGE_STATES == {
        "complete",
        "empty-confirmed",
        "partial",
        "unsupported",
        "failed",
    }


def test_default_fact_set_shape() -> None:
    fs = default_fact_set(
        producer="p", producer_version="1.2", compiler_version="18.1.3"
    )
    assert fs == {
        "name": SOURCE_ABI_FACT_SET_NAME,
        "version": SOURCE_ABI_FACT_SET_VERSION,
        "producer": "p",
        "producer_version": "1.2",
        "compiler_family": "clang",
        "compiler_version": "18.1.3",
    }


# -- SourceAbiTu.fact_set / .coverage round-trip -----------------------------


def test_source_abi_tu_fact_set_coverage_roundtrip() -> None:
    tu = SourceAbiTu(
        tu_id="cu://a.cpp",
        fact_set=default_fact_set(producer="p", producer_version="1"),
        coverage={"functions": "complete", "macros": "empty-confirmed"},
    )
    d = tu.to_dict()
    assert d["fact_set"]["producer"] == "p"
    assert d["coverage"]["functions"] == "complete"
    back = SourceAbiTu.from_dict(d)
    assert back.fact_set == tu.fact_set
    assert back.coverage == tu.coverage


def test_source_abi_tu_fact_set_coverage_default_empty_on_older_pack() -> None:
    """A pre-C.8 producer's dict has no fact_set/coverage keys — from_dict must
    not raise and must default to empty (forward-compat)."""
    tu = SourceAbiTu.from_dict({"id": "x", "tu_id": "cu://a.cpp"})
    assert tu.fact_set == {}
    assert tu.coverage == {}


def test_source_abi_tu_fact_set_malformed_type_does_not_raise() -> None:
    """A hand-edited/corrupt pack carrying a non-mapping ``fact_set`` (a string,
    a list) must not crash the parser — defensive ``.get()`` parsing, same
    forward-compat convention as every other field (CodeRabbit review)."""
    tu = SourceAbiTu.from_dict({"id": "x", "tu_id": "cu://a.cpp", "fact_set": "clang"})
    assert tu.fact_set == {}
    tu2 = SourceAbiTu.from_dict({"id": "x", "tu_id": "cu://a.cpp", "fact_set": [1, 2]})
    assert tu2.fact_set == {}


# -- rollup_fact_set / rollup_coverage ---------------------------------------


def _tu(fact_set: dict | None = None, coverage: dict | None = None) -> SourceAbiTu:
    return SourceAbiTu(fact_set=fact_set or {}, coverage=coverage or {})


def test_rollup_fact_set_consistent() -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    tus = [_tu(fact_set=fs), _tu(fact_set=dict(fs))]
    assert rollup_fact_set(tus) == fs


def test_rollup_fact_set_inconsistent_returns_empty() -> None:
    tus = [
        _tu(fact_set=default_fact_set(producer="a", producer_version="1")),
        _tu(fact_set=default_fact_set(producer="b", producer_version="1")),
    ]
    assert rollup_fact_set(tus) == {}


def test_rollup_fact_set_all_empty() -> None:
    assert rollup_fact_set([_tu(), _tu()]) == {}


def test_rollup_fact_set_mixed_present_and_missing_returns_empty() -> None:
    """A pack mixing a current TU record and a stale/pre-C.8 TU (no fact_set at
    all) must not report the non-empty subset's fact_set as the pack's common
    identity — the missing TU's coverage is simply unknown (Codex review)."""
    fs = default_fact_set(producer="p", producer_version="1")
    tus = [_tu(fact_set=fs), _tu(fact_set=dict(fs)), _tu()]
    assert rollup_fact_set(tus) == {}


def test_rollup_coverage_worst_of_wins() -> None:
    tus = [
        _tu(coverage={"functions": "complete", "macros": "complete"}),
        _tu(coverage={"functions": "partial", "macros": "complete"}),
    ]
    rolled = rollup_coverage(tus)
    assert rolled["functions"] == "partial"
    assert rolled["macros"] == "complete"


def test_rollup_coverage_failed_beats_partial() -> None:
    tus = [
        _tu(coverage={"functions": "partial"}),
        _tu(coverage={"functions": "failed"}),
    ]
    assert rollup_coverage(tus)["functions"] == "failed"


def test_rollup_coverage_omits_families_never_reported() -> None:
    tus = [_tu(coverage={"functions": "complete"})]
    rolled = rollup_coverage(tus)
    assert "macros" not in rolled


def test_rollup_coverage_missing_family_on_c8_tu_is_not_ignored() -> None:
    """A TU that declares the canonical fact_set (participates in C.8) but is
    missing a mandatory family's coverage entry must not let other TUs that
    did report it roll up as clean (Codex review, P2)."""
    fs = default_fact_set(producer="p", producer_version="1")
    tus = [
        _tu(fact_set=fs, coverage={"functions": "complete", "macros": "complete"}),
        # This TU never reported "macros" at all, though it does participate
        # in C.8 (has a fact_set) -- its silence must drag the rollup down.
        _tu(fact_set=dict(fs), coverage={"functions": "complete"}),
    ]
    rolled = rollup_coverage(tus)
    assert rolled["functions"] == "complete"
    assert rolled["macros"] == "failed"


def test_rollup_coverage_missing_family_on_pre_c8_tu_still_omitted() -> None:
    # A TU with no fact_set at all (pre-C.8 producer) missing a family entry
    # must not manufacture a "failed" state -- preserves the existing
    # forward-compat behavior for old baselines/fixtures.
    tus = [_tu(coverage={"functions": "complete"})]  # no fact_set
    rolled = rollup_coverage(tus)
    assert "macros" not in rolled


def test_rollup_coverage_unrecognized_state_coerces_to_failed() -> None:
    """A hand-written/third-party producer's misspelled or otherwise unknown
    coverage state (not in COVERAGE_STATES) must roll up as "failed", not
    pass through verbatim -- incomplete_families() only recognizes the
    documented states, so an unrecognized value slipping through untouched
    would make a malformed pack read as clean (Codex review, P2)."""
    fs = default_fact_set(producer="p", producer_version="1")
    tus = [_tu(fact_set=fs, coverage={"functions": "totally-not-a-real-state"})]
    rolled = rollup_coverage(tus)
    assert rolled["functions"] == "failed"
    assert "functions" in incomplete_families(rolled)


# -- incomplete_families ------------------------------------------------------


def test_incomplete_families() -> None:
    cov = {
        "functions": "complete",
        "macros": "partial",
        "types": "failed",
        "templates": "unsupported",
    }
    assert incomplete_families(cov) == ["macros", "types"]


def test_incomplete_families_empty_when_all_clean() -> None:
    assert (
        incomplete_families({"functions": "complete", "macros": "empty-confirmed"})
        == []
    )


# -- check_fact_set_compatibility --------------------------------------------


def test_compatibility_both_empty_reports_unknown() -> None:
    issues = check_fact_set_compatibility({}, {})
    assert len(issues) == 1
    assert issues[0].rule == "fact_set_unknown"
    assert issues[0].severity == "warning"


def test_compatibility_one_side_empty_reports_unknown() -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    issues = check_fact_set_compatibility(fs, {})
    assert [i.rule for i in issues] == ["fact_set_unknown"]


def test_compatibility_matching_fact_sets_no_issues() -> None:
    fs = default_fact_set(producer="p", producer_version="1", compiler_version="18.1.3")
    assert check_fact_set_compatibility(fs, dict(fs)) == []


def test_compatibility_name_mismatch_is_error() -> None:
    """Two surfaces with different fact_set names must not pass silently just
    because their version numbers happen to match (Codex review)."""
    old = default_fact_set(producer="p", producer_version="1")
    new = dict(old)
    new["name"] = "some-other-fact-set"
    issues = check_fact_set_compatibility(old, new)
    assert any(
        i.rule == "fact_set_name_mismatch" and i.severity == "error" for i in issues
    )


def test_compatibility_version_mismatch_is_error() -> None:
    old = default_fact_set(producer="p", producer_version="1")
    new = dict(old)
    new["version"] = 2
    issues = check_fact_set_compatibility(old, new)
    assert any(
        i.rule == "fact_set_version_mismatch" and i.severity == "error" for i in issues
    )


def test_compatibility_producer_mismatch_is_warning() -> None:
    old = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    new = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    issues = check_fact_set_compatibility(old, new)
    rules = {i.rule for i in issues}
    assert "producer_mismatch" in rules
    assert all(i.severity == "warning" for i in issues if i.rule == "producer_mismatch")


def test_compatibility_same_producer_different_version_is_warning() -> None:
    """A producer release can change its canonicalization/hashing recipe
    without bumping fact_set.version — flag it so opaque body/template hashes
    are not silently trusted as comparable (Codex review)."""
    old = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.3")
    new = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    issues = check_fact_set_compatibility(old, new)
    rules = {i.rule for i in issues}
    assert "producer_version_mismatch" in rules
    assert "producer_mismatch" not in rules
    assert all(
        i.severity == "warning" for i in issues if i.rule == "producer_version_mismatch"
    )


def test_compatibility_same_producer_same_version_no_producer_issue() -> None:
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    issues = check_fact_set_compatibility(fs, dict(fs))
    assert not any(
        i.rule in ("producer_mismatch", "producer_version_mismatch") for i in issues
    )


def test_compatibility_same_producer_and_version_different_compiler_version_is_warning() -> (
    None
):
    """Same abicheck producer release, but loaded by a different compiler
    version (e.g. clang 16 vs clang 18) — the hash recipe ports the
    compiler's own JSON AST dump, so opaque hashes are not guaranteed
    byte-stable across compiler versions even here (Codex review)."""
    old = default_fact_set(
        producer="abicheck-clang-plugin",
        producer_version="0.4",
        compiler_version="16.0.0",
    )
    new = default_fact_set(
        producer="abicheck-clang-plugin",
        producer_version="0.4",
        compiler_version="18.1.3",
    )
    issues = check_fact_set_compatibility(old, new)
    rules = {i.rule for i in issues}
    assert "compiler_version_mismatch" in rules
    assert "producer_mismatch" not in rules
    assert "producer_version_mismatch" not in rules
    assert all(
        i.severity == "warning" for i in issues if i.rule == "compiler_version_mismatch"
    )


def test_compatibility_same_producer_version_and_compiler_version_no_issue() -> None:
    fs = default_fact_set(
        producer="abicheck-clang-plugin",
        producer_version="0.4",
        compiler_version="18.1.3",
    )
    issues = check_fact_set_compatibility(fs, dict(fs))
    assert not any(
        i.rule
        in (
            "producer_mismatch",
            "producer_version_mismatch",
            "compiler_version_mismatch",
        )
        for i in issues
    )


def test_compatibility_compiler_family_mismatch_is_warning() -> None:
    old = default_fact_set(producer="p", producer_version="1")
    new = dict(old)
    new["compiler_family"] = "gcc"
    issues = check_fact_set_compatibility(old, new)
    assert any(i.rule == "compiler_family_mismatch" for i in issues)


def test_fact_set_issue_is_frozen() -> None:
    issue = FactSetIssue("warning", "r", "m")
    assert issue.severity == "warning"
    with pytest.raises(AttributeError):
        issue.severity = "error"  # type: ignore[misc]


# -- link_source_abi rollup ---------------------------------------------------


def test_link_source_abi_stamps_fact_set_and_family_states() -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    tu = SourceAbiTu(
        tu_id="cu://a.cpp",
        fact_set=fs,
        coverage={"functions": "complete", "macros": "partial"},
    )
    surface = link_source_abi([tu])
    assert surface.coverage["fact_set"] == fs
    assert surface.coverage["fact_family_states"]["macros"] == "partial"


def test_link_source_abi_no_fact_set_stays_empty() -> None:
    surface = link_source_abi([SourceAbiTu(tu_id="cu://a.cpp")])
    assert surface.coverage["fact_set"] == {}
    assert surface.coverage["fact_family_states"] == {}


# -- diff_source_abi: SOURCE_FACT_COVERAGE_INCOMPLETE ------------------------


def _surface(**kw: object) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    for key, val in kw.items():
        setattr(s, key, val)
    return s


def test_diff_silent_when_neither_side_has_fact_set() -> None:
    """Existing/hand-built fixtures with no coverage metadata must not gain a
    new finding just because ADR-038 C.8 was implemented (forward-compat)."""
    old = _surface()
    new = _surface()
    changes = diff_source_abi(old, new)
    assert ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE not in {c.kind for c in changes}


def test_diff_fires_fact_set_unknown_when_family_states_present_but_fact_set_empty() -> (
    None
):
    """A mixed pack rolls fact_set up to {} (rollup_fact_set's stricter rule)
    while fact_family_states can still be non-empty from the TUs that did
    report — that combination must still surface fact_set_unknown, not be
    silently skipped (Codex review)."""
    old = _surface(
        coverage={"fact_set": {}, "fact_family_states": {"macros": "complete"}}
    )
    new = _surface(coverage={"fact_set": {}, "fact_family_states": {}})
    changes = diff_source_abi(old, new)
    matches = [
        c for c in changes if c.kind == ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    ]
    assert len(matches) == 1
    assert "fact_set_unknown" in matches[0].description


def test_diff_fires_on_fact_set_version_mismatch() -> None:
    old_fs = default_fact_set(producer="p", producer_version="1")
    new_fs = dict(old_fs)
    new_fs["version"] = 2
    old = _surface(coverage={"fact_set": old_fs, "fact_family_states": {}})
    new = _surface(coverage={"fact_set": new_fs, "fact_family_states": {}})
    changes = diff_source_abi(old, new)
    matches = [
        c for c in changes if c.kind == ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    ]
    assert len(matches) == 1
    assert "fact_set_version_mismatch" in matches[0].description


def test_diff_fires_on_incomplete_mandatory_family() -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    old = _surface(
        coverage={"fact_set": fs, "fact_family_states": {"macros": "partial"}}
    )
    new = _surface(coverage={"fact_set": fs, "fact_family_states": {}})
    changes = diff_source_abi(old, new)
    matches = [
        c for c in changes if c.kind == ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    ]
    assert len(matches) == 1
    assert "macros" in matches[0].description


def test_diff_silent_when_fact_sets_match_and_coverage_clean() -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    cov = {"functions": "complete", "macros": "empty-confirmed"}
    old = _surface(coverage={"fact_set": fs, "fact_family_states": dict(cov)})
    new = _surface(coverage={"fact_set": fs, "fact_family_states": dict(cov)})
    changes = diff_source_abi(old, new)
    assert ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE not in {c.kind for c in changes}


def test_diff_degrades_on_non_dict_nested_coverage_metadata() -> None:
    """A hand-written/newer source_abi.json's nested coverage.fact_set or
    coverage.fact_family_states might not be a mapping at all -- `x or {}`
    alone doesn't catch that (a non-empty non-dict value is still truthy),
    so this must not raise AttributeError, matching the defensive handling
    every other optional field here already gets (Codex review, P2)."""
    old = _surface(
        coverage={"fact_set": "not-a-dict", "fact_family_states": "also-bad"}
    )
    new = _surface(coverage={"fact_set": ["also", "not", "a", "dict"]})
    changes = diff_source_abi(old, new)  # must not raise
    assert isinstance(changes, list)


def test_diff_flags_unrecognized_family_coverage_state_as_incomplete() -> None:
    """fact_family_states on a serialized SourceAbiSurface can come from a
    hand-written or forward-versioned source_abi.json, not just
    rollup_coverage()'s own output -- so its values aren't guaranteed to
    already be one of COVERAGE_STATES the way rollup_coverage()'s own
    coercion guarantees for its callers. incomplete_families() only matches
    documented incomplete states ("partial"/"failed"), so an unrecognized
    value like a typo or a newer vocabulary this build predates must not
    silently read as "fine" and suppress SOURCE_FACT_COVERAGE_INCOMPLETE
    (Codex review, P2)."""
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.5")
    old = _surface(
        coverage={"fact_set": fs, "fact_family_states": {"functions": "bad-typo-state"}}
    )
    new = _surface(
        coverage={"fact_set": fs, "fact_family_states": {"functions": "bad-typo-state"}}
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE in {c.kind for c in changes}


def test_diff_degrades_on_unhashable_family_coverage_state() -> None:
    """SourceAbiSurface.from_dict copies coverage with a shallow dict(...)
    (unlike SourceAbiTu.from_dict, which string-coerces every value), so a
    hand-written/forward-versioned source_abi.json can carry a non-string
    family-state value -- a JSON array or object. Checking
    `state in COVERAGE_STATES` (a frozenset[str]) hashes state first, and a
    list/dict is unhashable, raising TypeError before it could even be
    coerced -- crashing diff_source_abi() instead of degrading like every
    other malformed-coverage case (Codex review, P2)."""
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.5")
    old = _surface(
        coverage={"fact_set": fs, "fact_family_states": {"functions": ["bad"]}}
    )
    new = _surface(
        coverage={"fact_set": fs, "fact_family_states": {"functions": {"x": 1}}}
    )
    changes = diff_source_abi(old, new)  # must not raise
    assert ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE in {c.kind for c in changes}


def test_source_fact_coverage_incomplete_is_risk_kind() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE in RISK_KINDS


# -- hash_recipe_id / check_fact_compatibility (PR2 gating) ------------------


def test_hash_recipe_id_explicit_field_wins() -> None:
    fs = default_fact_set(producer="p", producer_version="1", compiler_version="18")
    fs["hash_recipe_id"] = "clang-json-canonical-v3"
    assert hash_recipe_id(fs) == "clang-json-canonical-v3"


def test_hash_recipe_id_falls_back_to_producer_quadruple() -> None:
    fs = default_fact_set(producer="p", producer_version="1", compiler_version="18")
    assert hash_recipe_id(fs) == "p|1|clang|18"


def test_hash_recipe_id_fallback_differs_across_compiler_families() -> None:
    """A gcc and a clang fact_set with otherwise-identical producer/
    producer_version/compiler_version must never collide on the same
    fallback recipe id -- their canonicalization/mangling differs."""
    clang_fs = default_fact_set(
        producer="p", producer_version="1", compiler_version="18"
    )
    gcc_fs = dict(clang_fs)
    gcc_fs["compiler_family"] = "gcc"
    assert hash_recipe_id(clang_fs) != hash_recipe_id(gcc_fs)


def test_check_fact_compatibility_matching_fact_sets_all_comparable() -> None:
    fs = default_fact_set(producer="p", producer_version="1", compiler_version="18")
    compat = check_fact_compatibility(fs, dict(fs))
    assert compat.structured_facts_comparable
    assert compat.opaque_hashes_comparable
    assert compat.source_edges_comparable
    assert compat.issues == ()


def test_check_fact_compatibility_producer_mismatch_blocks_opaque_and_edges_only() -> (
    None
):
    old = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    new = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    compat = check_fact_compatibility(old, new)
    assert compat.structured_facts_comparable
    assert not compat.opaque_hashes_comparable
    assert not compat.source_edges_comparable
    assert any(i.rule == "producer_mismatch" for i in compat.issues)


def test_check_fact_compatibility_compiler_family_mismatch_blocks_opaque_and_edges_only() -> (
    None
):
    """A gcc-vs-clang pair must not be treated as byte-comparable: different
    compiler families mangle/canonicalize differently even when the
    abicheck producer identity and version otherwise match."""
    old = default_fact_set(producer="p", producer_version="1")
    new = dict(old)
    new["compiler_family"] = "gcc"
    compat = check_fact_compatibility(old, new)
    assert compat.structured_facts_comparable
    assert not compat.opaque_hashes_comparable
    assert not compat.source_edges_comparable
    assert any(i.rule == "compiler_family_mismatch" for i in compat.issues)


def test_check_fact_compatibility_name_mismatch_blocks_everything() -> None:
    old = default_fact_set(producer="p", producer_version="1")
    new = dict(old)
    new["name"] = "some-other-fact-set"
    compat = check_fact_compatibility(old, new)
    assert not compat.structured_facts_comparable
    assert not compat.opaque_hashes_comparable
    assert not compat.source_edges_comparable


def test_check_fact_compatibility_matching_recipe_id_overrides_producer_mismatch() -> (
    None
):
    """A differential conformance run (ADR-038 C.6) can prove two differently
    named producers emit byte-comparable opaque hashes; declaring the same
    hash_recipe_id on both sides should override the producer-mismatch gate
    rather than forcing every future comparison to suppress unnecessarily."""
    old = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    old["hash_recipe_id"] = "clang-json-canonical-v3"
    new = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    new["hash_recipe_id"] = "clang-json-canonical-v3"
    compat = check_fact_compatibility(old, new)
    assert compat.opaque_hashes_comparable
    assert compat.source_edges_comparable
    # The producer_mismatch issue is still reported for visibility even though
    # it's overridden -- callers that want the raw prose still see it.
    assert any(i.rule == "producer_mismatch" for i in compat.issues)


def test_check_fact_compatibility_one_side_empty_stays_comparable() -> None:
    """A pre-C.8 producer (no fact_set at all) must not gate anything -- only
    the informational fact_set_unknown warning fires (forward-compat)."""
    fs = default_fact_set(producer="p", producer_version="1")
    compat = check_fact_compatibility(fs, {})
    assert compat.structured_facts_comparable
    assert compat.opaque_hashes_comparable
    assert compat.source_edges_comparable


def test_fact_compatibility_is_frozen() -> None:
    compat = FactCompatibility(True, True, True, ())
    with pytest.raises(AttributeError):
        compat.opaque_hashes_comparable = False  # type: ignore[misc]


# -- diff_source_abi: gating INLINE_BODY_CHANGED/TEMPLATE_BODY_CHANGED ------


def _inline_entity(body_hash: str) -> object:
    from abicheck.buildsource.source_abi import SourceEntity

    return SourceEntity(
        id="f",
        kind="inline",
        qualified_name="ns::f",
        mangled_name="_Z1fv",
        body_hash=body_hash,
    )


def _template_entity(body_hash: str) -> object:
    from abicheck.buildsource.source_abi import SourceEntity

    return SourceEntity(
        id="T",
        kind="template",
        qualified_name="ns::T",
        signature_hash="sig",
        body_hash=body_hash,
    )


def test_diff_suppresses_inline_body_changed_on_producer_mismatch() -> None:
    old_fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    new_fs = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    old = _surface(
        coverage={"fact_set": old_fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-a")],
    )
    new = _surface(
        coverage={"fact_set": new_fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-b")],
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_BODY_CHANGED not in {c.kind for c in changes}
    coverage_finding = next(
        c for c in changes if c.kind == ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    )
    assert "suppressed" in coverage_finding.description


def test_diff_suppresses_template_body_changed_on_producer_mismatch() -> None:
    old_fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    new_fs = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    old = _surface(
        coverage={"fact_set": old_fs, "fact_family_states": {}},
        reachable_templates=[_template_entity("hash-a")],
    )
    new = _surface(
        coverage={"fact_set": new_fs, "fact_family_states": {}},
        reachable_templates=[_template_entity("hash-b")],
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.TEMPLATE_BODY_CHANGED not in {c.kind for c in changes}


def test_diff_still_reports_inline_body_changed_when_compatible() -> None:
    fs = default_fact_set(producer="p", producer_version="1", compiler_version="18")
    old = _surface(
        coverage={"fact_set": fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-a")],
    )
    new = _surface(
        coverage={"fact_set": dict(fs), "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-b")],
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_BODY_CHANGED in {c.kind for c in changes}


def test_diff_still_reports_inline_function_removed_on_producer_mismatch() -> None:
    """Removal is an existence check, not a hash comparison -- it must not be
    suppressed by an opaque-hash incompatibility (only *_BODY_CHANGED is)."""
    old_fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    new_fs = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    old = _surface(
        coverage={"fact_set": old_fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-a")],
    )
    # An unrelated declaration keeps _surface_has_facts(new) True (L4 ran on
    # the new side too) without resurrecting the removed inline function.
    from abicheck.buildsource.source_abi import SourceEntity

    new = _surface(
        coverage={"fact_set": new_fs, "fact_family_states": {}},
        reachable_declarations=[SourceEntity(id="g", qualified_name="ns::g")],
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_FUNCTION_REMOVED in {c.kind for c in changes}


def test_diff_matching_recipe_id_keeps_hash_diffs_despite_producer_mismatch() -> None:
    old_fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    old_fs["hash_recipe_id"] = "clang-json-canonical-v3"
    new_fs = default_fact_set(
        producer="abicheck-cc-clang-extractor", producer_version="0.6"
    )
    new_fs["hash_recipe_id"] = "clang-json-canonical-v3"
    old = _surface(
        coverage={"fact_set": old_fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-a")],
    )
    new = _surface(
        coverage={"fact_set": new_fs, "fact_family_states": {}},
        reachable_inline_bodies=[_inline_entity("hash-b")],
    )
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_BODY_CHANGED in {c.kind for c in changes}

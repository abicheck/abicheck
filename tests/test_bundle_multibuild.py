# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Unit tests for G38 Phase 3's multibuild variant pairing
(``abicheck/bundle_multibuild.py``).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.bundle_facts import DEFAULT_VARIANT_FINGERPRINT, BundleFacts
from abicheck.bundle_multibuild import (
    VariantOutcome,
    coverage_regression_findings,
    pair_variants,
    variant_fingerprint,
)
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot


def _facts(fingerprint: str, libraries: tuple[str, ...] = ()) -> BundleFacts:
    return BundleFacts(
        variant_fingerprint=fingerprint,
        per_library_snapshots={
            name: AbiSnapshot(library=name, version="1.0") for name in libraries
        },
    )


# ---------------------------------------------------------------------------
# variant_fingerprint
# ---------------------------------------------------------------------------


class TestVariantFingerprint:
    def test_determinism(self):
        fp1 = variant_fingerprint(
            target_triple="x86_64-linux-gnu",
            compiler_family="gcc",
            feature_toggles={"ONEDAL_DATA_PARALLEL": "ON"},
        )
        fp2 = variant_fingerprint(
            target_triple="x86_64-linux-gnu",
            compiler_family="gcc",
            feature_toggles={"ONEDAL_DATA_PARALLEL": "ON"},
        )
        assert fp1 == fp2

    def test_compiler_version_is_not_part_of_identity(self):
        # A routine toolchain upgrade between releases (GCC 13 -> 14
        # building the identical variant) must NOT read as a different
        # variant -- there is no compiler_version parameter at all, so a
        # caller cannot even accidentally fingerprint it (Codex review,
        # fresh evidence: an earlier revision of this function did include
        # it, which would have made pair_variants() report OLD_ONLY +
        # NEW_ONLY for an ordinary compiler bump, skipping every real
        # per-library comparison for that variant).
        import inspect

        assert (
            "compiler_version" not in inspect.signature(variant_fingerprint).parameters
        )

    def test_feature_toggle_dict_order_does_not_matter(self):
        fp1 = variant_fingerprint(feature_toggles={"A": "1", "B": "2"})
        fp2 = variant_fingerprint(feature_toggles={"B": "2", "A": "1"})
        assert fp1 == fp2

    def test_default_no_distinction(self):
        # No caller-supplied identity at all -- every "variant" collapses to
        # the same fingerprint, matching BundleFacts.DEFAULT_VARIANT_FINGERPRINT's
        # existing "no multibuild distinction" behaviour.
        assert variant_fingerprint() == variant_fingerprint()

    def test_default_matches_the_literal_legacy_fingerprint(self):
        # A pre-existing/deserialized BundleFacts with no multibuild
        # distinction carries the literal "default" string
        # (bundle_facts.DEFAULT_VARIANT_FINGERPRINT). variant_fingerprint()
        # with no coordinates must return that *exact* value, not merely
        # some other constant equal to itself -- otherwise pairing a legacy
        # unqualified baseline against an equivalently unqualified side
        # computed through this function reads as OLD_ONLY + NEW_ONLY
        # instead of one paired variant (Codex review, fresh evidence).
        assert variant_fingerprint() == DEFAULT_VARIANT_FINGERPRINT
        legacy = _facts(DEFAULT_VARIANT_FINGERPRINT, ("libcore.so",))
        computed = _facts(variant_fingerprint(), ("libcore.so",))
        comparisons = pair_variants({"old": legacy}, {"new": computed})
        assert [c.outcome for c in comparisons] == [VariantOutcome.PAIRED]

    def test_toggle_encoding_has_no_delimiter_collision(self):
        # A naive "," / "=" joined encoding lets a delimiter character
        # embedded in a toggle key/value collide with the encoding's own
        # separators: {"A": "1,B=2"} and {"A": "1", "B": "2"} must NOT
        # fingerprint identically (Codex review, fresh evidence -- this
        # reproduced under the original join-based encoding).
        fp1 = variant_fingerprint(feature_toggles={"A": "1,B=2"})
        fp2 = variant_fingerprint(feature_toggles={"A": "1", "B": "2"})
        assert fp1 != fp2

    def test_coordinate_string_delimiter_does_not_collide_across_fields(self):
        # A coordinate value containing a character that could plausibly be
        # used as a field separator must not let two different
        # (target_triple, compiler_family) splits collide.
        fp1 = variant_fingerprint(target_triple="a|b", compiler_family="")
        fp2 = variant_fingerprint(target_triple="a", compiler_family="b")
        assert fp1 != fp2

    @pytest.mark.parametrize(
        ("kwargs_a", "kwargs_b"),
        [
            (
                {"target_triple": "x86_64-linux-gnu"},
                {"target_triple": "aarch64-linux-gnu"},
            ),
            ({"compiler_family": "gcc"}, {"compiler_family": "clang"}),
            (
                {"feature_toggles": {"ONEDAL_DATA_PARALLEL": "ON"}},
                {"feature_toggles": {"ONEDAL_DATA_PARALLEL": "OFF"}},
            ),
            (
                {"feature_toggles": {"ONEDAL_DATA_PARALLEL": "ON"}},
                {},
            ),
        ],
    )
    def test_logical_identity_coordinates_are_sensitive(self, kwargs_a, kwargs_b):
        """A build differing in a *logical-identity* coordinate (target
        triple, compiler family, a feature toggle like ONEDAL_DATA_PARALLEL)
        fingerprints differently -- G38 Phase 3's own acceptance bar."""
        assert variant_fingerprint(**kwargs_a) != variant_fingerprint(**kwargs_b)

    def test_no_parameter_exists_for_excluded_coordinates(self):
        """variant_fingerprint has no C/C++ standard, define, compiler-
        version, or artifact-membership parameter at all -- the exclusion
        is structural (the design's own rationale), not merely "callers
        happen not to pass it". This test documents the contract: only
        these three coordinates can ever affect the fingerprint."""
        import inspect

        params = set(inspect.signature(variant_fingerprint).parameters)
        assert params == {
            "target_triple",
            "compiler_family",
            "feature_toggles",
        }


# ---------------------------------------------------------------------------
# pair_variants
# ---------------------------------------------------------------------------


class TestPairVariants:
    def test_matches_by_fingerprint_not_by_caller_label(self):
        # Same fingerprint, different caller-chosen labels on each side --
        # must still pair (a caller renamed a variant between releases
        # without changing what it is).
        old = {"cpu_build": _facts("fp-cpu", ("libcore.so",))}
        new = {"cpu-only": _facts("fp-cpu", ("libcore.so",))}
        comparisons = pair_variants(old, new)
        assert len(comparisons) == 1
        (c,) = comparisons
        assert c.outcome is VariantOutcome.PAIRED
        assert c.old_label == "cpu_build"
        assert c.new_label == "cpu-only"

    def test_same_label_different_fingerprint_does_not_pair(self):
        old = {"cpu": _facts("fp-old-cpu")}
        new = {"cpu": _facts("fp-new-cpu")}
        comparisons = pair_variants(old, new)
        outcomes = {c.fingerprint: c.outcome for c in comparisons}
        assert outcomes == {
            "fp-old-cpu": VariantOutcome.OLD_ONLY,
            "fp-new-cpu": VariantOutcome.NEW_ONLY,
        }

    def test_old_only_is_coverage_regression(self):
        old = {"cpu": _facts("fp-cpu"), "dpc": _facts("fp-dpc")}
        new = {"cpu": _facts("fp-cpu")}
        comparisons = pair_variants(old, new)
        by_fp = {c.fingerprint: c for c in comparisons}
        assert by_fp["fp-cpu"].outcome is VariantOutcome.PAIRED
        assert by_fp["fp-dpc"].outcome is VariantOutcome.OLD_ONLY
        assert by_fp["fp-dpc"].new_facts is None
        assert by_fp["fp-dpc"].old_facts is not None

    def test_new_only_is_expansion_not_regression(self):
        # A newly added variant on the new side alone is NOT a regression --
        # this is the review-caught fix the plan's design section records.
        old = {"cpu": _facts("fp-cpu")}
        new = {"cpu": _facts("fp-cpu"), "dpc": _facts("fp-dpc")}
        comparisons = pair_variants(old, new)
        by_fp = {c.fingerprint: c for c in comparisons}
        assert by_fp["fp-dpc"].outcome is VariantOutcome.NEW_ONLY
        assert by_fp["fp-dpc"].old_facts is None

    def test_never_merges_two_variants(self):
        # Two distinct old variants and two distinct new variants, no
        # fingerprint shared at all -- pair_variants must report four
        # independent outcomes, never a merged/unioned one.
        old = {"a": _facts("fp-a"), "b": _facts("fp-b")}
        new = {"c": _facts("fp-c"), "d": _facts("fp-d")}
        comparisons = pair_variants(old, new)
        assert len(comparisons) == 4
        assert {c.outcome for c in comparisons} == {
            VariantOutcome.OLD_ONLY,
            VariantOutcome.NEW_ONLY,
        }

    def test_duplicate_fingerprint_on_one_side_raises(self):
        old = {"a": _facts("fp-shared"), "b": _facts("fp-shared")}
        with pytest.raises(ValueError, match="fp-shared"):
            pair_variants(old, {})

    def test_empty_fingerprint_raises_rather_than_pairing(self):
        # Codex review: variant_fingerprint() never produces "" (the
        # no-coordinates case is the DEFAULT_VARIANT_FINGERPRINT sentinel),
        # so an empty fingerprint means the BundleFacts skipped that
        # function entirely -- e.g. a malformed/hand-edited serialized pack.
        # Two such malformed, genuinely-unrelated entries must not silently
        # pair as "the same variant" just because they share the empty
        # string.
        old = {"malformed": _facts("")}
        new = {"also-malformed": _facts("")}
        with pytest.raises(ValueError, match="malformed"):
            pair_variants(old, new)

    def test_empty_inputs_produce_no_comparisons(self):
        assert pair_variants({}, {}) == []

    def test_result_order_is_deterministic(self):
        old = {"z": _facts("fp-z"), "a": _facts("fp-a")}
        new = {"z": _facts("fp-z"), "a": _facts("fp-a")}
        first = pair_variants(old, new)
        second = pair_variants(dict(reversed(old.items())), dict(reversed(new.items())))
        assert [c.fingerprint for c in first] == [c.fingerprint for c in second]
        assert [c.fingerprint for c in first] == sorted(c.fingerprint for c in first)


# ---------------------------------------------------------------------------
# coverage_regression_findings
# ---------------------------------------------------------------------------


class TestCoverageRegressionFindings:
    def test_only_old_only_comparisons_produce_findings(self):
        comparisons = pair_variants(
            {
                "cpu": _facts("fp-cpu", ("libcore.so",)),
                "dpc": _facts("fp-dpc", ("libcore_dpc.so",)),
            },
            {"cpu": _facts("fp-cpu", ("libcore.so",))},
        )
        findings = coverage_regression_findings(comparisons)
        assert len(findings) == 1
        (finding,) = findings
        assert finding.kind is ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED
        assert finding.symbol == "dpc"
        assert finding.affected_libraries == ["libcore_dpc.so"]
        assert finding.old_value == "fp-dpc"

    def test_paired_and_new_only_produce_no_findings(self):
        comparisons = pair_variants(
            {"cpu": _facts("fp-cpu")},
            {"cpu": _facts("fp-cpu"), "dpc": _facts("fp-dpc")},
        )
        assert coverage_regression_findings(comparisons) == []

    def test_no_variants_missing_produces_no_findings(self):
        comparisons = pair_variants(
            {"cpu": _facts("fp-cpu")}, {"cpu": _facts("fp-cpu")}
        )
        assert coverage_regression_findings(comparisons) == []


# ---------------------------------------------------------------------------
# Property: pair_variants never unions (mirrors this repo's
# "Primitive-level property tests" convention for a reusable merge/pairing
# primitive -- see AGENTS.md).
# ---------------------------------------------------------------------------

_fp_strategy = st.text(alphabet="abc", min_size=1, max_size=2)
_label_strategy = st.text(alphabet="xyz", min_size=1, max_size=2)
_variant_map_strategy = st.dictionaries(
    keys=_label_strategy, values=_fp_strategy, min_size=0, max_size=4
)


@pytest.mark.slow
@given(old_map=_variant_map_strategy, new_map=_variant_map_strategy)
@settings(max_examples=100, deadline=None)
def test_pair_variants_never_unions_property(old_map, new_map):
    # Duplicate fingerprints within one side are a caller error this
    # function refuses rather than silently resolving -- restrict the
    # generated maps to injective label->fingerprint before checking the
    # pairing invariants below.
    if len(set(old_map.values())) != len(old_map):
        return
    if len(set(new_map.values())) != len(new_map):
        return

    old = {label: _facts(fp) for label, fp in old_map.items()}
    new = {label: _facts(fp) for label, fp in new_map.items()}
    comparisons = pair_variants(old, new)

    old_fps = set(old_map.values())
    new_fps = set(new_map.values())

    # Every distinct fingerprint on either side gets exactly one comparison,
    # and no fingerprint is ever dropped.
    assert {c.fingerprint for c in comparisons} == old_fps | new_fps
    assert len(comparisons) == len(old_fps | new_fps)

    for c in comparisons:
        in_old = c.fingerprint in old_fps
        in_new = c.fingerprint in new_fps
        if in_old and in_new:
            assert c.outcome is VariantOutcome.PAIRED
            assert c.old_facts is not None and c.new_facts is not None
            # Never a merged/unioned pair -- both sides genuinely carry the
            # same fingerprint the comparison is keyed on, nothing else.
            assert c.old_facts.variant_fingerprint == c.fingerprint
            assert c.new_facts.variant_fingerprint == c.fingerprint
        elif in_old:
            assert c.outcome is VariantOutcome.OLD_ONLY
            assert c.old_facts is not None
            assert c.new_facts is None
        else:
            assert c.outcome is VariantOutcome.NEW_ONLY
            assert c.new_facts is not None
            assert c.old_facts is None

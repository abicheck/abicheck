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

"""Unit tests for :mod:`abicheck.bundle_variants_config` (G38 Phase 13) --
the ``bundle_variants:`` config-block parser and the first real caller of
``bundle_multibuild.pair_variants``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle_facts import BundleFacts
from abicheck.bundle_variants_config import (
    BundleVariantsConfigError,
    BundleVariantSpec,
    load_bundle_facts_by_variant,
    parse_bundle_variants_config,
    run_bundle_variant_pairing,
)
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.model import AbiSnapshot
from abicheck.serialization import save_bundle_facts


def _facts(fingerprint: str, libraries: tuple[str, ...] = ()) -> BundleFacts:
    return BundleFacts(
        variant_fingerprint=fingerprint,
        per_library_snapshots={
            name: AbiSnapshot(library=name, version="1.0") for name in libraries
        },
    )


# ---------------------------------------------------------------------------
# parse_bundle_variants_config
# ---------------------------------------------------------------------------


class TestParseBundleVariantsConfig:
    def test_valid_config_parses_every_field(self) -> None:
        raw = {
            "cpu": {
                "target_triple": "x86_64-linux-gnu",
                "compiler_family": "gcc",
                "required": True,
            },
            "dpc": {
                "target_triple": "x86_64-linux-gnu",
                "compiler_family": "icpx",
                "feature_toggles": {"ONEDAL_DATA_PARALLEL": "1"},
                "required": False,
            },
        }
        specs = parse_bundle_variants_config(raw)
        assert specs["cpu"] == BundleVariantSpec(
            name="cpu",
            target_triple="x86_64-linux-gnu",
            compiler_family="gcc",
            required=True,
        )
        assert specs["dpc"].feature_toggles == {"ONEDAL_DATA_PARALLEL": "1"}
        assert specs["dpc"].required is False

    def test_defaults_apply_when_a_field_is_omitted(self) -> None:
        specs = parse_bundle_variants_config({"cpu": {}})
        assert specs["cpu"] == BundleVariantSpec(name="cpu")
        assert specs["cpu"].required is False

    def test_top_level_must_be_a_mapping(self) -> None:
        with pytest.raises(BundleVariantsConfigError, match="must be a mapping"):
            parse_bundle_variants_config(["cpu"])  # type: ignore[arg-type]

    def test_variant_entry_must_be_a_mapping(self) -> None:
        with pytest.raises(BundleVariantsConfigError, match="must be a mapping"):
            parse_bundle_variants_config({"cpu": "gcc"})  # type: ignore[dict-item]

    def test_unknown_key_is_a_hard_error(self) -> None:
        with pytest.raises(BundleVariantsConfigError, match="unrecognized key"):
            parse_bundle_variants_config({"cpu": {"typo_field": "x"}})

    @pytest.mark.parametrize(
        "field_name,bad_value",
        [
            ("target_triple", 1),
            ("compiler_family", 1),
            ("feature_toggles", "not-a-mapping"),
            ("required", "true"),  # the YAML-string-"false" trap this
            # codebase's own BuildConfig schema doc explicitly calls out
        ],
    )
    def test_wrong_type_is_a_hard_error(
        self, field_name: str, bad_value: object
    ) -> None:
        with pytest.raises(BundleVariantsConfigError):
            parse_bundle_variants_config({"cpu": {field_name: bad_value}})

    def test_feature_toggles_values_must_be_strings(self) -> None:
        with pytest.raises(BundleVariantsConfigError, match="feature_toggles"):
            parse_bundle_variants_config(
                {"cpu": {"feature_toggles": {"ONEDAL_DATA_PARALLEL": 1}}}
            )

    def test_empty_config_parses_to_empty_dict(self) -> None:
        assert parse_bundle_variants_config({}) == {}


class TestBundleVariantSpecFingerprint:
    def test_fingerprint_matches_variant_fingerprint_directly(self) -> None:
        from abicheck.bundle_multibuild import variant_fingerprint

        spec = BundleVariantSpec(
            name="dpc",
            target_triple="x86_64-linux-gnu",
            compiler_family="icpx",
            feature_toggles={"ONEDAL_DATA_PARALLEL": "1"},
        )
        assert spec.fingerprint() == variant_fingerprint(
            target_triple="x86_64-linux-gnu",
            compiler_family="icpx",
            feature_toggles={"ONEDAL_DATA_PARALLEL": "1"},
        )


# ---------------------------------------------------------------------------
# run_bundle_variant_pairing -- the first real pair_variants() caller
# ---------------------------------------------------------------------------


class TestRunBundleVariantPairing:
    def test_missing_non_required_variant_stays_default_severity(self) -> None:
        specs = parse_bundle_variants_config({"cpu": {"required": False}})
        old = {"cpu": _facts("fp-cpu", ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        result = run_bundle_variant_pairing(specs, old, new)

        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.kind == ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED
        assert finding.effective_verdict is None
        assert result.missing_required_variants == []

    def test_missing_required_variant_escalates_to_breaking(self) -> None:
        specs = parse_bundle_variants_config({"cpu": {"required": True}})
        old = {"cpu": _facts("fp-cpu", ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        result = run_bundle_variant_pairing(specs, old, new)

        assert len(result.findings) == 1
        finding = result.findings[0]
        assert finding.kind == ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED
        assert finding.effective_verdict is Verdict.BREAKING
        assert finding.modulation_rule == "bundle_variants.required"
        assert result.missing_required_variants == ["cpu"]

    def test_paired_variant_produces_no_finding_regardless_of_required(self) -> None:
        specs = parse_bundle_variants_config({"cpu": {"required": True}})
        old = {"cpu": _facts("fp-cpu", ("libcore.so",))}
        new = {"cpu": _facts("fp-cpu", ("libcore.so",))}

        result = run_bundle_variant_pairing(specs, old, new)

        assert result.findings == []
        assert result.missing_required_variants == []

    def test_new_only_variant_is_never_a_finding_even_if_required(self) -> None:
        # A newly ADDED variant is coverage expansion, not regression --
        # bundle_multibuild.coverage_regression_findings never renders
        # NEW_ONLY as a finding at all, so `required:` has nothing to
        # escalate here regardless of which side it names.
        specs = parse_bundle_variants_config({"dpc": {"required": True}})
        old: dict[str, BundleFacts] = {}
        new = {"dpc": _facts("fp-dpc", ("libcore.so",))}

        result = run_bundle_variant_pairing(specs, old, new)

        assert result.findings == []
        assert result.missing_required_variants == []

    def test_undeclared_variant_still_pairs_at_default_severity(self) -> None:
        """A variant present in the facts maps but absent from `specs`
        (never declared in `bundle_variants:`) still participates in
        pairing -- it just has no `required:` spec to look up, so it can
        never be escalated."""
        old = {"undeclared": _facts("fp-x", ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        result = run_bundle_variant_pairing({}, old, new)

        assert len(result.findings) == 1
        assert result.findings[0].effective_verdict is None
        assert result.missing_required_variants == []


class TestRunBundleVariantPairingVerifyFingerprints:
    def test_disabled_by_default_ignores_mismatched_fingerprints(self) -> None:
        # No coordinates declared -> spec.fingerprint() == "default", but
        # the facts below carry an unrelated sentinel -- with verification
        # off (the default), this must not raise.
        specs = parse_bundle_variants_config({"cpu": {"required": False}})
        old = {"cpu": _facts("fp-cpu", ("libcore.so",))}
        new = {"cpu": _facts("fp-cpu", ("libcore.so",))}

        result = run_bundle_variant_pairing(specs, old, new)

        assert result.findings == []

    def test_matching_fingerprint_passes_verification(self) -> None:
        from abicheck.bundle_multibuild import variant_fingerprint

        spec_raw = {
            "cpu": {"target_triple": "x86_64-linux-gnu", "compiler_family": "gcc"}
        }
        specs = parse_bundle_variants_config(spec_raw)
        fp = variant_fingerprint(
            target_triple="x86_64-linux-gnu", compiler_family="gcc"
        )
        old = {"cpu": _facts(fp, ("libcore.so",))}
        new = {"cpu": _facts(fp, ("libcore.so",))}

        result = run_bundle_variant_pairing(
            specs, old, new, verify_fingerprints=True
        )

        assert result.findings == []

    def test_mismatched_fingerprint_raises(self) -> None:
        specs = parse_bundle_variants_config(
            {"cpu": {"target_triple": "x86_64-linux-gnu", "compiler_family": "gcc"}}
        )
        old = {"cpu": _facts("wrong-fingerprint", ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        with pytest.raises(BundleVariantsConfigError, match="bundle_variants.cpu"):
            run_bundle_variant_pairing(specs, old, new, verify_fingerprints=True)

    def test_default_sentinel_fingerprint_never_flagged(self) -> None:
        from abicheck.bundle_facts import DEFAULT_VARIANT_FINGERPRINT

        specs = parse_bundle_variants_config(
            {"cpu": {"target_triple": "x86_64-linux-gnu", "compiler_family": "gcc"}}
        )
        old = {"cpu": _facts(DEFAULT_VARIANT_FINGERPRINT, ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        # No raise: an un-variant-tagged capture has nothing to verify
        # against, so it degrades to the un-verified pairing behavior.
        result = run_bundle_variant_pairing(specs, old, new, verify_fingerprints=True)
        assert len(result.findings) == 1

    def test_undeclared_variant_skips_verification(self) -> None:
        old = {"undeclared": _facts("fp-x", ("libcore.so",))}
        new: dict[str, BundleFacts] = {}

        result = run_bundle_variant_pairing({}, old, new, verify_fingerprints=True)

        assert len(result.findings) == 1


class TestLoadBundleFactsByVariant:
    def test_loads_each_named_path(self, tmp_path: Path) -> None:
        cpu_path = tmp_path / "cpu.bundlefacts.json"
        dpc_path = tmp_path / "dpc.bundlefacts.json"
        save_bundle_facts(_facts("fp-cpu"), cpu_path)
        save_bundle_facts(_facts("fp-dpc"), dpc_path)

        loaded = load_bundle_facts_by_variant({"cpu": cpu_path, "dpc": dpc_path})

        assert loaded["cpu"].variant_fingerprint == "fp-cpu"
        assert loaded["dpc"].variant_fingerprint == "fp-dpc"

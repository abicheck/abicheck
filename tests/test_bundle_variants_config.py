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

"""ADR-062 A1.6: the ``.abicheck.yml`` ``bundle_variants:`` block.

The invalid-input tests enumerate a small domain exhaustively rather than
naming one bad value: every spec key is paired with every wrong-typed value
from an independent table, and the oracle for "is this entry valid" is
written here from the documented schema, not by calling the validator's own
helpers.
"""

from __future__ import annotations

import copy
import itertools

import pytest

from abicheck.buildsource.build_config import BuildConfig
from abicheck.model.bundle_variants import (
    BundleVariantsConfigError,
    bundle_variants_findings,
    parse_bundle_variants,
)


def _base() -> dict[str, object]:
    return {
        "x86": {
            "target_triple": "x86_64-linux-gnu",
            "compiler_family": "gcc",
            "feature_toggles": {"simd": "avx2", "threads": True, "abi": 2},
        },
        "arm": {
            "target_triple": "aarch64-linux-gnu",
            "compiler_family": "clang",
            "required": False,
        },
    }


def test_valid_block_parses_to_declared_maps() -> None:
    config = parse_bundle_variants(_base())
    assert config.names == ("x86", "arm")
    x86, arm = config.variants
    assert x86.required is True  # the documented default
    assert arm.required is False
    assert x86.declared() == {
        "target_triple": "x86_64-linux-gnu",
        "compiler_family": "gcc",
        "simd": "avx2",
        "threads": "true",
        "abi": "2",
    }
    assert arm.declared() == {
        "target_triple": "aarch64-linux-gnu",
        "compiler_family": "clang",
    }
    assert config.required_names == frozenset({"x86"})


# Every value that is *not* acceptable for each key, by an independent table.
_WRONG_VALUES: dict[str, list[object]] = {
    "target_triple": [None, 1, True, "", "   ", ["x"], {"a": "b"}, 1.5],
    "compiler_family": [None, 0, False, "", ["gcc"], {}, 2.0],
    "required": [None, "true", "yes", 1, 0, [], {}, 1.0],
    "feature_toggles": ["simd=avx2", ["simd"], 1, True],
}


@pytest.mark.parametrize(
    ("key", "value"),
    [(k, v) for k, values in _WRONG_VALUES.items() for v in values],
)
def test_every_wrong_type_for_every_key_is_rejected_and_named(
    key: str, value: object
) -> None:
    raw = _base()
    raw["x86"][key] = value  # type: ignore[index]
    with pytest.raises(BundleVariantsConfigError) as info:
        parse_bundle_variants(raw)
    assert any(f"bundle_variants.x86.{key}" in f for f in info.value.findings), (
        info.value.findings
    )
    # The other, valid variant contributes no finding.
    assert not any("bundle_variants.arm" in f for f in info.value.findings)


_BAD_TOGGLE_VALUES: list[object] = [None, 1.5, [], {}, ""]


@pytest.mark.parametrize("value", _BAD_TOGGLE_VALUES)
def test_every_unsupported_toggle_value_is_rejected(value: object) -> None:
    raw = _base()
    raw["x86"]["feature_toggles"] = {"simd": value}  # type: ignore[index]
    with pytest.raises(BundleVariantsConfigError, match="feature_toggles.simd"):
        parse_bundle_variants(raw)


@pytest.mark.parametrize("key", ["target_triple", "compiler_family"])
def test_toggle_may_not_shadow_a_fixed_coordinate(key: str) -> None:
    raw = _base()
    raw["x86"]["feature_toggles"] = {key: "other"}  # type: ignore[index]
    with pytest.raises(BundleVariantsConfigError, match="collides"):
        parse_bundle_variants(raw)


@pytest.mark.parametrize("key", ["target_triple", "compiler_family"])
def test_missing_fixed_coordinate_is_rejected(key: str) -> None:
    raw = _base()
    del raw["arm"][key]  # type: ignore[attr-defined]
    with pytest.raises(BundleVariantsConfigError, match=f"arm.{key} is required"):
        parse_bundle_variants(raw)


@pytest.mark.parametrize("key", ["compiler", "target", "Required", "features", ""])
def test_unknown_spec_key_is_rejected(key: str) -> None:
    raw = _base()
    raw["arm"][key] = "x"  # type: ignore[index]
    with pytest.raises(BundleVariantsConfigError, match="unknown key"):
        parse_bundle_variants(raw)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a/b",
        "a\\b",
        "..",
        ".hidden",
        "-x",
        "x.",
        "con",
        "LPT1.debug",
        "a b",
        "a:b",
        "x" * 101,
        3,
        None,
    ],
)
def test_unsafe_variant_names_are_rejected(name: object) -> None:
    raw = {name: _base()["arm"]}
    with pytest.raises(BundleVariantsConfigError, match="variant name"):
        parse_bundle_variants(raw)


def test_case_only_collision_is_rejected() -> None:
    raw = {"Linux": _base()["arm"], "linux": _base()["arm"]}
    with pytest.raises(BundleVariantsConfigError, match="differ only by case"):
        parse_bundle_variants(raw)


@pytest.mark.parametrize("raw", [None, [], "x86", 1, {}])
def test_block_must_be_a_non_empty_mapping(raw: object) -> None:
    with pytest.raises(BundleVariantsConfigError):
        parse_bundle_variants(raw)


def test_findings_are_collected_per_variant_not_first_only() -> None:
    """Compositionality: N independently broken variants yield >= N
    findings, each naming its own variant -- over every non-empty subset of
    a fixed set of breakages."""
    breakages = {
        "a": {"target_triple": 1, "compiler_family": "gcc"},
        "b": {"target_triple": "t", "compiler_family": "gcc", "required": "no"},
        "c": {"target_triple": "t", "compiler_family": "gcc", "bogus": 1},
        "d": {"target_triple": "t"},
    }
    for size in range(1, len(breakages) + 1):
        for subset in itertools.combinations(sorted(breakages), size):
            raw = {name: copy.deepcopy(breakages[name]) for name in subset}
            raw["ok"] = {"target_triple": "t", "compiler_family": "gcc"}
            findings = bundle_variants_findings(raw)
            for name in subset:
                assert any(f"bundle_variants.{name}" in f for f in findings), (
                    subset,
                    findings,
                )
            assert not any("bundle_variants.ok" in f for f in findings)


def test_abicheck_yml_ingestion_is_strict_about_bundle_variants() -> None:
    """Every `.abicheck.yml` load (not only capture-variants) rejects a
    malformed block, and a well-formed one is a recognized key."""
    BuildConfig.from_dict({"bundle_variants": _base()})  # no error
    with pytest.raises(ValueError, match="bundle_variants.x86.required"):
        BuildConfig.from_dict(
            {
                "bundle_variants": {
                    "x86": {
                        "target_triple": "t",
                        "compiler_family": "g",
                        "required": "yes",
                    }
                }
            }
        )
    with pytest.raises(ValueError, match="unknown .abicheck.yml key 'bundle_variant'"):
        BuildConfig.from_dict({"bundle_variant": _base()})

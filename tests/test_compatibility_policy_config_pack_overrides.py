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

"""CodeRabbit review, round 9/10: ``CompatibilityPolicyConfig.pack_overrides``
must be a real, value-equal subset of ``overrides`` -- a direct caller could
previously construct ``CompatibilityPolicyConfig(overrides={},
pack_overrides={"func_removed": Verdict.BREAKING})``, recording a pack
contribution no selected pack actually supplied. ``pack_application()``
reads this field for provenance, so a mismatched value would misreport an
override's source in a receipt.

Split out of ``tests/test_compatibility_evaluation_config.py`` (already at
its own ``new-test-size`` line-count ceiling) rather than trimmed to fit
there -- the same "move responsibility, don't shrink the file" rule the
root ``AGENTS.md`` states for every debt-tracked module applies identically
to a debt-tracked test file (see ``tests/test_pack_application_provenance.py``
for the same pattern).
"""

from __future__ import annotations

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_config import (
    CompatibilityPolicyConfig,
    ImmutableIdentity,
)


def _identity(
    identity_id: str, version: int = 1, sha256: str = "test-digest"
) -> ImmutableIdentity:
    return ImmutableIdentity(id=identity_id, version=version, sha256=sha256)


def test_pack_overrides_not_a_subset_of_overrides_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"pack_overrides.*value-equal subset"):
        CompatibilityPolicyConfig(
            base=_identity("strict_abi"),
            overrides={},
            pack_overrides={"func_removed": Verdict.BREAKING},
        )


def test_pack_overrides_disagreeing_value_is_rejected() -> None:
    # Same shape as the previous test, but the slug *is* present in
    # overrides -- just with a different Verdict. A value mismatch is
    # exactly as untrustworthy for provenance as a missing key.
    with pytest.raises(ValueError, match=r"pack_overrides.*value-equal subset"):
        CompatibilityPolicyConfig(
            base=_identity("strict_abi"),
            overrides={"func_removed": Verdict.COMPATIBLE_WITH_RISK},
            pack_overrides={"func_removed": Verdict.BREAKING},
        )


def test_non_mapping_pack_overrides_is_rejected() -> None:
    with pytest.raises(TypeError, match=r"CompatibilityPolicyConfig\.pack_overrides"):
        CompatibilityPolicyConfig(
            base=_identity("strict_abi"),
            overrides={"func_removed": Verdict.BREAKING},
            pack_overrides=["func_removed"],  # type: ignore[arg-type]
        )


def test_pack_overrides_raw_string_equal_to_a_verdict_value_is_rejected() -> None:
    # CodeRabbit review, round 10/11: Verdict is a `str, Enum` subclass, so
    # the raw string "BREAKING" == Verdict.BREAKING even though it is not a
    # Verdict instance. The subset check above (`self.overrides.get(k) != v`)
    # would silently accept this coincidental equality; only an explicit
    # `isinstance(v, Verdict)` check catches it. Left unrejected, this value
    # reaches `resolved_config_to_dict()`'s `verdict.value` access and raises
    # `AttributeError` there instead of failing loudly at construction.
    with pytest.raises(
        TypeError, match=r"pack_overrides values must be Verdict members"
    ):
        CompatibilityPolicyConfig(
            base=_identity("strict_abi"),
            overrides={"func_removed": Verdict.BREAKING},
            pack_overrides={"func_removed": "BREAKING"},  # type: ignore[dict-item]
        )


def test_pack_overrides_genuine_subset_constructs() -> None:
    # Negative control: a real, value-equal subset is accepted -- proving
    # the guard isn't just rejecting pack_overrides outright.
    cfg = CompatibilityPolicyConfig(
        base=_identity("strict_abi"),
        overrides={
            "func_removed": Verdict.BREAKING,
            "func_added": Verdict.COMPATIBLE,
        },
        pack_overrides={"func_removed": Verdict.BREAKING},
    )
    assert dict(cfg.pack_overrides) == {"func_removed": Verdict.BREAKING}

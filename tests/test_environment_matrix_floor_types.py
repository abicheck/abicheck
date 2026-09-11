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

"""``runtime_floors``' non-numeric-typed keys (``WHEEL_ARCH``/``MUSLLINUX``/
``WHEEL_CONTEXT``) must still reject a wrong-*shape* value.

Split out of ``tests/test_environment_drift.py`` (Codex review, PR #1221,
round-6 follow-up finding 1): that module sits at the AI-readiness 2000-line
hard cap, with no headroom for new tests -- see its own
``architecture/debt.yaml`` entry.

``EnvironmentMatrix._parse_runtime_floors`` exempts ``WHEEL_ARCH``/
``MUSLLINUX``/``WHEEL_CONTEXT`` from the dotted-numeric-version check every
other ``runtime_floors`` key gets, since they carry a non-version token (an
architecture name or a presence flag) rather than a floor. That exemption
must not become a license to accept *any* type: a YAML list, mapping, or
(for ``WHEEL_ARCH`` specifically, which is not a presence-flag key) a bare
bool would otherwise fall through to the unconditional ``str(value)`` and
become a nonsense literal string (``"['x86_64']"``), which the downstream
architecture-mismatch detector treats as an unrecognized claim and reports
nothing for -- a malformed ``strict=True`` config silently disabling a hard
check instead of raising the error it promises.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.environment_matrix import (
    CudaConstraints,
    EnvironmentMatrix,
    SyclConstraints,
)
from abicheck.model.dataclass_scalar_validation import scalar_str_field_types
from abicheck.model.wheel_arch_claims import WHEEL_ARCH_CLAIMS

#: Every scalar `str | None` field `EnvironmentMatrix.from_dict` should now
#: type-check (Finding 3 below), read straight off the same reflection
#: helper the fix itself uses -- so a field added to (or removed from) the
#: dataclass in the future changes this parametrization automatically
#: instead of the test silently going stale.
_SCALAR_STR_FIELDS = sorted(scalar_str_field_types(EnvironmentMatrix))

#: Same idea, one level down: `SyclConstraints`'/`CudaConstraints`' own
#: plain scalar `str` fields (PR #1221, round-9 finding 2 -- these were not
#: covered by the outer `EnvironmentMatrix` reflection check at all).
_SYCL_SCALAR_STR_FIELDS = sorted(scalar_str_field_types(SyclConstraints))
_CUDA_SCALAR_STR_FIELDS = sorted(scalar_str_field_types(CudaConstraints))


class TestNonNumericRuntimeFloorKeysRejectWrongShapeValues:
    @pytest.mark.parametrize(
        "bad_value",
        [["x86_64"], {"arch": "x86_64"}, True, False],
        ids=["list", "mapping", "bool_true", "bool_false"],
    )
    def test_wheel_arch_non_string_value_rejected(self, bad_value: object) -> None:
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"WHEEL_ARCH": bad_value}})

    def test_wheel_arch_non_string_value_rejected_strict(self) -> None:
        # The finding's own reported scenario: the mode .abicheck.yml's
        # `deployment:` block actually loads with.
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict(
                {"runtime_floors": {"WHEEL_ARCH": ["x86_64"]}}, strict=True
            )

    @pytest.mark.parametrize(
        "bad_value", [["level_zero"], {"arch": "x86_64"}], ids=["list", "mapping"]
    )
    @pytest.mark.parametrize("key", ["MUSLLINUX", "WHEEL_CONTEXT"])
    def test_presence_flag_keys_reject_list_and_mapping_values(
        self, key: str, bad_value: object
    ) -> None:
        # bool/int/float/None are legitimate presence-flag spellings for
        # these two keys (covered in test_environment_drift.py's own
        # TestEnvironmentMatrixRuntimeFloors) and must keep working; a
        # list/mapping is not a presence flag and must still raise rather
        # than silently stringify into a truthy nonsense value.
        with pytest.raises(ValueError, match="must be a quoted string"):
            EnvironmentMatrix.from_dict({"runtime_floors": {key: bad_value}})


class TestRuntimeFloorKeysRejectNonStringKeys:
    """Codex P2 finding on PR #1221: ``_parse_runtime_floors`` coerced every
    key with ``str(key).upper()`` unconditionally, before checking whether it
    was already a string. A malformed ``runtime_floors: {123: "2.28"}`` (an
    unquoted YAML int key -- or ``{true: "2.28"}``, which PyYAML parses as a
    bool key) loaded successfully into a spelling (``"123"``/``"TRUE"``) no
    named-prefix detector (``GLIBC``/``MUSLLINUX``/``WHEEL_ARCH``/...) ever
    recognizes, so the declared floor was silently inert -- the same
    "malformed config silently disables a hard check instead of raising the
    config error `strict=True` promises" failure mode already guarded for
    non-string *values* just above and for ``WHEEL_ARCH``'s value vocabulary
    below. The fix validates the key's type before ``.upper()`` normalizes
    it, raising the same kind of strict-config `ValueError` this whole
    ``deployment:`` parsing path already raises for other malformed shapes.
    """

    @pytest.mark.parametrize(
        "bad_key",
        [123, 2.28, True, False, None, ("GLIBC",)],
        ids=["int", "float", "bool_true", "bool_false", "none", "tuple"],
    )
    def test_non_string_key_rejected(self, bad_key: object) -> None:
        with pytest.raises(ValueError, match="keys must be strings"):
            EnvironmentMatrix.from_dict({"runtime_floors": {bad_key: "2.28"}})

    def test_non_string_key_rejected_strict(self) -> None:
        # The finding's own reported scenario: the mode .abicheck.yml's
        # `deployment:` block actually loads with.
        with pytest.raises(ValueError, match="keys must be strings"):
            EnvironmentMatrix.from_dict({"runtime_floors": {123: "2.28"}}, strict=True)

    def test_error_message_names_the_bad_key(self) -> None:
        with pytest.raises(ValueError, match="123"):
            EnvironmentMatrix.from_dict({"runtime_floors": {123: "2.28"}})

    def test_legitimate_string_keys_still_parse(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"runtime_floors": {"GLIBC": "2.28", "musllinux": True}}
        )
        assert matrix.runtime_floors["GLIBC"] == "2.28"
        assert matrix.runtime_floors["MUSLLINUX"] == "1"

    def test_bool_key_end_to_end_config_error(self, tmp_path: Path) -> None:
        """The YAML ``yes``/``true`` bool-key edge case, through the real
        ``.abicheck.yml`` ``deployment:`` block -- the CLI's strict-mode
        caller."""
        from click.testing import CliRunner

        from abicheck.cli import main

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            'deployment:\n  runtime_floors:\n    true: "2.28"\n',
            encoding="utf-8",
        )
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
        )
        assert result.exit_code == 64, result.output
        assert "keys must be strings" in result.output.lower()


class TestWheelArchUnrecognizedTokenRejected:
    """Codex review, PR #1221, Finding 1: `WHEEL_ARCH`'s value passing the
    str-type check above is not sufficient -- it must also be a token
    `diff_wheel_deployment.check_wheel_tag_architecture_mismatch` actually
    recognizes, or that detector silently treats the declared claim as "no
    claim at all" and reports nothing, disabling the hard
    architecture-mismatch gate a strict config believes it enabled.
    """

    def test_the_exact_reported_typo_is_rejected(self) -> None:
        # "x86-64" (a hyphen) for "x86_64" (an underscore) -- the finding's
        # own reported scenario.
        with pytest.raises(ValueError, match="not a recognized architecture token"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"WHEEL_ARCH": "x86-64"}})

    def test_the_exact_reported_typo_is_rejected_strict(self) -> None:
        with pytest.raises(ValueError, match="not a recognized architecture token"):
            EnvironmentMatrix.from_dict(
                {"runtime_floors": {"WHEEL_ARCH": "x86-64"}}, strict=True
            )

    @pytest.mark.parametrize(
        "bad_token", ["x86-64", "amd64", "arm", "ARM64X", "", "unknown-arch"]
    )
    def test_various_unrecognized_tokens_are_rejected(self, bad_token: str) -> None:
        with pytest.raises(ValueError, match="not a recognized architecture token"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"WHEEL_ARCH": bad_token}})

    @pytest.mark.parametrize("valid_token", sorted(WHEEL_ARCH_CLAIMS))
    def test_every_recognized_token_still_parses(self, valid_token: str) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"runtime_floors": {"WHEEL_ARCH": valid_token}}
        )
        assert matrix.runtime_floors["WHEEL_ARCH"] == valid_token

    @pytest.mark.parametrize("valid_token", sorted(WHEEL_ARCH_CLAIMS))
    def test_every_recognized_token_still_parses_strict(self, valid_token: str) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"runtime_floors": {"WHEEL_ARCH": valid_token}}, strict=True
        )
        assert matrix.runtime_floors["WHEEL_ARCH"] == valid_token

    def test_recognized_token_is_case_insensitive(self) -> None:
        # diff_wheel_deployment.py lower()s the claim before comparing, so
        # the config-time vocabulary check must accept the same casing.
        matrix = EnvironmentMatrix.from_dict(
            {"runtime_floors": {"WHEEL_ARCH": "X86_64"}}
        )
        assert matrix.runtime_floors["WHEEL_ARCH"] == "X86_64"

    def test_error_message_lists_valid_tokens(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            EnvironmentMatrix.from_dict({"runtime_floors": {"WHEEL_ARCH": "bogus"}})
        message = str(exc_info.value)
        for token in WHEEL_ARCH_CLAIMS:
            assert token in message

    def test_deployment_wheel_arch_typo_is_hard_config_error_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """The finding's own scenario, through the real `.abicheck.yml`
        `deployment:` block -- the CLI's strict-mode caller."""
        from click.testing import CliRunner

        from abicheck.cli import main

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "deployment:\n  runtime_floors:\n    WHEEL_ARCH: x86-64\n",
            encoding="utf-8",
        )
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
        )
        assert result.exit_code == 64, result.output
        assert "not a recognized architecture token" in result.output.lower()


class TestWheelArchListEndToEndConfigError:
    def test_deployment_wheel_arch_list_is_hard_config_error(
        self, tmp_path: Path
    ) -> None:
        """`WHEEL_ARCH: [x86_64]` (a YAML list, not a quoted string) in
        `.abicheck.yml`'s `deployment:` block must be a loud exit-64 error --
        not silently stringified into `"['x86_64']"`, which disables the
        wheel-architecture check instead of raising it."""
        from click.testing import CliRunner

        from abicheck.cli import main

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "deployment:\n  runtime_floors:\n    WHEEL_ARCH:\n      - x86_64\n",
            encoding="utf-8",
        )
        old_dir, new_dir = tmp_path / "old", tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
        )
        assert result.exit_code == 64, result.output
        assert "must be a quoted string" in result.output.lower()


class TestScalarStrFieldsRejectWrongShapeValues:
    """Codex review, PR #1221, Finding 3: `EnvironmentMatrix`'s plain scalar
    ``str | None`` fields (``abi_version``, ``libstdcxx_dual_abi``,
    ``target_os``, ``target_arch``) were never type-checked at all --
    ``abi_version: {bad: shape}`` loaded successfully into a frozen,
    hashable ``EnvironmentMatrix``, and ``hash(matrix)`` then raised
    ``TypeError: unhashable type: 'dict'`` the first time anything (e.g. a
    ``CompareRequest`` containing it) inserted the matrix into a dict/set --
    the same evidence-too-late failure mode round 8's list-element fix
    already closed for list elements.

    The fix (``model.dataclass_scalar_validation.scalar_str_field_types``) derives which fields to check from
    the dataclass's own ``str | None`` type annotations via
    ``typing.get_type_hints``/``dataclasses.fields`` rather than a
    hand-maintained list, so this parametrization enumerates every such
    field straight from that same function -- a newly added scalar field is
    covered automatically, without a new test needing to be written for it.
    """

    SCALAR_STR_FIELDS = _SCALAR_STR_FIELDS

    @pytest.mark.parametrize("field_name", SCALAR_STR_FIELDS)
    @pytest.mark.parametrize(
        "bad_value",
        [{"bad": "shape"}, ["list"], True, False, 1, 1.5],
        ids=["mapping", "list", "bool_true", "bool_false", "int", "float"],
    )
    def test_wrong_shape_value_rejected(
        self, field_name: str, bad_value: object
    ) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({field_name: bad_value})

    @pytest.mark.parametrize("field_name", SCALAR_STR_FIELDS)
    def test_wrong_shape_value_rejected_strict(self, field_name: str) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({field_name: ["bad"]}, strict=True)

    @pytest.mark.parametrize("field_name", SCALAR_STR_FIELDS)
    def test_valid_string_value_still_parses(self, field_name: str) -> None:
        matrix = EnvironmentMatrix.from_dict({field_name: "some-value"})
        assert getattr(matrix, field_name) == "some-value"

    @pytest.mark.parametrize("field_name", SCALAR_STR_FIELDS)
    def test_absent_field_still_defaults_to_none(self, field_name: str) -> None:
        matrix = EnvironmentMatrix.from_dict({})
        assert getattr(matrix, field_name) is None

    @pytest.mark.parametrize("field_name", SCALAR_STR_FIELDS)
    def test_explicit_none_is_still_accepted(self, field_name: str) -> None:
        matrix = EnvironmentMatrix.from_dict({field_name: None})
        assert getattr(matrix, field_name) is None

    def test_reflection_found_all_four_known_scalar_fields(self) -> None:
        # Pins the set itself, so a future field's *removal* from
        # `EnvironmentMatrix` (which would silently shrink parametrization
        # above to fewer cases) is caught here explicitly.
        assert set(self.SCALAR_STR_FIELDS) == {
            "abi_version",
            "libstdcxx_dual_abi",
            "target_os",
            "target_arch",
        }


class TestNestedSyclCudaScalarFieldsRejectWrongShapeValues:
    """Codex review, PR #1221, round-9 finding 2: the reflection check above
    (``TestScalarStrFieldsRejectWrongShapeValues``) validated only the
    outer ``EnvironmentMatrix``'s own scalar fields -- ``SyclConstraints``'
    ``implementation``/``min_pi_version`` and ``CudaConstraints``'
    ``toolkit_version`` still reached their plain ``str(...)`` coercions
    unchecked, so e.g. ``deployment.sycl.implementation: {bad: shape}`` or
    ``sycl.min_pi_version: [1.0]`` silently became the nonsense literal
    string ``"{'bad': 'shape'}"``/``"[1.0]"`` instead of the promised
    configuration error. The fix reuses the identical
    ``validate_scalar_str_fields`` reflection helper against
    ``SyclConstraints``/``CudaConstraints`` at the point each nested section
    is parsed, mirroring the outer check exactly.
    """

    @pytest.mark.parametrize("field_name", _SYCL_SCALAR_STR_FIELDS)
    @pytest.mark.parametrize(
        "bad_value",
        [{"bad": "shape"}, [1.0], True, 1, 1.5],
        ids=["mapping", "list", "bool", "int", "float"],
    )
    def test_sycl_field_wrong_shape_rejected(
        self, field_name: str, bad_value: object
    ) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({"sycl": {field_name: bad_value}})

    @pytest.mark.parametrize("field_name", _SYCL_SCALAR_STR_FIELDS)
    def test_sycl_field_valid_string_still_parses(self, field_name: str) -> None:
        matrix = EnvironmentMatrix.from_dict({"sycl": {field_name: "some-value"}})
        assert getattr(matrix.sycl, field_name) == "some-value"

    @pytest.mark.parametrize("field_name", _CUDA_SCALAR_STR_FIELDS)
    @pytest.mark.parametrize(
        "bad_value",
        [{"bad": "shape"}, [1.0], True, 1, 1.5],
        ids=["mapping", "list", "bool", "int", "float"],
    )
    def test_cuda_field_wrong_shape_rejected(
        self, field_name: str, bad_value: object
    ) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({"cuda": {field_name: bad_value}})

    @pytest.mark.parametrize("field_name", _CUDA_SCALAR_STR_FIELDS)
    def test_cuda_field_valid_string_still_parses(self, field_name: str) -> None:
        matrix = EnvironmentMatrix.from_dict({"cuda": {field_name: "some-value"}})
        assert getattr(matrix.cuda, field_name) == "some-value"

    def test_reflection_found_known_sycl_scalar_fields(self) -> None:
        assert set(_SYCL_SCALAR_STR_FIELDS) == {"implementation", "min_pi_version"}

    def test_reflection_found_known_cuda_scalar_fields(self) -> None:
        assert set(_CUDA_SCALAR_STR_FIELDS) == {"toolkit_version"}

    def test_sycl_min_pi_version_list_end_to_end_config_error(self) -> None:
        """The finding's own literal reported example."""
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({"sycl": {"min_pi_version": [1.0]}})

    def test_sycl_implementation_dict_end_to_end_config_error(self) -> None:
        """The finding's own literal reported example."""
        with pytest.raises(ValueError, match="must be a string"):
            EnvironmentMatrix.from_dict({"sycl": {"implementation": {"bad": "shape"}}})


class TestCudaDriverRangeElementValidation:
    """Codex review, PR #1221, round-9 finding 2: ``cuda.driver_range``'s
    2-element-list *shape* check did not validate each element's own type,
    so ``driver_range: [{bad: shape}, "580.0"]`` passed the outer
    ``len(...) == 2`` check and reached the bare ``str(...)`` coercion,
    stringifying the dict element into a meaningless literal instead of
    raising. Mirrors ``sycl.backends``/``cuda.gpu_architectures``/
    ``compilers``' own element-type checks (round 8's
    ``_validate_str_list_elements`` fix).
    """

    @pytest.mark.parametrize(
        "bad_element",
        [{"bad": "shape"}, [1.0], True, 1, 1.5],
        ids=["mapping", "list", "bool", "int", "float"],
    )
    def test_dict_or_list_element_rejected(self, bad_element: object) -> None:
        with pytest.raises(ValueError, match="must be strings"):
            EnvironmentMatrix.from_dict(
                {"cuda": {"driver_range": [bad_element, "580.0"]}}
            )

    def test_both_elements_strings_still_parses(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {"cuda": {"driver_range": ["525.0", "580.0"]}}
        )
        assert matrix.cuda.driver_range == ("525.0", "580.0")


class TestScalarFieldsAndHashabilityRoundTrip:
    """Positive hashability round-trip: a well-typed matrix using every
    scalar field must still hash cleanly after the Finding 3 fix (a
    regression here would mean the new validation rejected something it
    should not have)."""

    def test_fully_populated_scalar_fields_still_hash(self) -> None:
        matrix = EnvironmentMatrix.from_dict(
            {
                "abi_version": "18",
                "libstdcxx_dual_abi": "cxx11",
                "target_os": "linux",
                "target_arch": "x86_64",
            }
        )
        assert isinstance(hash(matrix), int)
        # A `CompareRequest`-shaped use: insertable into a dict/set.
        cache = {matrix: "resolved"}
        lookup = EnvironmentMatrix.from_dict(
            {
                "abi_version": "18",
                "libstdcxx_dual_abi": "cxx11",
                "target_os": "linux",
                "target_arch": "x86_64",
            }
        )
        assert cache[lookup] == "resolved"

    def test_empty_matrix_still_hashes(self) -> None:
        assert isinstance(hash(EnvironmentMatrix.from_dict({})), int)

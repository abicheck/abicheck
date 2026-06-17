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

"""Unit tests for the typed Tier-2 request structs (ADR-037 D2 / G22 Phase 1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from abicheck.api_types import CompareRequest, InputSpec, OutputSpec
from abicheck.errors import ValidationError


class TestInputSpec:
    def test_of_coerces_str_to_path(self):
        spec = InputSpec.of("lib.so", headers=["a.h", "b.h"], includes=["inc"])
        assert spec.path == Path("lib.so")
        assert spec.headers == (Path("a.h"), Path("b.h"))
        assert spec.includes == (Path("inc"),)

    def test_of_single_string_is_one_path_not_per_character(self):
        # A bare string must be one path, not a tuple of per-character paths.
        spec = InputSpec.of("lib.so", headers="include/api.h")
        assert spec.headers == (Path("include/api.h"),)

    def test_of_single_path_is_one_path(self):
        spec = InputSpec.of("lib.so", includes=Path("inc"))
        assert spec.includes == (Path("inc"),)

    def test_of_defaults_are_empty_tuples(self):
        spec = InputSpec.of("lib.so")
        assert spec.headers == ()
        assert spec.includes == ()
        assert spec.debug_roots == ()
        assert spec.pdb is None
        assert spec.version == ""

    def test_of_pdb_coerced(self):
        spec = InputSpec.of("lib.so", pdb="lib.pdb", debug_roots=["dbg"])
        assert spec.pdb == Path("lib.pdb")
        assert spec.debug_roots == (Path("dbg"),)

    def test_is_frozen(self):
        spec = InputSpec.of("lib.so")
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.path = Path("other.so")  # type: ignore[misc]


class TestCompareRequestDefaults:
    def test_scope_public_defaults_true(self):
        # The headline drift fix: one default for every front-end (ADR-037 §Context #1).
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.scope_public is True

    def test_other_defaults(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.lang == "c++"
        assert req.policy == "strict_abi"
        assert req.policy_file_path is None
        assert req.suppress is None
        assert req.force_public_symbols is None
        assert req.pattern_verdicts is False
        assert req.enable_debuginfod is False

    def test_is_frozen(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.scope_public = False  # type: ignore[misc]

    def test_distinct_instances_do_not_share_defaults(self):
        # A frozen dataclass with a bare mutable default would share it across
        # instances; the struct fields here are immutable, so this must hold.
        a = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        c = CompareRequest(old=InputSpec.of("c"), new=InputSpec.of("d"))
        assert a.old is not c.old
        assert a.old.headers == c.old.headers == ()


class TestCompareRequestValidate:
    def test_valid_request_has_no_errors(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        assert req.validation_errors() == []
        assert req.validate() is req

    @pytest.mark.parametrize("lang", ["c", "c++", "C", "C++"])
    def test_supported_langs_accepted(self, lang):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), lang=lang)
        assert req.validation_errors() == []

    def test_unsupported_lang_rejected(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), lang="rust")
        errors = req.validation_errors()
        assert len(errors) == 1
        assert "rust" in errors[0]
        with pytest.raises(ValidationError, match="rust"):
            req.validate()

    def test_empty_policy_rejected(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"), policy="")
        assert any("policy" in e for e in req.validation_errors())

    def test_multiple_errors_collected(self):
        req = CompareRequest(
            old=InputSpec.of("a"), new=InputSpec.of("b"), lang="go", policy=""
        )
        assert len(req.validation_errors()) == 2


class TestCompareRequestReplace:
    def test_replace_round_trips_fields(self):
        req = CompareRequest(old=InputSpec.of("a"), new=InputSpec.of("b"))
        changed = req.replace(scope_public=False, lang="c")
        assert changed.scope_public is False
        assert changed.lang == "c"
        # Original is untouched (frozen + copy semantics).
        assert req.scope_public is True
        assert req.lang == "c++"
        # Unchanged fields are carried over verbatim.
        assert changed.old is req.old
        assert changed.policy == req.policy


class TestOutputSpec:
    def test_defaults(self):
        out = OutputSpec()
        assert out.fmt == "text"
        assert out.path is None

    def test_is_frozen(self):
        out = OutputSpec(fmt="json")
        with pytest.raises(dataclasses.FrozenInstanceError):
            out.fmt = "sarif"  # type: ignore[misc]

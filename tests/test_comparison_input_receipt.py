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

"""The resolved-input digests a comparison stamps on its result.

Lifted out of ``checker.compare``'s result assembly, and tested here
directly rather than only through a full comparison: each digest exists so
two runs that resolved *different* inputs cannot collide on the
effective-configuration digest, and that is a property of the function, not
of any one comparison.
"""

from __future__ import annotations

from abicheck.workflows.comparison_input_receipt import (
    comparison_input_receipt,
    env_matrix_content_digest,
    explicit_scope_content_digest,
)


class TestExplicitScopeContentDigest:
    def test_no_scope_inputs_is_none(self) -> None:
        assert explicit_scope_content_digest(None, None) is None

    def test_an_empty_forced_public_set_is_no_scope(self) -> None:
        """Plain truthiness for this axis, deliberately -- every other
        consumer in this codebase treats an empty set as equivalent to
        ``None`` for ``force_public_symbols``."""
        assert explicit_scope_content_digest(set(), None) is None

    def test_an_empty_allowlist_is_a_real_distinct_scope(self) -> None:
        """Gated on ``is not None``, not truthiness: a POST manifest
        committing to zero exports is active configuration, and must not
        hash identically to having no manifest at all."""
        empty = explicit_scope_content_digest(None, set())
        assert empty is not None
        assert empty != explicit_scope_content_digest(None, None)

    def test_the_two_axes_stay_distinguishable(self) -> None:
        """Keyed rather than delimiter-joined: the same names under
        different axes must not collide."""
        assert explicit_scope_content_digest({"a"}, None) != (
            explicit_scope_content_digest(None, {"a"})
        )

    def test_it_is_order_insensitive_but_membership_sensitive(self) -> None:
        assert explicit_scope_content_digest({"a", "b"}, None) == (
            explicit_scope_content_digest({"b", "a"}, None)
        )
        assert explicit_scope_content_digest({"a", "b"}, None) != (
            explicit_scope_content_digest({"a"}, None)
        )

    def test_it_is_a_prefixed_sha256(self) -> None:
        digest = explicit_scope_content_digest({"a"}, None)
        assert digest is not None
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64


class TestEnvMatrixContentDigest:
    def test_no_matrix_is_none(self) -> None:
        assert env_matrix_content_digest(None) is None

    def test_a_declared_matrix_digests(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        digest = env_matrix_content_digest(EnvironmentMatrix.from_dict({}))
        assert digest is not None and digest.startswith("sha256:")

    def test_different_matrices_do_not_collide(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        a = env_matrix_content_digest(EnvironmentMatrix.from_dict({}))
        b = env_matrix_content_digest(
            EnvironmentMatrix.from_dict({"target_os": "linux"})
        )
        assert a != b


class TestComparisonInputReceipt:
    def test_nothing_resolved_is_an_empty_receipt(self) -> None:
        receipt = comparison_input_receipt(None, None, None)
        assert receipt.explicit_scope_source_sha256 is None
        assert receipt.env_matrix_source_sha256 is None

    def test_it_reports_exactly_what_each_helper_answers(self) -> None:
        """The assembly carries each value through unchanged -- a receipt
        that recomputed either one differently is the drift this shape
        exists to prevent."""
        from abicheck.environment_matrix import EnvironmentMatrix

        matrix = EnvironmentMatrix.from_dict({"target_os": "linux"})
        receipt = comparison_input_receipt({"a"}, None, matrix)
        assert receipt.explicit_scope_source_sha256 == explicit_scope_content_digest(
            {"a"}, None
        )
        assert receipt.env_matrix_source_sha256 == env_matrix_content_digest(matrix)

    def test_the_two_axes_are_independent(self) -> None:
        matrix_only = comparison_input_receipt(None, None, None)
        assert matrix_only.explicit_scope_source_sha256 is None
        scope_only = comparison_input_receipt({"a"}, None, None)
        assert scope_only.env_matrix_source_sha256 is None
        assert scope_only.explicit_scope_source_sha256 is not None

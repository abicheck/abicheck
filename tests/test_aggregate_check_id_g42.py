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

"""G42 "Explicit check identifiers" tests for
``abicheck.workflows.aggregate.contracts.parse_check_id`` -- split out of
``test_aggregate.py`` (that file sits at the AI-readiness 2000-line hard
cap and carries a ``no_growth`` debt-baseline entry, per this repo's own
``file-size`` gate/``architecture/debt.yaml`` convention: grow via a new
sibling test file, not by extending the file at its cap).

Covers the two new, composable ``check_id`` tail segments
(``!<environment_id>``/``~<explicit_id>``) ``_CHECK_ID_RE`` gained -- see
``docs/contribute/plans/g42-check-identity-environments-and-provider-
resolution.md``'s "Explicit check identifiers" section.
``test_aggregate.py::TestProfileMatrixIdParsing`` keeps its own pre-G42
``parse_check_id`` round-trip/rejection tests unchanged.
"""

from __future__ import annotations

from abicheck.workflows.aggregate.contracts import parse_check_id


class TestParseCheckIdG42Tails:
    def test_pre_g42_shape_has_no_new_fields(self) -> None:
        """Backward-compatibility guarantee: an unqualified id parses with
        both new fields ``None``."""
        parsed = parse_check_id("libfoo@linux-gcc14#release@headers")
        assert parsed is not None
        assert parsed.environment_id is None
        assert parsed.explicit_id is None

    def test_explicit_id_tail(self) -> None:
        parsed = parse_check_id("libfoo@linux-gcc14#release@headers~l4-plugin-rhel8")
        assert parsed is not None
        assert parsed.target == "libfoo"
        assert parsed.profile == "linux-gcc14"
        assert parsed.baseline_channel == "release"
        assert parsed.requested_depth == "headers"
        assert parsed.environment_id is None
        assert parsed.explicit_id == "l4-plugin-rhel8"

    def test_environment_and_explicit_id_tails_compose_in_fixed_order(self) -> None:
        parsed = parse_check_id("libfoo@linux-gcc14#release@headers!rhel8~myid")
        assert parsed is not None
        assert parsed.environment_id == "rhel8"
        assert parsed.explicit_id == "myid"

    def test_only_environment_tail(self) -> None:
        parsed = parse_check_id("libfoo@linux-gcc14#release@headers!rhel8")
        assert parsed is not None
        assert parsed.environment_id == "rhel8"
        assert parsed.explicit_id is None

    def test_wrong_tail_order_is_rejected(self) -> None:
        """``~<explicit_id>`` before ``!<environment_id>`` is not the fixed
        order this pattern accepts."""
        assert parse_check_id("libfoo@linux-gcc14#release@headers~myid!rhel8") is None

    def test_trailing_newline_is_rejected(self) -> None:
        """Codex review: ``_CHECK_ID_RE`` was anchored with a trailing
        ``$``, which (without ``re.MULTILINE``) also matches just before a
        trailing ``\\n`` -- kept in lockstep with ``CHECK_ID_PATTERN``'s
        identical fix per this module's own docstring."""
        assert parse_check_id("libfoo@linux-gcc14#release@headers~myid\n") is None

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
``abicheck.buildsource.check_report.build_check_id``'s new
``environment_id``/``explicit_id`` parameters -- split out of
``test_check_report.py`` (that file carries a ``no_growth`` debt-baseline
entry in ``architecture/debt.yaml``, per this repo's own ``file-size``
gate convention: grow via a new sibling test file, not by extending the
file at its baseline).

Covers the two new, composable ``check_id`` tail segments
(``!<environment_id>``/``~<explicit_id>``) -- see
``docs/contribute/plans/g42-check-identity-environments-and-provider-
resolution.md``'s "Explicit check identifiers" section.
``test_check_report.py::TestBuildCheckId`` keeps its own pre-G42
``build_check_id`` tests unchanged.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.check_report import build_check_id


class TestBuildCheckIdG42Tails:
    def test_explicit_id_appends_tilde_tail(self):
        """An explicit_id appends a '~<id>' tail, composed after the base
        target@profile#channel@depth shape."""
        check_id = build_check_id(
            "libpvxs", "p", "c", "source", explicit_id="l4-plugin-rhel8"
        )
        assert check_id == "libpvxs@p#c@source~l4-plugin-rhel8"

    def test_environment_id_appends_bang_tail_before_explicit_id(self):
        """!<environment_id> composes before ~<explicit_id>, in that fixed
        order."""
        check_id = build_check_id(
            "libpvxs",
            "p",
            "c",
            "source",
            environment_id="rhel8",
            explicit_id="myid",
        )
        assert check_id == "libpvxs@p#c@source!rhel8~myid"

    def test_no_explicit_or_environment_id_is_unqualified(self):
        """Omitting both new params produces the byte-identical pre-G42
        string -- the backward-compatibility guarantee."""
        check_id = build_check_id(
            "libpvxs", "p", "c", "source", environment_id=None, explicit_id=None
        )
        assert check_id == "libpvxs@p#c@source"

    def test_two_checks_differing_only_in_explicit_id_do_not_collide(self):
        replay_id = build_check_id(
            "libpvxs", "p", "c", "source", explicit_id="l4-replay"
        )
        plugin_id = build_check_id(
            "libpvxs", "p", "c", "source", explicit_id="l4-plugin"
        )
        assert replay_id != plugin_id

    def test_rejects_unsafe_explicit_id(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "source", explicit_id="bad~id")

    def test_rejects_unsafe_environment_id(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "source", environment_id="bad!id")

    def test_empty_string_explicit_id_is_treated_as_unset(self):
        """An empty string is falsy -- same "no explicit id" behavior as
        omitting the parameter entirely, not a validation error."""
        check_id = build_check_id("libpvxs", "p", "c", "source", explicit_id="")
        assert check_id == "libpvxs@p#c@source"

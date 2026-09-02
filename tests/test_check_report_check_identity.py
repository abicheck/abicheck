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

from abicheck.buildsource.check_report import (
    augment_report,
    build_bootstrap_report,
    build_check_id,
    build_new_target_report,
    build_operational_error_report,
)


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

    def test_rejects_explicit_id_with_a_trailing_newline(self):
        """Codex review: _IDENTIFIER_RE was anchored with a trailing '$',
        which (without re.MULTILINE) also matches just before a trailing
        '\\n' -- a value carrying one would otherwise validate cleanly and
        propagate a newline into the generated check_id."""
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "source", explicit_id="l4-plugin\n")

    def test_rejects_environment_id_with_a_trailing_newline(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "source", environment_id="rhel8\n")

    def test_rejects_environment_id_starting_with_punctuation(self):
        """Codex review, fresh evidence: build_check_id's own
        validate_identifier() already required an initial alphanumeric
        character for environment_id (same as explicit_id) -- this pins
        that existing constructor-side behavior. The real gap Codex found
        is that checker_types.CHECK_ID_PATTERN/contracts._CHECK_ID_RE and
        the published JSON Schema independently spelled the environment_id
        segment's charset as the more permissive [A-Za-z0-9._-]+ (no
        initial-character constraint), so the public validators/parser
        accepted an id (e.g. "...!_prod") this constructor could never
        produce -- pinned by the sibling assertions in
        test_report_schema.py (CHECK_ID_PATTERN via validate_check_id) and
        test_aggregate_check_id_g42.py (_CHECK_ID_RE via parse_check_id)."""
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "source", environment_id="_prod")


class TestReportBuildersThreadExplicitId:
    """Each of the four public report-envelope builders backing
    ``actions/check-target/report_envelope.py``'s four modes must fold
    ``explicit_id`` into the ``check_id`` it stamps -- a real gap a review
    round found: an earlier revision extended ``build_check_id`` but left
    all four call sites in this module (and ``report_envelope.py``'s own
    CLI, see ``test_action_check_target_explicit_id.py``) never passing
    the parameter through."""

    def test_augment_report(self):
        report = {"verdict": "COMPATIBLE", "exit_code": 0}
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            explicit_id="l4-plugin",
        )
        assert out["check_id"] == "libpvxs@p#c@headers~l4-plugin"
        assert out["target_id"] == out["check_id"]

    def test_augment_report_no_explicit_id_is_unqualified(self):
        report = {"verdict": "COMPATIBLE", "exit_code": 0}
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["check_id"] == "libpvxs@p#c@headers"

    def test_build_operational_error_report(self):
        report = build_operational_error_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_outcome="ambiguous",
            resolve_message="could not resolve.",
            explicit_id="l4-plugin",
        )
        assert report["check_id"] == "libpvxs@p#c@headers~l4-plugin"
        assert report["target_id"] == report["check_id"]

    def test_build_bootstrap_report(self):
        report = build_bootstrap_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_message="no baseline yet.",
            explicit_id="l4-plugin",
        )
        assert report["check_id"] == "libpvxs@p#c@headers~l4-plugin"

    def test_build_new_target_report(self):
        report = build_new_target_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_message="target new to this baseline-set.",
            explicit_id="l4-plugin",
        )
        assert report["check_id"] == "libpvxs@p#c@headers~l4-plugin"

    def test_augment_report_scan_shaped_report_bumps_scan_schema_version(self):
        """Codex review, fresh evidence: a no-baseline audit run
        (check-target's scan mode) augments a *scan*-shaped report
        (detected by _stamp_schema_version via the presence of
        scan_schema_version), and build_check_id is the single shared
        constructor for both compare- and scan-shaped reports -- so the
        widened check_id lexical contract (the ~<explicit_id>/
        !<environment_id> tail) reaches a scan report too, not just a
        compare one. Only REPORT_SCHEMA_VERSION had been bumped for G42;
        this pins that SCAN_SCHEMA_VERSION was bumped in lockstep so a
        scan consumer can feature-detect the widened shape."""
        from abicheck.schemas import SCAN_SCHEMA_VERSION

        report = {"scan_schema_version": "1.0", "verdict": "COMPATIBLE"}
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            explicit_id="l4-plugin",
        )
        assert out["scan_schema_version"] == SCAN_SCHEMA_VERSION
        assert out["check_id"] == "libpvxs@p#c@headers~l4-plugin"

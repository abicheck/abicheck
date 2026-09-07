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

"""G42 "Explicit check identifiers" tests for ``CheckSpec.id``/
``CheckSpec.analysis_*`` -- split out of ``test_project_targets.py`` (that
file sits at the AI-readiness 2000-line hard cap and carries a
``no_growth`` debt-baseline entry, per this repo's own ``file-size`` gate/
``architecture/debt.yaml`` convention: grow via a new sibling test file, not
by extending the file at its cap).

Covers ``checks[].id``/``checks[].analysis: {evidence, policy, assurance}``
parsing, round-tripping, and the "parsing alone isn't validation" split
this module's own convention documents: structural validity (mapping
shape, known keys, non-empty strings) raises directly from ``CheckSpec.
from_dict``, while the identifier-charset check is deferred to
``validate_project_targets`` (``_check_issues``), same as every other
identifier this module validates. See ``docs/contribute/plans/
g42-check-identity-environments-and-provider-resolution.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.project_targets import (
    CheckSpec,
    ProjectTargetsConfig,
    validate_project_targets,
)
from abicheck.cli import main


def test_check_id_and_analysis_round_trip() -> None:
    check = CheckSpec.from_dict(
        {
            "channel": "accepted-main",
            "depth": "source",
            "id": "l4-plugin-rhel8",
            "analysis": {
                "evidence": "clang-plugin",
                "policy": "strict-abi",
                "assurance": "complete",
            },
        },
        where="targets.libfoo.checks[0]",
    )
    assert check.id == "l4-plugin-rhel8"
    assert check.analysis_evidence == "clang-plugin"
    assert check.analysis_policy == "strict-abi"
    assert check.analysis_assurance == "complete"
    d = check.to_dict()
    assert d["id"] == "l4-plugin-rhel8"
    assert d["analysis"] == {
        "evidence": "clang-plugin",
        "policy": "strict-abi",
        "assurance": "complete",
    }
    round_tripped = CheckSpec.from_dict(d, where="targets.libfoo.checks[0]")
    assert round_tripped == check


def test_check_id_and_analysis_are_optional() -> None:
    """Every existing checks[] entry that predates id:/analysis: parses
    unchanged -- both default to empty, and to_dict() omits them entirely."""
    check = CheckSpec.from_dict(
        {"channel": "accepted-main", "depth": "headers"},
        where="targets.libfoo.checks[0]",
    )
    assert check.id == ""
    assert check.analysis_evidence == check.analysis_policy == check.analysis_assurance
    assert check.analysis_evidence == ""
    d = check.to_dict()
    assert "id" not in d
    assert "analysis" not in d


def test_check_id_must_be_a_valid_identifier() -> None:
    """id:'s charset is validated by validate_project_targets (deferred),
    not raised at parse time -- same split every other identifier in this
    module follows."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [{"channel": "none", "depth": "headers", "id": "bad id"}],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("id 'bad id' is not a valid identifier" in e for e in report.errors)


def test_check_id_with_a_trailing_newline_is_rejected() -> None:
    """Codex review: _IDENTIFIER_RE was anchored with a trailing '$', which
    (without re.MULTILINE) also matches just before a trailing '\\n' -- a
    YAML block-scalar id: value (PyYAML commonly appends one) would
    otherwise pass this check with an embedded newline still in it,
    silently propagating into a generated check_id that report_envelope.py
    then refuses to write (a newline would corrupt GITHUB_OUTPUT)."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {"channel": "none", "depth": "headers", "id": "l4-plugin\n"}
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not a valid identifier" in e for e in report.errors)


def test_check_analysis_field_with_a_trailing_newline_is_rejected() -> None:
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "none",
                            "depth": "headers",
                            "analysis": {"evidence": "replay\n"},
                        }
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any("is not a valid identifier" in e for e in report.errors)


def test_check_analysis_must_be_a_mapping() -> None:
    with pytest.raises(ValueError, match="analysis must be a mapping"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": "clang-plugin"},
            where="targets.libfoo.checks[0]",
        )


def test_check_analysis_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": {"bogus": "x"}},
            where="targets.libfoo.checks[0]",
        )


def test_check_analysis_field_must_be_a_valid_identifier() -> None:
    """Same deferred-validation split as id: above."""
    config = ProjectTargetsConfig.from_dict(
        {
            "targets": {
                "libfoo": {
                    "kind": "library",
                    "binary_pattern": "lib/libfoo.so",
                    "checks": [
                        {
                            "channel": "none",
                            "depth": "headers",
                            "analysis": {"evidence": "bad evidence"},
                        }
                    ],
                }
            }
        }
    )
    report = validate_project_targets(config)
    assert not report.ok
    assert any(
        "analysis.evidence 'bad evidence' is not a valid identifier" in e
        for e in report.errors
    )


def test_check_analysis_empty_string_field_is_rejected_structurally() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "analysis": {"evidence": ""}},
            where="targets.libfoo.checks[0]",
        )


def test_check_id_empty_string_is_rejected_structurally() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        CheckSpec.from_dict(
            {"channel": "none", "depth": "headers", "id": ""},
            where="targets.libfoo.checks[0]",
        )


class TestAnalysisAssuranceTruthfulness:
    """Product-gaps audit, "First vertical slice": ``analysis.assurance``
    accepted a free-form string with only ``"complete"`` ever mapping to a
    real, wired enforcement mechanism (``compare``/``scan --against``'s
    ``--require-complete-analysis`` gate) -- everything else was structurally
    valid (a plain identifier), carried all the way into the generated run
    plan, and honored by nothing. ``_check_issues`` now rejects any other
    value before a run plan is generated, rather than accepting and silently
    ignoring it. See ``SUPPORTED_ANALYSIS_ASSURANCE_VALUES``'s own docstring.
    """

    @staticmethod
    def _config(assurance: str) -> ProjectTargetsConfig:
        return ProjectTargetsConfig.from_dict(
            {
                "targets": {
                    "libfoo": {
                        "kind": "library",
                        "binary_pattern": "lib/libfoo.so",
                        "checks": [
                            {
                                "channel": "none",
                                "depth": "headers",
                                "analysis": {"assurance": assurance},
                            }
                        ],
                    }
                }
            }
        )

    @pytest.mark.parametrize(
        "assurance",
        ["partial", "best-effort", "high", "strict", "COMPLETE", "complete-ish"],
    )
    def test_unsupported_assurance_value_is_rejected(self, assurance: str) -> None:
        """Structurally valid (matches the identifier charset) but not a
        value any gate in this codebase enforces -- must fail validation,
        not pass through silently. Covers several distinct sibling values,
        not just one reported string (bug-class regression-testing
        convention), including a value differing from the supported one only
        by case."""
        report = validate_project_targets(self._config(assurance))
        assert not report.ok
        assert any(
            f"analysis.assurance {assurance!r} is not a supported assurance level" in e
            for e in report.errors
        )

    def test_supported_assurance_value_passes(self) -> None:
        """Negative control: the one value with a real gate is accepted."""
        report = validate_project_targets(self._config("complete"))
        assert report.ok

    def test_absent_assurance_is_unaffected(self) -> None:
        """No analysis: block at all -- this new rule must never fire for
        the overwhelming majority of checks that declare no assurance
        requirement."""
        config = ProjectTargetsConfig.from_dict(
            {
                "targets": {
                    "libfoo": {
                        "kind": "library",
                        "binary_pattern": "lib/libfoo.so",
                        "checks": [{"channel": "none", "depth": "headers"}],
                    }
                }
            }
        )
        report = validate_project_targets(config)
        assert report.ok


class TestAnalysisAssuranceTruthfulnessCli:
    """Same rule, exercised through the real public entry point
    (``abicheck project validate``/``abicheck project plan``), not just the
    internal ``validate_project_targets`` function -- per this repo's own
    "validate the user-facing result" product-decision rule (root
    ``AGENTS.md``)."""

    @staticmethod
    def _write_config(tmp_path: Path, assurance: str) -> Path:
        config_path = tmp_path / ".abicheck.yml"
        config_path.write_text(
            "targets:\n"
            "  libfoo:\n"
            "    kind: library\n"
            "    binary_pattern: lib/libfoo.so\n"
            "    checks:\n"
            "      - channel: none\n"
            "        depth: headers\n"
            "        analysis:\n"
            f"          assurance: {assurance}\n"
        )
        return config_path

    def test_project_validate_rejects_unsupported_assurance(
        self, tmp_path: Path
    ) -> None:
        config_path = self._write_config(tmp_path, "partial")
        result = CliRunner().invoke(main, ["project", "validate", str(config_path)])
        assert result.exit_code == 1, result.output
        assert "analysis.assurance 'partial' is not a supported assurance level" in (
            result.output
        )

    def test_project_plan_refuses_to_generate_for_unsupported_assurance(
        self, tmp_path: Path
    ) -> None:
        """The unsupported value is caught before any run-plan is
        generated: ``project plan`` fails with a usage error (64) rather
        than emitting a run-plan carrying a directive nothing will honor."""
        config_path = self._write_config(tmp_path, "partial")
        result = CliRunner().invoke(main, ["project", "plan", str(config_path)])
        assert result.exit_code == 64, result.output
        assert "analysis.assurance 'partial' is not a supported assurance level" in (
            result.output
        )

    def test_project_validate_accepts_supported_assurance(self, tmp_path: Path) -> None:
        """Negative control: the real end-to-end CLI path still accepts the
        one value with a wired gate."""
        config_path = self._write_config(tmp_path, "complete")
        result = CliRunner().invoke(main, ["project", "validate", str(config_path)])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

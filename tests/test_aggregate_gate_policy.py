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

"""Unit tests for the ``aggregate`` manifest's own ``gate`` block (CLI
cleanup phase two, PR 2) -- the versioned replacement for the removed
``--on-missing-required``/``--on-unexpected-target`` CLI flags.

Split out of ``test_aggregate.py`` (AI-readiness file-size cap), which
already covers the ``on_missing_required``/``on_unexpected_target`` Python-API
parameters themselves in depth; this file is scoped to the manifest-carried
policy and its projection through ``--manifest``/``--run-plan``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.aggregate import (
    AggregateError,
    CoverageStatus,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    aggregate_reports_dir,
)

LINUX = "linux-x86_64"
WINDOWS = "windows-x86_64"
MACOS = "macos-arm64"


def _write_report(d: Path, target_id: str, verdict: str | None, **extra) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    path = d / f"abi-report-{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _expect(*required: str, optional: tuple[str, ...] = ()) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), list(optional))


class TestGatePolicy:
    """The manifest's own ``gate`` block."""

    def test_manifest_gate_missing_required_warn_is_honored(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        manifest = ExpectedTargets.from_manifest_data(
            {
                "aggregate_manifest_version": "2.0",
                "targets": [{"id": LINUX}, {"id": WINDOWS}],
                "gate": {"missing_required": "warn"},
            }
        )
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert r.on_missing_required is OnMissingRequired.WARN
        assert r.coverage is CoverageStatus.PARTIAL
        assert not r.coverage_blocking
        assert r.exit_code() == 0

    def test_manifest_gate_unexpected_target_fail_is_honored(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "COMPATIBLE")  # unexpected, clean
        manifest = ExpectedTargets.from_manifest_data(
            {
                "aggregate_manifest_version": "2.0",
                "targets": [{"id": LINUX}],
                "gate": {"unexpected_target": "fail"},
            }
        )
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert r.on_unexpected_target is OnUnexpectedTarget.FAIL
        assert r.exit_code() == 1

    def test_default_policy_when_manifest_omits_gate(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert r.on_missing_required is OnMissingRequired.FAIL
        assert r.on_unexpected_target is OnUnexpectedTarget.INCLUDE
        assert r.policy_source == "default"

    def test_effective_policy_reports_manifest_source(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        manifest = ExpectedTargets.from_manifest_data(
            {
                "aggregate_manifest_version": "2.0",
                "targets": [{"id": LINUX}],
                "gate": {"missing_required": "warn"},
            }
        )
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert r.policy_source == "manifest"
        d = r.to_dict()
        assert d["effective_policy"] == {
            "missing_required": "warn",
            "unexpected_target": "include",
            "source": "manifest",
        }

    def test_effective_policy_reports_default_source(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        d = r.to_dict()
        assert d["effective_policy"] == {
            "missing_required": "fail",
            "unexpected_target": "include",
            "source": "default",
        }

    def test_discovered_only_policy_source_is_default(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, discovered_only=True)
        assert r.policy_source == "default"

    def test_explicit_override_wins_over_manifest_gate(self, tmp_path: Path):
        # A direct API caller forcing a value (no longer reachable via the
        # CLI, which has no flags for this) still wins over the manifest's
        # own gate block -- and is reported as "default" source, since it's
        # neither a manifest nor a run-plan value.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        manifest = ExpectedTargets.from_manifest_data(
            {
                "aggregate_manifest_version": "2.0",
                "targets": [{"id": LINUX}, {"id": WINDOWS}],
                "gate": {"missing_required": "warn"},
            }
        )
        r = aggregate_reports_dir(
            tmp_path, expected=manifest, on_missing_required=OnMissingRequired.FAIL
        )
        assert r.on_missing_required is OnMissingRequired.FAIL
        assert r.policy_source == "default"

    @pytest.mark.parametrize(
        "bad_gate",
        [
            {"gate": "not-an-object"},
            {"gate": {"missing_required": "bogus"}},
            {"gate": {"unexpected_target": "bogus"}},
            {"gate": {"unknown_key": "fail"}},
        ],
    )
    def test_manifest_rejects_malformed_gate(self, bad_gate):
        data = {
            "aggregate_manifest_version": "2.0",
            "targets": [{"id": LINUX}],
            **bad_gate,
        }
        with pytest.raises(AggregateError):
            ExpectedTargets.from_manifest_data(data)

    def test_gate_block_requires_major_2(self):
        # A gate block published under the pre-2.0 major would be silently
        # ignored by an old reader (the whole point of the MAJOR bump) --
        # confirm the *current* reader still accepts it (it supports 2.0),
        # and that an old-vintage 1.0 manifest with no gate block at all
        # keeps working (backward compatibility within the accepted range).
        exp = ExpectedTargets.from_manifest_data(
            {"aggregate_manifest_version": "1.0", "targets": [{"id": LINUX}]}
        )
        assert exp.gate_missing_required is None
        assert exp.gate_unexpected_target is None


class TestGatePolicyCLI:
    def _run(self, args):
        from abicheck.cli import main

        return CliRunner().invoke(main, ["aggregate", *args])

    def _manifest(self, tmp_path: Path, *ids: str):
        path = tmp_path / "expected.json"
        path.write_text(
            json.dumps({"targets": [{"id": i, "required": True} for i in ids]})
        )
        return path

    def test_removed_on_missing_required_flag_exits_64(self, tmp_path: Path):
        # CLI cleanup phase two, PR 2: the standalone flag is gone -- the
        # policy lives in the manifest's own `gate` block now.
        res = self._run(
            [
                "--manifest",
                str(self._manifest(tmp_path, LINUX)),
                "--on-missing-required",
                "warn",
                str(tmp_path),
            ]
        )
        assert res.exit_code == 64

    def test_removed_on_unexpected_target_flag_exits_64(self, tmp_path: Path):
        res = self._run(
            [
                "--manifest",
                str(self._manifest(tmp_path, LINUX)),
                "--on-unexpected-target",
                "fail",
                str(tmp_path),
            ]
        )
        assert res.exit_code == 64

    def test_manifest_gate_block_reaches_the_gate_through_the_real_cli(
        self, tmp_path: Path
    ):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / "m.json").write_text(
            json.dumps(
                {
                    "aggregate_manifest_version": "2.0",
                    "targets": [
                        {"id": LINUX, "required": True},
                        {"id": WINDOWS, "required": True},
                    ],
                    "gate": {"missing_required": "warn"},
                }
            )
        )
        res = self._run(
            ["--manifest", str(tmp_path / "m.json"), "--format", "json", str(tmp_path)]
        )
        assert res.exit_code == 0
        payload = json.loads(res.output)
        assert payload["effective_policy"] == {
            "missing_required": "warn",
            "unexpected_target": "include",
            "source": "manifest",
        }

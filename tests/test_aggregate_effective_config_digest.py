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

"""Phase 0 item 6 of docs/contribute/plans/duplication-and-convergence-
assessment.md ("every effective evaluation carries a digest") -- the exact
sibling of ``tests/test_aggregate_analysis_assurance.py``'s own split, for
a different reported field.

A sibling file, not a class inside ``test_aggregate.py``: that file is at
the AGENTS.md 2000-line hard cap already, and a new test class there pushed
it to 2015 -- the same split this repo's own established convention
already applies for exactly this reason (see
``test_aggregate_analysis_assurance.py``'s own docstring). Carries its own
small copy of ``test_aggregate.py``'s ``_write_report``/``_expect``
helpers rather than importing them, matching that same convention.

A per-target report already stamps its own ``effective_config_digest``
(``add_effective_config_digest``, ``reporter_contract_blocks.py``) for
``compare``/``scan``/``release`` -- but ``aggregate``'s own per-target
roll-up (``_LoadedReport`` -> ``TargetReport``) previously dropped it
rather than carrying it through, so a consumer of the aggregate report
could not tell which effective configuration each target was evaluated
under without opening every individual per-target report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

LINUX = "linux-x86_64"


def _write_report(
    d: Path,
    target_id: str,
    verdict: str | None,
    *,
    prefix: str = "abi-report-",
    **extra,
) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _expect(*required: str) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), [])


class TestEffectiveConfigDigestPassthrough:
    """A per-target report's own ``effective_config_digest`` must survive
    into the aggregate's own ``targets[].effective_config_digest`` — read,
    never recomputed, since the aggregate holds none of the ``DiffResult``/
    ``SeverityConfig`` evidence the digest is derived from."""

    def test_digest_carries_through_to_target_dict(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            effective_config_digest="sha256:deadbeef",
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        target = next(t for t in r.targets if t.target_id == LINUX)
        assert target.effective_config_digest == "sha256:deadbeef"
        assert r.to_dict()["targets"][0]["effective_config_digest"] == "sha256:deadbeef"

    def test_missing_digest_is_none_and_omitted_from_dict(self, tmp_path: Path) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        target = next(t for t in r.targets if t.target_id == LINUX)
        assert target.effective_config_digest is None
        assert "effective_config_digest" not in r.to_dict()["targets"][0]

    def test_unavailable_target_has_no_digest(self, tmp_path: Path) -> None:
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        target = next(t for t in r.targets if t.target_id == LINUX)
        assert target.effective_config_digest is None

    def test_scan_reports_nest_the_digest_under_diff(self, tmp_path: Path) -> None:
        """A `scan --against` report nests its whole baseline summary
        (`add_effective_config_digest`'s own write target) under `diff`,
        not the document root — the same document-shape distinction
        `_analysis_assurance_exit`/`_contract_coverage_exit` already
        account for (Codex review: reading only the root produced `None`
        for every scan target)."""
        _write_report(
            tmp_path,
            LINUX,
            "NO_CHANGE",
            scan_schema_version="1.8",
            exit_code=0,
            diff={
                "verdict": "NO_CHANGE",
                "effective_config_digest": "sha256:scandigest",
            },
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        target = next(t for t in r.targets if t.target_id == LINUX)
        assert target.effective_config_digest == "sha256:scandigest"


@pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
def test_digest_bearing_report_validates_against_the_schema(tmp_path: Path) -> None:
    from abicheck.schemas import load_aggregate_report_schema

    _write_report(
        tmp_path,
        LINUX,
        "COMPATIBLE",
        effective_config_digest="sha256:" + "ab" * 32,
    )
    d = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).to_dict()
    jsonschema.validate(d, load_aggregate_report_schema())
    assert d["targets"][0]["effective_config_digest"] == "sha256:" + "ab" * 32

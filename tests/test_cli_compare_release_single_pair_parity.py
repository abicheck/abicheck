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

"""Generalized regression closing the bug *class* behind PR #1016's P1 fix
and the Codex-found `sdk_vendor` `base_policy` follow-up -- not just the one
reported input each individually pinned. Writing this test is itself part
of that history: running it the first time (before `scope_public_headers`
was threaded through) caught a *third*, previously-unreported instance of
the same bug -- `surface.scope_to_public_surface`/
`.scope_to_public_surface_requested` silently read the stand-in's
`getattr(..., default)` fallback, ignoring `--scope-public-headers`/
`--no-scope-public-headers` entirely, on every release run including the
plain no-flags default.

Every instance is the same shape: a directory/package `compare`'s
release-level *summary* silently disagrees with what the identical
single-pair `compare` reports for the same configuration, because the
summary is computed by a separate stand-in
(`cli_compare_receipt._release_summary_effective_config_block`) rather than
inherited from the shared pipeline. Each fix so far was caught by a test
naming exactly one CLI flag against one specific fixture. This module
instead parametrizes the same parity assertion across every configuration
axis `effective_config_fields` actually draws from, so the next axis this
drifts on -- a future flag nobody has thought to pin yet -- fails here on
its own, rather than needing its own bug report and its own narrow test
first. `--depth` is deliberately not one of the axes below: verified (by
running this suite before writing the fix) that `effective_config_fields`
does not encode requested depth in any field at all, on either path, so a
depth-focused axis here would assert nothing.

Deliberately narrower than a full verdict/exit-code parity check: the
release path legitimately diverges from single-pair on those (library
removal promotion, budget/coverage axes that don't exist for one pair), so
comparing them here would encode a false expectation. `effective_config_fields`
is the one document this repository's own P1 fix guarantees is meant to be
identical -- see `cli_compare_receipt._release_summary_effective_config_block`'s
own docstring.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Remove a public function -- gives every scenario below a real,
    non-empty finding to compute a summary over."""
    old = AbiSnapshot(
        library=lib,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        from_headers=True,
    )
    new = AbiSnapshot(library=lib, version="2.0", functions=[], from_headers=True)
    return old, new


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _invoke(*args: str) -> tuple[int, str]:
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


# ── one factory per configuration axis ──────────────────────────────────
# Each returns the extra CLI args for that axis, given a scratch tmp_path to
# write any fixture file (a policy/suppression document) it needs.


def _axis_non_default_policy_base(tmp_path: Path) -> list[str]:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
    return ["--policy", str(policy_path)]


def _axis_policy_with_overrides(tmp_path: Path) -> list[str]:
    policy_path = tmp_path / "policy_overrides.yaml"
    policy_path.write_text(
        "base_policy: strict_abi\noverrides:\n  func_removed: risk\n",
        encoding="utf-8",
    )
    return ["--policy", str(policy_path)]


def _axis_suppress(tmp_path: Path) -> list[str]:
    suppress_path = tmp_path / "suppress.yaml"
    suppress_path.write_text(
        "version: 1\n"
        "suppressions:\n"
        "  - symbol: foo\n"
        "    reason: intentional removal, suppressed for this test\n",
        encoding="utf-8",
    )
    return ["--suppress", str(suppress_path)]


def _axis_severity_preset_strict(tmp_path: Path) -> list[str]:
    return ["--severity-preset", "strict"]


def _axis_no_scope_public_headers(tmp_path: Path) -> list[str]:
    return ["--no-scope-public-headers"]


_AXES: list[tuple[str, Callable[[Path], list[str]]]] = [
    ("non_default_policy_base", _axis_non_default_policy_base),
    ("policy_with_overrides", _axis_policy_with_overrides),
    ("suppress", _axis_suppress),
    ("severity_preset_strict", _axis_severity_preset_strict),
    ("no_scope_public_headers", _axis_no_scope_public_headers),
]


class TestReleaseSummaryEffectiveConfigNeverDivergesFromSinglePair:
    @pytest.mark.parametrize("axis_name,make_args", _AXES, ids=[a[0] for a in _AXES])
    def test_effective_config_fields_match(
        self,
        tmp_path: Path,
        axis_name: str,
        make_args: Callable[[Path], list[str]],
    ) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_snap, new_snap = _breaking_pair()
        old_path = _write_snap(old_dir / "libfoo.json", old_snap)
        new_path = _write_snap(new_dir / "libfoo.json", new_snap)

        extra_args = make_args(tmp_path)

        _, single_out = _invoke(
            "compare", str(old_path), str(new_path), *extra_args, "--format", "json"
        )
        single_fields = json.loads(single_out)["effective_config_fields"]

        _, release_out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            *extra_args,
            "--jobs",
            "1",
            "--format",
            "json",
        )
        release_fields = json.loads(release_out)["effective_config_fields"]

        # `gate.on_incomplete_scope` (ADR-065 D6) is the one release-only
        # axis: a scalar comparison's single pair is the whole scope, so it
        # records "" where a release records its resolved policy.
        assert single_fields.pop("gate.on_incomplete_scope") == ""
        assert release_fields.pop("gate.on_incomplete_scope") == "warn"
        assert release_fields == single_fields, (
            f"release-summary effective_config_fields diverged from the "
            f"identical single-pair compare on axis {axis_name!r}"
        )

    def test_axes_are_not_vacuous(self, tmp_path: Path) -> None:
        """Each axis above must actually change *something* relative to the
        no-flags baseline -- otherwise the parity assertion above would
        trivially pass by comparing two empty/default documents, proving
        nothing about that axis specifically."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_snap, new_snap = _breaking_pair()
        old_path = _write_snap(old_dir / "libfoo.json", old_snap)
        new_path = _write_snap(new_dir / "libfoo.json", new_snap)

        _, baseline_out = _invoke(
            "compare", str(old_path), str(new_path), "--format", "json"
        )
        baseline_fields = json.loads(baseline_out)["effective_config_fields"]

        for axis_name, make_args in _AXES:
            extra_args = make_args(tmp_path)
            _, out = _invoke(
                "compare", str(old_path), str(new_path), *extra_args, "--format", "json"
            )
            fields = json.loads(out)["effective_config_fields"]
            assert fields != baseline_fields, (
                f"axis {axis_name!r} produced no change in "
                f"effective_config_fields relative to the no-flags baseline "
                f"-- it isn't actually testing what it claims to"
            )

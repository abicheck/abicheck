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

"""New defect 3: ADR-068 §3 #23 documents ``.abicheck.yml``'s ``policy.
overrides`` as the replacement route for the retired ``--crosscheck
KEY=LEVEL`` flag -- but until this fix, a real ``.abicheck.yml`` carrying a
top-level ``policy:`` key failed outright with ``Error: unknown .abicheck.yml
key 'policy'``, so the documented route did not exist at all.

This covers: (1) the ``.abicheck.yml`` schema now accepts ``policy.
overrides``, rejecting a malformed one the same way every sibling block
does; (2) a real ``compare`` run picks it up and reclassifies findings the
same way the equivalent ``--policy <file>``'s ``overrides:`` block does;
(3) an explicit ``--policy <file>`` always wins over the project config for
a kind both state.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.buildsource.build_config import BuildConfig
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av")],
        from_headers=True,
    )
    return old, new


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    old, new = _breaking_pair()
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


class TestBuildConfigAcceptsPolicyBlock:
    def test_policy_overrides_accepted_and_round_trips(self) -> None:
        cfg = BuildConfig.from_dict({"policy": {"overrides": {"func_removed": "warn"}}})
        assert cfg.policy_overrides == {"func_removed": "warn"}
        assert cfg.to_dict()["policy"] == {"overrides": {"func_removed": "warn"}}

    def test_policy_block_wrong_type_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="policy.overrides"):
            BuildConfig.from_dict({"policy": {"overrides": "not-a-mapping"}})

    def test_policy_block_non_string_values_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="policy.overrides"):
            BuildConfig.from_dict({"policy": {"overrides": {"func_removed": 1}}})


class TestCompareHonorsProjectConfigPolicyOverrides:
    """The documented ADR-068 route: `.abicheck.yml`'s `policy.overrides`
    reclassifies findings the same way an equivalent `--policy <file>`'s
    `overrides:` block does."""

    def test_project_config_policy_overrides_changes_verdict_and_exit_code(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "policy:\n  overrides:\n    func_removed: ignore\n",
            encoding="utf-8",
        )

        # Baseline: no project config -> func_removed defaults to BREAKING
        # (exit 4) under strict_abi.
        baseline = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p)], catch_exceptions=False
        )
        assert baseline.exit_code == 4, baseline.output

        # With the project config's override in a directory that carries
        # .abicheck.yml, the run picks it up automatically (project-config
        # discovery walks up from the CWD) and the same comparison is now
        # compatible (exit 0).
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_explicit_policy_file_wins_over_project_config_for_same_kind(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        old_p, new_p = _write_pair(tmp_path)
        (tmp_path / ".abicheck.yml").write_text(
            "policy:\n  overrides:\n    func_removed: ignore\n",
            encoding="utf-8",
        )
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: risk\n",
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--policy",
                str(policy_path),
            ],
            catch_exceptions=False,
        )
        # func_removed: risk (COMPATIBLE_WITH_RISK) from the explicit
        # --policy file wins over the project config's `ignore` -- exit 0
        # under the legacy scheme (risk is not source_break/abi_break), but
        # the finding must still be present/classified as risk, not silently
        # dropped as `ignore` would have made it.
        assert result.exit_code == 0, result.output
        assert "func_removed" in result.output or "api_b" in result.output

    def test_no_policy_block_is_unaffected(self, tmp_path: Path, monkeypatch) -> None:
        old_p, new_p = _write_pair(tmp_path)
        (tmp_path / ".abicheck.yml").write_text("version: 1\n", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p)],
            catch_exceptions=False,
        )
        assert result.exit_code == 4, result.output

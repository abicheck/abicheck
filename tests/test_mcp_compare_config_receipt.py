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

"""ADR-049 Phase 5 — the MCP ``abi_compare`` tool's resolved configuration.

The sibling of ``tests/test_cli_compare_config_receipt.py``, one front end
over. What matters here is not that the numbers match the CLI's — they should
not, since this tool has a smaller input surface — but that the receipt is the
canonical resolver's and that it names only inputs ``abi_compare`` actually
has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from abicheck.mcp_server import abi_compare  # noqa: E402
from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402
from abicheck.serialization import snapshot_to_json  # noqa: E402


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _pair(tmp_path: Path) -> tuple[Path, Path]:
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
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _context(tmp_path: Path, **kwargs) -> dict:
    old_p, new_p = _pair(tmp_path)
    data = json.loads(
        abi_compare(str(old_p), str(new_p), contract_evaluation=True, **kwargs)
    )
    assert data["status"] == "ok", data
    return data["report"]["contract_context"]["evaluation_context"]


class TestResolvedFromRealArguments:
    def test_policy_is_an_api_request_not_a_cli_flag(self, tmp_path: Path):
        """D7 puts explicit CLI and explicit API in one tier but they are
        distinct layers, and this front end may only claim the latter."""
        ctx = _context(tmp_path, policy="sdk_vendor")
        assert ctx["resolved_config"]["policy"]["base"]["id"] == "sdk_vendor"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["layer"] in {"api_request", "legacy_alias"}
        assert prov["selected_by"][0]["option"] == "policy"

    def test_policy_file_names_the_file_and_its_digest(self, tmp_path: Path):
        policy = tmp_path / "p.yaml"
        policy.write_text("base_policy: sdk_vendor\n", encoding="utf-8")
        ctx = _context(tmp_path, policy_file=str(policy))

        assert ctx["resolved_config"]["policy"]["base"]["id"] == "sdk_vendor"
        prov = ctx["field_provenance"]["policy.base"]
        assert prov["path"] == str(policy)
        assert prov["sha256"]

    def test_suppression_file_is_identified_by_its_own_digest(self, tmp_path: Path):
        rules = tmp_path / "s.yaml"
        rules.write_text(
            "version: 1\nsuppressions:\n  - symbol: api_b\n    reason: intentional\n",
            encoding="utf-8",
        )
        ctx = _context(tmp_path, suppression_file=str(rules))

        prov = ctx["field_provenance"]["suppressions"]
        assert prov["path"] == str(rules)
        # From the single load the comparison itself used, so the digest
        # always describes the rules that ran.
        assert prov["sha256"] == ctx["resolved_config"]["suppressions"]["sha256"]

    def test_only_the_stated_severity_category_claims_the_caller(self, tmp_path: Path):
        """``abi_compare`` takes the four levels separately, so a call giving
        one must not record the other three as caller-supplied."""
        ctx = _context(tmp_path, severity_abi_breaking="warning")
        gate = ctx["resolved_config"]["gate"]
        assert gate["severity"]["abi_breaking"] == "warning"
        assert gate["exit_code_scheme"] == "severity"

        prov = ctx["field_provenance"]
        assert prov["gate.severity.abi_breaking"]["layer"] == "api_request"
        assert prov["gate.severity.addition"]["layer"] == "built_in_default"

    def test_an_unstated_scope_is_a_default_not_a_claim(self, tmp_path: Path):
        """This tool has no scope/contract parameter at all, so unlike
        ``CompareRequest.scope_public`` — whose dataclass default is still a
        caller's choice — there is nothing here for a caller to have chosen.
        """
        ctx = _context(tmp_path)
        assert ctx["field_provenance"]["contract.mode"]["layer"] == "built_in_default"

    def test_the_gate_reported_is_the_gate_the_call_was_scored_with(
        self, tmp_path: Path
    ):
        """Values from the run, provenance from the resolver — the same split
        the CLI receipt documents, checked here on the default path."""
        ctx = _context(tmp_path)
        gate = ctx["resolved_config"]["gate"]
        assert gate["exit_code_scheme"] == "legacy"
        assert ctx["field_provenance"]["gate.exit_code_scheme"]["layer"] == (
            "built_in_default"
        )


class TestCrossFrontEndEquivalence:
    def test_equivalent_cli_and_mcp_input_resolve_equally(self, tmp_path: Path):
        """Phase 1's gate, applied to the two live front ends: the same stated
        inputs must resolve to the same object, modulo only which front end
        stated them."""
        from abicheck.cli_compare_receipt import (
            COMPARE_CONFIG_PARAMS,
            resolve_cli_config,
        )
        from abicheck.compatibility_evaluation_frontend import (
            cross_front_end_differences,
        )
        from abicheck.mcp_compare_receipt import resolve_tool_config

        params = dict.fromkeys(COMPARE_CONFIG_PARAMS)
        params["policy"] = "sdk_vendor"
        params["severity_abi_breaking"] = "warning"
        cli = resolve_cli_config(
            params,
            typed={"policy"},
            project_cfg=None,
            project_path=None,
        )
        tool = resolve_tool_config(policy="sdk_vendor", severity_abi_breaking="warning")

        assert list(cross_front_end_differences(cli, tool)) == []

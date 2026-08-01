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

"""`abicheck.mcp_compare_receipt` on its own, without the `mcp` extra.

Its sibling, `tests/test_mcp_compare_config_receipt.py`, drives the module
through the real `abi_compare` tool -- the right test for "does the tool's
receipt say true things" but one that begins with
`pytest.importorskip("mcp")`, so in an environment without the optional
extra it contributes nothing. The module itself imports nothing from `mcp`
(it is a leaf over `compatibility_evaluation_frontend`/`contract_context`),
so its own behaviour is testable unconditionally, and this file does that --
including the two guards a tool-level test cannot reach at all: a call whose
comparison produced no persisted context, and the `"scoped"` exit-code
scheme, which `GateConfig` does not accept as a resolved value.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.contract_relevance_types import SelectorLayer
from abicheck.mcp_compare_receipt import record_resolved_config, resolve_tool_config
from abicheck.severity import SeverityConfig, SeverityLevel


class TestResolveToolConfig:
    def test_policy_is_read_as_stated(self):
        """The typed surface has no "unset" for `policy`, so a caller
        accepting the default still chose it -- same rule
        `compare_request_inputs` follows."""
        config = resolve_tool_config(policy="sdk_vendor")
        assert config.policy.base.id == "sdk_vendor"
        # `--policy`/`policy=` is D7's `legacy_alias` tier, not the explicit
        # one -- the same answer `compare`'s own receipt gives for the same
        # input, which is the point: one resolver, one classification.
        prov = config.provenance["policy.base"]
        assert prov.layer is SelectorLayer.LEGACY_ALIAS
        assert prov.selected_by[0].option == "policy"

    def test_an_absent_parameter_is_a_built_in_default(self):
        """This tool has no scope/contract parameter, so nothing here was
        chosen by a caller."""
        config = resolve_tool_config(policy="strict_abi")
        assert (
            config.provenance["contract.mode"].layer is SelectorLayer.BUILT_IN_DEFAULT
        )

    def test_only_the_stated_severity_category_claims_the_caller(self):
        config = resolve_tool_config(
            policy="strict_abi", severity_abi_breaking="warning"
        )
        assert config.gate.severity.abi_breaking == "warning"
        prov = config.provenance
        assert prov["gate.severity.abi_breaking"].layer is SelectorLayer.API_REQUEST
        assert prov["gate.severity.addition"].layer is SelectorLayer.BUILT_IN_DEFAULT

    def test_a_suppression_list_is_identified_by_the_digest_it_carried(self):
        """Built from the list the run really used, not a re-read of the
        path -- so the digest cannot describe content that did not score the
        run."""
        rules = SimpleNamespace(
            source_sha256="deadbeef",
            rule_identities=lambda: ("symbol:api_b",),
        )
        config = resolve_tool_config(
            policy="strict_abi", suppression=rules, suppression_path="/tmp/s.yaml"
        )
        assert config.suppressions is not None
        assert config.suppressions.sha256 == "deadbeef"
        assert config.provenance["suppressions"].path == "/tmp/s.yaml"

    def test_a_suppression_list_with_no_path_is_still_accepted(self):
        """An API caller can hand over rules it built in memory; there is no
        path to name, and that must not be mistaken for "no suppression"."""
        rules = SimpleNamespace(
            source_sha256="cafe1234", rule_identities=lambda: ("symbol:api_b",)
        )
        config = resolve_tool_config(policy="strict_abi", suppression=rules)
        assert config.suppressions is not None
        assert config.provenance["suppressions"].path is None


class TestRecordResolvedConfig:
    def test_a_call_with_no_persisted_context_is_a_no_op(self):
        """`contract_evaluation` off means `checker.compare` built no
        context; there is nothing to install onto and nothing to fail on."""
        result = SimpleNamespace(contract_context=None)
        record_resolved_config(result, exit_code_scheme="legacy")
        assert result.contract_context is None

    def test_a_non_context_attribute_is_left_alone(self):
        # Duck-typed callers exist; anything that is not a real persisted
        # context must be passed over rather than overwritten.
        result = SimpleNamespace(contract_context="not a context")
        record_resolved_config(result, exit_code_scheme="legacy")
        assert result.contract_context == "not a context"

    def _result_with_context(self):
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot, Function, Visibility

        def fn(name: str, mangled: str) -> Function:
            return Function(
                name=name,
                mangled=mangled,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )

        old = AbiSnapshot(
            library="lib.so.1",
            version="1.0",
            functions=[fn("a", "_Z1av"), fn("b", "_Z1bv")],
            from_headers=True,
        )
        new = AbiSnapshot(
            library="lib.so.1",
            version="2.0",
            functions=[fn("a", "_Z1av")],
            from_headers=True,
        )
        return compare(old, new, contract_evaluation=True)

    def test_the_installed_gate_carries_the_run_s_own_values(self):
        result = self._result_with_context()
        record_resolved_config(
            result,
            exit_code_scheme="severity",
            severity_config=SeverityConfig(abi_breaking=SeverityLevel.WARNING),
            severity={"abi_breaking": "warning"},
        )
        gate = result.contract_context.evaluation_context.resolved_config.gate
        assert gate.exit_code_scheme == "severity"
        assert gate.severity.abi_breaking is SeverityLevel.WARNING

    def test_a_scoped_scheme_records_the_scheme_it_resolved_from(self):
        """A `--used-by`/`--required-symbol` scope reports `"scoped"`, which
        is not one of the two values `GateConfig` accepts -- the underlying
        scheme is what gets recorded, taken from the result that knows it."""
        result = self._result_with_context()
        result.scoped_exit_code_scheme = "severity"
        record_resolved_config(result, exit_code_scheme="scoped")
        gate = result.contract_context.evaluation_context.resolved_config.gate
        assert gate.exit_code_scheme == "severity"

    def test_a_scoped_scheme_falls_back_to_legacy_when_none_was_resolved(self):
        result = self._result_with_context()
        record_resolved_config(result, exit_code_scheme="scoped")
        gate = result.contract_context.evaluation_context.resolved_config.gate
        assert gate.exit_code_scheme == "legacy"

    def test_a_resolver_error_is_raised_for_the_caller_to_map(self):
        """Mapping a D7/D8 failure to a tool error is the caller's job, so
        this module must not swallow one."""
        result = self._result_with_context()
        with pytest.raises(Exception):
            record_resolved_config(
                result, exit_code_scheme="legacy", policy="no_such_policy_base"
            )


class TestPolicyFileOverride:
    """`abi_compare` validates `policy` only when no `policy_file` is given
    ("policy_file takes precedence over the base policy name"), so an unknown
    `policy` alongside a valid file is an accepted request whose comparison
    completes under the file's own base. The receipt must not then reject
    what the tool accepted (Codex review).
    """

    def _policy_file(self):
        from abicheck.policy_file import PolicyFile

        return PolicyFile(base_policy="sdk_vendor")

    def test_an_overridden_unknown_policy_does_not_fail_the_receipt(self):
        config = resolve_tool_config(
            policy="not_a_real_policy", policy_file=self._policy_file()
        )
        # The file's base is what ran, and what the receipt names.
        assert config.policy.base.id == "sdk_vendor"

    def test_a_valid_policy_alongside_a_file_still_resolves_to_the_file(self):
        config = resolve_tool_config(
            policy="strict_abi", policy_file=self._policy_file()
        )
        assert config.policy.base.id == "sdk_vendor"

    def test_an_unknown_policy_with_no_file_still_fails_loudly(self):
        # Unreachable through the tool (its own validation returns an error
        # response first), but the receipt must not paper over it either.
        with pytest.raises(ValueError, match="unknown base policy"):
            resolve_tool_config(policy="not_a_real_policy")

    def test_the_completed_comparison_survives_an_overridden_policy(self):
        """The actual regression: a finished comparison must not be turned
        into an error by its own receipt."""
        result = TestRecordResolvedConfig()._result_with_context()
        record_resolved_config(
            result,
            exit_code_scheme="legacy",
            policy="not_a_real_policy",
            policy_file=self._policy_file(),
        )
        resolved = result.contract_context.evaluation_context.resolved_config
        assert resolved.policy.base.id == "sdk_vendor"


class TestSeveritySelectorsNameApiFields:
    """`abi_compare` takes `severity_preset`/`severity_*` as tool arguments,
    never as CLI flags -- so a receipt naming `--severity-preset` describes an
    input the caller could not have passed and a replay could not set (Codex
    review). The resolver's own `spell()` helper exists for this; the severity
    candidates were the two sites that bypassed it.
    """

    def test_a_stated_preset_names_the_tool_argument(self):
        from abicheck.compatibility_evaluation_frontend import unstatable_selectors

        config = resolve_tool_config(policy="strict_abi", severity_preset="strict")
        hops = [hop.option for hop in config.provenance["gate.preset"].selected_by]
        assert hops == ["severity_preset"]
        assert unstatable_selectors(config) == []

    @pytest.mark.parametrize(
        "category", ["abi_breaking", "potential_breaking", "quality_issues", "addition"]
    )
    def test_a_stated_category_override_names_the_tool_argument(self, category):
        from abicheck.compatibility_evaluation_frontend import unstatable_selectors

        config = resolve_tool_config(
            policy="strict_abi", **{f"severity_{category}": "error"}
        )
        prov = config.provenance[f"gate.severity.{category}"]
        assert [hop.option for hop in prov.selected_by] == [f"severity_{category}"]
        assert unstatable_selectors(config) == []

    def test_the_compare_cli_still_names_its_own_flags(self):
        """One-directional: the CLI receipt must keep naming a real flag."""
        from abicheck.compatibility_evaluation_frontend import (
            ExplicitCompatibilityInputs,
            FrontEnd,
            resolve_compatibility_evaluation_config,
        )

        config = resolve_compatibility_evaluation_config(
            front_end=FrontEnd.CLI,
            explicit=ExplicitCompatibilityInputs(
                severity_preset="strict", severity_addition="error"
            ),
        )
        preset = config.provenance["gate.preset"].selected_by
        addition = config.provenance["gate.severity.addition"].selected_by
        assert [hop.option for hop in preset] == ["--severity-preset"]
        assert [hop.option for hop in addition] == ["--severity-addition"]

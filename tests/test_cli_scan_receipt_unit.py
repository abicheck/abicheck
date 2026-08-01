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

"""`abicheck.cli_scan_receipt`'s own contract, below the command.

`tests/test_scan_compare_parity.py` drives this module end to end through
`scan --against` and the Python API — the assertions that matter. These
cover the guards those paths never reach: a caller forwarding a partial
parameter mapping, a run with no persisted context, and the gate-blanking
rule in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.cli_scan_receipt import (
    SCAN_CONFIG_PARAMS,
    context_block,
    record_resolved_config,
    resolve_scan_config,
)


def _params(**overrides):
    base = dict.fromkeys(SCAN_CONFIG_PARAMS)
    base["public_symbols"] = ()
    base.update(overrides)
    return base


class TestParamsContract:
    def test_a_partial_mapping_fails_loudly(self):
        """A dropped key would otherwise resolve as "not stated", silently
        changing what the receipt claims — the same guard
        `cli_compare_receipt` carries."""
        partial = _params()
        del partial["contract_mode"]
        with pytest.raises(KeyError, match="contract_mode"):
            resolve_scan_config(partial, typed=set())

    def test_the_full_mapping_resolves(self):
        config = resolve_scan_config(_params(), typed=set())
        assert config.contract.mode is not None


class TestGateBlanking:
    def test_a_project_gate_is_not_claimed_by_a_scan(self, tmp_path):
        """A scan's exit follows its verdict and never reads these keys, so
        recording them would make the receipt describe a gate the run did
        not use."""
        from abicheck.buildsource.inline import load_build_config

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "severity:\n  preset: info-only\nexit_code_scheme: severity\n",
            encoding="utf-8",
        )
        config = resolve_scan_config(
            _params(),
            typed=set(),
            project_cfg=load_build_config(cfg),
            project_path=cfg,
        )
        gate = config.gate
        assert gate.exit_code_scheme == "legacy"
        prov = config.provenance["gate.exit_code_scheme"]
        assert prov.layer.value == "built_in_default"

    def test_a_project_scope_setting_is_still_honored(self, tmp_path):
        """Only the *gate* fields are blanked. A project's scope choice is
        real configuration the scan does apply, so it must survive."""
        from abicheck.buildsource.inline import load_build_config

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public: false\n", encoding="utf-8")
        config = resolve_scan_config(
            _params(),
            typed=set(),
            project_cfg=load_build_config(cfg),
            project_path=cfg,
        )
        assert config.provenance["contract.mode"].layer.value == "project_config"


class TestInstallation:
    def test_installing_on_a_result_without_a_context_is_a_no_op(self):
        result = SimpleNamespace(contract_context=None)
        record_resolved_config(result, resolve_scan_config(_params(), typed=set()))
        assert result.contract_context is None

    def test_a_non_context_attribute_is_left_alone(self):
        result = SimpleNamespace(contract_context="not a context")
        record_resolved_config(result, resolve_scan_config(_params(), typed=set()))
        assert result.contract_context == "not a context"

    def test_no_block_is_serialized_without_a_context(self):
        assert context_block(SimpleNamespace(contract_context=None)) is None
        assert context_block(SimpleNamespace()) is None

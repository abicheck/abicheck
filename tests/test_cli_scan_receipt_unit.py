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

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.cli_scan_receipt import (
    SCAN_CONFIG_PARAMS,
    context_block,
    record_resolved_config,
    resolve_scan_config,
)
from abicheck.contract_relevance_types import ContractMode


def _params(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict.fromkeys(SCAN_CONFIG_PARAMS)
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
    def test_a_project_gate_is_not_claimed_by_a_scan(self, tmp_path: Path) -> None:
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

    def test_a_project_scope_setting_is_still_honored(self, tmp_path: Path) -> None:
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
        # Both halves: a regression could keep the provenance while resolving
        # the default mode (CodeRabbit review).
        assert config.provenance["contract.mode"].layer.value == "project_config"
        assert config.contract.mode is ContractMode.ALL


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


class TestNoProjectConfig:
    def test_blanking_a_missing_project_config_yields_nothing(self) -> None:
        """The common case: a scan with no `.abicheck.yml` has no project
        inputs to blank, and must not fabricate an empty one."""
        from abicheck.cli_scan_receipt import _without_gate_settings

        assert _without_gate_settings(None) is None


class TestResolverErrorsAreUsageErrors:
    """`scan`'s own mapping of a resolver failure to exit 64.

    The API half of this pair was tested when it was added; the CLI half
    never was, which is the asymmetry the two front ends' cross-referencing
    comments claim to have closed (Codecov flagged both lines as uncovered
    from the start).

    Patched rather than driven by a real conflicting invocation, matching
    `test_cli_compare_config_receipt`'s equivalent test and for the same
    reason: `scan` exposes no flag pair that resolves to a same-tier
    conflict today (`--policy`/`--policy-file` is D7's documented
    compatibility exception, and `--public-symbol`/`--public-symbols-list`
    union rather than conflict). So this pins the handler -- that the error
    becomes exit 64 with its message intact rather than a traceback -- not
    that some input reaches it.
    """

    def _pair(self, tmp_path: Path) -> tuple[Path, Path]:
        from abicheck.model import AbiSnapshot
        from abicheck.serialization import snapshot_to_json

        paths = []
        for name in ("old", "new"):
            p = tmp_path / f"{name}.abi.json"
            p.write_text(
                snapshot_to_json(AbiSnapshot(library="libfoo.so", version="1")),
                encoding="utf-8",
            )
            paths.append(p)
        return paths[0], paths[1]

    def _errors(self) -> list[tuple[Exception, str]]:
        """One real instance of each type the handler names, built the way
        the resolver builds it -- `PackConflictError` composes its message
        from real pack identities, so a bare string would not construct."""
        from abicheck.compatibility_evaluation_config import ImmutableIdentity
        from abicheck.compatibility_evaluation_resolver import (
            FieldResolutionError,
            PackConflictError,
        )
        from abicheck.errors import PackManifestError

        def _identity(name: str) -> ImmutableIdentity:
            return ImmutableIdentity(id=name, version=1, sha256="0" * 64)

        return [
            (
                FieldResolutionError("contract.mode: two explicit values"),
                "two explicit values",
            ),
            (
                PackConflictError(
                    "gate.preset",
                    [(_identity("strict"), "a"), (_identity("lax"), "b")],
                ),
                "gate.preset",
            ),
            (PackManifestError("pack manifest is malformed"), "malformed"),
        ]

    @pytest.mark.parametrize("index", [0, 1, 2])
    def test_each_resolver_error_becomes_exit_64(
        self, tmp_path: Path, monkeypatch, index: int
    ) -> None:
        from click.testing import CliRunner

        import abicheck.cli_scan_receipt as receipt
        from abicheck.cli import main

        error, message = self._errors()[index]

        def _boom(*args, **kwargs):
            raise error

        monkeypatch.setattr(receipt, "resolve_scan_config", _boom)
        old_p, new_p = self._pair(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "scan",
                str(new_p),
                "--against",
                str(old_p),
                "--contract-evaluation",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 64, result.output
        assert message in result.output

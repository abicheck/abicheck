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

"""One Semantic Pipeline plan, sub-phase 4B: the native ``compare`` CLI stops
independently re-deriving ``contract.mode`` once it already resolved an
ADR-049 D7 ``CompatibilityEvaluationConfig`` for this invocation.

Before this migration, ``cli_compare_helpers.run_compare`` always passed the
raw, pre-resolution ``contract_mode`` local (``None`` whenever no explicit
``--contract`` was typed) into ``compare_snapshots``/``checker.compare``, so
``contract_pipeline.build_contract_stage`` re-derived the identical domain a
second time via ``compatibility_evaluation_wiring.resolve_legacy_contract_mode``
-- that function's own documented fallback for a caller with no D7 config to
read at all. The two computations agree for every input reachable today
(``contract.mode`` is deliberately not pack-routable, and the legacy-alias
boolean both sides read is already CLI/``.abicheck.yml``-merged by the time
either sees it -- see ``CONTRACT_PACK_FIELD_ROUTES``'s own docstring), so
this is not a behavior fix for any real run; it closes the redundant second
resolution so the two cannot silently start disagreeing the next time either
side's own precedence changes.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from click.testing import CliRunner

import abicheck.cli_compare_helpers as cch
from abicheck.cli import main
from abicheck.contract_relevance_types import ContractMode
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
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


class TestResolvedContractModeWiring:
    def test_compare_snapshots_receives_the_resolved_evaluation_configs_mode(
        self, tmp_path, monkeypatch
    ):
        """``checker.compare`` must be handed ``evaluation_config``'s own
        resolved ``contract.mode`` -- not the raw, pre-resolution local --
        so a caller holding the already-resolved config never has that
        answer silently second-guessed one layer down.
        ``_resolve_evaluation_config`` is stubbed to return a config whose
        ``contract.mode`` deliberately disagrees with the raw ``--contract``
        value this invocation types, so this fails immediately if a future
        edit reverts to threading the stale local through instead.
        """
        old_p, new_p = _write_pair(tmp_path)
        real_resolve = cch._resolve_evaluation_config

        def _diverging_resolve(*args, **kwargs):
            evaluation_config, pf, resolved_cfg = real_resolve(*args, **kwargs)
            assert evaluation_config is not None
            assert evaluation_config.contract.mode == ContractMode.PUBLIC
            diverged = replace(
                evaluation_config,
                contract=replace(evaluation_config.contract, mode=ContractMode.EXPORTS),
            )
            return diverged, pf, resolved_cfg

        monkeypatch.setattr(cch, "_resolve_evaluation_config", _diverging_resolve)

        captured: dict[str, object] = {}
        from abicheck.service import compare_snapshots as _real_compare_snapshots

        def _capturing_compare_snapshots(*args, **kwargs):
            captured["contract_mode"] = kwargs.get("contract_mode")
            return _real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _capturing_compare_snapshots
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--contract", "public"],
        )
        # exit 1: floored by the orthogonal contract-coverage axis, since
        # `--contract exports`'s evidence (an export table) is unresolvable
        # against a bare JSON snapshot -- irrelevant to what this test pins.
        assert result.exit_code in (0, 1, 2, 4), result.output
        assert captured["contract_mode"] == ContractMode.EXPORTS

    def test_explicit_contract_flag_is_unaffected(self, tmp_path, monkeypatch):
        """An explicit ``--contract`` value already reaches
        ``build_contract_stage`` as a non-``None`` local, which takes the
        ``coerce_contract_mode`` branch, not the legacy-alias fallback --
        unaffected by this migration either way. Pinned so a future change
        can't silently start overriding an explicit flag with
        ``evaluation_config`` instead of merely converging the *redundant*
        no-explicit-flag path with it.
        """
        old_p, new_p = _write_pair(tmp_path)

        captured: dict[str, object] = {}
        from abicheck.service import compare_snapshots as _real_compare_snapshots

        def _capturing_compare_snapshots(*args, **kwargs):
            captured["contract_mode"] = kwargs.get("contract_mode")
            return _real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _capturing_compare_snapshots
        )

        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--contract", "exports"],
        )
        assert result.exit_code in (0, 1, 2, 4), result.output
        assert captured["contract_mode"] == ContractMode.EXPORTS

    def test_pack_only_run_with_no_contract_flag_stays_none(
        self, tmp_path, monkeypatch
    ):
        """Regression (full-suite run, fresh evidence): a ``--pack``-only run
        with no ``--contract`` still resolves a non-``None``
        ``evaluation_config`` (``resolve_and_apply``'s early-return only skips
        when *neither* ``contract_evaluation`` nor a pack applies), and
        ``ContractConfig.mode`` always carries a concrete default -- so
        naively threading ``evaluation_config.contract.mode`` through
        unconditionally passed a non-``None`` ``contract_mode`` into
        ``compare_snapshots`` while ``contract_evaluation`` stayed ``False``,
        tripping its own "contract_mode requires contract_evaluation"
        usage-error guard for every such run. Must stay ``contract_mode=None``
        (and exit 0), the same as before this migration.
        """
        old_p, new_p = _write_pair(tmp_path)
        pack_dir = tmp_path / "pack"
        pack_dir.mkdir()
        pack_path = pack_dir / "pack.yml"
        pack_path.write_text(
            "id: p\nversion: 1\nkind: policy\nassignments:\n  func_removed: ignore\n",
            encoding="utf-8",
        )

        captured: dict[str, object] = {}
        from abicheck.service import compare_snapshots as _real_compare_snapshots

        def _capturing_compare_snapshots(*args, **kwargs):
            captured["contract_mode"] = kwargs.get("contract_mode")
            captured["contract_evaluation"] = kwargs.get("contract_evaluation")
            return _real_compare_snapshots(*args, **kwargs)

        monkeypatch.setattr(
            "abicheck.service.compare_snapshots", _capturing_compare_snapshots
        )

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_p),
                str(new_p),
                "--pack",
                str(pack_path),
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0, result.output
        assert captured["contract_evaluation"] is False
        assert captured["contract_mode"] is None

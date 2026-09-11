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

"""ADR-068 D5: `.abicheck.yml`'s embedded `deployment:` block strictness.

Split out of `test_config_rebalance.py` (architecture/debt.yaml's
`no_growth` test-size cap) once that file's own CLI-surface-shrink content
plus this branch's `--env-matrix` retirement coverage and origin/main's
independent `--build-target` retirement coverage crossed 1200 lines on
merge. This file owns exactly the `EnvironmentMatrix.from_dict(...,
strict=...)` contract: lenient (the default, `strict=False`) preserves that
method's original forward-compat behavior for a direct typed-API/
`--env-matrix`-era caller (`workflows.input_resolution.load_env_matrix` /
`from_yaml`) -- an unknown key is logged and ignored, never raised.
`strict=True` (used only by `.abicheck.yml`'s embedded `deployment:` block,
`buildsource.build_config.BuildConfig`) raises instead, matching every
other `.abicheck.yml` block's hard-error-on-unknown-key contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main


class TestEnvironmentMatrixStrictMode:
    """Codex review finding 2: ``EnvironmentMatrix.from_dict(..., strict=...)``.

    Lenient (the default, ``strict=False``) preserves this method's
    original forward-compat behavior for a direct typed-API/``--env-
    matrix``-era caller (``workflows.input_resolution.load_env_matrix`` /
    ``from_yaml``) -- an unknown key is logged and ignored, never raised.
    ``strict=True`` (used only by ``.abicheck.yml``'s embedded
    ``deployment:`` block, ``buildsource.build_config.BuildConfig``) raises
    instead, matching every other ``.abicheck.yml`` block's hard-error-on-
    unknown-key contract.
    """

    def test_lenient_default_warns_not_raises_on_unknown_top_level_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        with caplog.at_level("WARNING"):
            m = EnvironmentMatrix.from_dict({"runtime_floor": {"GLIBC": "2.28"}})
        assert m.runtime_floors == {}
        assert any("unknown" in r.message.lower() for r in caplog.records)

    def test_lenient_default_warns_not_raises_on_unknown_nested_sycl_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        with caplog.at_level("WARNING"):
            m = EnvironmentMatrix.from_dict({"sycl": {"backend": ["level_zero"]}})
        assert m.sycl.backends == ()
        assert any("unknown" in r.message.lower() for r in caplog.records)

    def test_strict_raises_on_unknown_top_level_key(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        with pytest.raises(ValueError, match="unknown key"):
            EnvironmentMatrix.from_dict(
                {"runtime_floor": {"GLIBC": "2.28"}}, strict=True
            )

    def test_strict_raises_on_unknown_nested_sycl_key(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        with pytest.raises(ValueError, match="unknown key"):
            EnvironmentMatrix.from_dict(
                {"sycl": {"backend": ["level_zero"]}}, strict=True
            )

    def test_strict_raises_on_unknown_nested_cuda_key(self) -> None:
        from abicheck.environment_matrix import EnvironmentMatrix

        with pytest.raises(ValueError, match="unknown key"):
            EnvironmentMatrix.from_dict(
                {"cuda": {"gpu_architecture": ["sm_80"]}}, strict=True
            )

    def test_strict_accepts_every_known_key(self) -> None:
        # Strict mode must not reject anything from_dict already parses --
        # only genuinely unknown keys.
        from abicheck.environment_matrix import EnvironmentMatrix

        m = EnvironmentMatrix.from_dict(
            {
                "compilers": ["gcc-13"],
                "abi_version": "18",
                "libstdcxx_dual_abi": "cxx11",
                "runtime_floors": {"GLIBC": "2.28"},
                "sycl": {
                    "implementation": "dpcpp",
                    "backends": ["level_zero"],
                    "min_pi_version": "12",
                },
                "cuda": {
                    "gpu_architectures": ["sm_80"],
                    "driver_range": ["525.0", "580.0"],
                    "toolkit_version": "12.4",
                    "require_ptx": True,
                },
                "target_os": "linux",
                "target_arch": "x86_64",
            },
            strict=True,
        )
        assert m.runtime_floors == {"GLIBC": "2.28"}
        assert m.sycl.implementation == "dpcpp"
        assert m.cuda.gpu_architectures == ("sm_80",)

    def test_end_to_end_deployment_typo_is_hard_config_error(
        self, tmp_path: Path
    ) -> None:
        """A `.abicheck.yml` typo (`runtime_floor` for `runtime_floors`)
        must be a loud, exit-64 usage error -- not a silent no-op that
        disables the whole runtime-floor check and turns what should be a
        BREAKING verdict into a passing RISK."""
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            'deployment:\n  runtime_floor:\n    GLIBC: "2.28"\n', encoding="utf-8"
        )
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        result = CliRunner().invoke(
            main, ["compare", str(old_dir), str(new_dir), "--config", str(cfg)]
        )
        assert result.exit_code == 64, result.output

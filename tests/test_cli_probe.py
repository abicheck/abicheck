# Copyright 2026 Nikolay Petrov
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

"""Tests for the shipped oneDPL probe manifest.

The `probe run`/`probe compare` CLI commands (`cli_probe.py`) were deleted in
the pre-1.0 CLI reset (ADR-043) — `probe_harness.py`/`diff_build_config.py`
are unchanged and still power `compare --probe-matrix-old`/
`--probe-matrix-new` internally (see `abicheck.cli._load_probe_matrix_changes`).
Their old CLI-level test coverage here was either:
  - pure CLI-command orchestration (incomplete-matrix rejection, confidence
    marking, `--out`/stderr summary rendering) with no surviving library
    entry point to redirect to (removed with the command), or
  - already redundantly covered at the library level: `diff_matrix()` /
    `detect_cxx_standard_floor_raised()` / `detect_behavioural_default_changed()`
    in `tests/test_diff_build_config.py`, and the compiler-name/manifest
    command-execution guard (`load_probe_spec`/`_validate_compiler_name`,
    including the exact `/bin/sh` rejection case) in
    `tests/test_probe_harness.py`.
So nothing here needed rewriting — only the shipped-manifest parse check
below (a plain function, never went through the CLI) survives.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Shipped oneDPL manifest parses
# ---------------------------------------------------------------------------


def test_onedpl_example_spec_parses() -> None:
    from abicheck.probe_harness import load_probe_spec

    spec_path = (
        Path(__file__).resolve().parent.parent / "examples" / "probes" / "onedpl.yaml"
    )
    spec = load_probe_spec(spec_path)
    assert spec.name == "onedpl"
    assert len(spec.configurations) == 3
    assert len(spec.probes) == 2
    assert spec.defaults["execution_policy"] == "par"
    # -std=c++NN parsing populated the floor for each configuration.
    assert {c.cxx_std for c in spec.configurations} == {17, 20}

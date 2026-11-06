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

"""Regression: a build pack's L5 graph no longer displaces the header closure.

Formerly the known-gap canary for "A `--build-info` dump's L5 graph displaces
the header graph" (closed 2026-11-05, see the archive page). On
case187/188/189/191 both the ``debug-headers`` lane and the ``build-source``
lane (build packs embedded and passed out of band) report
``public_api_internal_dependency_added``. The debug-headers half is the
control proving the kind is detectable on the same sources.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="pins the gap as measured on Linux/ELF",
    ),
]

_ROOT = Path(__file__).resolve().parents[1]
_KIND = "public_api_internal_dependency_added"
_CASES = (
    "case187_public_struct_private_field_type",
    "case188_public_class_private_base_class",
    "case189_public_function_private_parameter_type",
    "case191_header_only_graph_field_type",
)


def _lane_kinds(variant: str) -> dict[str, set[str]]:
    for tool in ("castxml", "gcc", "g++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not installed")
    env = dict(
        os.environ,
        ABICHECK_AST_FRONTEND="castxml",
        ABICHECK_TRUSTED_SOURCE_SMOKE_RUN="1",
        PYTHONPATH=str(_ROOT),
    )
    proc = subprocess.run(
        [
            sys.executable,
            "tests/validate_examples.py",
            *_CASES,
            "--toolchain",
            "gcc",
            "--artifact-variant",
            variant,
            "--json",
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.stdout.strip(), (
        f"validate_examples produced no JSON (rc={proc.returncode}):\n{proc.stderr[-4000:]}"
    )
    rows = json.loads(proc.stdout)["results"]
    kinds = {
        row["name"]: set(row.get("actual_kinds") or ())
        for row in rows
        if row["status"] in {"PASS", "FAIL"}
    }
    assert set(kinds) == set(_CASES), (
        f"{variant}: cases not compared: {sorted(set(_CASES) - set(kinds))}\n{proc.stderr[-4000:]}"
    )
    return kinds


def test_build_source_lane_reports_public_internal_dependency_like_headers() -> None:
    headers = _lane_kinds("debug-headers")
    build = _lane_kinds("build-source")
    assert {case for case, kinds in headers.items() if _KIND in kinds} == set(_CASES)
    assert {case for case, kinds in build.items() if _KIND in kinds} == set(_CASES)

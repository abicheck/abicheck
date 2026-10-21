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

"""Shared fixtures for the ``test_analysis_assurance_*`` modules.

Split out of the former single ``test_analysis_assurance.py`` so each axis
module builds its pairs and CLI runs the same way without copying them.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.analysis_assurance import analysis_assurance_exit_contribution
from abicheck.serialization import snapshot_to_json


def fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def write_snapshot_pair(
    tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot
) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def header_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A real, header-scoped, unchanged pair -- resolves a clean public
    surface, so ``scope_resolved`` is True and ``status`` should read
    ``"complete"`` with no gating flag involved.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def elf_only_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """No headers, no build evidence -- the minimal invocation shape.

    ``elf_only_mode=True`` is what keeps ``_detect_evidence_tiers`` from
    falling back to "a declaration-only snapshot is header-level evidence"
    (its own documented in-memory/unit-test carve-out) -- without it this
    fixture would misleadingly read as HEADER_AWARE.
    """
    fns = [fn("pub_a", "_Z5pub_av")]
    common = {"library": "libfoo.so.1", "elf_only_mode": True}
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[fn("pub_a", "_Z5pub_av"), fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[fn("pub_a", "_Z5pub_av")], **common),
    )


#: rulings.py followup: --require-complete-analysis is retired --
#: assurance.require_complete: true in .abicheck.yml is its only source.
#: Call sites below still spell the literal flag (asserting behavior, not
#: spelling); assurance_config_args/compare_cli translate it in one place.
REQUIRE_COMPLETE_ANALYSIS_FLAG = "--require-complete-analysis"


def assurance_config_args(tmp_path: Path, *, scoped: bool = True) -> list[str]:
    """``--config`` for ``assurance.require_complete: true``; *scoped=False*
    adds ``scope.public: false``, the config spelling of the retired
    ``--no-scope-public-headers`` (one-comparison-product Phase 9b) -- one
    document, since ``--config`` names exactly one."""
    name = ".abicheck-assurance.yml" if scoped else ".abicheck-assurance-noscope.yml"
    cfg_path = tmp_path / name
    if not cfg_path.exists():
        text = "assurance:\n  require_complete: true\n"
        if not scoped:
            text += "scope:\n  public: false\n"
        cfg_path.write_text(text, encoding="utf-8")
    return ["--config", str(cfg_path)]


def compare_cli(tmp_path: Path, pair, *extra: str, scoped: bool = True):
    old_p, new_p = write_snapshot_pair(tmp_path, *pair)
    extra_list = list(extra)
    if REQUIRE_COMPLETE_ANALYSIS_FLAG in extra_list:
        extra_list.remove(REQUIRE_COMPLETE_ANALYSIS_FLAG)
        extra_list.extend(assurance_config_args(tmp_path, scoped=scoped))
    return CliRunner().invoke(main, ["compare", str(old_p), str(new_p), *extra_list])


def folded(base: int, result: DiffResult) -> int:
    """The exit a run with compatibility exit *base* ends with under
    ``assurance.require_complete``, through the one fold every command uses."""
    from abicheck.policy.exit_decision import resolve_exit_decision

    return resolve_exit_decision(
        compatibility_contribution=base,
        analysis_assurance_contribution=analysis_assurance_exit_contribution(
            result, require_complete=True
        ),
    ).code

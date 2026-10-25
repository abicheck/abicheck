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

"""Adaptive vs stated ``public`` over the catalog corpus.

Most of the suite runs the evidence-adaptive default, whose ``all`` fallback
(``unresolved_default_fallback_applies``) re-scores every finding the
selected domain leaves ``UNKNOWN_UNRESOLVED``. A stated ``--contract public``
keeps those findings unresolved, and each one is an unsuppressible
``contract_coverage_failures`` record and exit 1. That is right for a
declaration the headers could not place; it is wrong for a finding whose
subject is not a declaration at all -- a toolchain flag, an integer model,
an import edge -- which no header can ever place. The SVS field report
(#1751) was that second case, invisible to an adaptive-mode suite.

For each catalog case that compiles on this host, both sides are dumped once
through the CLI and compared twice: ``--contract auto`` and ``--contract
public``. A finding ``UNKNOWN_UNRESOLVED`` only under the stated mode whose
kind's subject is not a declaration is an offender.

Subject oracle: the kind's declared ``contract_subject`` in the change
catalog (#1770). Only a ``declaration`` names an entity a contract domain can
place; every other subject must be ``NOT_APPLICABLE`` in every mode.

:data:`KNOWN_OFFENDERS` is shrink-only: each case must produce exactly its
listed offenders, so a fixed kind fails here until its entry is deleted, and
a new offender fails until it is fixed. Write-up:
``docs/contribute/known-gaps/toolchain-runtime-rebuild-fails-stated-contract-coverage.md``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import tests.test_example_autodiscovery as corpus
from abicheck.change_registry import REGISTRY
from abicheck.cli import main
from abicheck.model.change_catalog.dimensions import ContractSubject

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="offender list observed on Linux ELF (gcc 13, castxml 0.7)",
    ),
]

_DECLARATION_KINDS = REGISTRY.contract_subject_kinds(ContractSubject.DECLARATION)


def _is_declaration_subject(kind: str) -> bool:
    return kind in _DECLARATION_KINDS


#: Shrink-only: case -> kinds that are UNKNOWN_UNRESOLVED only under a stated
#: ``public`` contract although their subject is not a declaration.
KNOWN_OFFENDERS: dict[str, frozenset[str]] = {
    # Emptied by #1770 (per-kind contract_subject): cases 71/103/104/112/178/180
    # were listed here. Add an entry only with a known-gaps write-up.
}


def _corpus_cases() -> list[str]:
    return sorted(
        case
        for case, expected in corpus._ALL_CASES
        if expected is not None
        and case not in corpus.BUILD_INFO_CASES
        and case not in corpus.SOURCE_SMOKE_CASES
    )


def _dump(lib: Path, header: Path | None, out: Path) -> None:
    args = ["dump", str(lib), "-o", str(out)]
    for h in corpus._header_list(header):
        args += ["-H", str(h)]
    result = CliRunner().invoke(main, args)
    assert out.is_file(), result.output


def _unresolved(old: Path, new: Path, mode: str, report: Path) -> set[str]:
    result = CliRunner().invoke(
        main,
        ["compare", str(old), str(new), "--contract", mode, "-o", f"json={report}"],
    )
    assert report.is_file(), result.output
    data = json.loads(report.read_text(encoding="utf-8"))
    scope = data.get("scope") or {}
    return {
        f["kind"]
        for f in (*data["changes"], *scope.get("filtered_internal_changes", []))
        if f.get("contract_relevance") == "UNKNOWN_UNRESOLVED"
    }


def test_offender_list_names_only_non_declaration_kinds() -> None:
    # The allowlist cannot launder a declaration kind: those are allowed to
    # stay unresolved under a stated contract and never belong here.
    listed = set().union(*KNOWN_OFFENDERS.values())
    assert {k for k in listed if _is_declaration_subject(k)} == set()
    assert set(KNOWN_OFFENDERS) <= set(_corpus_cases())


@pytest.mark.parametrize("case_name", _corpus_cases())
def test_stated_public_leaves_no_non_declaration_finding_unresolved(
    case_name: str, tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    if corpus.CURRENT_PLATFORM not in corpus.PLATFORMS.get(
        case_name, ["linux", "macos", "windows"]
    ):
        pytest.skip(f"{case_name} not supported on {corpus.CURRENT_PLATFORM}")
    archs = corpus.ARCHITECTURES.get(case_name)
    if archs and corpus.CURRENT_ARCHITECTURE not in archs:
        pytest.skip(f"{case_name} not supported on {corpus.CURRENT_ARCHITECTURE}")
    if not shutil.which("castxml"):
        pytest.skip("castxml not on PATH")
    feature = corpus.REQUIRES_FEATURE.get(case_name)
    if feature is not None:
        from tests.feature_probe import compiler_supports

        if not compiler_supports(feature):
            pytest.skip(f"{case_name} needs compiler feature {feature!r}")

    case_dir = corpus.example_catalog.case_dir(case_name)
    v1_src, v2_src, v1_hdr, v2_hdr = corpus._find_sources(case_dir)
    v1_lib, v2_lib = corpus._build_libs_for_case(
        case_name, case_dir, tmp_path, shared_cmake_build_dir, v1_src, v2_src
    )
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    _dump(v1_lib, v1_hdr, old)
    _dump(v2_lib, v2_hdr, new)

    adaptive = _unresolved(old, new, "auto", tmp_path / "auto.json")
    stated = _unresolved(old, new, "public", tmp_path / "public.json")
    offenders = {k for k in stated - adaptive if not _is_declaration_subject(k)}
    assert offenders == KNOWN_OFFENDERS.get(case_name, frozenset())

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

"""Unit tests for ``dumper_contract._profile_compiler_version`` (Codex
review, PR #624 follow-up): the AST frontend's own identity must not be
silently discarded from the ADR-050 profile fingerprint whenever a host
compiler_version is also present."""

from __future__ import annotations

from pathlib import Path

from abicheck.dumper_contract import (
    _attach_extraction_contract,
    _profile_compiler_version,
)
from abicheck.model import AbiSnapshot


def test_none_when_toolchain_empty() -> None:
    assert _profile_compiler_version({}) is None


def test_castxml_producer_includes_frontend_and_host_identity() -> None:
    toolchain = {
        "producer": "castxml",
        "selected": "/usr/bin/castxml",
        "version": "castxml version 0.6.5",
        "compiler_selected": "/usr/bin/g++",
        "compiler_version": "g++ (Ubuntu 13.2.0) 13.2.0",
    }
    result = _profile_compiler_version(toolchain)
    assert result is not None
    assert "castxml version 0.6.5" in result
    assert "g++ (Ubuntu 13.2.0) 13.2.0" in result


def test_different_castxml_versions_produce_different_values() -> None:
    """Pins the exact scenario the reviewer flagged: two dumps using
    different castxml binaries, but the same host compiler, must not
    collapse to the same profile input."""
    base = {
        "producer": "castxml",
        "compiler_selected": "/usr/bin/g++",
        "compiler_version": "g++ 13.2.0",
    }
    v1 = _profile_compiler_version({**base, "version": "castxml version 0.6.5"})
    v2 = _profile_compiler_version({**base, "version": "castxml version 0.7.0"})
    assert v1 != v2


def test_castxml_vs_clang_with_same_host_compiler_version_still_differ() -> None:
    """Pins the reviewer's second scenario: castxml-with-clang-backend vs.
    a pure clang dump, both reporting the same clang compiler_version,
    must still be distinguishable via the producer + frontend version."""
    castxml_side = _profile_compiler_version(
        {
            "producer": "castxml",
            "selected": "/usr/bin/castxml",
            "version": "castxml version 0.6.5",
            "compiler_selected": "/usr/bin/clang",
            "compiler_version": "clang version 18.1.3",
        }
    )
    clang_side = _profile_compiler_version(
        {
            "producer": "clang",
            "selected": "/usr/bin/clang",
            "version": "clang version 18.1.3",
            "compiler_selected": "/usr/bin/clang",
            "compiler_version": "clang version 18.1.3",
        }
    )
    assert castxml_side != clang_side


def test_missing_frontend_version_falls_back_to_host_only() -> None:
    result = _profile_compiler_version({"compiler_version": "gcc 13.2.0"})
    assert result is not None
    assert "gcc 13.2.0" in result


def test_attach_extraction_contract_with_malformed_gcc_options_does_not_raise(
    tmp_path: Path,
) -> None:
    """A malformed ``--gcc-options`` value (e.g. an unbalanced quote) must
    not abort the dump: ``shlex.split`` raising ``ValueError`` is caught and
    the contract is still computed from ``gcc_option_tokens`` alone."""
    header = tmp_path / "api.h"
    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    _attach_extraction_contract(
        snap,
        headers=[header],
        extra_includes=None,
        gcc_options='-DFOO="unterminated',
        gcc_option_tokens=(),
        lang=None,
        public_headers=None,
        public_header_dirs=None,
    )
    assert snap.contract is not None

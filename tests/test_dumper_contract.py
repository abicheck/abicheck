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


def _attach_resolved_standard(
    tmp_path: Path, *, ast_resolved_standard: str | None, from_headers: bool = True
) -> AbiSnapshot:
    header = tmp_path / "api.h"
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=from_headers,
        ast_resolved_standard=ast_resolved_standard,
    )
    _attach_extraction_contract(
        snap,
        headers=[header],
        extra_includes=None,
        gcc_options=None,
        gcc_option_tokens=(),
        lang="c++",
        public_headers=None,
        public_header_dirs=None,
    )
    return snap


class TestResolvedStandardFeedsProfileFingerprint:
    """P0 evidence-provider audit: two dumps with no explicit -std= must not
    silently share a profile_fingerprint when the C++20 requires/concept
    heuristic forced a different resolved standard on only one side --
    check_contracts_comparable had nothing to catch that divergence on
    before ast_resolved_standard (schema v15) started feeding this."""

    def test_resolved_standard_changes_profile_fingerprint(
        self, tmp_path: Path
    ) -> None:
        plain = _attach_resolved_standard(tmp_path, ast_resolved_standard=None)
        forced_cxx20 = _attach_resolved_standard(
            tmp_path, ast_resolved_standard="gnu++20"
        )
        assert plain.contract is not None
        assert forced_cxx20.contract is not None
        assert (
            plain.contract.profile_fingerprint
            != forced_cxx20.contract.profile_fingerprint
        )

    def test_same_resolved_standard_matches(self, tmp_path: Path) -> None:
        a = _attach_resolved_standard(tmp_path, ast_resolved_standard="gnu++17")
        b = _attach_resolved_standard(tmp_path, ast_resolved_standard="gnu++17")
        assert a.contract is not None
        assert b.contract is not None
        assert a.contract.profile_fingerprint == b.contract.profile_fingerprint

    def test_resolved_standard_ignored_when_not_from_headers(
        self, tmp_path: Path
    ) -> None:
        # A DWARF/symbols-only snapshot never ran an L2 frontend, so its
        # ast_resolved_standard (if any leaked through) must not feed the
        # profile fingerprint -- from_headers=False gates l2_frontend_ran.
        snap = _attach_resolved_standard(
            tmp_path, ast_resolved_standard="gnu++20", from_headers=False
        )
        assert snap.contract is None or snap.contract.profile_fingerprint is None


def _attach(
    tmp_path: Path,
    extra_includes: list[Path],
    extra_include_labels: dict[Path, str] | None,
) -> AbiSnapshot:
    header = tmp_path / "api.h"
    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    _attach_extraction_contract(
        snap,
        headers=[header],
        extra_includes=extra_includes,
        gcc_options=None,
        gcc_option_tokens=(),
        lang=None,
        public_headers=None,
        public_header_dirs=None,
        extra_include_labels=extra_include_labels,
    )
    assert snap.contract is not None
    return snap


class TestExtraIncludeLabels:
    """ADR-050 D1 (G32 Phase A) -- the ``--include old:LABEL=PATH`` CLI
    grammar's resolved label map, threaded here from
    ``dumper.dump()``/``service._dump_elf`` etc., must actually reach
    ``compute_extraction_contract``'s ``IncludeDir.label`` -- not be
    silently dropped at this, the final hop before the fingerprint itself
    (mirrors ``test_comparability.py::test_14_labeled_sibling_support_root_edit_does_not_flip_profile``,
    which exercises ``compute_extraction_contract`` directly; this covers
    the CLI-facing glue function one layer up)."""

    def test_label_changes_the_profile_fingerprint(self, tmp_path: Path) -> None:
        support = tmp_path / "src"
        support.mkdir()
        unlabeled = _attach(tmp_path, [support], None)
        labeled = _attach(tmp_path, [support], {support: "support"})
        assert (
            unlabeled.contract.profile_fingerprint
            != labeled.contract.profile_fingerprint
        )

    def test_no_labels_is_unaffected(self, tmp_path: Path) -> None:
        support = tmp_path / "src"
        support.mkdir()
        a = _attach(tmp_path, [support], None)
        b = _attach(tmp_path, [support], {})
        assert a.contract.profile_fingerprint == b.contract.profile_fingerprint

    def test_unrelated_path_in_the_label_map_is_a_no_op(self, tmp_path: Path) -> None:
        support = tmp_path / "src"
        support.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        a = _attach(tmp_path, [support], None)
        b = _attach(tmp_path, [support], {other: "unrelated"})
        assert a.contract.profile_fingerprint == b.contract.profile_fingerprint

    def test_same_label_both_sides_matches_despite_different_paths(
        self, tmp_path: Path
    ) -> None:
        """The whole point of the feature: a genuine two-checkout compare
        pairs the *same* label on each side's own differently-rooted
        support directory (``--include old:support=old/src --include
        new:support=new/src``), so the fingerprints match despite the
        paths themselves never being equal."""
        old_support = tmp_path / "old" / "src"
        old_support.mkdir(parents=True)
        new_support = tmp_path / "new" / "src"
        new_support.mkdir(parents=True)
        old = _attach(tmp_path, [old_support], {old_support: "support"})
        new = _attach(tmp_path, [new_support], {new_support: "support"})
        assert old.contract.profile_fingerprint == new.contract.profile_fingerprint

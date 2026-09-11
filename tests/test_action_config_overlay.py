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

"""Direct unit tests for ``abicheck.action_config_overlay`` -- the shared
sanitization ``action/run.sh``'s own config-overlay merge and
``actions/check-target/action.yml``'s "Generate assurance-overlay config"
step both call, so this trust boundary is defined in exactly one place
(PR #1222 Codex review findings 1 and 2). The two Action-level call sites
are exercised through their own executing tests
(``tests/test_action_release_topology_config.py``/
``tests/test_action_compile_context_parity.py`` for ``action/run.sh``;
``tests/test_reusable_workflows_require_complete_analysis.py``'s
``TestAssuranceOverlayStripsExecutableKeysFromDiscoveredConfig``/
``TestAssuranceOverlayRebasesRelativeIncludeDirs`` for the assurance-overlay
step) -- this module tests the shared primitives themselves, at the
function level, independent of either shell harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.action_config_overlay import (
    rebase_relative_config_paths,
    strip_untrusted_execution_keys,
    validate_base_config,
)


class TestStripUntrustedExecutionKeys:
    def test_build_query_is_stripped(self) -> None:
        base = {"build": {"query": "cmake --build .", "system": "cmake"}}
        out = strip_untrusted_execution_keys(base)
        assert out["build"] == {"system": "cmake"}

    def test_compile_compiler_is_stripped(self) -> None:
        base = {"compile": {"compiler": "/tmp/evil.sh", "std": "c++17"}}
        out = strip_untrusted_execution_keys(base)
        assert out["compile"] == {"std": "c++17"}

    def test_resource_limits_are_capped_not_raised(self) -> None:
        base = {"resource_limits": {"max_bundle_facts_decode_nodes": 999999999}}
        out = strip_untrusted_execution_keys(base)
        assert out["resource_limits"]["max_bundle_facts_decode_nodes"] < 999999999

    def test_a_lower_resource_limit_is_left_untouched(self) -> None:
        """Negative control: capping only ever lowers a raised value -- it
        must never raise a value the base document already set below the
        conservative default."""
        base = {"resource_limits": {"max_bundle_facts_decode_nodes": 5}}
        out = strip_untrusted_execution_keys(base)
        assert out["resource_limits"]["max_bundle_facts_decode_nodes"] == 5

    def test_absent_keys_are_a_no_op(self) -> None:
        base = {"severity": {"preset": "strict"}}
        out = strip_untrusted_execution_keys(base)
        assert out == base

    def test_does_not_mutate_the_input(self) -> None:
        base = {"build": {"query": "x"}, "compile": {"compiler": "/bin/cc"}}
        original = {"build": {"query": "x"}, "compile": {"compiler": "/bin/cc"}}
        strip_untrusted_execution_keys(base)
        assert base == original

    def test_compile_db_is_not_touched_here(self) -> None:
        """build.compile_db is deliberately each caller's own responsibility
        (see the module docstring) -- this function alone must leave it
        untouched."""
        base = {"build": {"compile_db": "compile_commands.json"}}
        out = strip_untrusted_execution_keys(base)
        assert out["build"]["compile_db"] == "compile_commands.json"

    def test_returns_a_dict_even_for_a_non_dict_build_or_compile_block(self) -> None:
        """A structurally-invalid base document (e.g. `build: []`) must not
        crash this function -- schema validation is a separate concern each
        caller already performs before calling this."""
        base = {"build": [], "compile": "not-a-mapping"}
        out = strip_untrusted_execution_keys(base)
        assert out == base

    @pytest.mark.parametrize(
        "bad_value",
        [
            pytest.param("no", id="string"),
            pytest.param([], id="list"),
            pytest.param(True, id="bool-true"),
            pytest.param(False, id="bool-false"),
            pytest.param(None, id="none"),
        ],
    )
    def test_wrong_typed_resource_limit_is_left_untouched_not_replaced(
        self, bad_value: object
    ) -> None:
        """CodeRabbit review, fresh evidence: a wrong-typed
        max_bundle_facts_decode_nodes used to be coerced to None before
        calling resolve_max_json_object_nodes_cfg(), which read that None
        straight back -- the resulting `capped_nodes` (None) then compared
        UNEQUAL to the original wrong-typed value, so the "capped" branch
        fired and silently OVERWROTE it with a literal null, printing a
        "capped to the conservative default" warning that describes
        nothing that actually happened. This function must instead leave a
        wrong-typed value alone -- schema validation (validate_base_config,
        called first by every real caller) is what rejects it, with the
        real BuildConfig error, not this stripping helper."""
        base = {"resource_limits": {"max_bundle_facts_decode_nodes": bad_value}}
        out = strip_untrusted_execution_keys(base)
        assert out["resource_limits"]["max_bundle_facts_decode_nodes"] == bad_value

    def test_valid_int_resource_limit_still_caps_correctly(self) -> None:
        """Companion negative control: the wrong-typed-value fix above must
        not disturb the ordinary valid-int capping path."""
        base = {"resource_limits": {"max_bundle_facts_decode_nodes": 999999999}}
        out = strip_untrusted_execution_keys(base)
        assert out["resource_limits"]["max_bundle_facts_decode_nodes"] < 999999999
        assert isinstance(out["resource_limits"]["max_bundle_facts_decode_nodes"], int)


class TestRebaseRelativeConfigPaths:
    def test_relative_include_dir_resolves_against_project_root(
        self, tmp_path: Path
    ) -> None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  include_dirs: [include]\n", encoding="utf-8")
        base = {"compile": {"include_dirs": ["include"]}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == [str((tmp_path / "include").resolve())]

    def test_multiple_relative_include_dirs_all_resolve(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  include_dirs: [a, b]\n", encoding="utf-8")
        base = {"compile": {"include_dirs": ["a", "b"]}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == [
            str((tmp_path / "a").resolve()),
            str((tmp_path / "b").resolve()),
        ]

    def test_absolute_include_dir_is_left_unchanged(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  include_dirs: [/abs/dir]\n", encoding="utf-8")
        base = {"compile": {"include_dirs": ["/abs/dir"]}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == ["/abs/dir"]

    def test_scalar_include_dirs_value_also_rebases(self, tmp_path: Path) -> None:
        """include_dirs need not be a list -- a bare string entry must be
        rebased the same way a list entry is."""
        cfg = tmp_path / ".abicheck.yml"
        base = {"compile": {"include_dirs": "include"}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == str((tmp_path / "include").resolve())

    def test_no_compile_block_is_a_no_op(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".abicheck.yml"
        base = {"severity": {"preset": "strict"}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out == base

    def test_no_include_dirs_key_is_a_no_op(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".abicheck.yml"
        base = {"compile": {"std": "c++17"}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out == base

    def test_does_not_mutate_the_input(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".abicheck.yml"
        base = {"compile": {"include_dirs": ["include"]}}
        original = {"compile": {"include_dirs": ["include"]}}
        rebase_relative_config_paths(base, found_path=cfg)
        assert base == original

    def test_github_dir_discovered_config_resolves_against_its_own_project_root(
        self, tmp_path: Path
    ) -> None:
        """A config discovered under .github/ anchors relative paths at the
        directory CONTAINING .github/, not at .github/ itself -- mirroring
        config_paths.project_root_for_config's own documented rule, which
        this function delegates to rather than re-deriving."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        cfg = github_dir / ".abicheck.yml"
        cfg.write_text("compile:\n  include_dirs: [include]\n", encoding="utf-8")
        base = {"compile": {"include_dirs": ["include"]}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == [str((tmp_path / "include").resolve())]


class TestValidateBaseConfig:
    """Both call sites (``action/run.sh``'s
    ``_merge_config_overlay_with_discovered_project_config`` and
    ``actions/check-target/action.yml``'s "Generate assurance-overlay
    config" step) must reject a schema-invalid base document with the same
    error a direct ``compare --config <file>`` invocation would raise --
    BEFORE either one's own stripping/overlay logic runs (PR #1222 Codex
    review, P2 finding: an invalid field that gets stripped anyway was
    previously never validated at all, silently masking the error)."""

    def test_valid_document_is_a_no_op(self) -> None:
        validate_base_config(
            {"compile": {"compiler": "gcc"}, "severity": {"preset": "strict"}}
        )

    def test_empty_document_is_valid(self) -> None:
        validate_base_config({})

    @pytest.mark.parametrize(
        "doc",
        [
            pytest.param({"build": {"query": 7}}, id="build.query-wrong-type"),
            pytest.param(
                {"compile": {"compiler": []}}, id="compile.compiler-wrong-type"
            ),
            pytest.param(
                {"build": {"compile_db": False}}, id="build.compile_db-wrong-type"
            ),
            pytest.param({"release": []}, id="release-block-not-a-mapping"),
            pytest.param(
                {"totally_unknown_top_level_key": 1}, id="unknown-top-level-key"
            ),
        ],
    )
    def test_schema_invalid_document_raises_value_error(self, doc: dict) -> None:
        """Every field one of the two call sites goes on to strip/cap/
        overwrite must already be rejected here, BEFORE that stripping ever
        runs -- otherwise the invalid value is silently deleted/replaced
        instead of surfaced (the exact bug this function exists to close)."""
        with pytest.raises(ValueError):
            validate_base_config(doc)

    def test_rejects_before_stripping_would_have_hidden_the_error(self) -> None:
        """Root-cause regression guard: build.query is a field
        strip_untrusted_execution_keys() unconditionally deletes, so
        validating AFTER stripping (the bug this function fixes) would never
        see the invalid value at all. Assert the two functions disagree on
        this document -- stripping alone reports it as clean, only
        validate_base_config (run first, per every real call site) catches
        it."""
        doc = {"build": {"query": 7}}
        stripped = strip_untrusted_execution_keys(doc)
        # Stripping alone silently "fixes" the invalid value by deleting it --
        # this is the masking behaviour the finding describes, reproduced
        # directly rather than only asserted in prose.
        assert "query" not in stripped.get("build", {})
        with pytest.raises(ValueError):
            validate_base_config(doc)

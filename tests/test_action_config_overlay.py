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

import os
from pathlib import Path

import pytest
import yaml

from abicheck.action_config_overlay import (
    apply_sources_root_config_blocks,
    discovered_compile_db_resolves,
    rebase_relative_config_paths,
    strip_untrusted_execution_keys,
    validate_base_config,
)
from abicheck.cli_options import merge_compile_config
from abicheck.dry_run_estimate import CompileContext


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

    def test_aliased_build_block_stripping_does_not_leak_into_sibling_key(
        self,
    ) -> None:
        """PR #1222 Codex review P2 finding's own root cause, checked against
        the sibling call site named in that finding: `yaml.safe_load()`
        resolves a YAML anchor/alias pair to the SAME dict object for both
        keys it's assigned to -- constructed directly here (rather than via
        `yaml.safe_load`) since that's exactly what an alias resolves to.
        `build.query` gets deleted from a COPY (`build_blk = dict(build_blk)`
        before the `del`), so the aliased sibling key's own object must be
        completely unaffected."""
        shared = {"query": "cmake --build .", "system": "cmake"}
        base = {"build": shared, "targets": shared}
        out = strip_untrusted_execution_keys(base)
        assert out["build"] == {"system": "cmake"}
        # The sibling key aliased to the same original object must still
        # carry `query` -- it was never the thing being stripped.
        assert out["targets"] == {"query": "cmake --build .", "system": "cmake"}
        assert "query" in shared  # the original object itself is untouched

    def test_aliased_compile_block_stripping_does_not_leak_into_sibling_key(
        self,
    ) -> None:
        shared = {"compiler": "/tmp/evil.sh", "std": "c++17"}
        base = {"compile": shared, "baseline": shared}
        out = strip_untrusted_execution_keys(base)
        assert out["compile"] == {"std": "c++17"}
        assert out["baseline"] == {"compiler": "/tmp/evil.sh", "std": "c++17"}

    def test_aliased_resource_limits_capping_does_not_leak_into_sibling_key(
        self,
    ) -> None:
        shared = {"max_bundle_facts_decode_nodes": 999999999}
        base = {"resource_limits": shared, "baseline": shared}
        out = strip_untrusted_execution_keys(base)
        assert out["resource_limits"]["max_bundle_facts_decode_nodes"] < 999999999
        # The aliased sibling must keep the ORIGINAL, uncapped value.
        assert out["baseline"]["max_bundle_facts_decode_nodes"] == 999999999


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
        # A *genuinely* absolute path on the running platform. "/abs/dir" is
        # absolute on POSIX but drive-relative on Windows
        # (`Path("/abs/dir").is_absolute()` is False there), so the rebase
        # correctly rewrote it to "C:\\abs\\dir" and this test failed on the
        # windows lane while claiming to be about absolute paths. Anchoring
        # it to the platform's own root keeps the invariant the name states.
        absolute = str(Path(tmp_path.anchor or "/") / "abs" / "dir")
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            yaml.safe_dump({"compile": {"include_dirs": [absolute]}}),
            encoding="utf-8",
        )
        base = {"compile": {"include_dirs": [absolute]}}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == [absolute]

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

    def test_aliased_compile_block_rebasing_does_not_leak_into_sibling_key(
        self, tmp_path: Path
    ) -> None:
        """Same YAML-alias hazard as TestStripUntrustedExecutionKeys's own
        aliased-block tests, checked here too since this function performs
        an independent in-place-shaped mutation
        (`compile_blk["include_dirs"] = ...`) on whatever object
        `base.get("compile")` returns. `compile_blk = dict(compile_blk)`
        before that assignment means an aliased sibling key must keep its
        own (relative, unrebased) value."""
        cfg = tmp_path / ".abicheck.yml"
        shared = {"include_dirs": ["include"], "std": "c++17"}
        base = {"compile": shared, "baseline": shared}
        out = rebase_relative_config_paths(base, found_path=cfg)
        assert out["compile"]["include_dirs"] == [str((tmp_path / "include").resolve())]
        # The aliased sibling must keep the ORIGINAL, unrebased value.
        assert out["baseline"]["include_dirs"] == ["include"]

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


class TestDiscoveredCompileDbResolves:
    """Direct unit tests for the shared resolution primitive PR #1222
    Finding 2 (fresh Codex review evidence) extracted so ``action/run.sh``'s
    compile-context overlay and ``actions/check-target/action.yml``'s
    assurance overlay can't independently drift on what counts as
    "resolves" -- see the function's own docstring for the full account,
    including why a plain ``match.is_file()`` check (mirroring
    ``buildsource/inline.py``'s own resolution) is not enough on its own.
    """

    def test_a_real_file_under_the_root_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
        assert discovered_compile_db_resolves("compile_commands.json", str(tmp_path))

    def test_a_glob_matching_a_real_file_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
        assert discovered_compile_db_resolves("*.json", str(tmp_path))

    def test_a_missing_file_does_not_resolve(self, tmp_path: Path) -> None:
        assert not discovered_compile_db_resolves(
            "nonexistent_compile_commands.json", str(tmp_path)
        )

    def test_a_glob_matching_only_a_directory_does_not_resolve(
        self, tmp_path: Path
    ) -> None:
        """Mirrors buildsource/inline.py's own ``match.is_file()`` check --
        a glob that matches only a directory is not usable evidence."""
        (tmp_path / "build_dir").mkdir()
        assert not discovered_compile_db_resolves("build_dir", str(tmp_path))

    def test_a_blank_sources_root_never_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
        assert not discovered_compile_db_resolves("compile_commands.json", "")

    def test_a_malformed_glob_pattern_does_not_raise(self, tmp_path: Path) -> None:
        assert not discovered_compile_db_resolves("[", str(tmp_path))

    def test_a_nonexistent_sources_root_does_not_raise(self, tmp_path: Path) -> None:
        assert not discovered_compile_db_resolves(
            "compile_commands.json", str(tmp_path / "does-not-exist")
        )

    def test_path_traversal_outside_the_root_does_not_resolve(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: ``Path.glob`` happily matches a
        pattern with ``..`` components, and ``match.is_file()`` alone does
        not reject it -- confirmed empirically before this containment
        check was added. A discovered ``build.compile_db`` naming a path
        that escapes ``sources_root`` via ``..`` must never be treated as
        "resolves", or a path-traversal read would be laundered into
        explicit, must-not-be-missing status."""
        sources = tmp_path / "sources"
        sources.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.json").write_text("[]", encoding="utf-8")
        assert not discovered_compile_db_resolves(
            "../outside/secret.json", str(sources)
        )

    def test_symlink_escaping_the_root_does_not_resolve(self, tmp_path: Path) -> None:
        """A symlink INSIDE sources_root whose target lives outside it must
        not resolve either -- the un-resolved path is nominally contained,
        but ``Path.resolve()`` (which dereferences symlinks) reveals the
        real target escapes the root, exactly the kind of trust-boundary
        bypass a discovered, untrusted config must not get credit for."""
        sources = tmp_path / "sources"
        sources.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        real_secret = outside / "secret.json"
        real_secret.write_text("[]", encoding="utf-8")
        link = sources / "compile_commands.json"
        try:
            link.symlink_to(real_secret)
        except OSError:
            pytest.skip("symlinks not supported on this filesystem")
        assert not discovered_compile_db_resolves("compile_commands.json", str(sources))

    def test_a_symlink_that_stays_within_the_root_still_resolves(
        self, tmp_path: Path
    ) -> None:
        """Negative control: the containment check must not reject every
        symlink -- only one whose real target escapes sources_root."""
        sources = tmp_path / "sources"
        sources.mkdir()
        real_file = sources / "real_compile_commands.json"
        real_file.write_text("[]", encoding="utf-8")
        link = sources / "compile_commands.json"
        try:
            link.symlink_to(real_file)
        except OSError:
            pytest.skip("symlinks not supported on this filesystem")
        assert discovered_compile_db_resolves("compile_commands.json", str(sources))

    def test_an_absolute_compile_db_pattern_does_not_raise_or_resolve(
        self, tmp_path: Path
    ) -> None:
        """CodeRabbit review, fresh evidence: ``Path(sources_root).
        glob(compile_db)`` raises ``NotImplementedError`` (not ``OSError``/
        ``ValueError``) for an absolute *compile_db* pattern -- confirmed
        still true on this interpreter. ``build.compile_db`` is documented
        as a glob RELATIVE to the ``--sources`` root, so an absolute
        pattern can never validly resolve against it; left uncaught, this
        crashed the whole assurance-overlay step instead of yielding the
        safe "doesn't resolve" outcome."""
        (tmp_path / "etc_passwd_stand_in.json").write_text("[]", encoding="utf-8")
        absolute_pattern = str(tmp_path / "etc_passwd_stand_in.json")
        assert not discovered_compile_db_resolves(absolute_pattern, str(tmp_path))

    def test_relative_sources_root_still_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]", encoding="utf-8")
        cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            assert discovered_compile_db_resolves("compile_commands.json", ".")
        finally:
            os.chdir(cwd)

    def test_a_glob_that_sorts_to_an_out_of_root_match_first_does_not_resolve(
        self, tmp_path: Path
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 eighth
        round): a glob matching BOTH an in-root file and an out-of-root
        file must be judged by whichever match the real collector
        (``buildsource/inline.py``'s ``sorted(sources.glob(cfg.
        compile_db))``, first ``is_file()`` hit) actually selects -- not by
        whether *some* contained match merely exists anywhere in the glob
        result. ``"../*/compile_commands.json"`` against a ``sources_root``
        named ``sources`` matches both ``sources/../aaa_outside/
        compile_commands.json`` (escapes the root) and ``sources/../
        sources/compile_commands.json`` (the root's own file, via the glob
        matching ``sources`` itself as one of its own siblings) --
        confirmed empirically that ``sorted()`` over the resulting
        ``PosixPath`` objects places the ``aaa_outside`` entry first
        (lexicographic path-component order: ``"aaa_outside" <
        "sources"``). The previous implementation iterated the SAME two
        matches in ``Path.glob``'s own (arbitrary) order and returned
        ``True`` the moment it reached the in-root one -- exactly the gap
        the finding reports: the overlay would validate this glob as
        "explicit and resolved", while the real collector resolves the
        SAME configured glob to the out-of-root database instead,
        silently reading external compiler flags into the run."""
        sources = tmp_path / "sources"
        sources.mkdir()
        (sources / "compile_commands.json").write_text("[]", encoding="utf-8")
        outside = tmp_path / "aaa_outside"
        outside.mkdir()
        (outside / "compile_commands.json").write_text("[]", encoding="utf-8")
        assert not discovered_compile_db_resolves(
            "../*/compile_commands.json", str(sources)
        )

    def test_a_glob_that_sorts_to_an_in_root_match_first_still_resolves(
        self, tmp_path: Path
    ) -> None:
        """Positive-control sibling of the test above: when the FIRST
        sorted, ``is_file()`` match is the one contained within
        ``sources_root``, the glob still resolves -- the fix must not
        become blanket-hostile to every glob that can also match something
        outside the root, only to one whose real, collector-selected match
        actually escapes it. Naming the outside sibling ``zzz_outside``
        (sorts AFTER ``sources``) flips which match is selected first
        relative to the test above, while keeping the identical glob
        pattern and directory shapes."""
        sources = tmp_path / "sources"
        sources.mkdir()
        (sources / "compile_commands.json").write_text("[]", encoding="utf-8")
        outside = tmp_path / "zzz_outside"
        outside.mkdir()
        (outside / "compile_commands.json").write_text("[]", encoding="utf-8")
        assert discovered_compile_db_resolves(
            "../*/compile_commands.json", str(sources)
        )


class TestApplySourcesRootConfigBlocks:
    """Direct unit tests for the shared block-selection primitive PR #1222
    (fresh Codex review evidence, third round) extracted so ``action/run.sh``'s
    compile-context overlay and ``actions/check-target/action.yml``'s
    assurance overlay can't independently drift on how a sources-root
    ``.abicheck.yml``'s own ``build:``/``sources:`` (and, for a genuinely
    single-sided caller, ``compile:``/``source:``/``debug:``) blocks are
    promoted over a checkout-root document's own such blocks. See the
    function's own docstring for the full account.
    """

    def test_present_block_replaces_the_base_ones(self) -> None:
        base = {"build": {"system": "bazel"}, "severity": {"preset": "strict"}}
        sources_doc = {"build": {"system": "cmake"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("build", "sources")
        )
        assert out["build"] == {"system": "cmake"}
        assert out["severity"] == {"preset": "strict"}

    def test_absent_block_is_removed_not_left_in_place(self) -> None:
        """REPLACE-or-remove, never "merge or leave untouched" -- a
        sources-root document that doesn't mention a block must clear the
        base's own one, matching ``discover_build_config()``'s exclusive
        selection (the native CLI never falls back to a DIFFERENT
        document's ``sources:`` just because the selected one omits it)."""
        base = {"build": {"system": "bazel"}, "sources": {"graph_detail": "full"}}
        sources_doc = {"build": {"system": "cmake"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("build", "sources")
        )
        assert "sources" not in out

    def test_only_named_blocks_are_touched(self) -> None:
        """A block outside *blocks* is untouched even if *sources_doc*
        defines it -- e.g. a pairwise caller excluding compile:/source:/
        debug: from promotion."""
        base = {"compile": {"std": "c++17"}}
        sources_doc = {"build": {"system": "cmake"}, "compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(base, sources_doc, blocks=("build",))
        assert out["compile"] == {"std": "c++17"}
        assert out["build"] == {"system": "cmake"}

    def test_none_sources_doc_clears_every_named_block(self) -> None:
        """An empty sources-root file (``yaml.safe_load`` -> ``None``) is
        NOT "no config found" -- ``discover_build_config()``'s selection is
        exclusive, so every named block must be cleared, matching
        ``load_build_config()``'s own empty-``BuildConfig`` outcome."""
        base = {"build": {"system": "bazel"}, "sources": {"graph_detail": "full"}}
        out = apply_sources_root_config_blocks(base, None, blocks=("build", "sources"))
        assert "build" not in out
        assert "sources" not in out

    def test_non_mapping_sources_doc_also_clears_every_named_block(self) -> None:
        """A malformed-shape file (e.g. a bare YAML list) parses to a
        non-``dict`` -- treated identically to ``None`` (see the ``None``
        case above), not left untouched."""
        base = {"build": {"system": "bazel"}}
        out = apply_sources_root_config_blocks(base, [1, 2, 3], blocks=("build",))
        assert "build" not in out

    def test_does_not_mutate_the_input(self) -> None:
        base = {"build": {"system": "bazel"}}
        original = {"build": {"system": "bazel"}}
        apply_sources_root_config_blocks(
            base, {"build": {"system": "cmake"}}, blocks=("build",)
        )
        assert base == original

    def test_empty_blocks_tuple_is_a_no_op(self) -> None:
        base = {"build": {"system": "bazel"}, "severity": {"preset": "strict"}}
        out = apply_sources_root_config_blocks(
            base, {"build": {"system": "cmake"}}, blocks=()
        )
        assert out == base


class TestApplySourcesRootConfigBlocksCompileMerge:
    """P1 finding (Codex review, fresh evidence, PR #1222 fourth round): a
    fresh regression from the previous round's own fix -- ``compile:`` was
    being REPLACED wholesale by ``apply_sources_root_config_blocks``, the
    same treatment correct for ``build:``/``sources:``/``source:``/
    ``debug:`` above but wrong for ``compile:`` specifically, since
    ``cli_options.merge_compile_config`` folds a sources-root document's
    ``compile:`` block onto an ALREADY-resolved checkout-config context,
    field by field, rather than selecting one document's block exclusively.
    These are direct unit tests for that per-field merge, at the raw
    ``compile:`` mapping level -- see ``_merge_compile_block``'s own
    docstring for the exact precedence transcribed from
    ``merge_compile_config``. All pass ``merge_compile=True`` explicitly,
    modeling ``mode: compare`` (the checkout-then-sources two-stage fold)
    -- ``merge_compile``'s own default (``False``) models ``dump``/
    ``scan``'s single-document-exclusive selection instead, covered by
    ``TestApplySourcesRootConfigBlocksCompileReplace`` below.
    """

    def test_checkout_only_key_survives_a_disjoint_sources_root_promotion(
        self,
    ) -> None:
        """The scenario the regression report names directly: a checkout
        document sets ONLY ``compile.std``, a sources-root document sets
        ONLY ``compile.include_dirs`` (no overlapping key) -- the merged
        ``compile:`` must carry BOTH, matching what ``merge_compile_config``
        would produce for the identical two documents."""
        base = {"compile": {"std": "c++20"}}
        sources_doc = {"compile": {"include_dirs": ["foo"]}}
        out = apply_sources_root_config_blocks(
            base,
            sources_doc,
            blocks=("build", "sources", "compile", "source", "debug"),
            merge_compile=True,
        )
        assert out["compile"]["std"] == "c++20"
        assert out["compile"]["include_dirs"] == ["foo"]

    def test_sources_root_only_scalar_key_is_promoted(self) -> None:
        base = {"compile": {"std": "c++20"}}
        sources_doc = {"compile": {"sysroot": "/opt/sysroot"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"] == {"std": "c++20", "sysroot": "/opt/sysroot"}

    def test_checkout_scalar_value_wins_a_genuine_conflict(
        self, tmp_path: Path
    ) -> None:
        """``frontend``/``sysroot``/``compiler`` resolve like
        ``merge_compile_config``'s own ``cli_ctx.<field> if cli_ctx.<field>
        is not None else bc.<field>`` -- the checkout document's own value
        (folded first, in the real two-stage pipeline) blocks a
        sources-root value for the SAME key from applying at all.

        ``std`` is NOT checked via the merged document's own ``std`` key
        here: once BOTH documents set ``std`` (a genuine same-field
        conflict), ``merge_compile_std_fields`` folds it into the joint
        ``options`` argv sequence instead of a plain scalar override
        (PR #1222 tenth round) -- so this asserts the FUNCTIONAL result
        (checkout's ``-std=`` ends up last and wins, matching the real
        two-stage pipeline exactly) rather than a literal ``compile.std``
        key that no longer exists post-fold."""
        base = {"compile": {"std": "c++17", "sysroot": "/checkout/sysroot"}}
        sources_doc = {"compile": {"std": "c++20", "sysroot": "/sources/sysroot"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"]["sysroot"] == "/checkout/sysroot"
        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, base["compile"], sources_doc["compile"]
        )
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", out["compile"])
        assert actual == expected
        assert actual[-1] == "-std=c++17"

    def test_include_dirs_concatenates_checkout_first(self) -> None:
        base = {"compile": {"include_dirs": ["a"]}}
        sources_doc = {"compile": {"include_dirs": ["b"]}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"]["include_dirs"] == ["a", "b"]

    def test_defines_concatenates_sources_first(self, tmp_path: Path) -> None:
        """``defines``/``options`` synthesize argv tokens in
        ``merge_compile_config``, prepending the current (sources-root)
        stage's own tokens ahead of the prior (checkout) stage's already-
        resolved ones.

        Once BOTH documents set ``defines`` (a genuine same-field
        contribution from each side), ``merge_compile_std_fields`` folds
        it into the joint ``options`` argv sequence rather than a plain
        ``compile.defines`` list (PR #1222 tenth round) -- checked here via
        the real two-stage oracle rather than a literal ``compile.defines``
        key, which the fold intentionally replaces."""
        base = {"compile": {"defines": ["CHECKOUT=1"]}}
        sources_doc = {"compile": {"defines": ["SOURCES=1"]}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, base["compile"], sources_doc["compile"]
        )
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", out["compile"])
        assert actual == expected
        assert actual == ("-DSOURCES=1", "-DCHECKOUT=1")

    @pytest.mark.parametrize(
        ("checkout_nostdinc", "sources_nostdinc", "expected"),
        [
            (True, True, True),
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ],
    )
    def test_nostdinc_merges_with_or_semantics(
        self, checkout_nostdinc: bool, sources_nostdinc: bool, expected: bool
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 fifth round):
        ``nostdinc`` is NOT one of the fields where the later-folded
        document's own value wins outright -- that precedence is real for
        ``frontend_context`` (below), but ``nostdinc``'s actual two-call
        shape (``cli_compare_helpers.py``'s ``nostdinc_explicit=
        _nostdinc_explicit or compile_context.nostdinc`` feeding
        ``cli_options.merge_compile_config``) combines the checkout and
        sources-root values with OR semantics: an already-``True`` value
        from EITHER document always survives. This parametrization is the
        full 2x2 truth table verified directly against the real
        ``merge_compile_config`` two-call sequence (see
        ``action_config_overlay.py``'s ``_COMPILE_OR_KEYS`` docstring for
        the exact enumeration this test mirrors) -- the previous version of
        this test asserted only the ``(True, False) -> False`` cell, which
        is exactly the regression: a checkout ``compile.nostdinc: true``
        must never be cleared by a sources-root ``compile.nostdinc:
        false``."""
        base = {"compile": {"nostdinc": checkout_nostdinc}}
        sources_doc = {"compile": {"nostdinc": sources_nostdinc}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"]["nostdinc"] is expected

    def test_nostdinc_checkout_true_survives_when_sources_root_omits_it(
        self,
    ) -> None:
        """Companion to the OR-semantics parametrization above: when the
        sources-root document doesn't mention ``nostdinc`` at all (as
        opposed to explicitly setting ``false``), the checkout's own
        ``true`` must still survive -- this was already correct before the
        fix (the per-field loop only visits keys the sources document
        actually sets) but is worth pinning explicitly alongside the
        explicit-``false`` regression case."""
        base = {"compile": {"nostdinc": True}}
        sources_doc = {"compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"]["nostdinc"] is True

    def test_frontend_context_lets_sources_root_win_when_it_sets_one(self) -> None:
        base = {"compile": {"frontend_context": "host"}}
        sources_doc = {"compile": {"frontend_context": "device"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"]["frontend_context"] == "device"

    @pytest.mark.parametrize(
        ("checkout_frontend", "sources_frontend", "expected"),
        [
            # Checkout explicitly "auto" -- the semantic-default sentinel --
            # never blocks a concrete sources-root value.
            ("auto", "clang", "clang"),
            ("auto", "castxml", "castxml"),
            # Checkout absent (unset) behaves identically to explicit "auto".
            (None, "clang", "clang"),
            # Checkout concrete always wins a genuine conflict, regardless
            # of what the sources-root document sets (even another concrete
            # value) -- `merge_compile_config` never even consults the
            # sources-root value once `cli_ctx.frontend != "auto"`.
            ("clang", "castxml", "clang"),
            ("clang", "auto", "clang"),
            # Both "auto" (explicitly or by omission on the sources side)
            # stays "auto" -- the fix must not invent a concrete frontend
            # neither document asked for.
            ("auto", "auto", "auto"),
            ("auto", None, "auto"),
            # Case-insensitive spellings (P1 finding, PR #1222 seventh
            # round): the real pipeline's own config parsing
            # (`build_config.BuildConfig.from_dict`'s `_lowered()`) accepts
            # `compile.frontend` case-insensitively, matching the CLI's
            # `click.Choice(AST_FRONTENDS, case_sensitive=False)` -- a
            # checkout value that only lowercases to "auto" must be treated
            # exactly like a lowercase "auto" sentinel, not a concrete
            # checkout choice.
            ("AUTO", "clang", "clang"),
            ("Auto", "castxml", "castxml"),
            ("AuTo", "clang", "clang"),
        ],
    )
    def test_frontend_auto_sentinel_merges_like_merge_compile_config(
        self,
        checkout_frontend: str | None,
        sources_frontend: str | None,
        expected: str,
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 sixth round):
        ``_merge_compile_block`` classified ``frontend`` under the plain
        "checkout already set this key blocks sources" default rule, which
        is right for a genuinely *unset* checkout value but wrong for
        ``frontend``'s own semantic-default sentinel ``"auto"`` -- the real
        ``cli_options.merge_compile_config`` checks for the literal string
        ``"auto"`` explicitly (``cli_ctx.frontend != "auto"``), so a
        checkout ``compile.frontend: auto`` must be treated exactly like an
        absent key: the sources-root document's value should win. This
        parametrization is the full truth table verified directly against
        ``merge_compile_config``'s real two-call expression (see
        ``action_config_overlay.py``'s ``_COMPILE_AUTO_DEFAULT_KEYS``
        docstring for the exact enumeration this test mirrors) -- checkout
        ``"auto"``/absent lets sources win outright, any concrete checkout
        value wins outright over sources (even a differing concrete sources
        value), and both-``"auto"``/both-absent stays ``"auto"``.

        Seventh round (fresh evidence): the first fix's sentinel check
        compared the raw, unnormalized checkout string, so a case variant a
        real ``.abicheck.yml`` legitimately accepts (``build_config.
        BuildConfig.from_dict``'s ``_lowered()`` lowercases ``compile.
        frontend`` before validating it, matching the CLI's own
        ``click.Choice(AST_FRONTENDS, case_sensitive=False)``) -- e.g.
        ``AUTO``/``Auto`` -- was wrongly read as a concrete checkout choice,
        discarding a sources-root ``clang``/``castxml`` selection. The
        ``AUTO``/``Auto``/``AuTo`` cases below pin that the fix normalizes
        case the same way before comparing."""
        base: dict[str, object] = (
            {"compile": {"frontend": checkout_frontend}}
            if checkout_frontend is not None
            else {"compile": {}}
        )
        sources_doc: dict[str, object] = (
            {"compile": {"frontend": sources_frontend}}
            if sources_frontend is not None
            else {"compile": {}}
        )
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"].get("frontend") == expected

    def test_checkout_only_fields_are_never_promoted_from_sources_root(self) -> None:
        """``ast_frontend_fallback``/``allow_unsupported_castxml``/``lang``
        are read from a SINGLE, already-resolved project config in the real
        pipeline (``apply_compile_config_env_toggles``'s own single ``bc``
        parameter; ``resolved_cfg.compile_lang`` in
        ``cli_compare_helpers.py``) -- never folded with a second,
        sources-root document at all, so promoting one from the
        sources-root document here would grant it an effect it never has
        downstream."""
        base = {"compile": {"lang": "c"}}
        sources_doc = {
            "compile": {
                "lang": "cpp",
                "ast_frontend_fallback": True,
                "allow_unsupported_castxml": True,
            }
        }
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"] == {"lang": "c"}

    def test_empty_sources_root_document_is_a_no_op_for_compile(self) -> None:
        """Unlike ``build:``/``sources:``/``source:``/``debug:`` (where an
        empty/non-mapping sources-root document CLEARS the base's own
        block, matching an exclusive single-document selection),
        ``compile:`` under ``merge_compile=True`` is a genuine merge -- an
        empty sources-root document sets nothing, which is a no-op fold,
        not a block-clearing one."""
        base = {"compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(
            base, None, blocks=("compile",), merge_compile=True
        )
        assert out["compile"] == {"std": "c++20"}

    def test_no_checkout_compile_block_still_promotes_sources_root(self) -> None:
        base: dict[str, object] = {}
        sources_doc = {"compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(
            base, sources_doc, blocks=("compile",), merge_compile=True
        )
        assert out["compile"] == {"std": "c++20"}

    def test_does_not_mutate_the_input(self) -> None:
        base = {"compile": {"std": "c++20"}}
        original = {"compile": {"std": "c++20"}}
        apply_sources_root_config_blocks(
            base,
            {"compile": {"include_dirs": ["foo"]}},
            blocks=("compile",),
            merge_compile=True,
        )
        assert base == original


def _real_two_stage_gcc_option_tokens(
    tmp_path: Path,
    checkout_compile: dict[str, object],
    sources_compile: dict[str, object],
) -> tuple[str, ...]:
    """Run the REAL two-stage ``merge_compile_config`` fold ``compare``'s own
    per-side implicit dump performs -- checkout document folded onto a bare
    ``CompileContext`` first, then the sources-root document folded on top
    of THAT already-resolved context -- and return the resulting
    ``gcc_option_tokens``. This is the oracle
    ``test_std_and_options_cross_key_ordering_matches_real_pipeline`` below
    checks the overlay's merged document against, rather than a
    hand-derived expected tuple that could itself be wrong."""
    import yaml

    checkout_cfg = tmp_path / "checkout.abicheck.yml"
    checkout_cfg.write_text(yaml.safe_dump({"compile": checkout_compile}))
    sources_dir = tmp_path / "sources_root"
    sources_dir.mkdir(exist_ok=True)
    (sources_dir / ".abicheck.yml").write_text(
        yaml.safe_dump({"compile": sources_compile})
    )
    ctx1, includes1 = merge_compile_config(CompileContext(), (), checkout_cfg, None)
    ctx2, _ = merge_compile_config(ctx1, includes1, None, sources_dir)
    return ctx2.gcc_option_tokens


def _single_stage_gcc_option_tokens(
    tmp_path: Path, name: str, compile_blk: dict[str, object]
) -> tuple[str, ...]:
    """Run a single ``merge_compile_config`` fold of *compile_blk* onto a
    bare ``CompileContext`` -- what the nested "Run analysis" invocation
    does when it re-parses an overlay-synthesized ``compile:`` block as an
    ordinary, single ``--build-config`` document."""
    import yaml

    cfg = tmp_path / f"{name}.abicheck.yml"
    cfg.write_text(yaml.safe_dump({"compile": compile_blk}))
    ctx, _ = merge_compile_config(CompileContext(), (), cfg, None)
    return ctx.gcc_option_tokens


class TestApplySourcesRootConfigBlocksCompileStdOptionsOrdering:
    """P1 finding (Codex review, fresh evidence, PR #1222 ninth round):
    ``_merge_compile_block`` merged ``compile.std`` and ``compile.options``
    as though they were independent fields, each with its own per-key
    precedence rule. In reality ``merge_compile_config`` folds them into
    ONE flattened compiler-argv sequence
    (``[-std=<std>] + [-D<define> ...] + <options...>``) per document, and
    decides a same-flag conflict by relative POSITION in that one combined
    sequence across both documents -- never by which YAML key either side
    used. A checkout ``std: c++17`` plus a sources-root
    ``options: [-std=c++23]`` must resolve with the checkout's ``-std=``
    winning (it is folded in LAST), exactly like every other genuine
    checkout/sources-root conflict in this module -- the old per-key
    merge produced the opposite, sources-winning order instead.

    Every case below is checked directly against the REAL
    ``merge_compile_config`` two-stage fold (see
    ``_real_two_stage_gcc_option_tokens``), not a hand-derived expectation.
    """

    def test_reported_scenario_checkout_std_vs_sources_options(
        self, tmp_path: Path
    ) -> None:
        """The exact scenario from the finding: checkout sets ``std:
        c++17``; the sources-root document sets ``options: [-std=c++23]``
        instead of ``std``. The real pipeline lets the checkout's token win
        (folded in last); the overlay must reproduce that when its merged
        document is later re-parsed by a single-stage fold."""
        checkout_compile = {"std": "c++17"}
        sources_compile = {"options": ["-std=c++23"]}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )
        assert expected == ("-std=c++23", "-std=c++17")  # pin the real oracle itself

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected
        # And the resolved compiler standard is checkout's, not sources'.
        assert actual[-1] == "-std=c++17"

    def test_sources_std_vs_checkout_options(self, tmp_path: Path) -> None:
        """The mirror image: sources-root sets ``std``, checkout expresses
        its own ``-std=`` via ``options`` instead -- checkout's token is
        still folded in last and still wins."""
        checkout_compile = {"options": ["-std=c++20"]}
        sources_compile = {"std": "c++23"}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected

    def test_std_and_defines_and_options_all_present_both_sides(
        self, tmp_path: Path
    ) -> None:
        """A fuller mix -- ``std``, ``defines``, AND ``options`` set on BOTH
        documents at once -- to check the general cross-key fold, not just
        the two-field case the finding names directly."""
        checkout_compile = {
            "std": "c++17",
            "defines": ["CHECKOUT_DEFINE=1"],
            "options": ["-fno-exceptions"],
        }
        sources_compile = {
            "std": "c++20",
            "defines": ["SOURCES_DEFINE=1"],
            "options": ["-DSOURCES_OPT"],
        }

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected

    def test_only_checkout_sets_std_sources_sets_nothing_relevant(
        self, tmp_path: Path
    ) -> None:
        """No actual cross-key conflict (sources-root sets an unrelated
        field) -- the fold must still reproduce the real pipeline's token
        sequence, which here is simply checkout's own ``-std=``."""
        checkout_compile = {"std": "c++20"}
        sources_compile = {"include_dirs": ["/extra/include"]}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected
        # No std-involved conflict here -- `std` is free to stay a plain
        # structured key rather than being folded into `options`.
        assert merged.get("std") == "c++20"
        assert merged.get("include_dirs") == ["/extra/include"]

    def test_only_sources_sets_std_checkout_sets_nothing_relevant(
        self, tmp_path: Path
    ) -> None:
        checkout_compile = {"sysroot": "/opt/sysroot"}
        sources_compile = {"std": "c++23"}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected

    def test_reported_scenario_checkout_defines_vs_sources_options(
        self, tmp_path: Path
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 tenth round):
        neither document sets ``std`` at all -- checkout sets
        ``defines: [A]``, the sources-root document redefines the same
        macro via ``options: [-DA=2]``. The old fix only widened the
        trigger to a ``std`` co-occurrence, so this cross-key
        defines-vs-options conflict still fell through to independent
        per-key folding and produced ``-DA`` before ``-DA=2`` -- sources
        winning the compiler's last-flag-wins rule, the reverse of the
        documented checkout-wins precedence. The real two-stage pipeline
        folds checkout's own ``-DA`` in LAST."""
        checkout_compile = {"defines": ["A"]}
        sources_compile = {"options": ["-DA=2"]}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )
        assert expected == ("-DA=2", "-DA")  # pin the real oracle itself

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected
        assert actual[-1] == "-DA"  # checkout's own token wins

    def test_reported_scenario_mirror_checkout_options_vs_sources_defines(
        self, tmp_path: Path
    ) -> None:
        """The mirror image of the reported scenario: checkout expresses
        its define via ``options``, sources-root uses the structured
        ``defines`` key. Checkout's token must still be folded in last."""
        checkout_compile = {"options": ["-DB=2"]}
        sources_compile = {"defines": ["B"]}

        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected
        assert actual[-1] == "-DB=2"  # checkout's own token wins

    @pytest.mark.parametrize(
        ("checkout_compile", "sources_compile"),
        [
            # Same field on both sides (defines vs defines).
            ({"defines": ["A"]}, {"defines": ["A=2"]}),
            # Same field on both sides (options vs options).
            ({"options": ["-DA"]}, {"options": ["-DA=2"]}),
            # std vs defines (no options anywhere).
            ({"std": "c++17"}, {"defines": ["A"]}),
            # defines vs std (mirror).
            ({"defines": ["A"]}, {"std": "c++17"}),
            # All three set on one side only, nothing relevant on the other.
            (
                {"std": "c++17", "defines": ["A"], "options": ["-fno-exceptions"]},
                {"sysroot": "/opt/sysroot"},
            ),
        ],
        ids=[
            "defines-vs-defines",
            "options-vs-options",
            "std-vs-defines",
            "defines-vs-std",
            "one-side-all-three-fields",
        ],
    )
    def test_general_std_defines_options_trigger_condition(
        self,
        tmp_path: Path,
        checkout_compile: dict[str, object],
        sources_compile: dict[str, object],
    ) -> None:
        """General invariant behind the fix: the joint fold must trigger
        (and match the real two-stage pipeline exactly) whenever EITHER
        document sets ANY of ``std``/``defines``/``options`` -- regardless
        of which of the three fields each side happens to use. This is the
        bug-class regression per this repo's AGENTS.md: a fix scoped to the
        one reported field pair (``defines`` vs ``options``) would still
        miss e.g. ``std`` vs ``defines``, so this enumerates several
        independent field combinations against the same real-pipeline
        oracle used by the reported-scenario tests above."""
        expected = _real_two_stage_gcc_option_tokens(
            tmp_path, checkout_compile, sources_compile
        )

        merged = apply_sources_root_config_blocks(
            {"compile": checkout_compile},
            {"compile": sources_compile},
            blocks=("compile",),
            merge_compile=True,
        )["compile"]
        actual = _single_stage_gcc_option_tokens(tmp_path, "merged", merged)
        assert actual == expected


class TestApplySourcesRootConfigBlocksCompileReplace:
    """The default-``merge_compile`` (``False``) sibling of the class
    above, modeling ``dump``/``scan --against``'s genuine single-document-
    exclusive ``compile:`` selection -- Codex review, fresh evidence, PR
    #1222 fourth round, second finding on this same fix: within the
    single-sided bucket, ``compile:`` does NOT always resolve the same way
    (see ``apply_sources_root_config_blocks``'s own ``merge_compile``
    parameter docstring)."""

    def test_conflicting_checkout_value_is_replaced_not_merged(self) -> None:
        base = {"compile": {"std": "c++17", "sysroot": "/checkout/sysroot"}}
        sources_doc = {"compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(base, sources_doc, blocks=("compile",))
        assert out["compile"] == {"std": "c++20"}

    def test_empty_sources_root_document_clears_compile(self) -> None:
        """Unlike the MERGE case above, an empty/non-mapping sources-root
        document under the default ``merge_compile=False`` CLEARS the
        checkout's own ``compile:`` block, matching the exclusive
        single-document selection ``build:``/``sources:``/``source:``/
        ``debug:`` already use."""
        base = {"compile": {"std": "c++20"}}
        out = apply_sources_root_config_blocks(base, None, blocks=("compile",))
        assert "compile" not in out

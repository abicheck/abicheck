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

"""ADR-043 (pre-1.0 CLI reset): strict ``.abicheck.yml`` loading.

``abicheck doctor``, ``abicheck init``, and the ``abicheck config`` group
(``validate``/``show-effective``) are removed entirely — no aliases, no
deprecation warnings. The structural strictness ``config validate`` used to
provide as a separate, easy-to-skip step now lives in
``BuildConfig.from_dict`` itself (``abicheck/buildsource/build_config.py`` —
split out of ``inline.py``, G38 Phase 15 file-split prerequisite; this test
module moved with it), so it fires on every real ``dump``/``compare``/
``scan`` config load. This module proves:

* ``BuildConfig.from_dict`` raises ``ValueError`` for every structural
  problem (unknown top-level key, unknown block subkey, non-mapping block,
  wrong scalar/list type, bad enum value) with no opt-in step required.
* A real CLI command (``compare``) exits 64 (the project's usage-error code)
  on a bad ``.abicheck.yml``, never an uncaught traceback.
* ``init``/``config``/``doctor`` are gone: invoking them produces Click's
  ordinary "no such command" usage error, exactly like any other unknown
  command name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource.build_config import BuildConfig
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _identical_pair(tmp_path: Path) -> tuple[Path, Path]:
    snap = AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    old_p = _write_snap(tmp_path / "old.json", snap)
    new_p = _write_snap(tmp_path / "new.json", snap)
    return old_p, new_p


# ── BuildConfig.from_dict: structural strictness ─────────────────────────────


class TestBuildConfigFromDictRejects:
    def test_unknown_top_level_key(self) -> None:
        with pytest.raises(ValueError, match="unknown .abicheck.yml key 'bogus_top'"):
            BuildConfig.from_dict({"bogus_top": 1})

    def test_unknown_block_subkey(self) -> None:
        with pytest.raises(ValueError, match=r"severity\.'bogus'"):
            BuildConfig.from_dict({"severity": {"preset": "strict", "bogus": 1}})

    def test_non_mapping_block_value(self) -> None:
        with pytest.raises(ValueError, match="severity must be a mapping"):
            BuildConfig.from_dict({"severity": "strict"})

    def test_wrong_scalar_type_bool_subkey(self) -> None:
        with pytest.raises(ValueError, match="scope.public must be a boolean"):
            BuildConfig.from_dict({"scope": {"public": "false"}})

    def test_wrong_scalar_type_string_subkey(self) -> None:
        with pytest.raises(ValueError, match="debug.debuginfod_url must be a string"):
            BuildConfig.from_dict({"debug": {"debuginfod_url": 456}})

    def test_wrong_list_type_container(self) -> None:
        with pytest.raises(
            ValueError, match="sources.public_headers must be a string or list"
        ):
            BuildConfig.from_dict({"sources": {"public_headers": 123}})

    def test_wrong_list_type_element(self) -> None:
        with pytest.raises(
            ValueError, match=r"sources\.public_headers must be a list of strings"
        ):
            BuildConfig.from_dict(
                {"sources": {"public_headers": ["include/foo.h", 123]}}
            )

    def test_wrong_top_level_scalar_string(self) -> None:
        # exit_code_scheme itself no longer exists as a top-level key at all
        # (CLI cleanup phase two PR G2) -- any presence of it, wrong type or
        # not, is now an unknown-key error rather than a type error.
        with pytest.raises(ValueError, match="exit_code_scheme"):
            BuildConfig.from_dict({"exit_code_scheme": 123})

    def test_wrong_top_level_scalar_int(self) -> None:
        with pytest.raises(ValueError, match="version must be an integer"):
            BuildConfig.from_dict({"version": "1"})

    def test_bad_enum_value(self) -> None:
        with pytest.raises(ValueError, match="severity.abi_breaking"):
            BuildConfig.from_dict({"severity": {"abi_breaking": "nope"}})

    def test_source_block_rejects_graph_subkey(self) -> None:
        """`graph` belongs to the `sources:` (plural) block -- `from_dict`
        never reads it from `source:` (singular), so a config that used the
        wrong block name used to be silently accepted and silently ignored
        rather than erroring. It must now be flagged as an unknown subkey
        like any other typo, not accepted as a no-op."""
        with pytest.raises(ValueError, match=r"source\.'graph'"):
            BuildConfig.from_dict({"source": {"method": "s4", "graph": "full"}})

    def test_multiple_findings_all_reported(self) -> None:
        """A single bad file reports every problem at once, not just the first."""
        with pytest.raises(ValueError) as exc_info:
            BuildConfig.from_dict({"bogus_top": 1, "version": "not-an-int"})
        message = str(exc_info.value)
        assert "bogus_top" in message
        assert "version must be an integer" in message

    def test_known_good_config_does_not_raise(self) -> None:
        """A config using only known keys/blocks/types must still load cleanly
        (guards against the hardening becoming stricter than the real schema)."""
        cfg = BuildConfig.from_dict(
            {
                "version": 1,
                "build": {
                    "system": "cmake",
                    "query": "cmake --version",
                    "compile_db": "x.json",
                },
                "sources": {
                    "public_headers": ["a.h"],
                    "exclude": "internal/**",
                    "graph": "full",
                },
                "severity": {
                    "preset": "strict",
                    "abi_breaking": "error",
                    "potential_breaking": "warning",
                    "quality_issues": "info",
                    "addition": "info",
                },
                "scope": {
                    "public": True,
                    "collapse_versioned_symbols": False,
                    "public_symbols": ["_Z3foov"],
                    "show_redundant": False,
                    "public_header_dirs": ["include/public"],
                },
                "suppression": {"strict": True, "require_justification": False},
                "source": {"method": "s4"},
                "compile": {
                    "frontend": "clang",
                    "std": "c++20",
                    "include_dirs": ["include"],
                    "defines": ["FOO=1"],
                    "sysroot": "/opt/sysroot",
                    "nostdinc": True,
                },
                "debug": {
                    "format": "dwarf",
                    "dwarf_only": True,
                    "debuginfod": True,
                    "debuginfod_url": "https://example.invalid",
                },
                "bundle": {
                    "system_providers": ["libvendor.so.1"],
                    "cohorts": ["libfoo_"],
                },
                # Keys parsed by sibling modules, not from_dict itself.
                "risk_rules": {},
                "crosschecks": {},
            }
        )
        assert cfg.version == 1
        assert cfg.compile_frontend == "clang"
        assert cfg.bundle_system_providers == ["libvendor.so.1"]
        assert cfg.bundle_cohorts == ["libfoo_"]
        assert cfg.public_header_dirs == ["include/public"]


class TestBuildConfigPublicHeaderDirs:
    """``scope.public_header_dirs`` (ADR-068 plan §5 P4): the config-sourced
    public/internal boundary for the four cross-source checks migrated onto
    ``compare()`` in this PR. Deliberately a *different* key from the
    pre-existing ``scope.public`` boolean (public-surface FP-scoping
    toggle) -- the two answer unrelated questions despite the naming
    collision the plan doc's own shorthand ("`.abicheck.yml` `scope.public`")
    might suggest."""

    def test_absent_by_default(self) -> None:
        cfg = BuildConfig.from_dict({})
        assert cfg.public_header_dirs == []

    def test_parses_a_list(self) -> None:
        cfg = BuildConfig.from_dict(
            {"scope": {"public_header_dirs": ["include", "public"]}}
        )
        assert cfg.public_header_dirs == ["include", "public"]

    def test_accepts_a_single_bare_string(self) -> None:
        """``_strs()``'s documented convention: a single bare string folds to
        a one-element list, same as every other list-shaped ``scope:``
        subkey (``LIST_SUBKEYS`` in ``build_config_schema.py``)."""
        cfg = BuildConfig.from_dict({"scope": {"public_header_dirs": "include"}})
        assert cfg.public_header_dirs == ["include"]

    def test_wrong_type_raises(self) -> None:
        with pytest.raises(ValueError):
            BuildConfig.from_dict({"scope": {"public_header_dirs": 42}})

    def test_independent_of_the_scope_public_boolean(self) -> None:
        """The two ``scope.public``/``scope.public_header_dirs`` keys are
        unrelated: setting one never implies or clears the other."""
        cfg = BuildConfig.from_dict(
            {"scope": {"public": False, "public_header_dirs": ["include"]}}
        )
        assert cfg.scope_public is False
        assert cfg.public_header_dirs == ["include"]

    def test_round_trips_through_to_dict(self) -> None:
        cfg = BuildConfig.from_dict({"scope": {"public_header_dirs": ["include"]}})
        assert BuildConfig.from_dict(cfg.to_dict()).public_header_dirs == ["include"]

    def test_omitted_from_to_dict_when_empty(self) -> None:
        cfg = BuildConfig.from_dict({})
        assert "public_header_dirs" not in cfg.to_dict().get("scope", {})


class TestBuildConfigBundleBlock:
    """CLI cleanup phase two, PR J: `bundle.system_providers`/`bundle.cohorts`
    are the sole source for both settings now, read by three independent
    consumers (compare's fan-out, scan --artifact-set, stored-BundleFacts
    compare) -- each must see the identical, normalized list (Codex review,
    fresh evidence: a quoted entry with stray whitespace matched one
    consumer's exact-string SONAME comparison, via compare's own incidental
    comma-join/split/strip round trip, while silently missing the other two,
    which forwarded the raw config value unchanged)."""

    def test_entries_are_stripped_once_at_the_source(self) -> None:
        cfg = BuildConfig.from_dict(
            {
                "bundle": {
                    "system_providers": [" libvendor.so.1 ", "libcuda.so.1"],
                    "cohorts": [" libfoo_ "],
                },
            }
        )
        assert cfg.bundle_system_providers == ["libvendor.so.1", "libcuda.so.1"]
        assert cfg.bundle_cohorts == ["libfoo_"]

    def test_whitespace_only_entries_are_dropped(self) -> None:
        cfg = BuildConfig.from_dict(
            {"bundle": {"system_providers": ["libvendor.so.1", "   "]}}
        )
        assert cfg.bundle_system_providers == ["libvendor.so.1"]

    def test_cohorts_whitespace_only_entries_are_dropped(self) -> None:
        # Symmetric with the system_providers case above -- cohorts goes
        # through the identical strip-and-filter comprehension.
        cfg = BuildConfig.from_dict({"bundle": {"cohorts": ["libfoo_", "   "]}})
        assert cfg.bundle_cohorts == ["libfoo_"]

    def test_to_dict_round_trips_the_bundle_block(self) -> None:
        # Codecov patch-coverage gap: _bundle_block() (to_dict()'s own
        # serialization half) had no test at all.
        cfg = BuildConfig.from_dict(
            {"bundle": {"system_providers": ["libvendor.so.1"], "cohorts": ["libfoo_"]}}
        )
        assert cfg.to_dict()["bundle"] == {
            "system_providers": ["libvendor.so.1"],
            "cohorts": ["libfoo_"],
        }

    def test_to_dict_omits_bundle_block_when_empty(self) -> None:
        assert "bundle" not in BuildConfig().to_dict()


class TestBuildConfigPhase7dBlocks:
    """Codecov patch-coverage gap: Phase 7d (one-comparison-product.md
    §4.1) added `gate:`/`release:` and `scope.on_incomplete` to `BuildConfig`
    but `to_dict()`'s own serialization half (`_gate_block()`/
    `_release_block()`, and the `scope["on_incomplete"] = ...` line) had no
    test at all -- mirrors `TestBuildConfigBundleBlock`'s own identical gap
    and fix for the `bundle:` block."""

    def test_to_dict_round_trips_the_gate_block(self) -> None:
        cfg = BuildConfig.from_dict({"gate": {"fail_on_removed_library": True}})
        assert cfg.to_dict()["gate"] == {"fail_on_removed_library": True}
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_to_dict_omits_gate_block_when_empty(self) -> None:
        assert "gate" not in BuildConfig().to_dict()

    def test_to_dict_round_trips_the_release_block(self) -> None:
        cfg = BuildConfig.from_dict(
            {"release": {"dso_only": True, "include_private_dso": True}}
        )
        assert cfg.to_dict()["release"] == {
            "dso_only": True,
            "include_private_dso": True,
        }
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_to_dict_omits_release_block_when_empty(self) -> None:
        assert "release" not in BuildConfig().to_dict()

    def test_to_dict_round_trips_scope_on_incomplete(self) -> None:
        cfg = BuildConfig.from_dict({"scope": {"on_incomplete": "block"}})
        assert cfg.to_dict()["scope"] == {"on_incomplete": "block"}
        assert BuildConfig.from_dict(cfg.to_dict()) == cfg

    def test_to_dict_omits_scope_on_incomplete_when_unset(self) -> None:
        assert "on_incomplete" not in BuildConfig().to_dict().get("scope", {})


# ── end-to-end: a bad .abicheck.yml exits 64 through a real command ─────────


class TestBadConfigExitsUsageError:
    def test_compare_with_bad_config_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text(
            "severity:\n  preset: strict\n  bogus: 1\n", encoding="utf-8"
        )
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)
        combined = result.output + (result.stderr or "")
        assert "bogus" in combined

    def test_compare_with_unknown_top_level_key_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text("not_a_real_key: 1\n", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        combined = result.output + (result.stderr or "")
        assert "not_a_real_key" in combined

    def test_compare_with_wrong_type_value_exits_64(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        bad_cfg = tmp_path / "bad.yml"
        bad_cfg.write_text('version: "1"\n', encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(bad_cfg)]
        )
        assert result.exit_code == 64, result.output
        combined = result.output + (result.stderr or "")
        assert "version" in combined

    def test_compare_with_good_config_still_works(self, tmp_path: Path) -> None:
        old_p, new_p = _identical_pair(tmp_path)
        good_cfg = tmp_path / "good.yml"
        good_cfg.write_text("severity:\n  preset: strict\n", encoding="utf-8")
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--config", str(good_cfg)]
        )
        assert result.exit_code == 0, result.output


# ── removed commands: no aliases, no deprecation warnings ───────────────────


class TestRemovedCommandsAreGone:
    @pytest.mark.parametrize("cmd", ["init", "config", "doctor"])
    def test_removed_command_is_no_such_command(self, cmd: str) -> None:
        """Same convention as any other unknown command name (e.g. a typo) —
        Click's ordinary "No such command" usage error, remapped to exit 64
        by the root group (see abicheck/cli.py's _AbicheckGroup)."""
        baseline = CliRunner().invoke(main, ["definitely-not-a-real-command"])
        result = CliRunner().invoke(main, [cmd])
        assert result.exit_code == baseline.exit_code == 64
        assert "No such command" in result.output
        assert cmd in result.output

    def test_no_config_group_no_init_no_doctor_in_command_list(self) -> None:
        assert "init" not in main.commands
        assert "config" not in main.commands
        assert "doctor" not in main.commands


# ── build_config_schema.py: opt_int/int_subkey_findings direct coverage ─────


class TestIntSubkeyHelpers:
    """Direct unit coverage for `build_config_schema.opt_int`/
    `int_subkey_findings` (Phase 7g) -- both are exercised indirectly
    through `BuildConfig.from_dict`/`_validate_structure` in
    tests/test_config_rebalance.py, but `_validate_structure` rejects a
    bool value before `opt_int` ever sees one on that path, so its own
    bool-rejection branch (matching `_opt_bool`/`_opt_str`'s identical
    defensive shape) needs a direct call to reach."""

    def test_opt_int_rejects_bool(self) -> None:
        from abicheck.buildsource.build_config_schema import opt_int

        assert opt_int({"n": True}, "n") is None
        assert opt_int({"n": False}, "n") is None

    def test_opt_int_accepts_real_int(self) -> None:
        from abicheck.buildsource.build_config_schema import opt_int

        assert opt_int({"n": 5}, "n") == 5
        assert opt_int({}, "n") is None

    def test_int_subkey_findings_rejects_bool(self) -> None:
        from abicheck.buildsource.build_config_schema import int_subkey_findings

        findings = int_subkey_findings(
            "resource_limits", "max_bundle_facts_decode_nodes", True
        )
        assert findings and "must be an integer" in findings[0]

    def test_int_subkey_findings_unregistered_subkey_is_silent(self) -> None:
        from abicheck.buildsource.build_config_schema import int_subkey_findings

        assert int_subkey_findings("scope", "public", "not-an-int") == []

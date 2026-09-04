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

"""ADR-049 Phase 1: what the resolution *receipt* records.

Split from ``test_compatibility_evaluation_frontend.py`` (which covers which
*value* each field resolves to) once that file passed the AI-readiness
soft size limit. The concern here is D6/D7 replay: every entry must name the
input a replay would actually set, carry the digest of the content that
produced it, and credit only sources that really contributed.
"""

from __future__ import annotations

import hashlib

from _compat_eval_fixtures import (
    policy_file as _policy_file,
    resolve as _resolve,
    write_pack as _write_pack,
)

from abicheck.api_types import CompareRequest, InputSpec
from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_frontend import (
    CONTRACT_MODE_FIELD,
    EXPLICIT_SCOPE_FIELD,
    GATE_PRESET_FIELD,
    INTERNAL_NAMESPACES_FIELD,
    POLICY_BASE_FIELD,
    POLICY_OVERRIDES_FIELD,
    SUPPRESSIONS_FIELD,
    ExplicitCompatibilityInputs,
    FrontEnd,
    ProjectCompatibilityInputs,
    PublicSymbolsList,
    SuppressionSource,
    compare_cli_inputs,
    compatibility_config_from_compare_request,
    cross_front_end_differences,
)
from abicheck.contract_relevance_types import SelectorLayer
from abicheck.policy_file import PolicyFile
from abicheck.suppression import SuppressionList


class TestReplayProvenance:
    """ADR-049 D6: a file-derived receipt entry identifies the exact content."""

    def test_policy_file_derived_entries_carry_the_file_digest(self, tmp_path):
        pf = _policy_file(
            tmp_path,
            """\
            base_policy: sdk_vendor
            internal_namespaces:
              - detail
            overrides:
              soname_changed: warn
            """,
        )
        expected = pf.source_sha256
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        assert expected is not None and len(expected) == 64
        for field_name in (
            POLICY_BASE_FIELD,
            POLICY_OVERRIDES_FIELD,
            INTERNAL_NAMESPACES_FIELD,
        ):
            assert cfg.provenance[field_name].sha256 == expected, field_name

    def test_the_digest_identifies_what_was_parsed_not_what_is_on_disk_now(
        self, tmp_path
    ):
        # The digest is captured by PolicyFile.load over the bytes it parsed.
        # Editing the file afterwards must NOT change what this run's receipt
        # records: re-reading would name content the run never applied.
        pf = _policy_file(tmp_path, "base_policy: sdk_vendor\n")
        before = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        (tmp_path / "policy.yml").write_text("base_policy: plugin_abi\n")
        after = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        assert (
            before.provenance[POLICY_BASE_FIELD].sha256
            == after.provenance[POLICY_BASE_FIELD].sha256
        )
        assert after.policy.base.id == "sdk_vendor"

    def test_reloading_an_edited_policy_file_does_change_the_digest(self, tmp_path):
        pf = _policy_file(tmp_path, "base_policy: sdk_vendor\n")
        before = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        reloaded = _policy_file(tmp_path, "base_policy: plugin_abi\n")
        after = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=reloaded))
        assert (
            before.provenance[POLICY_BASE_FIELD].sha256
            != after.provenance[POLICY_BASE_FIELD].sha256
        )

    def test_a_caller_supplied_digest_is_used_verbatim(self, tmp_path):
        pf = _policy_file(tmp_path, "base_policy: sdk_vendor\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(policy_file=pf, policy_file_sha256="ab" * 32)
        )
        assert cfg.provenance[POLICY_BASE_FIELD].sha256 == "ab" * 32

    def test_a_directly_constructed_policy_file_records_no_digest(self):
        # No parse, no digest to claim -- the receipt says so rather than
        # inventing one by reading whatever path it was handed.
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=PolicyFile()))
        assert cfg.provenance[POLICY_BASE_FIELD].sha256 is None

    def test_a_pack_filled_field_names_the_manifest_that_filled_it(self, tmp_path):
        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="rust_c_ffi",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        prov = cfg.provenance["contract.unresolved"]
        assert prov.reference == "rust_c_ffi"
        assert prov.version == 1
        assert prov.path == str(pack)
        assert prov.sha256 == hashlib.sha256(pack.read_bytes()).hexdigest()
        assert prov.field_location == "contract.unresolved"

    def test_the_recorded_pack_does_not_depend_on_path_order(self, tmp_path):
        # Two packs agreeing on a field are equals; which one the receipt
        # names must still not depend on the order they were passed in.
        first = _write_pack(
            tmp_path / "a.yml",
            pack_id="a",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        second = _write_pack(
            tmp_path / "b.yml",
            pack_id="b",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        forward = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(first), str(second)))
        )
        reverse = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(second), str(first)))
        )
        assert forward == reverse
        assert forward.provenance["contract.unresolved"].reference == "a"


class TestOverridesContributors:
    """`policy.overrides` is composed; the receipt lists real contributors only."""

    def test_a_non_policy_pack_is_not_credited(self, tmp_path):
        gate_pack = _write_pack(
            tmp_path / "g.yml",
            pack_id="g",
            kind="gate",
            assignments="gate.severity.addition: error\n",
        )
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: warn
            """,
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, pack_paths=(str(gate_pack),)
            )
        )
        options = {e.option for e in cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by}
        assert options == {"--policy"}

    def test_a_fully_shadowed_policy_pack_is_not_credited(self, tmp_path):
        pack = _write_pack(
            tmp_path / "p.yml",
            pack_id="p",
            kind="policy",
            assignments="soname_changed: break\n",
        )
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: ignore
            """,
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, pack_paths=(str(pack),)
            )
        )
        assert cfg.policy.overrides["soname_changed"] is Verdict.COMPATIBLE
        options = {e.option for e in cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by}
        assert options == {"--policy"}

    def test_a_contributing_policy_pack_is_credited(self, tmp_path):
        pack = _write_pack(
            tmp_path / "p.yml",
            pack_id="p",
            kind="policy",
            assignments="func_added: warn\n",
        )
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: ignore
            """,
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, pack_paths=(str(pack),)
            )
        )
        options = {e.option for e in cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by}
        assert options == {"--policy", "--pack"}


class TestPathInputsAreNotSilentlyIgnored:
    """A path an invocation named must reach the resolved configuration."""

    def test_public_symbols_list_alone_resolves_a_real_explicit_scope(self, tmp_path):
        listed = tmp_path / "symbols.txt"
        listed.write_text("# comment\n\nfrom_file\n")
        cfg = _resolve(
            explicit=compare_cli_inputs({"public_symbols_list": listed})
        )
        assert cfg.surface.explicit_scope is not None
        assert cfg.surface.explicit_scope.items == ("from_file",)

    def test_inline_symbols_and_the_list_file_merge(self, tmp_path):
        listed = tmp_path / "symbols.txt"
        listed.write_text("from_file\n")
        cfg = _resolve(
            explicit=compare_cli_inputs(
                {"public_symbols": ("inline",), "public_symbols_list": listed}
            )
        )
        assert cfg.surface.explicit_scope is not None
        assert cfg.surface.explicit_scope.items == ("from_file", "inline")

    def test_the_two_symbol_sources_stay_separately_attributed(self, tmp_path):
        # Flattening them lost which symbols came from the file, leaving a
        # receipt that named only --public-symbol and no path.
        listed = tmp_path / "symbols.txt"
        listed.write_text("from_file\n")
        inputs = compare_cli_inputs(
            {"public_symbols": ("inline",), "public_symbols_list": listed}
        )
        assert inputs.public_symbols == ("inline",)
        assert inputs.public_symbols_list is not None
        assert inputs.public_symbols_list.items == ("from_file",)

        cfg = _resolve(explicit=inputs)
        entries = {
            (e.option, e.path) for e in cfg.provenance[EXPLICIT_SCOPE_FIELD].selected_by
        }
        # Both spell the *config* key at the CLI layer: --public-symbol and
        # --public-symbols-list were hidden duplicates of scope.public_symbols
        # and were removed, so a CLI-front-end value can only have come from
        # .abicheck.yml and a receipt naming a flag would send a replay to an
        # unknown option. What still separates the two sources is `path`, not
        # `option` -- which is the property this test is actually about.
        assert entries == {
            ("scope.public_symbols", None),
            ("scope.public_symbols", str(listed)),
        }

    def test_a_list_only_invocation_names_the_file_in_its_receipt(self, tmp_path):
        listed = tmp_path / "symbols.txt"
        listed.write_text("from_file\n")
        cfg = _resolve(explicit=compare_cli_inputs({"public_symbols_list": listed}))
        selected_by = cfg.provenance[EXPLICIT_SCOPE_FIELD].selected_by
        assert [(e.option, e.path) for e in selected_by] == [
            ("scope.public_symbols", str(listed))
        ]

    def test_cli_policy_file_path_is_loaded_when_not_pre_supplied(self, tmp_path):
        path = tmp_path / "policy.yml"
        path.write_text("base_policy: sdk_vendor\n")
        cfg = _resolve(explicit=compare_cli_inputs({"policy_file_path": path}))
        assert cfg.policy.base.id == "sdk_vendor"

    def test_a_pre_supplied_policy_file_is_not_re_read(self, tmp_path):
        pf = _policy_file(tmp_path, "base_policy: plugin_abi\n")
        cfg = _resolve(
            explicit=compare_cli_inputs(
                {"policy_file_path": tmp_path / "other.yml"}, policy_file=pf
            )
        )
        assert cfg.policy.base.id == "plugin_abi"

    def test_cli_suppress_path_is_read_when_not_pre_supplied(self, tmp_path):
        path = tmp_path / "s.yml"
        path.write_text("version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n")
        cfg = _resolve(explicit=compare_cli_inputs({"suppress": path}))
        assert cfg.suppressions is not None
        assert cfg.suppressions.rules == ("symbol='foo'",)

    def test_request_policy_and_suppression_paths_are_honoured(self, tmp_path):
        policy = tmp_path / "policy.yml"
        policy.write_text("base_policy: sdk_vendor\n")
        suppress = tmp_path / "s.yml"
        suppress.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n"
        )
        request = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            policy_file_path=policy,
            suppress=suppress,
        )
        cfg = compatibility_config_from_compare_request(request)
        # Previously both request fields were ignored unless the caller
        # separately re-loaded the same files.
        assert cfg.policy.base.id == "sdk_vendor"
        assert cfg.suppressions is not None
        assert cfg.suppressions.rules == ("symbol='foo'",)


class TestReceiptMetadataIsCompared:
    def test_a_differing_source_digest_is_a_difference(self, tmp_path):
        pf = _policy_file(tmp_path, "base_policy: sdk_vendor\n")
        one = _resolve(explicit=ExplicitCompatibilityInputs(policy_file=pf))
        two = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, policy_file_sha256="cd" * 32
            )
        )
        # Same resolved base, different recorded digest: not equivalent.
        assert one.policy == two.policy
        differences = cross_front_end_differences(one, two)
        assert any(POLICY_BASE_FIELD in d for d in differences)

    def test_a_differing_selected_by_path_is_a_difference(self, tmp_path):
        first = _write_pack(
            tmp_path / "a.yml",
            pack_id="p",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        copied = tmp_path / "copy.yml"
        copied.write_text(first.read_text())
        a = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(first),)))
        b = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(copied),)))
        assert a.contract == b.contract
        assert cross_front_end_differences(a, b)

    def test_the_option_spelling_alone_is_still_permitted_to_differ(self, tmp_path):
        # The genuine cross-front-end case the normalization exists for.
        policy = tmp_path / "policy.yml"
        policy.write_text("base_policy: sdk_vendor\n")
        cli = _resolve(
            front_end=FrontEnd.CLI,
            explicit=compare_cli_inputs(
                {
                    "policy_file_path": policy,
                    "policy": "strict_abi",
                    # A typed request has no "unset" for scope_public, so the
                    # equivalent CLI run has to state the flag too.
                    "scope_public_headers": True,
                },
                explicit_parameters={"policy", "scope_public_headers"},
            ),
        )
        api = compatibility_config_from_compare_request(
            CompareRequest(
                old=InputSpec(path=tmp_path / "old.so"),
                new=InputSpec(path=tmp_path / "new.so"),
                policy_file_path=policy,
                policy="strict_abi",
            )
        )
        assert cross_front_end_differences(cli, api) == []


class TestProjectConfigProvenanceNamesItsSource:
    def test_contract_mode_from_a_project_config_names_the_key_and_file(self, tmp_path):
        # A PROJECT_CONFIG receipt claiming `--scope-public-headers`, with no
        # path, cannot identify what a replay would have to re-read.
        cfg = _resolve(
            project=ProjectCompatibilityInputs(
                path=str(tmp_path / ".abicheck.yml"), scope_public=False
            )
        )
        prov = cfg.provenance[CONTRACT_MODE_FIELD]
        assert prov.layer is SelectorLayer.PROJECT_CONFIG
        assert prov.path == str(tmp_path / ".abicheck.yml")
        assert [(e.option, e.path) for e in prov.selected_by] == [
            ("scope.public", str(tmp_path / ".abicheck.yml"))
        ]

    def test_the_cli_flag_still_records_its_own_spelling(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(scope_public_headers=False)
        )
        prov = cfg.provenance[CONTRACT_MODE_FIELD]
        assert prov.layer is SelectorLayer.LEGACY_ALIAS
        assert prov.path is None
        assert [e.option for e in prov.selected_by] == ["--no-scope-public-headers"]


class TestManifestsAreReadOncePerResolution:
    def test_each_manifest_is_loaded_exactly_once(self, tmp_path, monkeypatch):
        # Re-reading per resolver call is wasteful, and a manifest edited
        # between those reads would leave one configuration's receipt entries
        # carrying different identities for the same pack.
        import abicheck.compatibility_evaluation_wiring as wiring

        pack = _write_pack(
            tmp_path / "c.yml",
            pack_id="p",
            kind="contract",
            assignments="contract.unresolved: warn\n",
        )
        reads: list[str] = []
        real = wiring.load_pack_manifest

        def _counting(path):
            reads.append(str(path))
            return real(path)

        monkeypatch.setattr(wiring, "load_pack_manifest", _counting)
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        assert cfg.contract.unresolved == "warn"
        assert reads == [str(pack)]

    def test_the_suppression_receipt_carries_its_source_digest(self, tmp_path):
        path = tmp_path / "s.yml"
        path.write_text("version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n")
        source = SuppressionSource.from_file(path)
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(suppression=source))
        assert cfg.provenance[SUPPRESSIONS_FIELD].sha256 == source.sha256


class TestOverridesReceiptNamesEachContributingPack:
    def _pack(self, tmp_path, name, assignments):
        return _write_pack(
            tmp_path / f"{name}.yml",
            pack_id=name,
            kind="policy",
            assignments=assignments,
        )

    def test_each_contributing_pack_gets_its_own_entry_with_its_path(self, tmp_path):
        first = self._pack(tmp_path, "a", "soname_changed: break\n")
        second = self._pack(tmp_path, "b", "func_added: warn\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                pack_paths=(str(first), str(second))
            )
        )
        entries = [
            (e.option, e.path)
            for e in cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by
        ]
        assert entries == [("--pack", str(first)), ("--pack", str(second))]

    def test_each_contributing_pack_hop_carries_its_own_identity(self, tmp_path):
        # With two contributors there is no single winning manifest for the
        # entry-level reference/version/sha256 to name, so the identities have
        # to travel with the hops or a later edit to one of them cannot be
        # proved against this receipt.
        first = self._pack(tmp_path, "a", "soname_changed: break\n")
        second = self._pack(tmp_path, "b", "func_added: warn\n")
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                pack_paths=(str(first), str(second))
            )
        )
        hops = cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by
        assert [(e.path, e.identity.id) for e in hops] == [
            (str(first), "a"),
            (str(second), "b"),
        ]
        assert [e.identity.sha256 for e in hops] == [
            hashlib.sha256(p.read_bytes()).hexdigest() for p in (first, second)
        ]

    def test_the_packs_receipt_pairs_each_path_with_its_identity(self, tmp_path):
        pack = self._pack(tmp_path, "a", "soname_changed: break\n")
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        (hop,) = cfg.provenance["policy.packs"].selected_by
        assert hop.path == str(pack)
        assert hop.identity is not None
        assert hop.identity.id == "a"

    def test_a_single_contributor_still_names_its_manifest_outright(self, tmp_path):
        pack = self._pack(tmp_path, "a", "soname_changed: break\n")
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)))
        prov = cfg.provenance[POLICY_OVERRIDES_FIELD]
        assert prov.reference == "a"
        assert prov.version == 1
        assert prov.sha256 == hashlib.sha256(pack.read_bytes()).hexdigest()

    def test_a_fully_shadowed_pack_is_not_listed(self, tmp_path):
        shadowed = self._pack(tmp_path, "a", "soname_changed: break\n")
        contributing = self._pack(tmp_path, "b", "func_added: warn\n")
        pf = _policy_file(
            tmp_path,
            """\
            overrides:
              soname_changed: ignore
            """,
        )
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                policy_file=pf, pack_paths=(str(shadowed), str(contributing))
            )
        )
        entries = [
            (e.option, e.path)
            for e in cfg.provenance[POLICY_OVERRIDES_FIELD].selected_by
        ]
        assert entries == [
            ("--policy", str(tmp_path / "policy.yml")),
            ("--pack", str(contributing)),
        ]

    def test_a_lone_pack_source_is_named_outright(self, tmp_path):
        only = self._pack(tmp_path, "a", "func_added: warn\n")
        prov = _resolve(
            explicit=ExplicitCompatibilityInputs(pack_paths=(str(only),))
        ).provenance[POLICY_OVERRIDES_FIELD]
        assert prov.reference == "a"
        assert prov.version == 1
        assert prov.path == str(only)
        assert prov.sha256 == hashlib.sha256(only.read_bytes()).hexdigest()

    def test_several_pack_sources_claim_no_single_manifest(self, tmp_path):
        first = self._pack(tmp_path, "a", "soname_changed: break\n")
        second = self._pack(tmp_path, "b", "func_added: warn\n")
        prov = _resolve(
            explicit=ExplicitCompatibilityInputs(
                pack_paths=(str(first), str(second))
            )
        ).provenance[POLICY_OVERRIDES_FIELD]
        # No one manifest supplied the whole value, so none is named as *the*
        # source -- every contributor is in selected_by instead.
        assert prov.path is None
        assert prov.reference is None
        assert len(prov.selected_by) == 2


class TestSelectedButEmptySources:
    def test_an_empty_symbols_list_is_still_a_selected_source(self, tmp_path):
        # "A source was selected and resolved to zero items" is a different
        # fact from "no source was selected" -- and the path must survive.
        listed = tmp_path / "symbols.txt"
        listed.write_text("# only a comment\n\n")
        cfg = _resolve(explicit=compare_cli_inputs({"public_symbols_list": listed}))
        assert cfg.surface.explicit_scope is not None
        assert cfg.surface.explicit_scope.items == ()
        prov = cfg.provenance[EXPLICIT_SCOPE_FIELD]
        assert prov.layer is SelectorLayer.EXPLICIT_CLI
        assert [(e.option, e.path) for e in prov.selected_by] == [
            ("scope.public_symbols", str(listed))
        ]

    def test_no_source_at_all_stays_none(self):
        cfg = _resolve()
        assert cfg.surface.explicit_scope is None
        assert (
            cfg.provenance[EXPLICIT_SCOPE_FIELD].layer
            is SelectorLayer.BUILT_IN_DEFAULT
        )


class TestProjectDerivedFieldsNameTheirConfigDigest:
    """A caller that supplied the config's digest gets it back per field."""

    DIGEST = "c" * 64

    def _project(self, **kwargs):
        return ProjectCompatibilityInputs(
            path=".abicheck.yml", sha256=self.DIGEST, **kwargs
        )

    def test_the_legacy_scope_key_carries_it(self):
        cfg = _resolve(project=self._project(scope_public=False))
        prov = cfg.provenance[CONTRACT_MODE_FIELD]
        assert prov.sha256 == self.DIGEST
        assert [e.sha256 for e in prov.selected_by] == [self.DIGEST]

    def test_severity_and_exit_scheme_keys_carry_it(self):
        # gate.exit_code_scheme itself is purely derived (PR G2 deleted
        # ProjectCompatibilityInputs.exit_code_scheme along with the
        # exit_code_scheme: config key) and carries no field_provenance
        # entry any more -- only the severity field this project config
        # actually stated is checked here.
        cfg = _resolve(project=self._project(severity_addition="info"))
        prov = cfg.provenance["gate.severity.addition"]
        assert prov.sha256 == self.DIGEST
        assert [e.sha256 for e in prov.selected_by] == [self.DIGEST]
        assert "gate.exit_code_scheme" not in cfg.provenance

    def test_a_preset_keeps_its_own_identity_while_the_hop_names_the_file(self):
        # Two different artifacts: the value is the built-in preset, but the
        # file that selected it is what a replay has to re-read.
        cfg = _resolve(project=self._project(severity_preset="strict"))
        prov = cfg.provenance[GATE_PRESET_FIELD]
        assert prov.sha256 == cfg.gate.preset.sha256 != self.DIGEST
        assert [(e.option, e.sha256) for e in prov.selected_by] == [
            ("severity.preset", self.DIGEST)
        ]

    def test_the_symbol_overlay_hop_carries_it(self):
        cfg = _resolve(project=self._project(public_symbols=("foo",)))
        assert [
            (e.option, e.sha256)
            for e in cfg.provenance[EXPLICIT_SCOPE_FIELD].selected_by
        ] == [("scope.public_symbols", self.DIGEST)]

    def test_a_list_file_hop_names_its_own_digest(self, tmp_path):
        path = tmp_path / "symbols.txt"
        path.write_text("foo\n")
        listed = PublicSymbolsList.from_file(path)
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(public_symbols_list=listed)
        )
        assert [
            (e.option, e.sha256)
            for e in cfg.provenance[EXPLICIT_SCOPE_FIELD].selected_by
        ] == [("scope.public_symbols", listed.sha256)]


class TestExplicitScopeIdentityCoversItsSources:
    def _scope(self, path):
        return _resolve(
            explicit=ExplicitCompatibilityInputs(
                public_symbols_list=PublicSymbolsList.from_file(path)
            )
        ).surface.explicit_scope

    def test_an_edit_that_leaves_the_items_identical_still_changes_the_digest(
        self, tmp_path
    ):
        # Reading drops comments and blank lines and the union sorts and
        # deduplicates, so the items alone cannot stand in for the file.
        path = tmp_path / "symbols.txt"
        path.write_text("foo\nbar\n")
        before = self._scope(path)
        path.write_text("# the public surface\nbar\nfoo\nfoo\n")
        after = self._scope(path)
        assert before.items == after.items == ("bar", "foo")
        assert before.sha256 != after.sha256

    def test_the_list_files_digest_is_over_its_raw_bytes(self, tmp_path):
        path = tmp_path / "symbols.txt"
        path.write_bytes(b"foo\r\nbar\r\n")
        listed = PublicSymbolsList.from_file(path)
        assert listed.items == ("foo", "bar")
        assert listed.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    def test_a_project_sources_digest_reaches_the_identity(self, tmp_path):
        one, two = (
            _resolve(
                project=ProjectCompatibilityInputs(
                    public_symbols=("foo",), path=str(tmp_path / "p.yml"), sha256=d
                )
            ).surface.explicit_scope
            for d in ("a" * 64, "b" * 64)
        )
        assert one.items == two.items
        assert one.sha256 != two.sha256

    def test_inline_symbols_alone_digest_reproducibly(self):
        # No external source to drift: the same typed values must resolve to
        # the same identity every time.
        first = _resolve(explicit=ExplicitCompatibilityInputs(public_symbols=("foo",)))
        second = _resolve(explicit=ExplicitCompatibilityInputs(public_symbols=("foo",)))
        assert (
            first.surface.explicit_scope.sha256 == second.surface.explicit_scope.sha256
        )


class TestPresetDerivedCategoriesNameTheirPreset:
    def test_a_derived_level_is_attributed_to_the_cli_preset(self):
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(severity_preset="strict"))
        prov = cfg.provenance["gate.severity.addition"]
        assert prov.layer is SelectorLayer.EXPLICIT_CLI
        assert prov.source_kind == "severity_preset"
        assert prov.reference == "strict"
        # The exact preset revision, so a replay can tell it apart from a
        # future preset of the same name.
        assert prov.sha256 == cfg.gate.preset.sha256
        assert [e.option for e in prov.selected_by] == ["--severity-preset"]

    def test_a_derived_level_is_attributed_to_the_project_preset(self, tmp_path):
        cfg = _resolve(
            project=ProjectCompatibilityInputs(
                severity_preset="strict", path=str(tmp_path / ".abicheck.yml")
            )
        )
        prov = cfg.provenance["gate.severity.abi_breaking"]
        assert prov.layer is SelectorLayer.PROJECT_CONFIG
        assert [(e.option, e.path) for e in prov.selected_by] == [
            ("severity.preset", str(tmp_path / ".abicheck.yml"))
        ]

    def test_a_per_category_flag_keeps_its_own_receipt(self):
        cfg = _resolve(
            explicit=ExplicitCompatibilityInputs(
                severity_preset="strict", severity_addition="info"
            )
        )
        prov = cfg.provenance["gate.severity.addition"]
        assert prov.source_kind == "severity_override"
        assert [e.option for e in prov.selected_by] == ["--severity-addition"]
        # The categories the flag did not refine still name the preset.
        assert (
            cfg.provenance["gate.severity.abi_breaking"].source_kind
            == "severity_preset"
        )

    def test_without_a_preset_a_category_is_still_a_built_in_default(self):
        cfg = _resolve()
        prov = cfg.provenance["gate.severity.addition"]
        assert prov.layer is SelectorLayer.BUILT_IN_DEFAULT
        assert prov.selected_by == ()


class TestPolicyDigestIsOverRawBytes:
    def test_line_ending_only_changes_are_detected(self, tmp_path):
        # `read_text()` translates newlines, so hashing its result would give
        # a CRLF file and its LF twin the same digest.
        path = tmp_path / "policy.yml"
        path.write_bytes(b"base_policy: sdk_vendor\r\n")
        crlf = PolicyFile.load(path)
        path.write_bytes(b"base_policy: sdk_vendor\n")
        lf = PolicyFile.load(path)
        assert crlf.base_policy == lf.base_policy == "sdk_vendor"
        assert crlf.source_sha256 != lf.source_sha256

    def test_the_digest_matches_the_files_raw_bytes(self, tmp_path):
        path = tmp_path / "policy.yml"
        path.write_bytes(b"base_policy: plugin_abi\r\n")
        loaded = PolicyFile.load(path)
        assert loaded.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


class TestReceiptNamesTheStatingFrontEnd:
    """Every explicit entry names the input a replay would actually set."""

    def _api(self, tmp_path, **kwargs):
        return compatibility_config_from_compare_request(
            CompareRequest(
                old=InputSpec(path=tmp_path / "old.so"),
                new=InputSpec(path=tmp_path / "new.so"),
                **kwargs,
            )
        )

    def test_api_scope_selection_names_the_request_field(self, tmp_path):
        # `cross_front_end_differences` normalizes option spellings on
        # purpose, so it cannot catch this — the receipt has to be right by
        # itself.
        cfg = self._api(tmp_path, scope_public=False)
        prov = cfg.provenance[CONTRACT_MODE_FIELD]
        assert [e.option for e in prov.selected_by] == ["scope_public"]

    def test_api_policy_and_contract_name_their_request_fields(self, tmp_path):
        policy = tmp_path / "policy.yml"
        policy.write_text("base_policy: sdk_vendor\ninternal_namespaces: [detail]\n")
        cfg = self._api(
            tmp_path,
            policy="plugin_abi",
            policy_file_path=policy,
            contract_mode="all",
            contract_evaluation=True,
            force_public_symbols=frozenset({"sym"}),
        )
        assert [
            e.option for e in cfg.provenance[CONTRACT_MODE_FIELD].selected_by
        ] == ["contract_mode"]
        assert [e.option for e in cfg.provenance[POLICY_BASE_FIELD].selected_by] == [
            "policy_file_path"
        ]
        assert (
            cfg.provenance[POLICY_BASE_FIELD].shadowed_legacy.selected_by[0].option
            == "policy"
        )
        assert [
            e.option for e in cfg.provenance[INTERNAL_NAMESPACES_FIELD].selected_by
        ] == ["policy_file_path"]
        assert [
            e.option for e in cfg.provenance[EXPLICIT_SCOPE_FIELD].selected_by
        ] == ["force_public_symbols"]

    def test_api_suppression_names_the_request_field(self, tmp_path):
        suppress = tmp_path / "s.yml"
        suppress.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    reason: ok\n"
        )
        cfg = self._api(tmp_path, suppress=suppress)
        assert [
            e.option for e in cfg.provenance[SUPPRESSIONS_FIELD].selected_by
        ] == ["suppress"]

    def test_the_cli_still_names_its_own_flags(self, tmp_path):
        policy = tmp_path / "policy.yml"
        policy.write_text("base_policy: sdk_vendor\n")
        cfg = _resolve(
            explicit=compare_cli_inputs(
                {"policy_file_path": policy, "scope_public_headers": False},
                explicit_parameters={"scope_public_headers"},
            )
        )
        assert [
            e.option for e in cfg.provenance[CONTRACT_MODE_FIELD].selected_by
        ] == ["--no-scope-public-headers"]
        assert [e.option for e in cfg.provenance[POLICY_BASE_FIELD].selected_by] == [
            "--policy"
        ]


class TestSuppressionDigestAndRulesShareOneRead:
    def test_the_digest_comes_from_the_read_that_produced_the_rules(self, tmp_path):
        path = tmp_path / "s.yml"
        path.write_bytes(b"version: 1\r\nsuppressions:\r\n  - symbol: foo\r\n")
        source = SuppressionSource.from_file(path)
        assert source.rules == ("symbol='foo'",)
        # Raw bytes, so a CRLF file does not digest as its LF twin.
        assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    def test_an_empty_document_keeps_its_digest(self, tmp_path):
        # A file with no `suppressions:` key is a valid, empty rule set, not an
        # absent source: it still has content that can drift between the
        # recorded run and a replay, so D6 wants its digest.
        path = tmp_path / "s.yml"
        path.write_text("version: 1\n")
        loaded = SuppressionList.load(path)
        assert loaded.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    def test_an_empty_document_resolves_instead_of_raising(self, tmp_path):
        # Regression: losing the digest above made this valid source fail the
        # non-empty-digest check and raise out of both front-end adapters.
        path = tmp_path / "s.yml"
        path.write_text("version: 1\n")
        source = SuppressionSource.from_file(path)
        assert source.rules == ()
        cfg = _resolve(explicit=ExplicitCompatibilityInputs(suppression=source))
        assert cfg.suppressions.rules == ()
        assert cfg.provenance[SUPPRESSIONS_FIELD].sha256 == source.sha256

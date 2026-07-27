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

"""ADR-049 Phase 1: tests for the pack-manifest loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.change_registry_types import Verdict
from abicheck.compatibility_evaluation_packs import (
    LoadedPack,
    PackKind,
    assignments_for_conflict_check,
    load_pack_manifest,
)
from abicheck.compatibility_evaluation_resolver import (
    PackConflictError,
    detect_pack_conflicts,
)
from abicheck.errors import PackManifestError


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


class TestLoadPackManifestHappyPath:
    def test_policy_pack_resolves_verdicts(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "rust_c_ffi.yaml",
            """
            id: rust_c_ffi
            version: 1
            kind: policy
            assignments:
              func_removed: break
              func_added: ignore
            """,
        )
        pack = load_pack_manifest(path)
        assert pack.kind is PackKind.POLICY
        assert pack.identity.id == "rust_c_ffi"
        assert pack.identity.version == 1
        assert pack.identity.sha256  # non-empty content digest
        assert pack.assignments == {
            "func_removed": Verdict.BREAKING,
            "func_added": Verdict.COMPATIBLE,
        }

    def test_contract_pack_resolves_raw_field_values(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "ffi.yaml",
            """
            id: ffi_boundary
            version: 2
            kind: contract
            assignments:
              contract.mode: exports
              contract.overlays: [extern_c, ffi_root]
            """,
        )
        pack = load_pack_manifest(path)
        assert pack.kind is PackKind.CONTRACT
        assert pack.assignments == {
            "contract.mode": "exports",
            "contract.overlays": ("extern_c", "ffi_root"),
        }

    def test_gate_pack_resolves_raw_field_values(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "security.yaml",
            """
            id: security_hardening
            version: 1
            kind: gate
            assignments:
              exit_code_scheme: severity
            """,
        )
        pack = load_pack_manifest(path)
        assert pack.kind is PackKind.GATE
        assert pack.assignments == {"exit_code_scheme": "severity"}

    def test_nested_lists_convert_recursively_to_tuples(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "nested.yaml",
            """
            id: nested_lists
            version: 1
            kind: contract
            assignments:
              contract.overlays: [[a, b], [c]]
            """,
        )
        pack = load_pack_manifest(path)
        assert pack.assignments["contract.overlays"] == (("a", "b"), ("c",))

    def test_identity_digest_changes_when_content_changes(self, tmp_path: Path) -> None:
        # ADR-049 D6: exact replay needs the digest to detect content drift,
        # including a change that doesn't alter the resolved assignments.
        p1 = _write(
            tmp_path,
            "a.yaml",
            "id: p\nversion: 1\nkind: gate\nassignments: {exit_code_scheme: severity}\n",
        )
        p2 = _write(
            tmp_path,
            "b.yaml",
            "id: p\nversion: 1\nkind: gate\n"
            "assignments: {exit_code_scheme: severity}\n# a comment\n",
        )
        assert (
            load_pack_manifest(p1).identity.sha256
            != load_pack_manifest(p2).identity.sha256
        )

    def test_assignments_are_immutable(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: gate\nassignments: {exit_code_scheme: severity}\n",
        )
        pack = load_pack_manifest(path)
        with pytest.raises(TypeError):
            pack.assignments["exit_code_scheme"] = "legacy"  # type: ignore[index]

    def test_directly_constructed_pack_freezes_a_mutable_dict(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        mutable = {"exit_code_scheme": "severity"}
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.GATE,
            assignments=mutable,
        )
        mutable["exit_code_scheme"] = "legacy"
        assert pack.assignments["exit_code_scheme"] == "severity"

    def test_directly_constructed_pack_deep_freezes_a_mutable_list_value(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        mutable_list = ["a", "b"]
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"contract.overlays": mutable_list},
        )
        mutable_list.append("c")
        assert pack.assignments["contract.overlays"] == ("a", "b")

    def test_directly_constructed_pack_rejects_unhashable_value(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="not hashable"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"contract.overlays": {1, 2, 3}},
            )


class TestLoadPackManifestValidation:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PackManifestError, match="cannot read"):
            load_pack_manifest(tmp_path / "missing.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "bad.yaml", "id: [unterminated\n")
        with pytest.raises(PackManifestError, match="invalid YAML"):
            load_pack_manifest(path)

    def test_non_mapping_document_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "list.yaml", "- a\n- b\n")
        with pytest.raises(PackManifestError, match="YAML mapping"):
            load_pack_manifest(path)

    @pytest.mark.parametrize("bad_id", ["", "123"])
    def test_missing_or_empty_id_raises(self, tmp_path: Path, bad_id: str) -> None:
        text = f"id: {bad_id!r}\nversion: 1\nkind: gate\nassignments: {{}}\n"
        if bad_id == "123":
            # "123" as a YAML scalar without quotes parses as an int, not a
            # str -- exercise that shape explicitly instead of relying on
            # repr() quoting it.
            text = "id: 123\nversion: 1\nkind: gate\nassignments: {}\n"
        path = _write(tmp_path, "badid.yaml", text)
        with pytest.raises(PackManifestError, match="'id'"):
            load_pack_manifest(path)

    def test_missing_id_key_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "noid.yaml", "version: 1\nkind: gate\nassignments: {}\n"
        )
        with pytest.raises(PackManifestError, match="'id'"):
            load_pack_manifest(path)

    def test_non_int_version_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "v.yaml", "id: p\nversion: '1'\nkind: gate\nassignments: {}\n"
        )
        with pytest.raises(PackManifestError, match="'version'"):
            load_pack_manifest(path)

    def test_bool_version_raises(self, tmp_path: Path) -> None:
        # bool is an int subclass -- must be rejected explicitly, matching
        # ImmutableIdentity's own isinstance(..., bool) guard.
        path = _write(
            tmp_path, "v.yaml", "id: p\nversion: true\nkind: gate\nassignments: {}\n"
        )
        with pytest.raises(PackManifestError, match="'version'"):
            load_pack_manifest(path)

    def test_unknown_kind_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "k.yaml", "id: p\nversion: 1\nkind: nonsense\nassignments: {}\n"
        )
        with pytest.raises(PackManifestError, match="'kind'"):
            load_pack_manifest(path)

    def test_unhashable_kind_raises_pack_manifest_error_not_type_error(
        self, tmp_path: Path
    ) -> None:
        # `kind_raw not in _VALID_PACK_KINDS` hashes kind_raw internally
        # (frozenset membership) -- an unhashable decoded value (a YAML list)
        # must still surface the documented PackManifestError, not a raw
        # TypeError (Codex review).
        path = _write(
            tmp_path, "k.yaml", "id: p\nversion: 1\nkind: [gate]\nassignments: {}\n"
        )
        with pytest.raises(PackManifestError, match="'kind'"):
            load_pack_manifest(path)

    def test_non_mapping_assignments_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "a.yaml", "id: p\nversion: 1\nkind: gate\nassignments: [1, 2]\n"
        )
        with pytest.raises(PackManifestError, match="'assignments'"):
            load_pack_manifest(path)

    def test_unknown_change_kind_slug_in_policy_pack_raises(
        self, tmp_path: Path
    ) -> None:
        # ADR-049 D8: a hard load error, matching --policy-file's behavior --
        # a renamed/typo'd slug must never silently vanish from a pack.
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: policy\n"
            "assignments: {totally_not_a_real_kind: break}\n",
        )
        with pytest.raises(PackManifestError, match="unknown ChangeKind slugs"):
            load_pack_manifest(path)

    def test_invalid_severity_in_policy_pack_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: policy\nassignments: {func_removed: nonsense}\n",
        )
        with pytest.raises(PackManifestError, match="invalid severity"):
            load_pack_manifest(path)

    def test_non_string_policy_slug_key_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: policy\nassignments: {1: break}\n",
        )
        with pytest.raises(PackManifestError, match="ChangeKind slug"):
            load_pack_manifest(path)

    def test_empty_field_name_key_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: contract\nassignments: {'': public}\n",
        )
        with pytest.raises(PackManifestError, match="non-empty str field name"):
            load_pack_manifest(path)

    def test_null_assignment_value_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: contract\nassignments: {contract.mode: null}\n",
        )
        with pytest.raises(PackManifestError, match="must not be null"):
            load_pack_manifest(path)

    def test_nested_mapping_assignment_value_raises(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: contract\n"
            "assignments: {contract.mode: {nested: true}}\n",
        )
        with pytest.raises(PackManifestError, match="nested mappings"):
            load_pack_manifest(path)

    def test_non_utf8_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad_encoding.yaml"
        path.write_bytes(b"id: \xff\xfe bad bytes\n")
        with pytest.raises(PackManifestError, match="not valid UTF-8"):
            load_pack_manifest(path)

    def test_invalid_timestamp_scalar_raises_pack_manifest_error(
        self, tmp_path: Path
    ) -> None:
        # PyYAML's implicit timestamp resolver constructs a real
        # datetime.date for a timestamp-shaped scalar -- an out-of-range
        # value raises a raw ValueError from that stdlib constructor, not
        # yaml.YAMLError, and must not escape this loader's documented
        # PackManifestError contract (Codex review, fresh evidence).
        path = _write(
            tmp_path,
            "badtime.yaml",
            "id: p\nversion: 1\nkind: contract\n"
            "assignments: {contract.mode: 2020-99-99}\n",
        )
        with pytest.raises(PackManifestError, match="invalid YAML scalar"):
            load_pack_manifest(path)

    def test_duplicate_top_level_key_raises(self, tmp_path: Path) -> None:
        # yaml.safe_load alone silently keeps last-value-wins for a repeated
        # mapping key -- a hard-load-error manifest format must not let a
        # duplicated `id`/`kind`/etc. silently pick one value over the other.
        path = _write(
            tmp_path,
            "dup_top.yaml",
            "id: p\nid: q\nversion: 1\nkind: gate\nassignments: {}\n",
        )
        with pytest.raises(PackManifestError, match="duplicate key 'id'"):
            load_pack_manifest(path)

    def test_duplicate_assignment_key_in_policy_pack_raises(
        self, tmp_path: Path
    ) -> None:
        # A repeated ChangeKind slug with contradictory severities must not
        # silently resolve to whichever one YAML happened to parse last --
        # that would silently weaken (or strengthen) the pack's real intent.
        path = _write(
            tmp_path,
            "dup_assignment.yaml",
            "id: p\nversion: 1\nkind: policy\n"
            "assignments:\n  func_removed: break\n  func_removed: ignore\n",
        )
        with pytest.raises(PackManifestError, match="duplicate key 'func_removed'"):
            load_pack_manifest(path)

    def test_unhashable_mapping_key_raises_pack_manifest_error(
        self, tmp_path: Path
    ) -> None:
        # A complex YAML key (`? [a, b] : value`) constructs to an unhashable
        # list -- `key in seen` must not surface a raw TypeError instead of
        # the documented PackManifestError (Codex review).
        path = _write(
            tmp_path,
            "unhashable_key.yaml",
            "id: p\nversion: 1\nkind: gate\n"
            "assignments:\n  ? [a, b]\n  : value\n",
        )
        with pytest.raises(PackManifestError, match="unhashable mapping key"):
            load_pack_manifest(path)

    def test_unknown_top_level_field_raises(self, tmp_path: Path) -> None:
        # A misspelled `assigments:` alongside a well-formed `assignments: {}`
        # must not silently resolve to an empty pack -- every top-level key
        # must be recognized (ADR-049 D7: unknown config keys fail at load
        # time) (Codex review).
        path = _write(
            tmp_path,
            "unknown_field.yaml",
            "id: p\nversion: 1\nkind: gate\nassignments: {}\n"
            "assigments: {exit_code_scheme: legacy}\n",
        )
        with pytest.raises(PackManifestError, match="unknown top-level field"):
            load_pack_manifest(path)

    def test_heterogeneous_unknown_top_level_fields_raise_cleanly(
        self, tmp_path: Path
    ) -> None:
        # Two unknown keys of different types (int `1:` and str `extra:`)
        # must not crash sorting the unknown-key list with a raw TypeError
        # ("'<' not supported between instances of 'int' and 'str'") instead
        # of the documented PackManifestError (Codex review, fresh evidence).
        path = _write(
            tmp_path,
            "mixed_unknown.yaml",
            "id: p\nversion: 1\nkind: gate\nassignments: {}\n1: x\nextra: y\n",
        )
        with pytest.raises(PackManifestError, match="unknown top-level field"):
            load_pack_manifest(path)


class TestAssignmentsForConflictCheck:
    def test_feeds_detect_pack_conflicts_directly(self, tmp_path: Path) -> None:
        agree_a = _write(
            tmp_path,
            "a.yaml",
            "id: a\nversion: 1\nkind: policy\nassignments: {func_removed: break}\n",
        )
        agree_b = _write(
            tmp_path,
            "b.yaml",
            "id: b\nversion: 1\nkind: policy\nassignments: {func_removed: break}\n",
        )
        packs = [load_pack_manifest(agree_a), load_pack_manifest(agree_b)]
        grouped = assignments_for_conflict_check(packs)
        assert detect_pack_conflicts(grouped[PackKind.POLICY]) is None

    def test_disagreeing_packs_raise_pack_conflict_error(self, tmp_path: Path) -> None:
        pack_a = _write(
            tmp_path,
            "a.yaml",
            "id: a\nversion: 1\nkind: policy\nassignments: {func_removed: break}\n",
        )
        pack_b = _write(
            tmp_path,
            "b.yaml",
            "id: b\nversion: 1\nkind: policy\nassignments: {func_removed: ignore}\n",
        )
        packs = [load_pack_manifest(pack_a), load_pack_manifest(pack_b)]
        grouped = assignments_for_conflict_check(packs)
        with pytest.raises(PackConflictError) as exc_info:
            detect_pack_conflicts(grouped[PackKind.POLICY])
        assert exc_info.value.field_name == "func_removed"

    def test_empty_pack_list_returns_empty_groups(self) -> None:
        grouped = assignments_for_conflict_check([])
        assert grouped == {kind: [] for kind in PackKind}

    def test_projects_identity_and_assignments_only(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.GATE,
            assignments={"exit_code_scheme": "severity"},
        )
        grouped = assignments_for_conflict_check([pack])
        assert grouped[PackKind.GATE] == [(pack.identity, pack.assignments)]
        assert grouped[PackKind.CONTRACT] == []
        assert grouped[PackKind.POLICY] == []

    def test_policy_and_gate_packs_with_same_field_name_do_not_collide(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: a flat, ungrouped projection let a
        # policy pack's ChangeKind slug and an unrelated gate pack's own
        # field name collide by string coincidence alone, raising a
        # spurious cross-namespace PackConflictError even though D8 scopes
        # conflict detection to comparing packs within one namespace.
        policy_pack = _write(
            tmp_path,
            "policy.yaml",
            "id: p\nversion: 1\nkind: policy\nassignments: {func_removed: break}\n",
        )
        gate_pack = _write(
            tmp_path,
            "gate.yaml",
            "id: g\nversion: 1\nkind: gate\nassignments: {func_removed: something_else}\n",
        )
        packs = [load_pack_manifest(policy_pack), load_pack_manifest(gate_pack)]
        grouped = assignments_for_conflict_check(packs)
        for pairs in grouped.values():
            assert detect_pack_conflicts(pairs) is None

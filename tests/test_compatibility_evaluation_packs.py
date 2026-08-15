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
        # A field name deliberately distinct from "contract.overlays" -- that
        # one is now a reserved order-insensitive flat-str-list field (see
        # _ORDER_INSENSITIVE_LIST_FIELDS) and would reject this nested,
        # non-str-element shape.
        path = _write(
            tmp_path,
            "nested.yaml",
            """
            id: nested_lists
            version: 1
            kind: contract
            assignments:
              some.nested.field: [[a, b], [c]]
            """,
        )
        pack = load_pack_manifest(path)
        assert pack.assignments["some.nested.field"] == (("a", "b"), ("c",))

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

        with pytest.raises(PackManifestError, match="unsupported type"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"contract.overlays": {1, 2, 3}},
            )

    def test_directly_constructed_pack_rejects_mutable_but_hashable_value(
        self,
    ) -> None:
        # Regression (Codex review): hash(value) succeeding is not proof of
        # immutability -- a custom class can define __hash__ while remaining
        # fully mutable, aliasing into pack.assignments and letting a later
        # mutation change the pack's content without changing
        # identity.sha256. Only the closed scalar type set a real YAML
        # manifest can ever produce is accepted now.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class MutableButHashable:
            def __init__(self) -> None:
                self.value = 1

            def __hash__(self) -> int:
                return 42

        with pytest.raises(PackManifestError, match="unsupported type"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"field": MutableButHashable()},
            )

    def test_directly_constructed_pack_accepts_a_timestamp_value(self) -> None:
        # A YAML manifest's implicit timestamp resolver produces a real
        # datetime.date/datetime -- both must stay accepted (immutable).
        import datetime

        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": datetime.date(2026, 1, 1)},
        )
        assert pack.assignments["field"] == datetime.date(2026, 1, 1)

    def test_directly_constructed_pack_canonicalizes_a_mutable_str_subclass(
        self,
    ) -> None:
        # Regression (Codex review, seventh round): isinstance() alone still
        # accepted a *subclass* of an allowed type (str, float, ...) whose
        # overridden __eq__/__hash__ reads mutable instance state -- the
        # original aliased subclass instance stayed in pack.assignments, so
        # mutating it later could flip detect_pack_conflicts() between
        # agreement and conflict while identity.sha256 stayed unchanged.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class EvilStr(str):
            def __new__(cls, s: str) -> EvilStr:
                obj = super().__new__(cls, s)
                obj.mutable_state = 1  # type: ignore[attr-defined]
                return obj

            def __eq__(self, other: object) -> bool:
                return self.mutable_state == getattr(  # type: ignore[attr-defined]
                    other, "mutable_state", object()
                )

            def __hash__(self) -> int:
                return 42

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": EvilStr("hello")},
        )
        value = pack.assignments["field"]
        assert type(value) is str
        assert value == "hello"

    def test_directly_constructed_pack_canonicalizes_a_mutable_float_subclass(
        self,
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class EvilFloat(float):
            pass

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": EvilFloat(1.5)},
        )
        assert type(pack.assignments["field"]) is float

    def test_directly_constructed_pack_rejects_nan_value(self) -> None:
        # Regression (Codex review): float('nan') != float('nan') (IEEE
        # 754), so two packs both assigning a YAML `.nan` to the same
        # field would each keep a *distinct* nan object, and
        # detect_pack_conflicts()'s equality-based comparison would raise
        # a spurious PackConflictError for what a manifest author clearly
        # intended as the identical value. Confirmed empirically that
        # {(float, float('nan')), (float, float('nan'))} has length 2.
        # Rejecting the value outright (there is no equality-preserving
        # canonical form for nan) closes this before it can happen.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="finite"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"field": float("nan")},
            )

    @pytest.mark.parametrize("value", [float("inf"), float("-inf")])
    def test_directly_constructed_pack_rejects_infinite_value(
        self, value: float
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="finite"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"field": value},
            )

    @pytest.mark.parametrize(
        ("value", "expected_type"),
        [
            (True, bool),
            (7, int),
            (b"raw", bytes),
        ],
    )
    def test_directly_constructed_pack_accepts_plain_scalar_types(
        self, value: object, expected_type: type
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": value},
        )
        assert pack.assignments["field"] == value
        assert type(pack.assignments["field"]) is expected_type

    def test_directly_constructed_pack_accepts_a_datetime_value(self) -> None:
        # datetime.datetime is a datetime.date subclass -- must canonicalize
        # to a plain datetime.datetime (not collapse to a bare date), and a
        # tzinfo must survive the reconstruction.
        import datetime

        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        dt = datetime.datetime(2026, 1, 1, 12, 30, 45, 123456, datetime.timezone.utc)
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": dt},
        )
        value = pack.assignments["field"]
        assert type(value) is datetime.datetime
        assert value == dt
        assert value.tzinfo == datetime.timezone.utc

    def test_directly_constructed_pack_preserves_datetime_fold(self) -> None:
        # `fold` disambiguates a wall-clock time that occurs twice (e.g. a
        # DST fall-back transition) -- it's deliberately ignored by
        # datetime's own __eq__/__hash__ (per the stdlib docs), so a naive
        # `value == dt` assertion can't catch a canonicalization that drops
        # it. Reconstructing via the positional-args constructor without
        # `fold=value.fold` silently resets it to 0 (Codex/CodeRabbit
        # review).
        import datetime

        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        ambiguous = datetime.datetime(2026, 11, 1, 1, 30, fold=1)
        assert ambiguous.fold == 1
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": ambiguous},
        )
        assert pack.assignments["field"].fold == 1

    def test_directly_constructed_pack_snapshots_a_mutable_tzinfo(self) -> None:
        # Codex review, fresh evidence: an aware datetime's tzinfo object is
        # passed straight through unchanged by the naive reconstruction --
        # a custom, mutable tzinfo whose utcoffset() reads mutable state
        # therefore stays aliased into pack.assignments, so mutating it
        # after construction changes the stored value's effective equality
        # without changing identity.sha256 (confirmed empirically: two
        # packs assigning equal-offset aware datetimes through distinct
        # mutable tzinfo instances agreed initially, then detect_pack_conflicts
        # flipped to a conflict purely from mutating one tzinfo afterward).
        import datetime

        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class MutableTZ(datetime.tzinfo):
            def __init__(self, offset_minutes: int = 0) -> None:
                self.offset_minutes = offset_minutes

            def utcoffset(self, dt: object) -> datetime.timedelta:
                return datetime.timedelta(minutes=self.offset_minutes)

            def dst(self, dt: object) -> datetime.timedelta:
                return datetime.timedelta(0)

            def tzname(self, dt: object) -> str:
                return "MUT"

        tz = MutableTZ(0)
        value = datetime.datetime(2026, 1, 1, tzinfo=tz)
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"field": value},
        )
        stored = pack.assignments["field"]
        assert stored.tzinfo is not tz
        assert type(stored.tzinfo) is datetime.timezone
        assert stored == value

        tz.offset_minutes = 60
        assert stored == datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    def test_directly_constructed_pack_rejects_a_tzinfo_with_no_offset(self) -> None:
        import datetime

        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class NoOffsetTZ(datetime.tzinfo):
            def utcoffset(self, dt: object) -> None:
                return None

            def dst(self, dt: object) -> None:
                return None

            def tzname(self, dt: object) -> str:
                return "NONE"

        value = datetime.datetime(2026, 1, 1, tzinfo=NoOffsetTZ())
        with pytest.raises(PackManifestError, match="does not report a UTC offset"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.CONTRACT,
                assignments={"field": value},
            )

    def test_directly_constructed_policy_pack_flattens_a_changekind_slug_key(
        self,
    ) -> None:
        # Codex review, fresh evidence: ChangeKind is a (str, Enum) mixin, so
        # a real ChangeKind member passes `isinstance(slug, str)` and the
        # `slug in _VALID_CHANGE_KIND_SLUGS` membership check (both via
        # str equality/hash) -- but `str(ChangeKind.FUNC_REMOVED)` is
        # "ChangeKind.FUNC_REMOVED", not "func_removed" (Enum's own
        # __str__ override, not the member's string payload). A directly
        # constructed policy pack keying its assignment on the real enum
        # member must still store the plain slug, or it silently stops
        # matching/conflicting with an equivalent manifest-loaded pack's
        # plain-str key.
        from abicheck.checker_policy import ChangeKind
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.POLICY,
            assignments={ChangeKind.FUNC_REMOVED: "break"},
        )
        assert list(pack.assignments.keys()) == ["func_removed"]
        assert pack.assignments["func_removed"] is Verdict.BREAKING

    def test_directly_constructed_contract_pack_flattens_a_str_enum_value(
        self,
    ) -> None:
        # Same flattening bug, but for a CONTRACT/GATE pack's *value* rather
        # than a POLICY pack's key: ContractMode is also a (str, Enum)
        # mixin, so str(ContractMode.PUBLIC) is "ContractMode.PUBLIC", not
        # "public" -- a directly constructed pack assigning the real enum
        # member as a field value must still store its plain payload.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity
        from abicheck.contract_relevance_types import ContractMode

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={"contract.mode": ContractMode.PUBLIC},
        )
        assert pack.assignments["contract.mode"] == "public"
        assert type(pack.assignments["contract.mode"]) is str

    def test_directly_constructed_policy_pack_coerces_raw_severity_string(
        self,
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.POLICY,
            assignments={"func_removed": "break"},
        )
        assert pack.assignments["func_removed"] is Verdict.BREAKING

    def test_directly_constructed_policy_pack_accepts_a_real_verdict(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.POLICY,
            assignments={"func_removed": Verdict.BREAKING},
        )
        assert pack.assignments["func_removed"] is Verdict.BREAKING

    def test_directly_constructed_policy_pack_matches_manifest_loaded_equivalent(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review): a raw severity string left uncoerced
        # would compare unequal to a manifest-loaded pack's real Verdict
        # value inside detect_pack_conflicts, raising a false conflict.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        direct = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.POLICY,
            assignments={"func_removed": "break"},
        )
        manifest_path = _write(
            tmp_path,
            "policy.yaml",
            "id: p\nversion: 1\nkind: policy\nassignments:\n  func_removed: break\n",
        )
        loaded = load_pack_manifest(manifest_path)
        assert direct.assignments == loaded.assignments

    def test_directly_constructed_policy_pack_rejects_unknown_kind_slug(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="unknown ChangeKind slugs"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.POLICY,
                assignments={"not_a_real_kind": "break"},
            )

    def test_directly_constructed_policy_pack_rejects_unknown_severity(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="invalid severity values"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=PackKind.POLICY,
                assignments={"func_removed": "not_a_severity"},
            )

    def test_directly_constructed_pack_coerces_a_bare_kind_string(self) -> None:
        # Regression (Codex review): `kind="policy"` (a bare str, not the
        # PackKind enum member) failed the `is PackKind.POLICY` identity
        # check, silently skipping severity coercion, while
        # assignments_for_conflict_check() still grouped it as a policy
        # pack (equality/hash, not identity) -- the two disagreed.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind="policy",
            assignments={"func_removed": "break"},
        )
        assert pack.kind is PackKind.POLICY
        assert pack.assignments["func_removed"] is Verdict.BREAKING

    def test_directly_constructed_pack_with_bare_kind_string_does_not_false_conflict(
        self,
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        bare_kind = LoadedPack(
            identity=ImmutableIdentity(id="p1", version=1, sha256="deadbeef"),
            kind="policy",
            assignments={"func_removed": "break"},
        )
        typed_kind = LoadedPack(
            identity=ImmutableIdentity(id="p2", version=1, sha256="beefdead"),
            kind=PackKind.POLICY,
            assignments={"func_removed": "break"},
        )
        grouped = assignments_for_conflict_check([bare_kind, typed_kind])
        detect_pack_conflicts(grouped[PackKind.POLICY])  # must not raise

    def test_directly_constructed_pack_rejects_unknown_kind(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="kind must be one of"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind="bogus",
                assignments={},
            )

    def test_directly_constructed_pack_rejects_unhashable_kind(self) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="kind must be one of"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=["policy"],
                assignments={},
            )

    @pytest.mark.parametrize("kind", [PackKind.CONTRACT, PackKind.GATE])
    def test_directly_constructed_pack_rejects_empty_string_key(
        self, kind: PackKind
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="non-empty str field name"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=kind,
                assignments={"": "value"},
            )

    @pytest.mark.parametrize("kind", [PackKind.CONTRACT, PackKind.GATE])
    def test_directly_constructed_pack_rejects_non_str_key(
        self, kind: PackKind
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        with pytest.raises(PackManifestError, match="non-empty str field name"):
            LoadedPack(
                identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
                kind=kind,
                assignments={1: "value"},
            )

    def test_directly_constructed_pack_canonicalizes_a_mutable_str_subclass_key(
        self,
    ) -> None:
        # Regression (Codex review, fourteenth round): a mutable str
        # subclass accepted as an assignment *key* previously stayed the
        # same aliased object -- mutating it after construction (if its
        # __eq__/__hash__ read mutable state) could change the field
        # identity detect_pack_conflicts() consumes without changing
        # identity.sha256. Mirrors the identical, already-fixed concern for
        # assignment *values*.
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class TrackedKey(str):
            def __new__(cls, s: str) -> TrackedKey:
                obj = super().__new__(cls, s)
                obj.tag = "mine"  # type: ignore[attr-defined]
                return obj

        key = TrackedKey("contract.mode")
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={key: "public"},
        )
        stored_key = next(iter(pack.assignments))
        assert type(stored_key) is str
        assert stored_key is not key

    def test_directly_constructed_policy_pack_canonicalizes_a_mutable_str_subclass_key(
        self,
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        class TrackedKey(str):
            def __new__(cls, s: str) -> TrackedKey:
                obj = super().__new__(cls, s)
                obj.tag = "mine"  # type: ignore[attr-defined]
                return obj

        key = TrackedKey("func_removed")
        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.POLICY,
            assignments={key: "break"},
        )
        stored_key = next(iter(pack.assignments))
        assert type(stored_key) is str
        assert stored_key is not key


@pytest.mark.parametrize("field", ["contract.overlays", "surface.internal_namespaces"])
class TestOrderInsensitiveFieldCanonicalization:
    """Regression (Codex review): every field listed in
    ``_ORDER_INSENSITIVE_LIST_FIELDS`` is an unordered selection (mirroring
    the corresponding ``compatibility_evaluation_config.py`` field's own
    ``_canonical_tuple`` sort+dedupe -- ``ContractConfig.overlays`` and
    ``SurfaceConfig.internal_namespaces``), not an ordered sequence -- two
    packs assigning the same set in a different order must resolve to an
    equal, conflict-free value. ``surface.internal_namespaces`` was added in
    a second round after ``contract.overlays`` shipped without it (fresh
    Codex evidence: the two-packs no-conflict repro reproduced identically
    for this field)."""

    def test_field_is_sorted_at_load_time(self, tmp_path: Path, field: str) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            f"id: p\nversion: 1\nkind: contract\nassignments: {{{field}: [zeta, alpha]}}\n",
        )
        pack = load_pack_manifest(path)
        assert pack.assignments[field] == ("alpha", "zeta")

    def test_field_is_deduped_at_load_time(self, tmp_path: Path, field: str) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            f"id: p\nversion: 1\nkind: contract\nassignments: {{{field}: [a, b, a]}}\n",
        )
        pack = load_pack_manifest(path)
        assert pack.assignments[field] == ("a", "b")

    def test_differently_ordered_values_do_not_conflict(
        self, tmp_path: Path, field: str
    ) -> None:
        p1_path = _write(
            tmp_path,
            "p1.yaml",
            f"id: p1\nversion: 1\nkind: contract\nassignments: {{{field}: [a, b]}}\n",
        )
        p2_path = _write(
            tmp_path,
            "p2.yaml",
            f"id: p2\nversion: 1\nkind: contract\nassignments: {{{field}: [b, a]}}\n",
        )
        p1 = load_pack_manifest(p1_path)
        p2 = load_pack_manifest(p2_path)
        grouped = assignments_for_conflict_check([p1, p2])
        detect_pack_conflicts(grouped[PackKind.CONTRACT])  # must not raise

    def test_non_str_element_raises(self, tmp_path: Path, field: str) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            f"id: p\nversion: 1\nkind: contract\nassignments: {{{field}: [a, 1]}}\n",
        )
        with pytest.raises(PackManifestError, match="must be a list of str"):
            load_pack_manifest(path)

    def test_directly_constructed_pack_gets_the_same_canonicalization(
        self, field: str
    ) -> None:
        from abicheck.compatibility_evaluation_config import ImmutableIdentity

        pack = LoadedPack(
            identity=ImmutableIdentity(id="x", version=1, sha256="deadbeef"),
            kind=PackKind.CONTRACT,
            assignments={field: ["zeta", "alpha", "alpha"]},
        )
        assert pack.assignments[field] == ("alpha", "zeta")

    def test_unrelated_field_order_is_preserved(
        self, tmp_path: Path, field: str
    ) -> None:
        # Only a reserved field name is canonicalized -- this module has no
        # closed field vocabulary (its own docstring), so an arbitrary field
        # name must keep insertion order regardless of which reserved field
        # is being parametrized over in this test class.
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: contract\n"
            "assignments: {some.ordered.field: [zeta, alpha]}\n",
        )
        pack = load_pack_manifest(path)
        assert pack.assignments["some.ordered.field"] == ("zeta", "alpha")


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
        # ADR-049 D8: a hard load error, matching --policy's behavior --
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

    def test_nan_assignment_value_raises(self, tmp_path: Path) -> None:
        # Regression (Codex review): a YAML `.nan` scalar decodes to
        # float('nan'), which is unequal to itself -- two manifests both
        # assigning it to the same field would otherwise make
        # detect_pack_conflicts() raise a spurious PackConflictError over
        # what a manifest author intended as the same value. Rejected at
        # load time instead, matching the null/nested-mapping treatment
        # immediately above.
        path = _write(
            tmp_path,
            "p.yaml",
            "id: p\nversion: 1\nkind: contract\nassignments: {some.field: .nan}\n",
        )
        with pytest.raises(PackManifestError, match="finite"):
            load_pack_manifest(path)

    @pytest.mark.parametrize("literal", [".inf", "-.inf"])
    def test_infinite_assignment_value_raises(
        self, tmp_path: Path, literal: str
    ) -> None:
        path = _write(
            tmp_path,
            "p.yaml",
            f"id: p\nversion: 1\nkind: contract\nassignments: {{some.field: {literal}}}\n",
        )
        with pytest.raises(PackManifestError, match="finite"):
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
            "id: p\nversion: 1\nkind: gate\nassignments:\n  ? [a, b]\n  : value\n",
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

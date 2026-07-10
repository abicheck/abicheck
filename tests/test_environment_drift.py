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

"""Toolchain / runtime environment drift (binutils & glibc skew).

Covers the runtime-floor synthesis (RUNTIME_FLOOR_RAISED), the declared
runtime-floor contract (EnvironmentMatrix.runtime_floors), DT_RELR drift,
the DT_RPATH↔DT_RUNPATH type flip, symbol hash-style drift, and the
time64/LFS ABI-flip collapse. All tests use synthetic ``ElfMetadata`` /
``AbiSnapshot`` — no real binaries required.
"""
from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.diff_platform_elf_dynamic import (
    _diff_dt_relr,
    _diff_elf_dynamic_section,
    _diff_hash_styles,
)
from abicheck.diff_platform_elf_symbols import _diff_elf_symbol_versioning
from abicheck.diff_time64 import _diff_time64_abi
from abicheck.diff_versioning import apply_runtime_floor_contract
from abicheck.elf_metadata import ElfImport, ElfMetadata
from abicheck.environment_matrix import EnvironmentMatrix
from abicheck.model import AbiSnapshot


def _elf(**kwargs) -> ElfMetadata:
    """ElfMetadata with the parse-generation markers the detectors gate on."""
    kwargs.setdefault("machine", "EM_X86_64")
    kwargs.setdefault("hash_styles", frozenset({"gnu"}))
    return ElfMetadata(**kwargs)


def _snap(elf: ElfMetadata, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        elf=elf,
        elf_only_mode=True,
        platform="elf",
        **kwargs,
    )


def _kinds(changes) -> set[ChangeKind]:
    return {c.kind for c in changes}


# ── RUNTIME_FLOOR_RAISED synthesis ───────────────────────────────────────────


class TestRuntimeFloorRaised:
    def _old(self) -> ElfMetadata:
        return _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28"]},
        )

    def test_raised_floor_emits_headline_with_evidence(self) -> None:
        new = _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28", "GLIBC_2.34"]},
            imports=[
                ElfImport(name="__libc_start_main", version="GLIBC_2.34",
                          version_soname="libc.so.6"),
                ElfImport(name="memcpy", version="GLIBC_2.14",
                          version_soname="libc.so.6"),
            ],
        )
        changes = _diff_elf_symbol_versioning(self._old(), new)
        floors = [c for c in changes if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED]
        assert len(floors) == 1
        c = floors[0]
        assert c.old_value == "GLIBC_2.28"
        assert c.new_value == "GLIBC_2.34"
        # Only the imports above the old floor are evidence.
        assert "__libc_start_main@GLIBC_2.34" in c.description
        assert "memcpy" not in c.description
        # The per-node finding still fires alongside the roll-up.
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in _kinds(changes)

    def test_no_floor_finding_when_added_version_below_old_max(self) -> None:
        new = _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28", "GLIBC_2.22"]},
        )
        changes = _diff_elf_symbol_versioning(self._old(), new)
        assert ChangeKind.RUNTIME_FLOOR_RAISED not in _kinds(changes)

    def test_no_floor_finding_for_entirely_new_lib(self) -> None:
        new = _elf(
            needed=["libc.so.6", "libm.so.6"],
            versions_required={
                "libc.so.6": ["GLIBC_2.17", "GLIBC_2.28"],
                "libm.so.6": ["GLIBC_2.35"],
            },
        )
        changes = _diff_elf_symbol_versioning(self._old(), new)
        assert ChangeKind.RUNTIME_FLOOR_RAISED not in _kinds(changes)

    def test_prefixes_tracked_independently(self) -> None:
        old = _elf(
            needed=["libstdc++.so.6"],
            versions_required={
                "libstdc++.so.6": ["GLIBCXX_3.4.28", "CXXABI_1.3.11"]
            },
        )
        new = _elf(
            needed=["libstdc++.so.6"],
            versions_required={
                "libstdc++.so.6": ["GLIBCXX_3.4.28", "CXXABI_1.3.11", "CXXABI_1.3.13"]
            },
        )
        changes = _diff_elf_symbol_versioning(old, new)
        floors = [c for c in changes if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED]
        assert len(floors) == 1
        assert floors[0].new_value == "CXXABI_1.3.13"

    def test_unparseable_marker_does_not_raise_floor(self) -> None:
        new = _elf(
            needed=["libc.so.6"],
            versions_required={
                "libc.so.6": ["GLIBC_2.17", "GLIBC_2.28", "GLIBC_PRIVATE"]
            },
        )
        changes = _diff_elf_symbol_versioning(self._old(), new)
        assert ChangeKind.RUNTIME_FLOOR_RAISED not in _kinds(changes)

    def test_floor_is_risk_verdict_through_compare(self) -> None:
        new = _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28", "GLIBC_2.34"]},
        )
        result = compare(_snap(self._old()), _snap(new))
        assert ChangeKind.RUNTIME_FLOOR_RAISED in _kinds(result.changes)
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


# ── Declared runtime-floor contract (EnvironmentMatrix.runtime_floors) ──────


class TestRuntimeFloorContract:
    def _pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = _snap(_elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28"]},
        ))
        new = _snap(_elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.28", "GLIBC_2.34"]},
        ))
        return old, new

    def test_requirement_within_floor_is_compatible(self) -> None:
        old, new = self._pair()
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.36"})
        result = compare(old, new, env_matrix=matrix)
        assert result.verdict is Verdict.COMPATIBLE
        floor = next(c for c in result.changes
                     if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED)
        assert floor.effective_verdict is Verdict.COMPATIBLE
        assert floor.modulation_rule == "runtime_floor_contract"

    def test_requirement_above_floor_is_breaking(self) -> None:
        old, new = self._pair()
        matrix = EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        result = compare(old, new, env_matrix=matrix)
        assert result.verdict is Verdict.BREAKING

    def test_undeclared_prefix_keeps_default_risk(self) -> None:
        old, new = self._pair()
        matrix = EnvironmentMatrix(runtime_floors={"GLIBCXX": "3.4.30"})
        result = compare(old, new, env_matrix=matrix)
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_floor_breaking_triggers_soname_bump_advisory(self) -> None:
        # The floor contract runs BEFORE the SONAME policy, so a floor-decided
        # BREAKING on an unchanged SONAME also yields the bump advisory
        # (Codex review #510).
        old, new = self._pair()
        old.elf.soname = new.elf.soname = "libtest.so.1"
        result = compare(old, new, env_matrix=EnvironmentMatrix(
            runtime_floors={"GLIBC": "2.28"}
        ))
        assert result.verdict is Verdict.BREAKING
        assert ChangeKind.SONAME_BUMP_RECOMMENDED in _kinds(result.changes)

    def test_unparseable_floor_value_left_at_default(self) -> None:
        # A floor that parses to no numeric components (possible via direct
        # construction, bypassing from_dict validation) must not modulate.
        old, new = self._pair()
        result = compare(old, new, env_matrix=EnvironmentMatrix(
            runtime_floors={"GLIBC": "unknown"}
        ))
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_tag_without_underscore_skipped(self) -> None:
        from abicheck.diff_helpers import make_change

        change = make_change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
            symbol="NOUNDERSCORE",
            name="NOUNDERSCORE",
            detail="libc.so.6",
        )
        apply_runtime_floor_contract([change], {"GLIBC": "2.36"})
        assert change.effective_verdict is None

    def test_empty_floors_no_op(self) -> None:
        old, new = self._pair()
        changes = list(compare(old, new).changes)
        assert apply_runtime_floor_contract(changes, {}) is changes
        assert all(c.modulation_rule != "runtime_floor_contract" for c in changes)

    def test_floor_keys_case_insensitive(self) -> None:
        changes = [
            c for c in compare(*self._pair()).changes
        ]
        apply_runtime_floor_contract(changes, {"glibc": "2.36"})
        floor = next(c for c in changes
                     if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED)
        assert floor.effective_verdict is Verdict.COMPATIBLE

    def test_dt_relr_settled_by_glibc_floor(self) -> None:
        # DT_RELR implies "needs glibc >= 2.36"; a declared GLIBC floor at or
        # above that settles it COMPATIBLE, one below settles it BREAKING.
        old = _snap(_elf())
        new = _snap(_elf(has_dt_relr=True))
        assert compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.36"})
        ).verdict is Verdict.COMPATIBLE
        assert compare(
            old, new, env_matrix=EnvironmentMatrix(runtime_floors={"GLIBC": "2.28"})
        ).verdict is Verdict.BREAKING

    def test_existing_modulation_not_overridden(self) -> None:
        old, new = self._pair()
        result = compare(old, new)
        floor = next(c for c in result.changes
                     if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED)
        floor.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
        floor.modulation_rule = "someone_else"
        apply_runtime_floor_contract([floor], {"GLIBC": "2.36"})
        assert floor.modulation_rule == "someone_else"


class TestEnvironmentMatrixRuntimeFloors:
    def test_from_dict_normalizes_keys_to_upper(self) -> None:
        m = EnvironmentMatrix.from_dict({"runtime_floors": {"glibc": "2.28"}})
        assert m.runtime_floors == {"GLIBC": "2.28"}

    def test_from_dict_rejects_non_dict(self) -> None:
        with pytest.raises(ValueError, match="runtime_floors"):
            EnvironmentMatrix.from_dict({"runtime_floors": ["GLIBC"]})

    def test_from_dict_rejects_non_numeric_floor(self) -> None:
        with pytest.raises(ValueError, match="dotted numeric"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"GLIBC": "latest"}})

    def test_yaml_float_floor_rejected(self) -> None:
        # An unquoted YAML floor is lossy BEFORE we see it: `GLIBC: 2.40`
        # arrives as the float 2.4 — silently a lower floor than written.
        # Reject with a quote-it message instead of guessing.
        with pytest.raises(ValueError, match="quoted string"):
            EnvironmentMatrix.from_dict({"runtime_floors": {"GLIBC": 2.28}})

    def test_int_floor_accepted(self) -> None:
        # Integers are not lossy — accept them.
        m = EnvironmentMatrix.from_dict({"runtime_floors": {"GLIBC": 3}})
        assert m.runtime_floors == {"GLIBC": "3"}


class TestLoadEnvMatrix:
    """Tier-2 loader: identical error text across front-ends (service layer)."""

    def test_none_path_returns_none(self) -> None:
        from abicheck.service import load_env_matrix

        assert load_env_matrix(None) is None

    def test_valid_yaml_loads(self, tmp_path) -> None:
        from abicheck.service import load_env_matrix

        p = tmp_path / "env.yaml"
        p.write_text('runtime_floors:\n  GLIBC: "2.28"\n')
        matrix = load_env_matrix(p)
        assert matrix is not None
        assert matrix.runtime_floors == {"GLIBC": "2.28"}

    def test_malformed_yaml_raises_validation_error(self, tmp_path) -> None:
        from abicheck.errors import ValidationError
        from abicheck.service import load_env_matrix

        p = tmp_path / "env.yaml"
        p.write_text("runtime_floors: [unclosed\n  GLIBC: {")
        with pytest.raises(ValidationError, match="Invalid environment matrix"):
            load_env_matrix(p)

    def test_bad_shape_raises_validation_error(self, tmp_path) -> None:
        from abicheck.errors import ValidationError
        from abicheck.service import load_env_matrix

        p = tmp_path / "env.yaml"
        p.write_text("runtime_floors:\n  GLIBC: latest\n")
        with pytest.raises(ValidationError, match="dotted numeric"):
            load_env_matrix(p)

    def test_missing_file_raises_validation_error(self, tmp_path) -> None:
        from abicheck.errors import ValidationError
        from abicheck.service import load_env_matrix

        with pytest.raises(ValidationError, match="Cannot read environment matrix"):
            load_env_matrix(tmp_path / "nope.yaml")

    def test_compare_request_validates_path_exists(self, tmp_path) -> None:
        from abicheck.api_types import CompareRequest, InputSpec

        req = CompareRequest(
            old=InputSpec(path=tmp_path / "old.so"),
            new=InputSpec(path=tmp_path / "new.so"),
            env_matrix_path=tmp_path / "missing.yaml",
        )
        assert any(
            "environment matrix file not found" in e
            for e in req.validation_errors()
        )


# ── DT_RELR drift ────────────────────────────────────────────────────────────


class TestDtRelr:
    def test_introduced(self) -> None:
        changes = _diff_dt_relr(_elf(), _elf(has_dt_relr=True))
        assert _kinds(changes) == {ChangeKind.DT_RELR_INTRODUCED}

    def test_removed(self) -> None:
        changes = _diff_dt_relr(_elf(has_dt_relr=True), _elf())
        assert _kinds(changes) == {ChangeKind.DT_RELR_REMOVED}

    def test_no_change_silent(self) -> None:
        assert _diff_dt_relr(_elf(has_dt_relr=True), _elf(has_dt_relr=True)) == []
        assert _diff_dt_relr(_elf(), _elf()) == []

    def test_legacy_snapshot_gated_off(self) -> None:
        # A legacy baseline rehydrates hash_styles empty — no fabricated finding.
        legacy = ElfMetadata(machine="EM_X86_64")
        assert _diff_dt_relr(legacy, _elf(has_dt_relr=True)) == []

    def test_glibc_abi_dt_relr_marker_folds_into_relr_finding(self) -> None:
        old = _elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.28"]},
        )
        new = _elf(
            needed=["libc.so.6"],
            has_dt_relr=True,
            versions_required={"libc.so.6": ["GLIBC_2.28", "GLIBC_ABI_DT_RELR"]},
        )
        result = compare(_snap(old), _snap(new))
        kinds = _kinds(result.changes)
        assert ChangeKind.DT_RELR_INTRODUCED in kinds
        # The synthetic marker must not double-report as a cryptic version add.
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED not in kinds

    def test_marker_still_reported_when_relr_fields_not_captured(self) -> None:
        # Old side is a legacy snapshot: the DT_RELR detector is gated off, so
        # the conservative verneed finding must survive as the only signal.
        old = ElfMetadata(
            machine="EM_X86_64",
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.28"]},
        )
        new = ElfMetadata(
            machine="EM_X86_64",
            needed=["libc.so.6"],
            has_dt_relr=True,
            versions_required={"libc.so.6": ["GLIBC_2.28", "GLIBC_ABI_DT_RELR"]},
        )
        changes = _diff_elf_symbol_versioning(old, new)
        assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in _kinds(changes)


# ── DT_RPATH ↔ DT_RUNPATH type flip ─────────────────────────────────────────


class TestRpathTypeFlip:
    def test_pure_flip_replaces_value_findings(self) -> None:
        changes = _diff_elf_dynamic_section(
            _elf(rpath="/opt/lib"), _elf(runpath="/opt/lib")
        )
        kinds = _kinds(changes)
        assert ChangeKind.RPATH_TYPE_CHANGED in kinds
        assert ChangeKind.RPATH_CHANGED not in kinds
        assert ChangeKind.RUNPATH_CHANGED not in kinds
        flip = next(c for c in changes if c.kind is ChangeKind.RPATH_TYPE_CHANGED)
        assert "DT_RPATH → DT_RUNPATH" in flip.description

    def test_flip_with_value_change_reports_both(self) -> None:
        changes = _diff_elf_dynamic_section(
            _elf(runpath="/old/lib"), _elf(rpath="/new/lib")
        )
        kinds = _kinds(changes)
        assert ChangeKind.RPATH_TYPE_CHANGED in kinds
        assert ChangeKind.RPATH_CHANGED in kinds
        assert ChangeKind.RUNPATH_CHANGED in kinds

    def test_value_only_change_does_not_flip(self) -> None:
        changes = _diff_elf_dynamic_section(
            _elf(runpath="/old/lib"), _elf(runpath="/new/lib")
        )
        kinds = _kinds(changes)
        assert ChangeKind.RPATH_TYPE_CHANGED not in kinds
        assert ChangeKind.RUNPATH_CHANGED in kinds

    def test_both_tags_present_is_not_a_flip(self) -> None:
        # A binary carrying BOTH tags that drops one is ambiguous; the value
        # diffs cover it without a type-flip claim.
        changes = _diff_elf_dynamic_section(
            _elf(rpath="/a", runpath="/a"), _elf(runpath="/a")
        )
        assert ChangeKind.RPATH_TYPE_CHANGED not in _kinds(changes)


# ── Symbol hash-style drift ──────────────────────────────────────────────────


class TestHashStyles:
    def test_dropped_sysv_reports(self) -> None:
        changes = _diff_hash_styles(
            _elf(hash_styles=frozenset({"sysv", "gnu"})),
            _elf(hash_styles=frozenset({"gnu"})),
        )
        assert _kinds(changes) == {ChangeKind.HASH_STYLE_REMOVED}
        assert "gnu+sysv → gnu" in changes[0].description

    def test_gained_style_silent(self) -> None:
        changes = _diff_hash_styles(
            _elf(hash_styles=frozenset({"gnu"})),
            _elf(hash_styles=frozenset({"sysv", "gnu"})),
        )
        assert changes == []

    def test_legacy_snapshot_gated_off(self) -> None:
        legacy = ElfMetadata(machine="EM_X86_64")
        assert _diff_hash_styles(legacy, _elf()) == []
        assert _diff_hash_styles(_elf(), legacy) == []


# ── time64 / LFS ABI flip ────────────────────────────────────────────────────


def _snap32(
    typedefs: dict[str, str], *, referenced: bool = True
) -> AbiSnapshot:
    """32-bit ELF snapshot with the given typedefs.

    By default each typedef is referenced by a public function's return type —
    the detector only rolls up flips the public surface actually carries.
    """
    from abicheck.model import Function, Visibility

    functions = (
        [
            Function(
                name=f"use_{n}", mangled=f"use_{n}",
                return_type=n, visibility=Visibility.PUBLIC,
            )
            for n in typedefs
        ]
        if referenced
        else []
    )
    return _snap(
        _elf(machine="EM_ARM", elf_class=32, pointer_size=4),
        typedefs=typedefs,
        functions=functions,
    )


class TestTime64AbiFlip:
    def test_32bit_time64_flip_detected(self) -> None:
        old = _snap32({"time_t": "long int", "off_t": "long int"})
        new = _snap32({"time_t": "long long int", "off_t": "long long int"})
        changes = _diff_time64_abi(old, new)
        assert _kinds(changes) == {ChangeKind.TIME64_ABI_CHANGED}
        desc = changes[0].description
        assert "time_t" in desc and "off_t" in desc
        assert "_TIME_BITS=64" in desc and "_FILE_OFFSET_BITS=64" in desc

    def test_disable_direction_detected(self) -> None:
        old = _snap32({"time_t": "long long int"})
        new = _snap32({"time_t": "long int"})
        changes = _diff_time64_abi(old, new)
        assert len(changes) == 1
        assert "disabled" in changes[0].description

    def test_64bit_target_silent(self) -> None:
        old = _snap(_elf(), typedefs={"time_t": "long int"})
        new = _snap(_elf(), typedefs={"time_t": "long int"})
        assert _diff_time64_abi(old, new) == []

    def test_unrelated_typedef_flip_silent(self) -> None:
        old = _snap32({"my_handle_t": "long int"})
        new = _snap32({"my_handle_t": "long long int"})
        assert _diff_time64_abi(old, new) == []

    def test_unused_family_typedef_flip_silent(self) -> None:
        # A resized system typedef nothing public carries (header-scoped or
        # DWARF-rich runs pick up unused system typedefs) must not roll up to
        # a BREAKING pseudo-symbol (Codex review #510).
        old = _snap32({"time_t": "long int"}, referenced=False)
        new = _snap32({"time_t": "long long int"}, referenced=False)
        assert _diff_time64_abi(old, new) == []

    def test_struct_field_reference_counts_as_public_use(self) -> None:
        from abicheck.model import RecordType, TypeField

        old = _snap32({"time_t": "long int"}, referenced=False)
        new = _snap32({"time_t": "long long int"}, referenced=False)
        for snap in (old, new):
            snap.types = [RecordType(
                name="event", kind="struct",
                fields=[TypeField(name="stamp", type="time_t")],
            )]
        changes = _diff_time64_abi(old, new)
        assert _kinds(changes) == {ChangeKind.TIME64_ABI_CHANGED}

    def test_unsigned_long_spellings_bucketed(self) -> None:
        # DWARF spells the LFS typedefs many ways: `unsigned long int`,
        # `long unsigned int`, … — all must bucket, or an ino_t/fsblkcnt_t
        # flip is silently missed (Codex review #510).
        old = _snap32({"ino_t": "unsigned long int",
                       "fsblkcnt_t": "long unsigned int"})
        new = _snap32({"ino_t": "unsigned long long int",
                       "fsblkcnt_t": "unsigned long long int"})
        changes = _diff_time64_abi(old, new)
        assert _kinds(changes) == {ChangeKind.TIME64_ABI_CHANGED}
        assert "ino_t" in changes[0].description
        assert "fsblkcnt_t" in changes[0].description

    def test_pe_snapshots_skipped(self) -> None:
        old = AbiSnapshot(library="x.dll", version="1", platform="pe",
                          typedefs={"time_t": "long int"})
        new = AbiSnapshot(library="x.dll", version="2", platform="pe",
                          typedefs={"time_t": "long long int"})
        assert _diff_time64_abi(old, new) == []

    def test_breaking_verdict_through_compare(self) -> None:
        old = _snap32({"time_t": "long int"})
        new = _snap32({"time_t": "long long int"})
        result = compare(old, new)
        assert ChangeKind.TIME64_ABI_CHANGED in _kinds(result.changes)
        assert result.verdict is Verdict.BREAKING


# ── ElfMetadata serialization of the new linker-artifact fields ─────────────


class TestSerializationRoundtrip:
    def test_new_fields_roundtrip(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap(_elf(has_dt_relr=True, hash_styles=frozenset({"sysv", "gnu"})))
        restored = snapshot_from_dict(snapshot_to_dict(snap))
        assert restored.elf.has_dt_relr is True
        assert restored.elf.hash_styles == frozenset({"sysv", "gnu"})

    def test_legacy_dict_defaults(self) -> None:
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        d = snapshot_to_dict(_snap(_elf()))
        d["elf"].pop("has_dt_relr")
        d["elf"].pop("hash_styles")
        restored = snapshot_from_dict(d)
        assert restored.elf.has_dt_relr is False
        assert restored.elf.hash_styles == frozenset()


# ── examples/case165 — committed snapshot-pair fixture (compiler-free) ──────


class TestCase165Example:
    """Validate the environment-drift catalog case against its ground truth.

    The case ships a committed AbiSnapshot pair instead of a compilable
    v1/v2 source pair (producing a glibc verneed-floor raise for real would
    need two sysroots), so this is its compiler-free validation lane —
    mirroring how tests/test_g20_catalog.py validates the audit corpus.
    """

    CASE = "case165_env_runtime_floor_raised"

    @pytest.fixture()
    def snapshots(self):
        import json
        from pathlib import Path

        from abicheck.serialization import snapshot_from_dict

        case_dir = Path(__file__).parent.parent / "examples" / self.CASE
        old = snapshot_from_dict(json.loads((case_dir / "old.abi.json").read_text()))
        new = snapshot_from_dict(json.loads((case_dir / "new.abi.json").read_text()))
        return old, new

    def test_matches_ground_truth(self, snapshots) -> None:
        import json
        from pathlib import Path

        gt = json.loads(
            (Path(__file__).parent.parent / "examples" / "ground_truth.json").read_text()
        )["verdicts"][self.CASE]
        result = compare(*snapshots)
        assert result.verdict.value == gt["expected"]
        kinds = {c.kind.value for c in result.changes}
        assert kinds == set(gt["expected_kinds"])

    def test_floor_evidence_names_relink_artifact(self, snapshots) -> None:
        result = compare(*snapshots)
        floor = next(c for c in result.changes
                     if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED)
        assert "__libc_start_main@GLIBC_2.34" in floor.description
        assert floor.old_value == "GLIBC_2.28"
        assert floor.new_value == "GLIBC_2.34"

    def test_env_matrix_files_settle_the_verdict(self, snapshots) -> None:
        from pathlib import Path

        case_dir = Path(__file__).parent.parent / "examples" / self.CASE
        newer = EnvironmentMatrix.from_yaml(case_dir / "env-newer.yaml")
        older = EnvironmentMatrix.from_yaml(case_dir / "env-older.yaml")
        assert compare(*snapshots, env_matrix=newer).verdict is Verdict.COMPATIBLE
        assert compare(*snapshots, env_matrix=older).verdict is Verdict.BREAKING


# ── Markdown environment-drift section ───────────────────────────────────────


class TestDriftReportSection:
    def test_drift_section_lists_environment_findings(self) -> None:
        from abicheck.reporter import to_markdown

        old = _snap(_elf(
            needed=["libc.so.6"],
            versions_required={"libc.so.6": ["GLIBC_2.28"]},
        ))
        new = _snap(_elf(
            needed=["libc.so.6"],
            has_dt_relr=True,
            versions_required={"libc.so.6": ["GLIBC_2.28", "GLIBC_2.34"]},
        ))
        md = to_markdown(compare(old, new))
        assert "Environment & Toolchain Drift" in md
        assert "runtime_floor_raised" in md
        assert "dt_relr_introduced" in md

    def test_no_drift_no_section(self) -> None:
        from abicheck.reporter import to_markdown

        snap = _snap(_elf())
        md = to_markdown(compare(snap, snap))
        assert "Environment & Toolchain Drift" not in md

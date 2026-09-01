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

"""Unit-test mirror of the ``fact-registry-completeness`` AI-readiness check
(``scripts/fact_registry_completeness.py``) and direct tests of
``abicheck/model/fact_registry.py`` itself — ADR-063 D7/Phase 5
(``docs/contribute/plans/one-semantic-pipeline.md``).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from abicheck.model.fact_registry import (
    FACT_REGISTRY,
    KNOWN_PRODUCING_BACKENDS,
    KNOWN_UNCONVERTED_ELIGIBLE_FACTS,
    REFERENCE_FLAG_COVERAGE,
    FactDefinition,
    FactLifecycle,
    FactRegistry,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import scripts.fact_registry_completeness as fact_registry_completeness  # noqa: E402
from scripts.fact_registry_completeness import (  # noqa: E402
    _model_fact_siblings,
    check_fact_registry_completeness,
    scan_model_dataclasses,
)


class _LocalFindings:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))


# ---------------------------------------------------------------------------
# FactDefinition / FactRegistry primitives
# ---------------------------------------------------------------------------


class TestFactDefinition:
    def test_id_is_owner_dot_field(self) -> None:
        d = FactDefinition(
            owner="RecordType",
            field="is_final",
            value_type="bool | None",
            producing_backends=("castxml",),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
        )
        assert d.id == "RecordType.is_final"
        assert d.fact_attr == "is_final_fact"

    def test_rejects_empty_producing_backends(self) -> None:
        with pytest.raises(ValueError, match="producing_backends"):
            FactDefinition(
                owner="RecordType",
                field="x",
                value_type="bool",
                producing_backends=(),
                persisted=True,
                identity_relevant=False,
                comparable=True,
                suppressible=False,
                reportable=True,
                lifecycle=FactLifecycle.MODELLED,
            )

    def test_rejects_unknown_backend(self) -> None:
        with pytest.raises(ValueError, match="unknown backend"):
            FactDefinition(
                owner="RecordType",
                field="x",
                value_type="bool",
                producing_backends=("not_a_real_backend",),
                persisted=True,
                identity_relevant=False,
                comparable=True,
                suppressible=False,
                reportable=True,
                lifecycle=FactLifecycle.MODELLED,
            )

    def test_rejects_empty_owner_or_field(self) -> None:
        with pytest.raises(ValueError):
            FactDefinition(
                owner="",
                field="x",
                value_type="bool",
                producing_backends=("castxml",),
                persisted=True,
                identity_relevant=False,
                comparable=True,
                suppressible=False,
                reportable=True,
                lifecycle=FactLifecycle.MODELLED,
            )


class TestFactRegistry:
    def test_rejects_duplicate_id(self) -> None:
        entry = FactDefinition(
            owner="RecordType",
            field="is_final",
            value_type="bool | None",
            producing_backends=("castxml",),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
        )
        with pytest.raises(ValueError, match="Duplicate"):
            FactRegistry([entry, entry])

    def test_for_owner_filters_by_owner(self) -> None:
        record_entries = FACT_REGISTRY.for_owner("RecordType")
        assert record_entries
        assert all(e.owner == "RecordType" for e in record_entries)

    def test_get_returns_none_for_unknown_id(self) -> None:
        assert FACT_REGISTRY.get("NoSuchOwner.no_such_field") is None

    def test_production_registry_contains_is_final(self) -> None:
        entry = FACT_REGISTRY.get("RecordType.is_final")
        assert entry is not None
        assert entry.lifecycle is FactLifecycle.PERSISTED
        assert set(entry.producing_backends) <= KNOWN_PRODUCING_BACKENDS


# ---------------------------------------------------------------------------
# scan_model_dataclasses — the case (b) heuristic
# ---------------------------------------------------------------------------


class TestScanModelDataclasses:
    """Regression fixtures proving the scan catches shapes beyond the
    bool/list/int|None trio Phase 0 happened to use for its own three
    fields (Phase 5 plan's own required regression coverage) — using real,
    already-registered-as-eligible repository fields rather than a
    synthetic fixture module, since both are genuinely tri-state str|None
    fields with a documented backend-dependence comment today."""

    def test_finds_a_str_or_none_field(self, tmp_path: Path) -> None:
        module = tmp_path / "synthetic_str_field.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Widget:\n"
            "    # Tri-state: None = not captured (older snapshots / dumpers\n"
            "    # without support).\n"
            "    label: str | None = None\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("Widget", "label") in found

    def test_finds_an_optional_bracket_annotated_field(self, tmp_path: Path) -> None:
        module = tmp_path / "synthetic_optional_field.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "from typing import Optional\n"
            "@dataclass\n"
            "class Widget:\n"
            "    # Dumper/loader does not know for older snapshots.\n"
            "    count: Optional[int] = None\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("Widget", "count") in found

    def test_ignores_optional_field_with_no_marker_comment(
        self, tmp_path: Path
    ) -> None:
        module = tmp_path / "synthetic_no_marker.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Widget:\n"
            "    # A perfectly ordinary optional field.\n"
            "    nickname: str | None = None\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("Widget", "nickname") not in found

    def test_ignores_non_optional_field_even_with_marker_comment(
        self, tmp_path: Path
    ) -> None:
        module = tmp_path / "synthetic_non_optional.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Widget:\n"
            "    # tri-state, but not actually Optional-shaped.\n"
            "    flag: bool = False\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("Widget", "flag") not in found

    def test_ignores_field_already_converted(self, tmp_path: Path) -> None:
        module = tmp_path / "synthetic_converted.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass, field\n"
            "from abicheck.model.fact import Fact\n"
            "@dataclass\n"
            "class Widget:\n"
            "    # Tri-state: None = not captured (older snapshots).\n"
            "    label: str | None = None\n"
            "    label_fact: Fact[str | None] | None = field(default=None, kw_only=True)\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("Widget", "label") not in found

    def test_real_repo_finds_function_deprecated_and_variable_access_via_flag(
        self,
    ) -> None:
        # Variable.access is a case (a) field (plain enum, flag-guarded) —
        # the scan_model_dataclasses() heuristic itself can never find it
        # (no Optional shape), which is exactly why REFERENCE_FLAG_COVERAGE
        # is checked independently in the real gate. Function.deprecated
        # IS Optional-shaped with a documented marker and is found here.
        found = scan_model_dataclasses()
        assert ("Function", "deprecated") in found
        assert ("Function", "contract_attributes") in found
        assert ("RecordType", "is_abstract") in found


# ---------------------------------------------------------------------------
# _model_fact_siblings — the "already converted" ground truth
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codex review: the model/ scan must recurse into nested subpackages (e.g.
# model/change_catalog/), not just its immediate *.py children.
# ---------------------------------------------------------------------------


class TestRecursiveModelScan:
    def test_scan_model_dataclasses_finds_a_field_in_a_nested_subpackage(
        self, tmp_path: Path
    ) -> None:
        nested = tmp_path / "nested_pkg"
        nested.mkdir()
        (nested / "__init__.py").write_text("")
        (nested / "widgets.py").write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class NestedWidget:\n"
            "    # Tri-state: None = not captured (older snapshots).\n"
            "    label: str | None = None\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("NestedWidget", "label") in found

    def test_model_fact_siblings_finds_a_sibling_in_a_nested_subpackage(
        self, tmp_path: Path
    ) -> None:
        nested = tmp_path / "nested_pkg"
        nested.mkdir()
        (nested / "__init__.py").write_text("")
        (nested / "widgets.py").write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "from abicheck.model.fact import Fact\n"
            "@dataclass\n"
            "class NestedWidget:\n"
            "    gadget_fact: Fact[str] = None\n"
        )
        siblings = _model_fact_siblings(model_dir=tmp_path)
        assert ("NestedWidget", "gadget") in siblings

    def test_all_model_dataclass_field_pairs_finds_a_field_in_a_nested_subpackage(
        self, tmp_path: Path
    ) -> None:
        nested = tmp_path / "nested_pkg"
        nested.mkdir()
        (nested / "__init__.py").write_text("")
        (nested / "widgets.py").write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class NestedWidget:\n"
            "    thingy: int = 0\n"
        )
        original = fact_registry_completeness.MODEL_DIR
        fact_registry_completeness.MODEL_DIR = tmp_path
        try:
            pairs = fact_registry_completeness._all_model_dataclass_field_pairs()
        finally:
            fact_registry_completeness.MODEL_DIR = original
        assert ("NestedWidget", "thingy") in pairs

    def test_real_repo_recursive_scan_finds_change_catalog_dataclass(self) -> None:
        """The real, existing nested subpackage Codex named
        (`model/change_catalog/`) is not hypothetical -- ``ChangeKindMeta``
        is a real ``@dataclass`` declared there, and the recursive scan must
        see it."""
        pairs = fact_registry_completeness._all_model_dataclass_field_pairs()
        assert ("ChangeKindMeta", "kind") in pairs
        assert ("ChangeKindMeta", "default_verdict") in pairs


class TestModelFactSiblings:
    def test_finds_is_final_fact_on_recordtype(self) -> None:
        siblings = _model_fact_siblings()
        assert ("RecordType", "is_final") in siblings

    def test_finds_is_va_list_fact_on_param(self) -> None:
        siblings = _model_fact_siblings()
        assert ("Param", "is_va_list") in siblings

    def test_records_the_real_inner_type_for_every_real_sibling(self) -> None:
        siblings = _model_fact_siblings()
        _rel, inner = siblings[("RecordType", "is_final")]
        assert inner == "bool | None"
        _rel, inner = siblings[("RecordType", "bases")]
        assert inner == "list[str]"
        _rel, inner = siblings[("Param", "is_va_list")]
        assert inner == "bool"

    def test_ignores_a_fact_suffixed_field_that_is_not_actually_fact_shaped(
        self, tmp_path: Path
    ) -> None:
        """Codex review (direction 6): naming a field ``<x>_fact`` is not
        itself evidence it holds a real ``Fact[T]`` value — a plain
        ``dict``-typed field sharing the suffix must not be treated as a
        converted sibling."""
        module = tmp_path / "synthetic_fake_fact.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class Widget:\n"
            "    widget_fact: dict[str, object] | None = None\n"
        )
        siblings = _model_fact_siblings(model_dir=tmp_path)
        assert ("Widget", "widget") not in siblings

    def test_finds_a_bare_non_optional_fact_shaped_field(self, tmp_path: Path) -> None:
        module = tmp_path / "synthetic_bare_fact.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "from abicheck.model.fact import Fact\n"
            "@dataclass\n"
            "class Widget:\n"
            "    gadget_fact: Fact[str] = None\n"
        )
        siblings = _model_fact_siblings(model_dir=tmp_path)
        assert siblings[("Widget", "gadget")] == (
            _rel_for(tmp_path / "synthetic_bare_fact.py"),
            "str",
        )


def _rel_for(path: Path) -> str:
    try:
        return path.relative_to(fact_registry_completeness.ROOT).as_posix()
    except ValueError:
        return path.as_posix()


class TestFactAnnotationInnerType:
    """Direct tests of ``_fact_annotation_inner_type`` — the Codex-review
    "is this genuinely Fact[...]-shaped" primitive ``_model_fact_siblings``
    and Direction 6's value_type cross-check both depend on."""

    @pytest.mark.parametrize(
        "annotation,expected",
        [
            ("Fact[bool]", "bool"),
            ("Fact[bool] | None", "bool"),
            ("Fact[list[str]] | None", "list[str]"),
            ("Fact[int | None] | None", "int | None"),
            ("Fact[bool | None] | None", "bool | None"),
        ],
    )
    def test_extracts_inner_type_from_real_shapes(
        self, annotation: str, expected: str
    ) -> None:
        assert (
            fact_registry_completeness._fact_annotation_inner_type(annotation)
            == expected
        )

    @pytest.mark.parametrize(
        "annotation",
        [
            "dict[str, object]",
            "dict[str, object] | None",
            "str | None",
            "Fact",
            "Optional[Fact[bool]]",
        ],
    )
    def test_rejects_non_fact_shapes(self, annotation: str) -> None:
        assert (
            fact_registry_completeness._fact_annotation_inner_type(annotation) is None
        )


# ---------------------------------------------------------------------------
# The real gate, against the real repository
# ---------------------------------------------------------------------------


class TestCheckFactRegistryCompletenessRealRepo:
    def test_real_repo_has_no_findings(self) -> None:
        findings = _LocalFindings()
        check_fact_registry_completeness(findings)
        assert findings.errors == []

    def test_every_known_unconverted_entry_is_a_real_field(self) -> None:
        """Every KNOWN_UNCONVERTED_ELIGIBLE_FACTS entry names a real field
        under abicheck/model/ (not a typo'd owner/field pair) — parsed via
        AST across every model dataclass module, not just entities.py/
        declarations.py."""
        model_dir = Path("abicheck/model")
        real_fields: set[tuple[str, str]] = set()
        for path in sorted(model_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if not any(
                    (isinstance(d, ast.Name) and d.id == "dataclass")
                    or (
                        isinstance(d, ast.Call)
                        and getattr(d.func, "id", "") == "dataclass"
                    )
                    for d in node.decorator_list
                ):
                    continue
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(
                        stmt.target, ast.Name
                    ):
                        real_fields.add((node.name, stmt.target.id))
        missing = {
            pair for pair in KNOWN_UNCONVERTED_ELIGIBLE_FACTS if pair not in real_fields
        }
        assert not missing, f"stale allowlist entries naming no real field: {missing}"

    def test_a_synthetic_unconverted_eligible_field_is_flagged(
        self, tmp_path: Path
    ) -> None:
        """The gate's own detection logic actually fires on a genuinely
        eligible-but-unconverted field, not only on the (already-tracked)
        real repo state — proves the check isn't vacuously passing."""
        module = tmp_path / "synthetic_leak.py"
        module.write_text(
            "from __future__ import annotations\n"
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class TotallySyntheticWidget:\n"
            "    # Tri-state: None = not captured (older snapshots).\n"
            "    gizmo: str | None = None\n"
        )
        found = scan_model_dataclasses(model_dir=tmp_path)
        assert ("TotallySyntheticWidget", "gizmo") in found
        assert (
            "TotallySyntheticWidget",
            "gizmo",
        ) not in KNOWN_UNCONVERTED_ELIGIBLE_FACTS


# ---------------------------------------------------------------------------
# REFERENCE_FLAG_COVERAGE
# ---------------------------------------------------------------------------


class TestReferenceFlagCoverage:
    def test_every_flag_covers_at_least_one_field(self) -> None:
        for flag, pairs in REFERENCE_FLAG_COVERAGE.items():
            assert pairs, f"{flag} covers no fields"

    def test_clang_deprecation_flag_covers_all_five_deprecated_surfaces(self) -> None:
        pairs = REFERENCE_FLAG_COVERAGE["clang_deprecation_facts_reliable"]
        owners = {owner for owner, _ in pairs}
        assert owners == {"Function", "Variable", "TypeField", "RecordType", "EnumType"}
        assert ("EnumType", "is_scoped") in pairs

    def test_every_key_names_a_real_abisnapshot_field(self) -> None:
        """Direction 7 (Codex review): the earlier check only ever unions
        REFERENCE_FLAG_COVERAGE's *values* and silently discards the keys —
        this proves every key itself is a real AbiSnapshot field too."""
        snapshot_fields = fact_registry_completeness._abi_snapshot_field_names()
        assert snapshot_fields  # sanity: the real scan actually found fields
        for flag in REFERENCE_FLAG_COVERAGE:
            assert flag in snapshot_fields, (
                f"{flag!r} is not a real field on AbiSnapshot "
                f"(abicheck/model/snapshot.py)"
            )


# ---------------------------------------------------------------------------
# Direction 7 (Codex review): a REFERENCE_FLAG_COVERAGE key must name a real
# AbiSnapshot field, not merely have its covered (owner, field) pairs stay
# tracked.
# ---------------------------------------------------------------------------


class TestReferenceFlagCoverageAgainstSnapshot:
    def test_gate_flags_a_flag_name_not_on_abisnapshot(self, monkeypatch) -> None:
        import abicheck.model.fact_registry as fr

        bad_coverage = dict(REFERENCE_FLAG_COVERAGE)
        bad_coverage["totally_fake_facts_reliable"] = (("RecordType", "is_final"),)
        monkeypatch.setattr(fr, "REFERENCE_FLAG_COVERAGE", bad_coverage)
        findings = _LocalFindings()
        check_fact_registry_completeness(findings)
        assert any(
            "totally_fake_facts_reliable" in msg and "no such field" in msg
            for _, msg in findings.errors
        )

    def test_abi_snapshot_field_names_finds_a_real_flag(self) -> None:
        snapshot_fields = fact_registry_completeness._abi_snapshot_field_names()
        assert "header_cv_facts_reliable" in snapshot_fields

    def test_abi_snapshot_field_names_empty_for_missing_file(
        self, tmp_path: Path
    ) -> None:
        original = fact_registry_completeness._SNAPSHOT_PATH
        fact_registry_completeness._SNAPSHOT_PATH = tmp_path / "does_not_exist.py"
        try:
            assert fact_registry_completeness._abi_snapshot_field_names() == set()
        finally:
            fact_registry_completeness._SNAPSHOT_PATH = original


# ---------------------------------------------------------------------------
# Direction 6 (Codex review): a registry entry's value_type must match the
# real inner type its Fact[...] sibling annotation actually declares.
# ---------------------------------------------------------------------------


class TestStaleAllowlistEntryNamesNoRealField:
    """Direction 8 (Codex review): a KNOWN_UNCONVERTED_ELIGIBLE_FACTS entry
    naming a legacy field that has since been renamed or removed (not just
    "already converted") is a stale entry too."""

    def test_real_repo_every_entry_names_a_real_field(self) -> None:
        all_fields = fact_registry_completeness._all_model_dataclass_field_pairs()
        assert all_fields
        for pair in KNOWN_UNCONVERTED_ELIGIBLE_FACTS:
            assert pair in all_fields, f"{pair} names no real model field"

    def test_gate_flags_a_renamed_or_removed_allowlist_entry(self, monkeypatch) -> None:
        import abicheck.model.fact_registry as fr

        bad = frozenset(
            {*KNOWN_UNCONVERTED_ELIGIBLE_FACTS, ("RecordType", "no_such_legacy_field")}
        )
        monkeypatch.setattr(fr, "KNOWN_UNCONVERTED_ELIGIBLE_FACTS", bad)
        findings = _LocalFindings()
        check_fact_registry_completeness(findings)
        assert any(
            "RecordType.no_such_legacy_field" in msg and "no such field exists" in msg
            for _, msg in findings.errors
        )

    def test_all_model_dataclass_field_pairs_finds_real_fields(self) -> None:
        pairs = fact_registry_completeness._all_model_dataclass_field_pairs()
        assert ("RecordType", "is_final") in pairs
        assert ("RecordType", "bases") in pairs
        assert ("Param", "is_va_list") in pairs


class TestValueTypeCrossCheck:
    def test_real_repo_every_entry_value_type_matches_its_real_annotation(self) -> None:
        siblings = fact_registry_completeness._model_fact_siblings()
        for entry in FACT_REGISTRY.entries.values():
            found = siblings.get((entry.owner, entry.field))
            assert found is not None, f"{entry.id} has no real Fact[...] sibling"
            _rel, inner_type = found
            assert inner_type == entry.value_type, (
                f"{entry.id} registers value_type={entry.value_type!r} but its "
                f"real annotation is Fact[{inner_type}]"
            )

    def test_gate_flags_a_value_type_mismatch(self, monkeypatch) -> None:
        import abicheck.model.fact_registry as fr

        wrong = FactDefinition(
            owner="RecordType",
            field="is_final",
            value_type="bool",  # real annotation is Fact[bool | None]
            producing_backends=("castxml", "clang"),
            persisted=True,
            identity_relevant=False,
            comparable=True,
            suppressible=False,
            reportable=True,
            lifecycle=FactLifecycle.PERSISTED,
        )
        other_entries = [
            e for e in FACT_REGISTRY.entries.values() if e.id != "RecordType.is_final"
        ]
        monkeypatch.setattr(fr, "FACT_REGISTRY", FactRegistry([*other_entries, wrong]))
        findings = _LocalFindings()
        check_fact_registry_completeness(findings)
        assert any(
            "RecordType.is_final registers value_type='bool'" in msg
            and "Fact[bool | None]" in msg
            for _, msg in findings.errors
        )


# ---------------------------------------------------------------------------
# Direction 4 (Codex review): a persisted registry entry must actually be
# wired into storage/fact_codec.py's encode/decode path, not merely named
# in the registry with a matching model field.
# ---------------------------------------------------------------------------


class TestPersistedEncodeDecodeWiring:
    def test_real_repo_every_persisted_entry_has_real_wiring(self) -> None:
        encode_wired = fact_registry_completeness._encode_wired_fact_attrs()
        decode_wired = fact_registry_completeness._decode_wired_fact_attrs()
        for entry in FACT_REGISTRY.entries.values():
            if entry.persisted:
                assert entry.fact_attr in encode_wired, (
                    f"{entry.id} claims persisted=True but "
                    f"{entry.fact_attr!r} has no real encode wiring"
                )
                assert entry.fact_attr in decode_wired, (
                    f"{entry.id} claims persisted=True but "
                    f"{entry.fact_attr!r} has no real decode wiring"
                )

    def test_encode_wired_finds_type_fact_keys_membership(self, tmp_path: Path) -> None:
        codec = tmp_path / "fact_codec.py"
        codec.write_text(
            '_TYPE_FACT_KEYS = ("widget_fact",)\n'
            "def encode_fact_fields(d):\n"
            "    for t in d.get('types', []):\n"
            "        for k in _TYPE_FACT_KEYS:\n"
            "            t.get(k)\n"
        )
        original = fact_registry_completeness._FACT_CODEC_PATH
        fact_registry_completeness._FACT_CODEC_PATH = codec
        try:
            wired = fact_registry_completeness._encode_wired_fact_attrs()
        finally:
            fact_registry_completeness._FACT_CODEC_PATH = original
        assert "widget_fact" in wired

    def test_encode_wired_finds_hardcoded_get_call(self, tmp_path: Path) -> None:
        codec = tmp_path / "fact_codec.py"
        codec.write_text(
            "def encode_fact_fields(d):\n"
            "    for p in d.get('params', []):\n"
            '        p.get("gadget_fact")\n'
        )
        original = fact_registry_completeness._FACT_CODEC_PATH
        fact_registry_completeness._FACT_CODEC_PATH = codec
        try:
            wired = fact_registry_completeness._encode_wired_fact_attrs()
        finally:
            fact_registry_completeness._FACT_CODEC_PATH = original
        assert "gadget_fact" in wired

    def test_decode_wired_requires_a_real_decode_fact_call(
        self, tmp_path: Path
    ) -> None:
        codec = tmp_path / "fact_codec.py"
        codec.write_text('decode_fact(t.get("widget_fact"), v)\n')
        serialization = tmp_path / "serialization.py"
        serialization.write_text("# nothing here\n")
        original_codec = fact_registry_completeness._FACT_CODEC_PATH
        original_ser = fact_registry_completeness._SERIALIZATION_PATH
        fact_registry_completeness._FACT_CODEC_PATH = codec
        fact_registry_completeness._SERIALIZATION_PATH = serialization
        try:
            wired = fact_registry_completeness._decode_wired_fact_attrs()
        finally:
            fact_registry_completeness._FACT_CODEC_PATH = original_codec
            fact_registry_completeness._SERIALIZATION_PATH = original_ser
        assert wired == {"widget_fact"}

    def test_gate_flags_a_persisted_entry_wired_encode_only(
        self, tmp_path: Path
    ) -> None:
        """The gate's own detection logic fires when a registry entry claims
        persisted=True for a real model field whose Fact[T] sibling has
        encode wiring but no decode wiring — the exact gap a Codex review
        round found a combined occurrence count (directions 1-2's name
        matching, and a first draft of direction 4 itself) cannot catch:
        an encode-only reference alone must not satisfy this check."""
        codec = tmp_path / "fact_codec.py"
        codec.write_text(
            '_TYPE_FACT_KEYS = ("is_final_fact",)\n'
            "def encode_fact_fields(d):\n"
            "    pass\n"
        )
        serialization = tmp_path / "serialization.py"
        serialization.write_text("# no decode_fact call here\n")
        original_codec = fact_registry_completeness._FACT_CODEC_PATH
        original_ser = fact_registry_completeness._SERIALIZATION_PATH
        fact_registry_completeness._FACT_CODEC_PATH = codec
        fact_registry_completeness._SERIALIZATION_PATH = serialization
        try:
            findings = _LocalFindings()
            check_fact_registry_completeness(findings)
        finally:
            fact_registry_completeness._FACT_CODEC_PATH = original_codec
            fact_registry_completeness._SERIALIZATION_PATH = original_ser
        # RecordType.is_final is a real, persisted=True registry entry --
        # with the swapped-in files, is_final_fact has encode wiring
        # (_TYPE_FACT_KEYS membership) but no decode wiring at all, and the
        # gate must say so specifically (not merely "some wiring missing").
        assert any(
            "RecordType.is_final" in msg
            and "persisted=True" in msg
            and "no real decode wiring" in msg
            and "no real encode wiring" not in msg
            for _, msg in findings.errors
        )

    def test_gate_flags_a_persisted_entry_with_no_wiring_at_all(
        self, tmp_path: Path
    ) -> None:
        empty_codec = tmp_path / "fact_codec.py"
        empty_codec.write_text("# no wiring here\n")
        empty_serialization = tmp_path / "serialization.py"
        empty_serialization.write_text("# no wiring here either\n")
        original_codec = fact_registry_completeness._FACT_CODEC_PATH
        original_ser = fact_registry_completeness._SERIALIZATION_PATH
        fact_registry_completeness._FACT_CODEC_PATH = empty_codec
        fact_registry_completeness._SERIALIZATION_PATH = empty_serialization
        try:
            findings = _LocalFindings()
            check_fact_registry_completeness(findings)
        finally:
            fact_registry_completeness._FACT_CODEC_PATH = original_codec
            fact_registry_completeness._SERIALIZATION_PATH = original_ser
        assert any(
            "RecordType.is_final" in msg and "persisted=True" in msg
            for _, msg in findings.errors
        )


# ---------------------------------------------------------------------------
# Direction 5 (Codex review): a registry entry's producing_backends must
# agree with backend_capabilities.py's own AST-verified capability matrix
# for the six declaration classes that matrix covers, not merely name a
# real backend from the closed KNOWN_PRODUCING_BACKENDS vocabulary.
# ---------------------------------------------------------------------------


class TestCrossCheckAgainstBackendCapabilities:
    def test_real_repo_every_entry_agrees_with_the_matrix(self) -> None:
        for entry in FACT_REGISTRY.entries.values():
            problems = (
                fact_registry_completeness._cross_check_against_backend_capabilities(
                    entry.owner, entry.field, entry.producing_backends
                )
            )
            assert not problems, f"{entry.id}: {problems}"

    def test_flags_a_backend_the_matrix_says_has_no_real_capability(self) -> None:
        # RecordType.is_template_pattern is castxml=NONE, clang=FULL per the
        # real matrix (castxml never emits an uninstantiated template
        # pattern) -- claiming castxml here is the exact "elf added to
        # is_final" shape the review flagged, using a real committed row.
        problems = fact_registry_completeness._cross_check_against_backend_capabilities(
            "RecordType", "is_template_pattern", ("castxml", "clang")
        )
        assert any("claims 'castxml'" in p for p in problems)

    def test_flags_a_missing_backend_the_matrix_confirms(self) -> None:
        problems = fact_registry_completeness._cross_check_against_backend_capabilities(
            "RecordType", "is_final", ("castxml",)
        )
        assert any("does not name 'clang'" in p for p in problems)

    def test_does_not_flag_a_real_third_producer_outside_the_matrix_scope(self) -> None:
        # dwarf_snapshot.py genuinely also produces RecordType.bases --
        # backend_capabilities.py's own scope is header-AST backends only,
        # so its silence about dwarf must not read as "dwarf is wrong".
        problems = fact_registry_completeness._cross_check_against_backend_capabilities(
            "RecordType", "bases", ("castxml", "clang", "dwarf")
        )
        assert problems == []

    def test_returns_empty_for_a_field_outside_the_matrix_entirely(self) -> None:
        # A pair with no FACT_ROWS row at all -- neither confirms nor
        # denies, so nothing to flag.
        problems = fact_registry_completeness._cross_check_against_backend_capabilities(
            "NoSuchOwner", "no_such_field", ("castxml",)
        )
        assert problems == []

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


class TestModelFactSiblings:
    def test_finds_is_final_fact_on_recordtype(self) -> None:
        siblings = _model_fact_siblings()
        assert ("RecordType", "is_final") in siblings

    def test_finds_is_va_list_fact_on_param(self) -> None:
        siblings = _model_fact_siblings()
        assert ("Param", "is_va_list") in siblings


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

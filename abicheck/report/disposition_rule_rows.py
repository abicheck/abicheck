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

"""The JSON shape of one configured-rule audit row (ADR-067 P1).

Split out of :mod:`abicheck.report.disposition_audit` (at the production line
cap) because this is one responsibility of its own: how a
``RuleAuditRow`` is written to, and read back from, the ``disposition_audit``
JSON block. The three kinds keep their pre-P1 key names (``rule_id``,
``record_id``) and add the P1 fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from ..policy.rule_inventory import (
    KIND_ACKNOWLEDGMENT,
    STATUS_UNUSED,
    RuleAuditRow,
    RuleRef,
)


def iso_date(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _missing_evidence_json(row: RuleAuditRow) -> dict[str, object]:
    """``missing_evidence`` (ADR-067 P1 addendum 2), on ``not_evaluated`` rows only."""
    return (
        {}
        if row.missing_evidence is None
        else {"missing_evidence": row.missing_evidence}
    )


def _identity_json(row: RuleAuditRow) -> dict[str, object]:
    """``rule_identity`` next to ``rule_id``, for an id-less rule only."""
    return {} if row.rule_identity is None else {"rule_identity": row.rule_identity}


def suppression_row_json(row: RuleAuditRow) -> dict[str, object]:
    """A suppression row, as JSON. Keeps the pre-P1 keys, adds the P1 ones."""
    return {
        "rule_id": row.rule_id,
        **_identity_json(row),
        "source_file": row.source_file,
        "entry_index": row.entry_index,
        "reason": row.reason,
        "label": row.label,
        "expires": iso_date(row.expires),
        "intent": row.intent,
        "allow_public_break": row.allow_public_break,
        "status": row.status,
        "matched_count": row.matched_count,
        "shadowed_count": row.shadowed_count,
        "selector_matched_count": row.selector_matched_count,
        **_missing_evidence_json(row),
    }


def reclassify_row_json(row: RuleAuditRow) -> dict[str, object]:
    """A reclassify row. ``matched_count`` keeps its pre-P1 meaning (verdict
    changed); ``changed_count`` names the same number explicitly (ADR-067 P1)."""
    return {
        "rule_id": row.rule_id,
        **_identity_json(row),
        "source_file": row.source_file,
        "entry_index": row.entry_index,
        "label": row.label,
        "reason": row.reason,
        "expires": iso_date(row.expires),
        "status": row.status,
        "matched_count": row.matched_count,
        "changed_count": row.matched_count,
        "shadowed_count": row.shadowed_count,
        "selector_matched_count": row.selector_matched_count,
        **_missing_evidence_json(row),
    }


def acknowledgment_row_json(row: RuleAuditRow) -> dict[str, object]:
    return {
        "record_id": row.rule_id,
        "source_file": row.source_file,
        "entry_index": row.entry_index,
        "reason": row.reason,
        "expires": iso_date(row.expires),
        "status": row.status,
        "matched_count": row.matched_count,
        **_missing_evidence_json(row),
    }


def row_from_json(kind: str, entry: Mapping[str, Any]) -> RuleAuditRow:
    """Rebuild one row from its JSON entry (the round trip ``from_dict`` needs)."""
    rule_key = "record_id" if kind == KIND_ACKNOWLEDGMENT else "rule_id"
    matched = int(entry.get("matched_count", 0) or 0)
    status = str(entry.get("status") or ("active" if matched else STATUS_UNUSED))
    expires_raw = entry.get("expires")
    index_raw = entry.get("entry_index")
    return RuleAuditRow(
        ref=RuleRef(
            kind,
            entry.get("source_file"),
            int(index_raw) if index_raw is not None else None,
        ),
        rule_id=str(entry[rule_key]),
        label=entry.get("label"),
        reason=entry.get("reason"),
        expires=date.fromisoformat(str(expires_raw)) if expires_raw else None,
        status=status,
        matched_count=matched,
        shadowed_count=int(entry.get("shadowed_count", 0) or 0),
        selector_matched_count=int(entry.get("selector_matched_count", matched) or 0),
        intent=str(entry.get("intent") or "unspecified"),
        allow_public_break=bool(entry.get("allow_public_break", False)),
        missing_evidence=(
            str(entry["missing_evidence"]) if entry.get("missing_evidence") else None
        ),
        rule_identity=(
            str(entry["rule_identity"]) if entry.get("rule_identity") else None
        ),
    )

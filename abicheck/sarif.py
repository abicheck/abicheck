# Copyright 2026 Nikolay Petrov
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

"""SARIF 2.1.0 output for abicheck.

Produces a Static Analysis Results Interchange Format (SARIF) document
suitable for upload to GitHub Code Scanning via:

    abicheck compare old.so new.so --format sarif > results.sarif

GitHub Code Scanning docs:
  https://docs.github.com/en/code-security/code-scanning/integrating-with-code-scanning/sarif-support-for-code-scanning

SARIF spec: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.checker_policy import impact_for, policy_for
from abicheck.reporter import apply_show_only

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------
_BREAKING_SEVERITY = "error"

# Rule ID = change_kind value (snake_case, already stable)


def _tool_version() -> str:
    try:
        return _pkg_version("abicheck")
    except Exception:  # noqa: BLE001
        return "unknown"


_VERDICT_TO_SARIF_LEVEL = {
    Verdict.BREAKING: "error",
    Verdict.API_BREAK: "error",
    Verdict.COMPATIBLE_WITH_RISK: "warning",
    Verdict.COMPATIBLE: "note",
}


def _severity(change: Change) -> str:
    # Honour an A4 per-finding effective_verdict (ADR-027): a demoted opaque/
    # PIMPL layout change reports as a SARIF "note", not an "error".
    eff = getattr(change, "effective_verdict", None)
    if isinstance(eff, Verdict):
        return _VERDICT_TO_SARIF_LEVEL.get(eff, policy_for(change.kind).severity)
    return policy_for(change.kind).severity


def _rule_for(kind: ChangeKind) -> dict[str, Any]:
    """Produce a SARIF reportingDescriptor for a ChangeKind."""
    rule_id = kind.value
    severity = policy_for(kind).severity
    doc_slug = policy_for(kind).doc_slug
    help_uri = f"https://github.com/napetrov/abicheck/blob/main/docs/abi_breaking_cases_catalog.md#{doc_slug}"
    impact = impact_for(kind)
    full_desc = impact if impact else f"ABI change detected: {rule_id.replace('_', ' ')}"
    return {
        "id": rule_id,
        "name": "".join(w.capitalize() for w in rule_id.split("_")),
        "shortDescription": {"text": rule_id.replace("_", " ").capitalize()},
        "fullDescription": {"text": full_desc},
        "helpUri": help_uri,
        "defaultConfiguration": {"level": severity},
        "properties": {"tags": ["abi", "binary-compatibility"]},
    }


def _result_for(
    change: Change, library: str, old_version: str, new_version: str
) -> dict[str, Any]:
    """Produce a SARIF result object for a Change."""
    msg_parts = [change.description]
    if change.old_value or change.new_value:
        msg_parts.append(f"({change.old_value or '?'} → {change.new_value or '?'})")

    # Build physical location — prefer source header over .so when available
    phys_loc: dict[str, Any]
    if change.source_location:
        # Parse "header.h:42" into file + line
        parts = change.source_location.rsplit(":", 1)
        uri = parts[0]
        phys_loc = {
            "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
        }
        if len(parts) == 2:
            try:
                phys_loc["region"] = {"startLine": int(parts[1])}
            except ValueError:
                pass
    else:
        phys_loc = {
            "artifactLocation": {"uri": library, "uriBaseId": "%SRCROOT%"},
        }

    properties: dict[str, Any] = {
        "symbol": change.symbol,
        "oldVersion": old_version,
        "newVersion": new_version,
    }
    if change.old_value is not None:
        properties["oldValue"] = change.old_value
    if change.new_value is not None:
        properties["newValue"] = change.new_value
    if change.affected_symbols:
        properties["affectedSymbols"] = change.affected_symbols
    if change.caused_by_type:
        properties["causedByType"] = change.caused_by_type
    if change.caused_count > 0:
        properties["causedCount"] = change.caused_count

    return {
        "ruleId": change.kind.value,
        "level": _severity(change),
        "message": {
            "text": " ".join(msg_parts),
        },
        "locations": [
            {
                "physicalLocation": phys_loc,
                "logicalLocations": [
                    {
                        "name": change.symbol,
                        "kind": "member",
                    }
                ],
            }
        ],
        "properties": properties,
    }


def to_sarif(
    result: DiffResult,
    *,
    show_only: str | None = None,
) -> dict[str, Any]:
    """Convert a DiffResult to a SARIF 2.1.0 document (dict)."""
    tool_version = _tool_version()

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)

    # Collect unique rules used
    rules_seen: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []

    for change in changes:
        rule_id = change.kind.value
        if rule_id not in rules_seen:
            rules_seen[rule_id] = _rule_for(change.kind)
        sarif_results.append(
            _result_for(change, result.library, result.old_version, result.new_version)
        )

    # Overall ABI verdict as a notification
    invocation_success = result.verdict != Verdict.BREAKING

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "abicheck",
                        "version": tool_version,
                        "informationUri": "https://github.com/napetrov/abicheck",
                        "rules": list(rules_seen.values()),
                    }
                },
                "invocations": [
                    {
                        "executionSuccessful": invocation_success,
                        # Exit codes mirror abicheck compare CLI contract:
                        # BREAKING=4 (mapped to SARIF 1), API_BREAK=2, others=0.
                        # COMPATIBLE_WITH_RISK intentionally exits 0 — binary-compatible,
                        # deployment risk is surfaced via exitCodeDescription only.
                        "exitCode": (
                            4
                            if result.verdict == Verdict.BREAKING
                            else 2
                            if result.verdict == Verdict.API_BREAK
                            else 0
                        ),
                        "exitCodeDescription": result.verdict.value,
                    }
                ],
                "results": sarif_results,
                "automationDetails": {
                    "id": f"abicheck/{result.library}/{result.old_version}_to_{result.new_version}",
                    "description": {
                        "text": (
                            f"ABI comparison: {result.library} "
                            f"{result.old_version} → {result.new_version} "
                            f"verdict={result.verdict.value}"
                        )
                    },
                },
                "properties": {
                    "abiVerdict": result.verdict.value,
                    "oldVersion": result.old_version,
                    "newVersion": result.new_version,
                    "library": result.library,
                    "changeCount": len(changes),
                    "suppressedCount": result.suppressed_count,
                    **({"redundantCount": result.redundant_count} if result.redundant_count > 0 else {}),
                    **({"oldFile": {"path": result.old_metadata.path, "sha256": result.old_metadata.sha256, "sizeBytes": result.old_metadata.size_bytes}} if result.old_metadata is not None else {}),
                    **({"newFile": {"path": result.new_metadata.path, "sha256": result.new_metadata.sha256, "sizeBytes": result.new_metadata.size_bytes}} if result.new_metadata is not None else {}),
                    "confidence": result.confidence.value,
                    "evidenceTiers": list(result.evidence_tiers),
                    **({"coverageWarnings": list(result.coverage_warnings)} if result.coverage_warnings else {}),
                    "policy": result.policy or "strict_abi",
                    **({"policyOverrides": {k.value: v.value for k, v in result.policy_file.overrides.items()}} if result.policy_file and result.policy_file.overrides else {}),
                    # ADR-024 §D4/D5: header-scope ledger. Out-of-surface
                    # findings are disclosed here for auditability (never
                    # silently dropped) when --scope-public-headers is active.
                    **(
                        {
                            "surfaceScope": {
                                "enabled": True,
                                "confidence": result.surface_scope_confidence,
                                "notes": list(result.surface_scope_notes),
                                "outOfSurfaceCount": result.out_of_surface_count,
                                "outOfSurfaceChanges": [
                                    {
                                        "kind": c.kind.value,
                                        "symbol": c.symbol,
                                        "description": c.description,
                                        **({"sourceLocation": c.source_location} if c.source_location else {}),
                                        **({"reason": c.surface_exclusion_reason} if c.surface_exclusion_reason else {}),
                                    }
                                    for c in result.out_of_surface_changes
                                ],
                            }
                        }
                        if result.scope_to_public_surface
                        else {}
                    ),
                },
            }
        ],
    }


def to_sarif_str(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
) -> str:
    """Serialize DiffResult to a SARIF JSON string."""
    return json.dumps(to_sarif(result, show_only=show_only), indent=indent)


def write_sarif(result: DiffResult, path: Path) -> None:
    """Write SARIF output to *path*."""
    path.write_text(to_sarif_str(result), encoding="utf-8")

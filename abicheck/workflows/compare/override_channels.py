# SPDX-License-Identifier: Apache-2.0
"""The ``.abicheck.yml`` keys a typed ``compare`` request applies, and their fields.

Each row is ``(config path, CompareRequest field, resolved attribute)``. A
``config_overrides`` entry whose path is a row here is applied to that field;
the CLI's ``--set`` accepts the same rows (see ``frontends.cli.options.config_set``).
"""

from __future__ import annotations

TYPED_OVERRIDE_CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("scope.public", "scope_public", "scope_public"),
    (
        "scope.collapse_versioned_symbols",
        "collapse_versioned_symbols",
        "collapse_versioned_symbols",
    ),
    ("scope.public_symbols", "public_symbols", "public_symbols"),
    ("severity.abi_breaking", "severity_abi_breaking", "merged_severity_abi_breaking"),
    (
        "severity.potential_breaking",
        "severity_potential_breaking",
        "merged_severity_potential_breaking",
    ),
    (
        "severity.quality_issues",
        "severity_quality_issues",
        "merged_severity_quality_issues",
    ),
    ("severity.addition", "severity_addition", "merged_severity_addition"),
    ("suppression.strict", "strict_suppressions", "strict_suppressions"),
    (
        "suppression.require_justification",
        "require_justification",
        "require_justification",
    ),
    (
        "assurance.require_complete",
        "require_complete_analysis",
        "require_complete_analysis",
    ),
    ("debug.format", "debug_format", "debug_format"),
    ("debug.dwarf_only", "dwarf_only", "dwarf_only"),
    ("debug.debuginfod", "debuginfod", "debuginfod"),
    ("debug.debuginfod_url", "debuginfod_url", "debuginfod_url"),
    ("deployment", "env_matrix", "deployment"),
)

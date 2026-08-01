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

"""ADR-049: what the ``compare`` CLI, and only it, knows about its own run.

``checker.compare`` builds the persisted contract context from the arguments
it was handed, so it can honestly claim no more than ``API_REQUEST`` for any
of them. Two things are resolved *after* it returns, by this front end: the
gate (exit-code scheme and the four severity levels) and which D7 layer
actually selected the contract mode. This module refreshes both onto the
context before any report is rendered.

Split out of :mod:`abicheck.cli_compare_helpers` when that file reached the
2000-line hard cap -- a cohesive unit (one concern, one caller) rather than
an arbitrary cut. Deliberately a **leaf**: it imports nothing from its
caller, so no cycle forms. "Which parameters did the user actually type" is
the caller's question to answer (it holds the Click context), so the answers
arrive here as data (*typed*) rather than this module reaching back for
them, and ``resolved_cfg``/``project_cfg`` stay ``Any`` for the same reason
-- typing them would import the very module this one is split out of.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Every Click parameter whose "was it typed?" answer this module needs. The
#: caller reads them from its own Click context (see ``record_resolved_gate``'s
#: *typed*), which is what keeps this module free of any import back into it.
SEVERITY_PARAMS: tuple[str, ...] = (
    "contract_mode",
    "exit_code_scheme",
    "severity_preset",
    "severity_abi_breaking",
    "severity_potential_breaking",
    "severity_quality_issues",
    "severity_addition",
)

#: The four :class:`~abicheck.severity.SeverityConfig` categories, in the
#: spelling ``compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS``
#: keys its ``gate.severity.<category>`` provenance entries by.
_SEVERITY_CATEGORIES = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)


def _cli_field_provenance(field: str, *, from_cli: bool, from_config: bool) -> Any:
    """Which D7 layer selected one field, as this front end actually resolved it.

    ``checker.compare`` records ``API_REQUEST`` for everything it was handed
    because that is all a core verb can honestly claim (see
    :mod:`abicheck.contract_context`). The gate is different: it is resolved
    here, by the front end, so the real layer *is* observable — a typed flag
    is ``EXPLICIT_CLI``, a value that only ``.abicheck.yml`` supplied is
    ``PROJECT_CONFIG``, and neither is ``BUILT_IN_DEFAULT``.
    """
    from .compatibility_evaluation_config import SelectedByEntry, ValueProvenance
    from .contract_relevance_types import SelectorLayer

    layer = (
        SelectorLayer.EXPLICIT_CLI
        if from_cli
        else SelectorLayer.PROJECT_CONFIG
        if from_config
        else SelectorLayer.BUILT_IN_DEFAULT
    )
    return ValueProvenance(
        layer=layer,
        source_kind=(
            "cli_option" if from_cli else "project_config" if from_config else None
        ),
        field_location=field,
        selected_by=(
            SelectedByEntry(layer=layer, option=f"--{field.replace('_', '-')}"),
        ),
    )


def _severity_from_config(project_cfg: Any, category: str) -> bool:
    """Whether ``.abicheck.yml`` supplied *category*'s level specifically.

    ``ResolvedCompareConfig.severity_active`` is deliberately run-wide ("a
    severity level was set *anywhere*"), so using it per category recorded
    the three untouched categories as ``PROJECT_CONFIG`` for a run whose only
    input was ``--severity-abi-breaking`` and which had no project config at
    all (Codex review, fresh evidence). ``BuildConfig`` carries the four
    levels separately, so the honest answer is available per field --
    ``severity.preset`` counts for every category, since that is what it
    sets.
    """
    if project_cfg is None:
        return False
    if getattr(project_cfg, "severity_preset", None) is not None:
        return True
    return getattr(project_cfg, f"severity_{category}", None) is not None


def record_resolved_gate(
    result: Any,
    resolved_cfg: Any,
    project_cfg: Any,
    *,
    typed: Mapping[str, bool],
) -> None:
    """Replace the persisted context's placeholder gate with the real one.

    A no-op unless ``--contract-evaluation`` produced a context. Runs before
    any report is rendered, so every output path sees the gate the run was
    actually scored with rather than :class:`GateConfig`'s built-in defaults
    (Codex review, fresh evidence).
    """
    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .contract_context import with_field_provenance, with_resolved_gate

    # `checker.compare` receives the mode as an argument and can claim no
    # more than `API_REQUEST` for it; a typed `--contract` is `EXPLICIT_CLI`,
    # and only this front end knows which it was (Codex review). Left alone
    # when the flag was not typed, so the legacy-alias provenance
    # `resolve_legacy_contract_mode` produced for `--scope-public-headers`
    # survives.
    if typed.get("contract_mode"):
        ctx = with_field_provenance(
            ctx,
            {
                "contract.mode": _cli_field_provenance(
                    "contract", from_cli=True, from_config=False
                )
            },
        )

    scheme_cli = typed.get("exit_code_scheme", False)
    # Per category, not one verdict for the block: `--severity-abi-breaking`
    # beside an `addition` level only `.abicheck.yml` supplied are two
    # different layers, and the old `any(...)` labelled both `EXPLICIT_CLI`
    # (Codex review, fresh evidence). `--severity-preset` sets all four, so
    # it counts as a typed flag for each.
    preset_cli = typed.get("severity_preset", False)
    result.contract_context = with_resolved_gate(
        ctx,
        exit_code_scheme=resolved_cfg.exit_code_scheme,
        severity=resolved_cfg.severity,
        scheme_provenance=_cli_field_provenance(
            "exit_code_scheme",
            from_cli=scheme_cli,
            # ``auto`` is this key's own built-in default, so a config that
            # left it alone did not select the resolved concrete scheme.
            from_config=getattr(project_cfg, "exit_code_scheme", "auto") != "auto",
        ),
        severity_provenance={
            category: _cli_field_provenance(
                f"severity_{category}",
                from_cli=preset_cli or typed.get(f"severity_{category}", False),
                from_config=_severity_from_config(project_cfg, category),
            )
            for category in _SEVERITY_CATEGORIES
        },
    )

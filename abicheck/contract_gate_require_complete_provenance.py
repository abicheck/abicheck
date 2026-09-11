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

"""``contract_context.with_resolved_gate``'s own ``gate.
require_complete_analysis`` field-provenance construction, split out purely
to keep ``contract_context.py`` under its ``architecture/debt.yaml``
``no_growth`` baseline (P2, Codex review, fresh evidence, PR #1222) -- one
small, independently testable leaf function rather than more inline code in
an already-capped file.

``assurance.require_complete`` has no D7 resolver of its own --
``resolve_compare_config`` reads it straight off ``.abicheck.yml`` with no
CLI override and no pack route (the same "simple config-demoted key" shape
as ``gate.fail_on_removed_library``/``release.dso_only``, neither of which
is projected into the canonical resolver's ``ProjectCompatibilityInputs``
either), so an explicit ``True`` can only ever have come from the project
config document the caller resolved -- :func:`with_resolved_gate` can name
that source itself rather than the caller constructing and threading a
provenance object for a value with only one possible non-default origin.
``False``/``None`` get no entry, the same "absent, not defaulted" rule the
sibling per-category ``severity_provenance`` follows for an unsupplied
category.

*project_config_path*/*project_config_sha256* identify the exact
``.abicheck.yml`` document that supplied ``assurance.require_complete`` --
the same path/digest identity every OTHER project-config-sourced
``field_provenance`` entry in the same receipt already carries (e.g. the
``policy.overrides``/``surface.internal_namespaces`` entries
``compatibility_evaluation_frontend.py`` builds with ``path=project_path,
sha256=project_sha256``). Without them, a project whose ``.abicheck.yml``
sets ONLY ``assurance.require_complete`` (no other override) left this
entry naming just the layer -- unable to identify or replay which exact
document/revision enabled the gate. The caller already has these values
resolved for the SAME request (they are what built the receipt's other
project-config-sourced entries); this function only threads them through
rather than re-reading the file a second time. ``None`` for either (the
release fan-out's own caller, which has no project-config document of its
own to name here) falls back to the layer-only entry this function has
always produced.
"""

from __future__ import annotations

from .compatibility_evaluation_config import SelectedByEntry, ValueProvenance
from .contract_relevance_types import SelectorLayer


def resolve_require_complete_analysis_provenance(
    *,
    default_value: bool,
    require_complete_analysis: bool | None,
    project_config_path: str | None,
    project_config_sha256: str | None,
) -> tuple[bool, ValueProvenance | None]:
    """Return ``(resolved_value, provenance_or_none)`` for ``gate.
    require_complete_analysis``.

    *default_value* is what the core verb's own :class:`GateConfig` already
    carries (the built-in default forwarded through when no front end has
    resolved the field). *require_complete_analysis* is the front end's own
    resolved value; ``None`` (the release fan-out's own caller, which has
    no per-library equivalent of this field yet) preserves *default_value*
    unchanged rather than silently asserting a value nobody resolved. See
    the module docstring for why only an explicit ``True`` gets a
    ``ValueProvenance`` entry.
    """
    resolved_value = (
        default_value
        if require_complete_analysis is None
        else require_complete_analysis
    )
    if not require_complete_analysis:
        return resolved_value, None
    return resolved_value, ValueProvenance(
        layer=SelectorLayer.PROJECT_CONFIG,
        source_kind="project_config" if project_config_path else None,
        path=project_config_path,
        sha256=project_config_sha256,
        field_location="assurance.require_complete",
        selected_by=(
            (
                SelectedByEntry(
                    layer=SelectorLayer.PROJECT_CONFIG,
                    option="assurance.require_complete",
                    path=project_config_path,
                    sha256=project_config_sha256,
                ),
            )
            if project_config_path
            else ()
        ),
    )

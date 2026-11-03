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

"""Structured function-template signatures for the L4 replay (case122).

The replay used to keep only a whole-subtree hash of a template, so a change
of an uninstantiated function template's parameter list (``clamp<T>(T, int,
int)`` -> ``clamp<T>(T, long, long)``) was indistinguishable from a body edit
and reported as ``template_body_changed`` (RISK). A consumer that takes the
address of ``clamp<int>`` as ``int (*)(int, int, int)`` stops compiling, so a
parameter-list change is a proven source break even though no library
binary carries the template.

:func:`template_relations` records, for a ``FunctionTemplateDecl``, the
templated declaration's parameter types with the template parameters
replaced by their position (``$0``), so renaming ``T`` is not a change.
:func:`template_change` grades a changed template: a different recorded
parameter list is ``HEADER_ONLY_FUNCTION_PARAMS_CHANGED`` (API_BREAK); any
other difference -- body only, return type only, or a side without the
record (older snapshots, class templates) -- stays ``TEMPLATE_BODY_CHANGED``
(RISK). Whether a binary exports an instantiation is graded by the artifact
tier, never here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from .source_abi import SourceEntity

PARAMS_KEY = "function_parameters"
_FUNCTION_KINDS = frozenset({"FunctionDecl", "CXXMethodDecl"})


def _template_param_names(node: Mapping[str, Any]) -> list[str]:
    return [
        str(c.get("name", ""))
        for c in node.get("inner", []) or []
        if isinstance(c, dict)
        and str(c.get("kind", "")).startswith("Template")
        and str(c.get("kind", "")).endswith("ParmDecl")
    ]


def template_parameter_types(node: Mapping[str, Any]) -> str | None:
    """``$0|int|int``-style parameter types of a function template, or None."""
    fns = [
        c
        for c in node.get("inner", []) or []
        if isinstance(c, dict) and c.get("kind") in _FUNCTION_KINDS
    ]
    if node.get("kind") != "FunctionTemplateDecl" or len(fns) != 1:
        return None
    names = [n for n in _template_param_names(node) if n]
    out: list[str] = []
    for p in fns[0].get("inner", []) or []:
        if not isinstance(p, dict) or p.get("kind") != "ParmVarDecl":
            continue
        spelling = str((p.get("type") or {}).get("qualType", ""))
        for i, n in enumerate(names):
            spelling = re.sub(rf"\b{re.escape(n)}\b", f"${i}", spelling)
        out.append(spelling)
    return "|".join(out)


def template_relations(node: Mapping[str, Any], params: list[str]) -> dict[str, str]:
    """The ``relations`` a replayed template entity carries."""
    relations = {
        "template_kind": str(node.get("kind", "")),
        "template_parameters": ",".join(params),
    }
    fn_params = template_parameter_types(node)
    if fn_params is not None:
        relations[PARAMS_KEY] = fn_params
    return relations


def template_change(
    ov: SourceEntity, nv: SourceEntity, discriminator: Any, loc: str
) -> Change:
    """The finding for a template whose fingerprint changed."""
    name = nv.qualified_name
    old_p = (ov.relations or {}).get(PARAMS_KEY)
    new_p = (nv.relations or {}).get(PARAMS_KEY)
    if old_p is not None and new_p is not None and old_p != new_p:
        return Change(
            kind=ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED,
            symbol=name,
            entity_discriminator=discriminator,
            description=(
                f"Uninstantiated public function template {name!r} changed its "
                f"parameter list ({old_p.replace('|', ', ')} -> "
                f"{new_p.replace('|', ', ')}). Source that names an exact "
                "instantiation type (e.g. takes its address) no longer compiles."
            ),
            old_value=old_p.replace("|", ", "),
            new_value=new_p.replace("|", ", "),
            source_location=loc,
        )
    return Change(
        kind=ChangeKind.TEMPLATE_BODY_CHANGED,
        symbol=name,
        entity_discriminator=discriminator,
        description=(
            f"Uninstantiated public template {name!r} implementation "
            "changed. Invisible to artifact comparison; consumers pick "
            "up the new body on recompile."
        ),
        old_value=ov.body_hash,
        new_value=nv.body_hash,
        source_location=loc,
    )

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

"""The header spelling of a declaration bound to an export across schemes.

``extract.header_export_binding`` binds a header declaration to an export
spelled in another C++ mangling scheme and makes the export's spelling the
declaration's ``mangled`` linker name. The header parse's own spelling is
kept as a diagnostic on the declaration's export fact, so a graph built from
the same header AST (whose edges name that spelling) can still join the
node. A leaf module: ``model.graph_entity_identity`` reads it without
importing ``model.surface_facts`` (which reaches ``model.snapshot``).
"""

from __future__ import annotations

__all__ = ["HEADER_SPELLING_DIAGNOSTIC_PREFIX", "bound_header_spelling"]

#: Diagnostic prefix on ``binary_exported_fact`` naming the header spelling.
HEADER_SPELLING_DIAGNOSTIC_PREFIX = "header-spelling:"


def bound_header_spelling(decl: object) -> str | None:
    """The header parse's own spelling of a declaration bound to an export
    spelled in another scheme, or ``None``."""
    fact = getattr(decl, "binary_exported_fact", None)
    for diag in getattr(fact, "diagnostics", ()) or ():
        if isinstance(diag, str) and diag.startswith(HEADER_SPELLING_DIAGNOSTIC_PREFIX):
            return diag[len(HEADER_SPELLING_DIAGNOSTIC_PREFIX) :] or None
    return None

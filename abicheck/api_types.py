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

"""Compatibility facade over :mod:`abicheck.workflows.request_inputs` and
:mod:`abicheck.workflows.contracts` (ADR-061 gap B).

The real implementation — the typed request/response structs for the
Tier-2 service layer (ADR-037 D2) — moved to those two `workflows`-owned
modules, split purely to keep each under the 800-line new-file ceiling:
``request_inputs.py`` owns :class:`InputSpec` and its validation helpers;
``contracts.py`` owns :class:`OutputSpec`/:class:`CompareRequest`/
:class:`DumpRequest`/:class:`CompareResult`, the pair/result shapes built
on top. :data:`HEADER_AST_FRONTENDS` moved one layer further in, to
:mod:`abicheck.model.header_ast_frontends`, because an `extract`-layer
caller (``buildsource/header_compile_context.py``) needs it too and
`extract` cannot depend on `workflows` — see that module's own docstring.

This flat module now only re-exports their combined public surface,
unchanged, so every existing import path
(``from abicheck.api_types import ...``) keeps working. Canonical internal
callers import the owning module directly.
"""

from __future__ import annotations

from .model.header_ast_frontends import HEADER_AST_FRONTENDS as HEADER_AST_FRONTENDS
from .workflows.contracts import (
    CompareRequest as CompareRequest,
    CompareResult as CompareResult,
    DumpRequest as DumpRequest,
    OutputSpec as OutputSpec,
)
from .workflows.request_inputs import (
    FRONTEND_CONTEXTS as FRONTEND_CONTEXTS,
    SUPPORTED_DEBUG_FORMATS as SUPPORTED_DEBUG_FORMATS,
    SUPPORTED_FRONTENDS as SUPPORTED_FRONTENDS,
    SUPPORTED_LANGS as SUPPORTED_LANGS,
    InputSpec as InputSpec,
    frontend_context_errors as frontend_context_errors,
    frontend_value_errors as frontend_value_errors,
    required_path as required_path,
)

__all__ = [
    "FRONTEND_CONTEXTS",
    "HEADER_AST_FRONTENDS",
    "SUPPORTED_DEBUG_FORMATS",
    "SUPPORTED_FRONTENDS",
    "SUPPORTED_LANGS",
    "CompareRequest",
    "CompareResult",
    "DumpRequest",
    "InputSpec",
    "OutputSpec",
    "frontend_context_errors",
    "frontend_value_errors",
    "required_path",
]

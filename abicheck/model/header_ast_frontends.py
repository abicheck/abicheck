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

"""The set of header-AST frontends a header-only dump/parse may select
(ADR-061 gap B).

Split out of the former flat ``abicheck.api_types`` module (now
`workflows`-owned, see ``workflows/request_inputs.py``) because this one
constant has a second, independent real need: ``buildsource/
header_compile_context.py`` (`extract`) resolves a header-AST frontend
choice too, and `extract`'s ADR-061 imports are `model`/`storage` only --
it cannot depend on a `workflows`-owned module. A bare frozenset with no
further dependency is squarely a `model`-owned shared domain value, so it
moved here rather than gaining a second, duplicated definition.
"""

from __future__ import annotations

#: AST frontends valid for header-AST parsing (a subset of the CLI's full
#: ``--ast-frontend`` choice set, :data:`abicheck.workflows.request_inputs.
#: SUPPORTED_FRONTENDS`, minus ``"android"`` -- a source-ABI-only value with
#: no header-AST path of its own). "hybrid" (G28 Phase 3) runs both castxml
#: and clang and merges them.
HEADER_AST_FRONTENDS: frozenset[str] = frozenset({"auto", "castxml", "clang", "hybrid"})

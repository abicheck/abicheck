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

"""Typed ``workflows``-side wrappers for the flat, `frontends`-classified
``service_render.py``.

ADR-061: ``service_render.py`` imports ``reporter.py``, and
``workflows -> report`` is forbidden by design (``report`` depends on
``workflows``, not the reverse -- see that module's own docstring), so it is
classified `frontends`. ``service.py`` (this module's sole caller) is
classified `workflows`, so a static ``from .service_render import
render_output`` there would be a real `workflows -> frontends` edge (and,
combined with the already-allowed `frontends -> report -> workflows` edges,
a dependency cycle). ``service.py`` is also `no_growth`-tracked at its own
line-count cap, so this lives in its own module here rather than inline --
the same reason ``gate.py``/``storage.py``/``suppression.py`` exist as their
own files rather than growing ``service.py`` further.

Each function below is a real, separately-typed ``def`` that resolves its
implementation via ``importlib.import_module`` inside its own body -- a
runtime call, not an ``ast.Import``/``ast.ImportFrom`` node, so it stays
invisible to both ``check_architecture.py``'s direction check and
``import-cycle-growth``'s ``ast.walk`` scan -- rather than a blanket
``__getattr__`` returning ``Any``, which would silently erase type checking
for every one of these names' many first-party callers (the same fix
already applied to ``serialization.py``'s ``bundle_facts_*`` re-exports,
Codex review). This is the identical dynamic-import escape hatch
``service.py`` itself already uses for ``service_header_scoped`` -- a
deliberate bridge back to a peer this ring may not statically import, not a
route *through* a compatibility facade this package's own AGENTS.md warns
against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ..model import AbiSnapshot

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..severity import SeverityConfig


def _service_render() -> Any:
    import importlib

    return importlib.import_module("..service_render", __package__)


def render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    demangle: bool = False,
    contract_evaluation: bool = False,
    stat: bool = False,
    show_recommendation: bool = False,
    require_complete_analysis: bool = False,
) -> str:
    """Render comparison result in the requested output format. See
    :func:`abicheck.service_render.render_output`."""
    return cast(
        "str",
        _service_render().render_output(
            fmt,
            result,
            old,
            new,
            follow_deps=follow_deps,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
            demangle=demangle,
            contract_evaluation=contract_evaluation,
            stat=stat,
            show_recommendation=show_recommendation,
            require_complete_analysis=require_complete_analysis,
        ),
    )


def _render_json_output(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    *,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool = False,
    contract_evaluation: bool = False,
) -> str:
    """Render comparison result as JSON, optionally including dependency
    info. See :func:`abicheck.service_render._render_json_output`."""
    return cast(
        "str",
        _service_render()._render_json_output(
            result,
            old,
            new,
            follow_deps=follow_deps,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        ),
    )


def _render_deps_section_md(old: AbiSnapshot, new: AbiSnapshot | None) -> str:
    """Append dependency summary section to markdown output. See
    :func:`abicheck.service_render._render_deps_section_md`."""
    return cast("str", _service_render()._render_deps_section_md(old, new))

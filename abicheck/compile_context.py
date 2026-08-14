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

""":class:`CompileContext` -- a leaf module (stdlib-only deps) so a module
outside the ``cli``/``service``/``scan_engine`` import-cycle-allowlisted
cluster (ADR-055 D1's ``api_types.InputSpec.compile`` field, in particular)
can type-annotate against it without joining that cluster itself
(AGENTS.md "What NOT to do": prefer moving shared logic to a leaf module
both sides can depend on, over extending ``IMPORT_CYCLE_ALLOWLIST``).

``service_scan.py`` re-exports this name (``from .compile_context import
CompileContext``) so every pre-existing ``from .service_scan import
CompileContext`` import keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["CompileContext"]


@dataclass(frozen=True)
class CompileContext:
    """L2 header-AST compile context — shared by ``dump`` and ``scan``.

    The cross-toolchain + frontend knobs the header frontend needs to parse the
    public headers: the cross-compiler (``--compiler``/``--compiler-prefix``), extra
    compiler flags (``--compiler-option``), an alternate
    ``--sysroot``, ``--nostdinc``, and which ``--ast-frontend`` to drive. ADR-037
    D3 (parity: ``dump`` and ``scan`` carry the *same* family via one decorator)
    and the ADR-035 amendment (``scan`` must be able to reach a real L2 — the
    cross-source checks depend on header provenance). All fields defaulted, so a
    bare ``CompileContext()`` is additive over every request and dump path.
    """

    gcc_path: str | None = None
    gcc_prefix: str | None = None
    gcc_options: str | None = None
    gcc_option_tokens: tuple[str, ...] = ()
    sysroot: Path | None = None
    nostdinc: bool = False
    frontend: str = "auto"  # --ast-frontend (auto/castxml/clang)
    # --frontend-context (ADR-050 D3/D5): which AST context the L2 header
    # frontend should target -- "host" (default) or "device" (SYCL/DPC++
    # offload target, real selector in sycl_context.py; see
    # dump_manifest.py's own docstring for the manifest-field half of this
    # same rule).
    frontend_context: str = "host"

    @property
    def is_default(self) -> bool:
        """True when nothing was customised (lets call sites skip threading)."""
        return self == CompileContext()

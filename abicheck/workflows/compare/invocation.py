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

"""Build the classification request for one resolved compare pair.

The native ``compare`` CLI resolves its configuration (``.abicheck.yml``,
``--pack``, the gate) before it classifies a pair. The resolved values land on
a :class:`~abicheck.workflows.service_requests.CompareRequest` here, so the
mapping is a workflow-owned function a test can call without Click. The front
end still computes each value; this module only maps it onto the request, the
same field-for-field copy the CLI made inline before.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..service_requests import CompareRequest, ContractDomain

__all__ = ["build_resolved_compare_request"]


def build_resolved_compare_request(
    resolution_request: CompareRequest,
    *,
    policy: str,
    policy_file_path: Path | None,
    suppress: Path | tuple[Path, ...] | None,
    strict_suppressions: bool,
    require_justification: bool,
    scope_public: bool,
    force_public: Iterable[str] | None,
    post_manifest_allowlist: Iterable[str] | None,
    collapse_versioned_symbols: bool,
    env_matrix: Any,
    diagnostic_comparison: bool,
    require_same_generation: bool,
    contract: ContractDomain | None,
    depth: str | None,
    probe_matrix_old: Any,
    probe_matrix_new: Any,
    abi3_floor: Any,
) -> CompareRequest:
    """Return *resolution_request* with every resolved classification input set.

    An empty *force_public* means "no forced symbols" (``None``), and a
    ``None`` *post_manifest_allowlist* means "not manifest-scoped"; any other
    iterable becomes a ``frozenset``, as ``CompareRequest`` requires. Every
    other field is copied through unchanged, and ``pattern_verdicts`` is
    always ``True`` (ADR-068 D4: pattern verdicts are unconditional).
    """
    return dataclasses.replace(
        resolution_request,
        policy=policy_file_path if policy_file_path is not None else policy,
        suppress=suppress,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
        scope_public=scope_public,
        public_symbols=frozenset(force_public) if force_public else None,
        public_surface_allowlist=(
            frozenset(post_manifest_allowlist)
            if post_manifest_allowlist is not None
            else None
        ),
        pattern_verdicts=True,
        collapse_versioned_symbols=collapse_versioned_symbols,
        env_matrix=env_matrix,
        diagnostic_comparison=diagnostic_comparison,
        require_same_generation=require_same_generation,
        contract=contract,
        depth=depth,
        old_probe_matrix=probe_matrix_old,
        new_probe_matrix=probe_matrix_new,
        abi3_floor=abi3_floor,
    )

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

"""``workflows``-layer re-export of ``policy.depth_evidence_contract`` (ADR-061).

ADR-061's dependency direction lets ``frontends`` import ``workflows``, not
``policy`` directly (``scripts/check_architecture.py``'s
``dependency-direction`` gate). ``frontends/cli/compare_enrichment.py`` already
establishes this exact shape for the sibling `--abi3` evidence-contract axis
(``workflows.abi3_audit.record_abi3_evidence_contract_error`` wrapping
``policy``-layer logic) -- this module is the identical shim for
:mod:`abicheck.policy.depth_evidence_contract`, so
``cli_compare_helpers.run_compare`` (a ``frontends`` module) has a legal
import path to the same primitive :func:`abicheck.service_compare_pipeline.
classify_compare_pair` (a ``workflows`` module) already calls straight from
``policy``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def record_depth_evidence_contract_error(
    result: Any,
    depth: str | None,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_is_live: bool = True,
    new_is_live: bool = True,
) -> None:
    """See :func:`abicheck.policy.depth_evidence_contract.record_depth_evidence_contract_error`."""
    from ..policy.depth_evidence_contract import (
        record_depth_evidence_contract_error as _record,
    )

    _record(result, depth, old, new, old_is_live=old_is_live, new_is_live=new_is_live)


def record_for_compare_request(
    result: Any, request: Any, old: AbiSnapshot, new: AbiSnapshot
) -> None:
    """Record the axis for a resolved :class:`CompareRequest`'s own two sides.

    The liveness half lives here rather than at the call site (PR #1195): it
    is a fact about how *this run* obtained each side, and deriving it needs
    ``workflows.input_resolution.side_is_live``'s rule, which
    ``policy`` may not import at all (ADR-061's dependency direction). Doing
    it here keeps one answer for every caller that has a ``CompareRequest``,
    instead of each one re-deriving liveness beside its own call.

    Liveness comes from the ``InputSpec``, never from the resolved snapshot's
    ``old_fmt``/``new_fmt``: `detect_binary_format` answers ``None`` for a GNU
    ld linker script that this run *does* follow and parse, which would read
    as "stored" and skip the axis for a genuinely live side (Codex review).
    """
    from .input_resolution import input_spec_is_live

    record_depth_evidence_contract_error(
        result,
        request.depth,
        old,
        new,
        old_is_live=input_spec_is_live(request.old),
        new_is_live=input_spec_is_live(request.new),
    )

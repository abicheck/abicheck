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

"""Canonical content digests of a comparison's own resolved inputs.

One cohesive responsibility, lifted out of ``checker.compare``'s result
assembly. Several already-resolved inputs -- a forced-public symbol set, a
``--post-manifest`` allowlist, a declared deployment matrix -- change which
findings a run retains or how it classifies them while leaving no other
trace on the ``DiffResult``. Each is therefore content-digested onto the
result and folded into the effective-configuration digest, so two runs that
resolved different inputs cannot collide there.

They were independent derivations inline in ``checker.compare``, each
restating the same rule about itself. Collecting them is also what lets a
caller that is not a pairwise ``compare`` -- a release fan-out that
completed zero pairs, the no-baseline audit -- compute the same values the
same way rather than reinventing the convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..environment_matrix import EnvironmentMatrix


def env_matrix_content_digest(env_matrix: EnvironmentMatrix | None) -> str | None:
    """The canonical ``sha256:<hex>`` content digest of *env_matrix*'s
    resolved configuration, or ``None`` when none was declared (Codex
    review, PR #1221 follow-up). The one place this is computed: reused by
    :func:`~abicheck.workflows.no_baseline_compare.run_no_baseline_compare`
    and a release fan-out at release scope (no per-library ``DiffResult``
    to read it off of when zero pairs complete). Uses ``EnvironmentMatrix.
    to_dict()``, not ``dataclasses.asdict()``: the latter cannot traverse
    the ``MappingProxyType``-typed ``runtime_floors`` field (frozen for the
    hash-invariant fix, same follow-up).
    """
    if env_matrix is None:
        return None
    from ..contract_evidence_collect import content_digest

    return "sha256:" + content_digest(env_matrix.to_dict())


def explicit_scope_content_digest(
    force_public_symbols: set[str] | None,
    public_surface_allowlist: set[str] | None,
) -> str | None:
    """Canonical content digest of every resolved explicit-scope input (Codex
    review, PR #803).

    Both ``force_public_symbols`` and ``public_surface_allowlist`` are
    already-resolved ``set[str] | None`` here and each independently changes
    which findings ``compare()`` retains, so a digest of just one axis would
    let two differing-only-by-the-other runs collide. Keyed JSON (not
    delimiter-joined) keeps the axes distinguishable, avoiding the
    non-injective-join bug class already fixed elsewhere in this digest work.

    ``public_surface_allowlist`` is gated on ``is not None``, not truthiness
    (Codex review, PR #803): an empty allowlist is a real, distinct, active
    configuration -- a POST manifest committing to zero exports -- matching
    ``checker``'s own ``scope_active`` check; collapsing ``set()`` to "no
    scope" would hash an absent manifest identically to a zero-export one.
    ``force_public_symbols`` deliberately keeps plain truthiness: every other
    consumer in this codebase already treats an empty set as equivalent to
    ``None`` for that axis, so the two are intentionally asymmetric here.

    ``content_digest`` is the same canonical-JSON-then-SHA-256 primitive
    ``contract_context.py`` uses for overlay digests (CodeRabbit, PR #803) --
    reused rather than a second hand-rolled hashing convention.
    """
    from ..contract_evidence_collect import content_digest

    sources: dict[str, list[str]] = {}
    if force_public_symbols:
        sources["force_public_symbols"] = sorted(force_public_symbols)
    if public_surface_allowlist is not None:
        sources["public_surface_allowlist"] = sorted(public_surface_allowlist)
    return "sha256:" + content_digest(sources) if sources else None


@dataclass(frozen=True)
class ComparisonInputReceipt:
    """The digests :func:`comparison_input_receipt` resolved for one run."""

    explicit_scope_source_sha256: str | None
    env_matrix_source_sha256: str | None


def comparison_input_receipt(
    force_public_symbols: set[str] | None,
    public_surface_allowlist: set[str] | None,
    env_matrix: EnvironmentMatrix | None,
) -> ComparisonInputReceipt:
    """Every digest above, resolved together for one comparison."""
    return ComparisonInputReceipt(
        explicit_scope_source_sha256=explicit_scope_content_digest(
            force_public_symbols, public_surface_allowlist
        ),
        env_matrix_source_sha256=env_matrix_content_digest(env_matrix),
    )

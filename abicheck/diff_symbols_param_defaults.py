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

"""``Param.default`` value-comparison reliability gate (Codex review, PR #687).

Split out of ``diff_symbols.py`` to stay under its line-count cap — a leaf
module (must not import from ``diff_symbols`` to avoid an import cycle);
``diff_symbols._diff_param_defaults`` imports these two functions back.

Unlike a literal default's plain value (``"42"``), a non-literal default's
clang-side representation is a structural fingerprint
(``dumper_clang._canonical_expr``/``_expr_fingerprint``, prefixed
``"expr:"``) whose exact algorithm changed within the same PR that first
wired ``TypeField.default`` extraction: folding in a referenced
declaration's identity, its scope, ``sizeof``/``alignof`` operand types, and
anonymous-type/lambda source-location normalization — none of which touch a
LITERAL default's value at all. A pre-v20 clang-producer snapshot's
fingerprint for an UNCHANGED non-literal default can therefore differ from a
fresh one purely from that algorithm change, not a real edit — the same
class of gap ``AbiSnapshot.clang_field_initializer_facts_reliable`` already
closes for ``TypeField.default``'s own presence/absence, reused here for a
different reason: ``Param.default``'s presence was never unreliable, only
the non-literal VALUE representation is.
"""

from __future__ import annotations

from .model import AbiSnapshot


def param_default_value_representation_unreliable(
    snap: AbiSnapshot, producer: str | None
) -> bool:
    """True if *snap*'s non-literal ``Param.default`` fingerprint, for a
    function whose resolved provenance is *producer*, predates this PR's
    ``_canonical_expr`` stabilization (schema v20).

    *producer* is the caller's already-resolved per-function value (typically
    ``fact_provenance.fact_producer(snap, func_fact_key(mangled,
    "param_defaults"))``) — NOT ``snap.ast_producer`` directly. A pure
    ``"clang"``-producer snapshot and a ``"hybrid"`` snapshot whose merge
    stamped this SPECIFIC function's ``param_defaults`` provenance as
    ``"clang"`` (a clang-only-appended function — ``dumper_hybrid.py``'s
    merge does this unconditionally) share the identical risk (Codex review,
    fresh evidence, second round): checking ``snap.ast_producer == "clang"``
    alone missed the hybrid case entirely, since a hybrid snapshot's own
    top-level producer is ``"hybrid"``, not ``"clang"``, even though the
    individual function's fingerprint went through the exact same unstable
    ``_canonical_expr``. Only meaningful for a FINGERPRINT-shaped value (the
    ``"expr:"`` prefix) -- a literal default's plain value never touches
    ``_canonical_expr`` at all, so it stays fully comparable regardless of
    schema version or producer.
    """
    return (
        producer == "clang"
        and snap.from_headers
        and not snap.from_headers_inferred
        and not snap.clang_field_initializer_facts_reliable
    )


def param_default_fingerprint_comparison_unreliable(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_producer: str | None,
    new_producer: str | None,
    old_value: str,
    new_value: str,
) -> bool:
    """True if comparing *old_value* against *new_value* risks a false
    ``PARAM_DEFAULT_VALUE_CHANGED`` from a fingerprint-algorithm version
    mismatch rather than a real edit. *old_producer*/*new_producer* are the
    caller's already-resolved per-function producers — see
    :func:`param_default_value_representation_unreliable`.
    """
    if not (old_value.startswith("expr:") or new_value.startswith("expr:")):
        return False
    return param_default_value_representation_unreliable(
        old, old_producer
    ) or param_default_value_representation_unreliable(new, new_producer)

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

"""Default-value fingerprint-comparison reliability gate (Codex review, PR #687).

Shared by ``Param.default`` (``diff_symbols._diff_param_defaults``) and
``TypeField.default`` (``diff_types._diff_field_default_initializer``) — split
out of ``diff_symbols.py`` originally to stay under its line-count cap, then
generalized to also cover the field-initializer call site rather than
duplicating the identical value-reliability logic there. A leaf module (must
not import from ``diff_symbols``/``diff_types`` to avoid an import cycle);
both call sites import these two functions back.

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
class of gap ``AbiSnapshot.clang_field_initializer_facts_reliable`` closes
for ``TypeField.default``'s own presence/absence, reused here for a
different reason: a default's presence was never unreliable, only the
non-literal VALUE representation is.

PR #720 retrospective — why five distinct bugs in this small a module took
five separate review rounds to surface, and what changed about how it's
tested: every one of them was a different edge of the SAME finite state
space (each side's producer x reliability-flag x value-shape), and every
test that existed before that PR picked ONE hand-crafted example per
property instead of covering that space. A hand-picked example proves the
one scenario its author thought of works; it says nothing about the dozens
of other reachable combinations. Concretely, the OR-vs-XOR bug (both guards
suppressed whenever EITHER side was independently unreliable, when the
correct rule is a MISMATCH between the two sides' extraction generations)
survived four earlier review rounds that each fixed a different bug in the
same two functions, because none of those rounds' new tests happened to
cover "both sides equally unreliable." ``tests/test_fingerprint_
reliability_properties.py`` closes that gap structurally rather than
example-by-example: the reachable domain here is genuinely small (four
producer values x two reliability states x two value shapes, per side) —
cheap enough to enumerate EXHAUSTIVELY rather than sample, so it asserts
invariants ("two fingerprint-shaped values with identical producer and
reliability are never suppressed", "two non-fingerprint values are never
suppressed", "the decision is symmetric in old/new") across the entire
space at once. The general lesson, not just this module's: when a
function's behavior is fully determined by a handful of independent
enum/boolean inputs, prefer a test that enumerates that state space and
asserts invariants over one more hand-picked scenario per bug report —
it converts "did we think to test this one case" into "is this true for
every case," which is exactly the class of check a human/LLM review pass
manually re-deriving edge cases one at a time cannot exhaustively provide.
"""

from __future__ import annotations

import re

from .model import AbiSnapshot

#: The exact shape ``dumper_clang_expr._expr_fingerprint`` produces --
#: ``"expr:" + sha256(...).hexdigest()[:16]`` (16 lowercase hex digits) --
#: not merely the ``"expr:"`` PREFIX. A plain ``.startswith("expr:")`` check
#: also matches castxml's raw, verbatim source-text initializer whenever it
#: happens to spell a qualified name whose next component is literally
#: ``expr`` (e.g. an expression-template library's ``expr::`` namespace) --
#: ``dumper_castxml._iter_public_constants``/``_ClangAstParser`` parsers both
#: pass that XML/AST ``init`` text straight through unmodified, no fingerprint
#: involved at all. Matching the full shape means a genuine
#: ``"expr::OLD_VALUE"`` vs. ``"expr::NEW_VALUE"`` castxml comparison is never
#: mistaken for a clang fingerprint and silently suppressed (Codex review,
#: fresh evidence, PR #720).
_EXPR_FINGERPRINT_RE = re.compile(r"^expr:[0-9a-f]{16}$")


def _is_expr_fingerprint(value: str) -> bool:
    """True if *value* is exactly a clang structural-expression fingerprint,
    not merely ``"expr:"``-prefixed text (see ``_EXPR_FINGERPRINT_RE``)."""
    return bool(_EXPR_FINGERPRINT_RE.match(value))


def _fingerprint_comparison_unreliable(
    old_is_fingerprint: bool,
    old_legacy: bool,
    new_is_fingerprint: bool,
    new_legacy: bool,
) -> bool:
    """The single decision rule every fingerprint-reliability guard in this
    module reduces to. *_is_fingerprint* is whether THIS side's own value
    has the fingerprint shape at all (:func:`_is_expr_fingerprint`);
    *_legacy* is whether, IF it were fingerprint-shaped, that fingerprint
    would have come from the unstable pre-v20 algorithm (producer- and
    reliability-flag-derived, independent of the value's actual shape).

    Two risks this collapses into one earlier boolean ("affected" =
    ``is_fingerprint and legacy``) used to conflate, each fixed the same
    review round (Codex review, fresh evidence, PR #720):

    1. Suppressing whenever *either* side was independently affected (an
       OR) is too conservative when BOTH sides are fingerprint-shaped:
       two extractions that both used the identical pre-v20 algorithm
       produce fingerprints that are directly comparable TO EACH OTHER
       (same algorithm, same input -> same output). Only a MISMATCH
       between generations -- one side pre-v20, the other stabilized --
       means a fingerprint difference could be an algorithm artifact
       rather than a real edit. Fixed by comparing legacy status only
       between two sides that are BOTH fingerprint-shaped.
    2. The mirror bug the first fix alone still had: when EXACTLY ONE
       side is fingerprint-shaped (a literal on one side, a compound
       expression on the other), that is ALWAYS a genuine value-shape
       change no algorithm version could produce spuriously -- the
       unstable ``_canonical_expr`` only ever affects the HASH of an
       already-compound expression, never whether a declaration's own
       AST node was a literal or not (:func:`dumper_clang_expr.
       _initializer_value` keeps a literal's plain value regardless of
       algorithm version). Naively reducing ``is_fingerprint and legacy``
       to one boolean before comparing lost this distinction: a legacy
       fingerprint XORed against a non-fingerprint literal read as
       "generations differ" and wrongly suppressed a confirmed
       expression-to-literal (or literal-to-expression) edit.

    So: never suppress unless BOTH sides are genuinely fingerprint-shaped,
    and even then only when their legacy status actually differs.
    """
    if not (old_is_fingerprint and new_is_fingerprint):
        return False
    return old_legacy != new_legacy


def default_value_representation_unreliable(
    snap: AbiSnapshot, producer: str | None
) -> bool:
    """True if *snap*'s non-literal default-value fingerprint, for a
    declaration whose resolved provenance is *producer*, predates this PR's
    ``_canonical_expr`` stabilization (schema v20).

    *producer* is the caller's already-resolved per-declaration value
    (typically ``fact_provenance.fact_producer``/``resolved_fact_producer``
    for the matching ``param_defaults``/``field:...:default`` key) — NOT
    ``snap.ast_producer`` directly. A pure ``"clang"``-producer snapshot and
    a ``"hybrid"`` snapshot whose merge stamped this SPECIFIC declaration's
    provenance as ``"clang"`` (a clang-only-appended function/field —
    ``dumper_hybrid.py``'s merge does this unconditionally) share the
    identical risk (Codex review, fresh evidence, second round): checking
    ``snap.ast_producer == "clang"`` alone missed the hybrid case entirely,
    since a hybrid snapshot's own top-level producer is ``"hybrid"``, not
    ``"clang"``, even though the individual declaration's fingerprint went
    through the exact same unstable ``_canonical_expr``. Only meaningful for
    a FINGERPRINT-shaped value (the ``"expr:"`` prefix) -- a literal
    default's plain value never touches ``_canonical_expr`` at all, so it
    stays fully comparable regardless of schema version or producer.

    *producer* being ``None`` (unresolved/unrecorded) is ALSO treated as
    clang-family risk, not excluded (Codex review, fresh evidence, third
    round): a direct-clang snapshot persisted before ``ast_producer`` was
    tracked at all (e.g. schema v9) still has real ``"expr:"``-prefixed
    values from that same unstable pre-v20 ``_canonical_expr`` -- its
    per-declaration producer resolves to ``None`` (unknown), not ``"clang"``,
    so the stricter equality check let its legacy fingerprint compare
    directly against a freshly-stabilized one and report a false CHANGED.
    Since the caller already restricts every call here to a value that
    starts with ``"expr:"`` (:func:`default_value_fingerprint_comparison_unreliable`),
    and ONLY the direct-clang backend ever produces that prefix (castxml
    always keeps the verbatim source expression instead), a producer of
    ``None`` at this point is presumptively clang-authored regardless of
    whether provenance was tracked at dump time -- ``"castxml"`` is the only
    producer value this check must still exclude.
    """
    return (
        producer != "castxml"
        and snap.from_headers
        and not snap.from_headers_inferred
        and not snap.clang_field_initializer_facts_reliable
    )


def default_value_fingerprint_comparison_unreliable(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_producer: str | None,
    new_producer: str | None,
    old_value: str,
    new_value: str,
) -> bool:
    """True if comparing *old_value* against *new_value* risks a false
    ``PARAM_DEFAULT_VALUE_CHANGED``/``FIELD_DEFAULT_INITIALIZER_CHANGED``
    from a fingerprint-algorithm version mismatch rather than a real edit.
    *old_producer*/*new_producer* are the caller's already-resolved
    per-declaration producers — see
    :func:`default_value_representation_unreliable`.

    Checked per side: a side whose OWN value is a plain literal (no
    ``"expr:"`` prefix) never touched the unstable fingerprint algorithm at
    all, so its reliability is irrelevant even when the OTHER side's value
    is fingerprint-shaped — folding both sides through one shared
    "either value looks like a fingerprint" precondition (as an earlier
    version of this function did) let an unreliable-but-literal side
    incorrectly suppress a genuine change on the other, fingerprint-shaped
    side (Codex review, fresh evidence): a pre-v20 clang snapshot storing a
    literal ``"42"`` compared against a fresh snapshot's non-literal
    ``"expr:..."`` for the same declaration would decline the comparison
    solely because the OLD snapshot's ``clang_field_initializer_facts_reliable``
    flag was unset — even though the old side's own literal value never
    depended on that flag at all.

    The actual suppress/don't-suppress decision is
    :func:`_fingerprint_comparison_unreliable` — never suppressed unless
    BOTH sides are genuinely fingerprint-shaped (a literal-vs-expression
    shape change is always a confirmed real edit, whatever either side's
    reliability), and even then only on a legacy-generation MISMATCH, not
    whenever either side is independently unreliable (Codex review, fresh
    evidence, PR #720).
    """
    return _fingerprint_comparison_unreliable(
        _is_expr_fingerprint(old_value),
        default_value_representation_unreliable(old, old_producer),
        _is_expr_fingerprint(new_value),
        default_value_representation_unreliable(new, new_producer),
    )


def constant_value_fingerprint_comparison_unreliable(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_value: str,
    new_value: str,
) -> bool:
    """True if comparing two ``AbiSnapshot.constants`` entries (same name,
    different value) risks a false ``CONSTANT_CHANGED`` from the identical
    fingerprint-algorithm version mismatch
    :func:`default_value_fingerprint_comparison_unreliable` guards against
    for ``Param.default``/``TypeField.default`` -- ``dumper_clang.py``'s
    ``parse_constants()`` calls the very same ``_initializer_value()``, so a
    non-literal constant's ``"expr:"``-prefixed value on the direct-clang
    backend is exactly as unstable pre-schema-v20 (measured against a real
    corpus: 173 of 440 findings across a v18-vs-v24 comparison were exactly
    this -- one constant's stale fingerprint colliding with 35 unrelated
    others').

    Unlike the default-value case there is no per-declaration provenance to
    resolve: ``dumper_hybrid.merge_snapshots`` keeps ``constants`` verbatim
    from its castxml base (never clang-merged per-entry), so a snapshot's
    own top-level ``ast_producer`` already answers which single backend
    produced every entry. Checked as ``NOT in ("castxml", "hybrid")`` --
    NEITHER of :func:`default_value_representation_unreliable`'s two
    producer checks alone is right here. Its broader ``producer !=
    "castxml"`` treats a ``"hybrid"`` producer as at-risk too, because a
    hybrid merge's clang-only-APPENDED fields keep their own unstable
    fingerprint -- true for ``TypeField.default``, but not for constants,
    which never take that per-declaration merge path at all, so ``"hybrid"``
    is excluded here same as ``"castxml"``. But an exact ``== "clang"``
    match is too narrow the other direction: ``ast_producer`` itself wasn't
    tracked before schema v10 (eight versions before this class of risk even
    existed in the wild), so a snapshot straddling that boundary reads
    ``ast_producer=None`` -- "unknown", not "definitely not clang" -- and
    excluding it would silently trust an ``"expr:"`` value from exactly the
    producer this guard exists to distrust (Codex review, PR #720; mirrors
    :func:`default_value_representation_unreliable`'s own third-round
    ``None``-is-clang-family-risk fix for the identical shape of gap). Safe
    for a genuinely castxml-authored ``None``-producer legacy snapshot too:
    ``_is_expr_fingerprint`` matches the full 16-hex-digit shape, not merely
    the ``"expr:"`` prefix -- castxml keeps the verbatim source expression
    for a constant's value (``dumper_castxml._iter_public_constants`` passes
    its raw XML ``init`` text straight through), so a real castxml constant
    referencing a qualified name whose next component happens to spell
    ``expr`` (e.g. ``"expr::OLD_VALUE"``, an expression-template library's
    namespace) would otherwise collide with the ``"expr:"`` PREFIX check and
    get its genuine ``CONSTANT_CHANGED`` silently suppressed regardless of
    producer (Codex review, fresh evidence, PR #720).

    The actual suppress/don't-suppress decision is
    :func:`_fingerprint_comparison_unreliable` — never suppressed unless
    BOTH sides are genuinely fingerprint-shaped (a literal-vs-expression
    shape change is always a confirmed real edit), and even then only on a
    legacy-generation MISMATCH: two archived baselines that BOTH came from
    the same pre-v20 direct-clang build are directly comparable to each
    other, so a genuine edit between them must still be reported (Codex
    review, fresh evidence, PR #720).
    """
    return _fingerprint_comparison_unreliable(
        _is_expr_fingerprint(old_value),
        old.ast_producer not in ("castxml", "hybrid")
        and not old.clang_field_initializer_facts_reliable,
        _is_expr_fingerprint(new_value),
        new.ast_producer not in ("castxml", "hybrid")
        and not new.clang_field_initializer_facts_reliable,
    )

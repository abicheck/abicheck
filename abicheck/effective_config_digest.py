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

"""CLI cleanup phase two, PR B: the effective-configuration digest.

The plan's stated goal is "one effective configuration ... with the same
effective-config digest recorded in every report" -- ``compare``, the
directory/package release fan-out, and ``scan --against`` alike. There is
**no single object that holds every configuration axis for every run**
(``CompatibilityEvaluationConfig`` is only built under ``--contract``/
``--pack``; a plain run's gate/policy configuration lives on the resolved
``DiffResult`` and its ``PolicyFile`` instead) -- see the module docstring
of :mod:`abicheck.pack_application` and ADR-049 Phase 1-6 for why that
object is deliberately opt-in rather than universal. This module is
therefore honest about two tiers rather than pretending one:

* **Rich tier** -- when the comparison resolved one at all,
  :class:`~abicheck.checker_types.DiffResult.evaluation_config` carries a
  full, already-resolved ``CompatibilityEvaluationConfig`` (which happens
  whenever ``--pack`` selected a pack, *not only* under ``--contract`` --
  see that field's own docstring), including real pack identities
  (``ImmutableIdentity(id, version, sha256)``), suppression/explicit-scope
  content digests, and contract overlays. :func:`effective_config_fields`
  reads it directly (falling back to the older ``contract_context.
  evaluation_context.resolved_config`` for a caller that only populated
  that block).
* **Baseline tier** -- otherwise, the digest is built from the fields every
  comparison *does* resolve regardless: the active policy name and its
  overrides/reclassify-rules/internal-namespaces/public-surface-scoping
  (``DiffResult.policy``/``policy_file``/``scope_to_public_surface``), and
  the resolved severity/exit-code-scheme gate (the same ``SeverityConfig``
  /``exit_code_scheme`` pair :mod:`abicheck.reporter_contract_blocks`
  already threads through ``add_contract_context`` for the ``exit`` block).

Both tiers hash through :func:`effective_config_digest`, so two runs that
resolved the *same* values -- whether or not either passed ``--contract`` --
produce the same digest only when the same fields were available; the two
tiers are intentionally not cross-comparable (a rich-tier digest also covers
axes -- ``contract.mode``, pack identities -- a baseline-tier run never
resolved at all). ``fields["_tier"]`` records which tier produced a given
digest so a report reader is never left guessing.

This is a *fingerprint*, not a byte-for-byte serialization: it exists so two
runs (or a run replayed later) can be compared for "did the resolved
configuration change", mirroring the existing ``profile_fingerprint``/
``scope_fingerprint`` precedent in :mod:`abicheck.comparability` -- the
field values themselves are also recorded (verbatim, not just hashed) so a
mismatch can be attributed to a specific field rather than read as an
opaque hash.

**Closed: the directory/package release fan-out's own *per-library* digests
now reach the rich tier under ``--pack``** (Codex review, PR #803, fresh
evidence; closed in CLI cleanup phase two, "PR B" first slice).
``cli_compare_release._run_compare_pair`` used to forward only
``PackApplication.policy_overrides``/``internal_namespaces`` to
``service.run_compare`` for each library -- it never stamped the resolved
``CompatibilityEvaluationConfig`` onto that library's own ``DiffResult`` the
way ``cli_compare_receipt.record_resolved_config`` does for single-pair
``compare``. So a release run under two different pack *revisions* that
happen to project the same policy/severity assignments used to produce the
same per-library digest, even though the rich tier's whole point is real,
versioned pack identities. Closed by ``cli_compare_receipt.
record_release_resolved_config`` (``PackApplication.resolved_config``,
populated by the shared ``pack_application()`` factory both paths call
through, threaded to each library's ``DiffResult.evaluation_config`` *and*,
when one exists, merged into that library's own ``contract_context`` --
``effective_config_fields`` below prefers the latter over the bare attribute
whenever a ``PersistedContractContext`` exists, which a release run given
``--contract`` builds per library same as single-pair `compare`). Still
deferred, and *not* closed by this slice: the release fan-out's own gate
resolution (exit-code scheme/severity) has no per-library
``GateOptions``-shaped object yet -- see the plan doc's PR B section, "the
GateOptions unification" -- so this closed slice covers only the
config-merge half, never ``with_resolved_gate``. The release-level
*summary* digest (``cli_compare_release_helpers._format_release_json``) is
a separate, narrower computation -- it only ever resolves the baseline tier
(no `CompatibilityEvaluationConfig` object exists at that scope at all), so
it was never subject to this same gap; it captures the release's own
resolved severity/exit-code-scheme (which a gate pack's contribution
already folds into, per ``apply_release_gate_pack``), not pack identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .severity import SeverityConfig

#: Stable, ordered key set the digest is hashed over. Extending this tuple
#: is additive (a new field just starts contributing to future digests);
#: reordering or removing a key changes every existing digest's value, so
#: don't do that without a real reason.
EFFECTIVE_CONFIG_FIELD_KEYS: tuple[str, ...] = (
    "_tier",
    "policy.base",
    "policy.overrides",
    "policy.reclassify",
    "policy.frozen_namespaces",
    "policy.pattern_verdicts",
    "policy.collapse_versioned_symbols",
    "policy.surface_metrics",
    "policy.env_matrix",
    "policy.reconcile_build_context",
    "surface.internal_namespaces",
    "surface.explicit_scope",
    "surface.scope_to_public_surface",
    "surface.scope_to_public_surface_requested",
    "contract.mode",
    "contract.unresolved",
    "contract.overlays",
    "gate.exit_code_scheme",
    "gate.require_complete_analysis",
    "gate.scope",
    "gate.severity.abi_breaking",
    "gate.severity.potential_breaking",
    "gate.severity.quality_issues",
    "gate.severity.addition",
    "gate.on_incomplete_scope",
    "assurance.require_evidence",
    "suppressions",
    "packs",
)


def _sha256_of(*parts: str) -> str:
    """NUL-delimited SHA-256 over *parts*, prefixed ``sha256:`` (hex)."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return f"sha256:{digest.hexdigest()}"


def effective_config_digest(fields: dict[str, str]) -> str:
    """Hash *fields* (a dict produced by :func:`effective_config_fields`)."""
    return _sha256_of(*[fields.get(key, "") for key in EFFECTIVE_CONFIG_FIELD_KEYS])


def _overrides_str(overrides: Any) -> str:
    pairs = sorted(
        (getattr(kind, "value", str(kind)), getattr(verdict, "value", str(verdict)))
        for kind, verdict in dict(overrides or {}).items()
    )
    return ";".join(f"{kind}={verdict}" for kind, verdict in pairs)


def _json_list(items: Any) -> str:
    """Canonical (sorted, compact) JSON array encoding for a string
    collection -- injective, unlike a delimiter-joined string (Codex
    review, PR #803, fresh evidence: `";".join(...)` collapses
    `("api;detail",)` and `("api", "detail")` to the identical
    `"api;detail"`, even though a namespace/selector pattern is an
    arbitrary string that can legally contain the delimiter)."""
    return json.dumps(
        sorted(str(item) for item in (items or ())), separators=(",", ":")
    )


def _namespaces_str(namespaces: Any) -> str:
    return _json_list(namespaces)


def _identity_str(identity: Any) -> str:
    """``id@version:sha256`` for one ``ImmutableIdentity``, or ``""`` when
    absent. Includes the digest, not just the ``id`` -- for a base policy,
    ``builtin_policy_identity()`` deliberately hashes the policy's effective
    ``ChangeKind`` sets into it (Codex review, PR #803, fresh evidence: an
    earlier revision recorded only the bare ``id``, so the same policy
    *name* classifying findings differently across a tool version bump --
    exactly what the digest reflects -- produced an unchanged field)."""
    if identity is None:
        return ""
    return (
        f"{getattr(identity, 'id', '')}@{getattr(identity, 'version', '')}:"
        f"{getattr(identity, 'sha256', '')}"
    )


def _builtin_policy_base_str(name: Any) -> str:
    """``policy.base`` for the baseline tier: the full built-in identity
    (``id@version:sha256``, matching the rich tier's own :func:`_identity_str`
    encoding) when *name* is a recognized built-in base, else the bare name
    unchanged. Codex review, PR #803, fresh evidence: an ordinary comparison
    (no ``--contract``/``--pack``) recorded only the policy *name* here, so
    two baseline reports from different abicheck versions could both read
    ``policy.base="strict_abi"`` and hash identically despite
    ``builtin_policy_identity()``'s own effective-``ChangeKind``-set digest
    (the same one the rich tier now carries) having genuinely changed.
    Falls back to the bare name rather than raising for a name outside
    ``VALID_BASE_POLICIES`` -- an unrecognized/typo'd policy name is a real,
    pre-existing possibility on this tier (see ``compatibility_evaluation_
    frontend.stated_policy_base``'s own docstring for why a receipt must
    never turn a completed comparison into a failure over this), so this
    degrades to the same bare-string behavior every other tier's unresolved
    field already uses rather than crashing report generation."""
    name_str = str(name or "")
    if not name_str:
        return ""
    try:
        from .compatibility_evaluation_frontend import builtin_policy_identity

        return _identity_str(builtin_policy_identity(name_str))
    except ValueError:
        return name_str


def _on_incomplete_scope_str(result: Any) -> str:
    """ADR-065 D6's ``--on-incomplete-scope`` policy (``warn``/``block``)
    for a directory/package release, read off *result*; ``""`` for a
    scalar comparison, whose one pair is the whole scope and to which the
    policy does not apply. Two otherwise identical incomplete releases
    exit ``0`` and ``1`` under the two values, so the digest must tell
    them apart (Codex review)."""
    return str(getattr(result, "on_incomplete_scope", "") or "")


def _gate_scope_str(result: Any) -> str:
    """Canonical encoding of an ADR-043 scoped-gate selection
    (``--used-by``/``--required-symbol(s)``), so two runs selecting
    different consumers/entrypoints don't collide on the digest (Codex
    review, PR #803, fresh evidence): ``cli_helpers_compare._apply_used_by_
    scoping``/``_apply_required_symbol_scoping`` stamp ``DiffResult.
    gate_scope``/``used_by``/``required_symbols`` onto *result* before the
    report is rendered, and that scoped gate can genuinely replace the
    reported verdict/findings/exit code -- but neither this digest's rich
    nor baseline tier read it (it isn't a D7 ``CompatibilityEvaluationConfig``
    namespace field, and it isn't a ``PolicyFile``/``SeverityConfig`` fact
    either), so two ``compare --required-symbol A``/``--required-symbol B``
    runs against the identical pair previously hashed identically. Reads
    the same JSON-safe projections (``result.used_by``'s ``app`` paths,
    ``result.required_symbols``'s ``required_entrypoints``) the renderer
    itself already serializes -- not a second traversal of the underlying
    ``AppCompatResult``/``PluginHostContractResult`` objects. ``""`` when no
    scoping was requested at all, the common case.

    **Known, deliberate limitation** (Codex review, PR #803, fresh
    evidence): for ``used_by``, ``targets`` identifies each consumer only
    by its ``app`` *path*, not its content -- if the binary at that path is
    rebuilt in place between two runs, ``scope_diff_to_app`` can select a
    genuinely different set of findings while this field (and the digest)
    stay identical. Not fixed here: closing it would mean this
    file-content-free fingerprint module reading and hashing an arbitrary
    consumer binary at digest-computation time -- a real I/O/cost decision
    (every report generation would hash every ``--used-by`` app, however
    large) and a real design question (a full-file hash, or a narrower
    identity of only the imports/symbols this scoping actually reads) that
    ``AppCompatResult``/``_app_compat_summary`` don't carry any answer for
    today. Left as a known gap rather than a reactive file-hashing patch,
    per this repo's own "known gaps over risky reactive patches"
    convention (AGENTS.md)."""
    gate_scope = getattr(result, "gate_scope", None)
    if gate_scope is None:
        return ""
    if gate_scope == "used_by":
        used_by = getattr(result, "used_by", None) or ()
        targets = sorted(str(entry.get("app", "")) for entry in used_by)
    elif gate_scope == "required_symbol":
        required = getattr(result, "required_symbols", None) or {}
        targets = sorted(
            str(e) for e in (required.get("required_entrypoints", ()) or ())
        )
    else:
        targets = []
    return json.dumps(
        {"kind": str(gate_scope), "targets": targets},
        sort_keys=True,
        separators=(",", ":"),
    )


def _packs_str(*pack_groups: Any) -> str:
    """``id@version:sha256`` for every pack identity across *pack_groups*,
    canonical-JSON-encoded (see :func:`_json_list`) -- a pack id/version/
    sha256 are far more constrained than a namespace pattern, but the same
    injectivity argument applies uniformly rather than being special-cased
    away for "probably safe" inputs."""
    identities = {
        _identity_str(identity) for group in pack_groups for identity in group or ()
    }
    return _json_list(identities)


def _digested_items_str(items: Any) -> str:
    """The stored ``sha256`` of a ``DigestedItems``/``SuppressionConfig``-
    shaped object, or ``""`` when no source was selected at all (``None``,
    per each field's own docstring) -- the content digest already answers
    "did the source change" for whatever selected it, so this reads that
    digest rather than re-hashing the (possibly large) item list itself."""
    if items is None:
        return ""
    return str(getattr(items, "sha256", "") or "")


def _rich_tier_explicit_scope_str(surface: Any, result: Any) -> str:
    """``surface.explicit_scope`` for the rich tier, merging *two*
    independent explicit-scope sources rather than falling back to only
    one (CodeRabbit review, PR #803, fresh evidence: a plain ``or``
    fallback -- "use the config's digest when present, else the result's"
    -- is unsound, because the two sources are not alternatives; a single
    run can carry both at once).

    *resolved_config.surface.explicit_scope* (D7's
    :func:`compatibility_evaluation_frontend._explicit_scope`) covers only
    ``--public-symbol``/``--public-symbols-list``/``.abicheck.yml``'s
    ``scope.public_symbols``. *result.explicit_scope_source_sha256*
    (``checker.compare()``) independently covers *both* the resolved
    ``force_public_symbols`` set *and* ``--post-manifest``'s
    ``public_surface_allowlist`` -- and ``force_public_symbols`` is always
    threaded into ``compare()`` regardless of ``--pack``/``--contract``
    (``cli_compare_helpers.py``'s ``force_public`` local is resolved once
    and passed unconditionally), so a ``--pack``-only run combining
    ``--public-symbols-list`` *and* ``--post-manifest`` genuinely
    populates both sources at once. Falling back only when the config side
    is empty would silently drop the post-manifest axis whenever the
    config side happens to be populated too -- exactly the collision this
    field exists to prevent. Both digest strings are already canonical
    content digests, so keying them together (rather than trying to
    de-duplicate their overlapping coverage of ``force_public_symbols``)
    is safe: two runs are indistinguishable here only when *both*
    contributing digests agree."""
    config_scope = _digested_items_str(getattr(surface, "explicit_scope", None))
    result_scope = str(getattr(result, "explicit_scope_source_sha256", "") or "")
    if not config_scope and not result_scope:
        return ""
    return json.dumps(
        {"config": config_scope, "compare": result_scope},
        sort_keys=True,
        separators=(",", ":"),
    )


def _reclassify_str(rules: Any) -> str:
    """Order-*preserving* encoding of the *active* (non-expired) subset of
    a ``PolicyFile.reclassify`` rule set (``ReclassifyRule.to_report_dict()``,
    the same shared audit encoding ``reporter.py``/``sarif.py`` already
    render, filtered through the same ``active_reclassify_rules`` every
    other renderer disclosing this set already uses), so two runs differing
    only in an active selector-scoped reclassification rule -- which can
    change a finding's classification the same way a plain
    ``policy.overrides`` entry does -- produce different digests. An
    expired rule never matches, so it is excluded here too, matching what
    every other renderer already discloses.

    Deliberately **not** sorted (Codex review, PR #803, fresh evidence): a
    ``reclassify`` list is first-match-wins in policy-file order, so two
    rule sets that are the same rules in a different order -- e.g. a
    ``break`` rule and an ``ignore`` rule that both match the same selector,
    swapped -- can select a different rule for an overlapping finding and
    therefore a different verdict, while a sorted encoding would collapse
    them to the identical digest."""
    from .reclassify import active_reclassify_rules

    active = active_reclassify_rules(list(rules or ()))
    encoded = [
        json.dumps(dict(sorted(rule.to_report_dict().items())), separators=(",", ":"))
        for rule in active
    ]
    return json.dumps(encoded, separators=(",", ":"))


def _enum_value(value: Any) -> str:
    """``value.value`` for an ``Enum`` member, ``str(value)`` for anything
    else, ``""`` for ``None`` -- avoids ``str(SomeEnum.MEMBER)`` returning
    ``"SomeEnum.MEMBER"`` on a pre-3.11 ``(str, Enum)`` mixin."""
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _severity_field(severity: SeverityConfig | None, category: str) -> str:
    if severity is None:
        return ""
    level = getattr(severity, category, None)
    return getattr(level, "value", str(level)) if level is not None else ""


def effective_config_fields_from_full_config(
    resolved_config: Any,
    *,
    result: Any = None,
    policy_file: Any = None,
    require_complete_analysis: bool = False,
    severity_config: SeverityConfig | None = None,
    exit_code_scheme: str | None = None,
) -> dict[str, str]:
    """Rich-tier fields from a real ``CompatibilityEvaluationConfig``.

    *resolved_config* is either ``DiffResult.evaluation_config`` (whenever
    ``--pack``/``--contract`` resolved one -- see that field's own
    docstring) or, for a caller that only ever populated the older
    ``contract_context`` block, ``contract_context.evaluation_context.
    resolved_config``. Reads every field straight off the six namespaces D7
    already resolved; nothing here re-derives a value D7 precedence already
    decided. *policy_file* is *result.policy_file* (the actual ``PolicyFile``
    the run scored with) -- ``reclassify`` rules are a checker-level
    ``PolicyFile`` concept with no ``CompatibilityEvaluationConfig``
    namespace of their own, so they are read from there regardless of tier.
    *result* is the ``DiffResult`` itself -- ``scope_to_public_surface`` is
    likewise a checker-level fact with no D7 namespace of its own (Codex
    review, PR #803: an earlier revision hard-coded this field empty for
    the rich tier, so two ``--contract`` runs differing only in
    ``--scope-public-headers`` collided). ``surface.explicit_scope`` is
    computed by :func:`_rich_tier_explicit_scope_str`, which *merges*
    *resolved_config.surface.explicit_scope* with
    *result.explicit_scope_source_sha256* rather than falling back to only
    one (CodeRabbit review, PR #803, fresh evidence, following an earlier
    Codex-reported gap: a ``--pack``-only run combining
    ``--public-symbols-list`` and ``--post-manifest`` can populate *both*
    at once, since ``force_public_symbols`` is threaded into
    ``compare()`` unconditionally -- a plain ``or`` fallback would then
    silently drop whichever axis lost the fallback race). See that
    function's own docstring for the full reasoning.
    *require_complete_analysis*
    mirrors the identically-named CLI/API flag (P0.4's analysis-
    completeness gate): it is not a D7 configuration namespace at all --
    ``compatibility_evaluation_config.py`` has no field for it -- but it
    genuinely changes gating behavior the same way a severity setting
    does (an otherwise-identical incomplete-evidence result exits 0 vs. 1
    depending on it, Codex review, PR #803), so it is threaded here as an
    independent parameter exactly like *severity_config*/*exit_code_scheme*
    already are, rather than pretended to live inside *resolved_config*.

    *severity_config*/*exit_code_scheme* are the same already-resolved pair
    every caller already threads for the ``exit`` block (see
    :func:`effective_config_fields_from_diff_result`'s identical parameters)
    -- ``gate.exit_code_scheme``/``gate.severity.*`` are populated from
    *these*, never from *resolved_config.gate* directly (CodeRabbit review,
    PR #803, fresh evidence: ``scan --against``'s own ``resolve_scan_config``
    deliberately blanks ``resolved_config.gate``'s severity/exit-code-scheme
    fields to built-in defaults regardless of the run's real
    ``--severity-preset``/``--exit-code-scheme`` -- see
    ``cli_scan_receipt._without_gate_settings`` -- so reading them from
    *resolved_config* there silently discarded the run's real gate. This
    also closes a class of drift the module docstring already promises
    doesn't happen ("the digest can never disagree with the exit block it
    sits beside"): *resolved_config.gate* is D7's own resolved copy, while
    *severity_config*/*exit_code_scheme* are the value that actually scored
    this run's real process exit -- for `compare` the two happen to agree
    today, but there is no structural guarantee they always will, and using
    the caller-supplied pair removes the possibility entirely rather than
    relying on that coincidence).
    """
    policy = getattr(resolved_config, "policy", None)
    surface = getattr(resolved_config, "surface", None)
    contract = getattr(resolved_config, "contract", None)
    gate = getattr(resolved_config, "gate", None)
    assurance = getattr(resolved_config, "assurance", None)
    evidence = getattr(resolved_config, "evidence", None)
    pack_groups = (
        getattr(policy, "packs", ()),
        getattr(gate, "packs", ()),
        getattr(contract, "packs", ()),
        getattr(surface, "packs", ()),
        getattr(evidence, "packs", ()),
    )
    return {
        "_tier": "contract",
        "policy.base": _identity_str(getattr(policy, "base", None)),
        "policy.overrides": _overrides_str(getattr(policy, "overrides", {})),
        "policy.reclassify": _reclassify_str(getattr(policy_file, "reclassify", ())),
        "policy.frozen_namespaces": _namespaces_str(
            getattr(policy_file, "frozen_namespaces", ())
        ),
        "policy.pattern_verdicts": str(
            bool(getattr(result, "pattern_verdicts_enabled", False))
        ),
        "policy.collapse_versioned_symbols": str(
            bool(getattr(result, "collapse_versioned_symbols_enabled", False))
        ),
        "policy.surface_metrics": str(
            bool(getattr(result, "surface_metrics_enabled", False))
        ),
        "policy.env_matrix": str(getattr(result, "env_matrix_source_sha256", "") or ""),
        "policy.reconcile_build_context": str(
            bool(getattr(result, "reconcile_build_context_enabled", False))
        ),
        "surface.internal_namespaces": _namespaces_str(
            getattr(surface, "internal_namespaces", ())
        ),
        "surface.explicit_scope": _rich_tier_explicit_scope_str(surface, result),
        "surface.scope_to_public_surface": str(
            bool(getattr(result, "scope_to_public_surface", False))
        ),
        "surface.scope_to_public_surface_requested": str(
            bool(getattr(result, "scope_to_public_surface_requested", True))
        ),
        "contract.mode": _enum_value(getattr(contract, "mode", None)),
        "contract.unresolved": str(getattr(contract, "unresolved", "") or ""),
        "contract.overlays": _namespaces_str(getattr(contract, "overlays", ())),
        "gate.exit_code_scheme": str(exit_code_scheme or ""),
        "gate.require_complete_analysis": str(bool(require_complete_analysis)),
        "gate.scope": _gate_scope_str(result),
        "gate.severity.abi_breaking": _severity_field(severity_config, "abi_breaking"),
        "gate.severity.potential_breaking": _severity_field(
            severity_config, "potential_breaking"
        ),
        "gate.severity.quality_issues": _severity_field(
            severity_config, "quality_issues"
        ),
        "gate.severity.addition": _severity_field(severity_config, "addition"),
        "gate.on_incomplete_scope": _on_incomplete_scope_str(result),
        "assurance.require_evidence": str(
            bool(getattr(assurance, "require_evidence", True))
        ),
        "suppressions": _digested_items_str(
            getattr(resolved_config, "suppressions", None)
        )
        or str(getattr(result, "suppression_source_sha256", "") or ""),
        "packs": _packs_str(*pack_groups),
    }


def effective_config_fields_from_diff_result(
    result: Any,
    *,
    severity_config: SeverityConfig | None,
    exit_code_scheme: str,
    require_complete_analysis: bool = False,
) -> dict[str, str]:
    """Baseline-tier fields, resolved from an ordinary comparison.

    *result* is the ``DiffResult`` every comparison produces; *severity_config*/
    *exit_code_scheme* are the same already-resolved pair
    :mod:`abicheck.reporter_contract_blocks`'s ``add_contract_context``
    already receives for the ``exit`` block (``None``/``"legacy"`` when no
    severity setting is in effect) -- read here, never re-derived.
    *require_complete_analysis* mirrors the identically-named CLI/API flag,
    same as the rich tier's own field (see
    :func:`effective_config_fields_from_full_config`'s docstring).
    ``surface.explicit_scope`` reads ``result.explicit_scope_source_
    sha256`` (Codex review, PR #803, fresh evidence: an ordinary run with
    neither ``--contract`` nor ``--pack`` can still resolve a forced-public
    symbol set from ``--public-symbols-list``/``.abicheck.yml``'s
    ``scope.public_symbols``, which changes which findings are retained --
    an earlier revision hard-coded this field empty for the baseline tier,
    so two such runs selecting different forced-public roots collided).
    """
    policy_file = getattr(result, "policy_file", None)
    return {
        "_tier": "baseline",
        "policy.base": _builtin_policy_base_str(getattr(result, "policy", "")),
        "policy.overrides": _overrides_str(getattr(policy_file, "overrides", {})),
        "policy.reclassify": _reclassify_str(getattr(policy_file, "reclassify", ())),
        "policy.frozen_namespaces": _namespaces_str(
            getattr(policy_file, "frozen_namespaces", ())
        ),
        "policy.pattern_verdicts": str(
            bool(getattr(result, "pattern_verdicts_enabled", False))
        ),
        "policy.collapse_versioned_symbols": str(
            bool(getattr(result, "collapse_versioned_symbols_enabled", False))
        ),
        "policy.surface_metrics": str(
            bool(getattr(result, "surface_metrics_enabled", False))
        ),
        "policy.env_matrix": str(getattr(result, "env_matrix_source_sha256", "") or ""),
        "policy.reconcile_build_context": str(
            bool(getattr(result, "reconcile_build_context_enabled", False))
        ),
        "surface.internal_namespaces": _namespaces_str(
            getattr(policy_file, "internal_namespaces", ())
        ),
        "surface.explicit_scope": str(
            getattr(result, "explicit_scope_source_sha256", "") or ""
        ),
        "surface.scope_to_public_surface": str(
            bool(getattr(result, "scope_to_public_surface", False))
        ),
        "surface.scope_to_public_surface_requested": str(
            bool(getattr(result, "scope_to_public_surface_requested", True))
        ),
        "contract.mode": "",
        "contract.unresolved": "",
        "contract.overlays": "",
        "gate.exit_code_scheme": str(exit_code_scheme or ""),
        "gate.require_complete_analysis": str(bool(require_complete_analysis)),
        "gate.scope": _gate_scope_str(result),
        "gate.severity.abi_breaking": _severity_field(severity_config, "abi_breaking"),
        "gate.severity.potential_breaking": _severity_field(
            severity_config, "potential_breaking"
        ),
        "gate.severity.quality_issues": _severity_field(
            severity_config, "quality_issues"
        ),
        "gate.severity.addition": _severity_field(severity_config, "addition"),
        "gate.on_incomplete_scope": _on_incomplete_scope_str(result),
        "assurance.require_evidence": "",
        "suppressions": str(getattr(result, "suppression_source_sha256", "") or ""),
        "packs": "",
    }


def effective_config_fields(
    result: Any,
    *,
    severity_config: SeverityConfig | None,
    exit_code_scheme: str,
    require_complete_analysis: bool = False,
) -> dict[str, str]:
    """The digest field dict for *result*, picking the richest available tier.

    Prefers ``result.contract_context.evaluation_context.resolved_config``
    over the bare ``result.evaluation_config`` whenever a
    ``PersistedContractContext`` exists (Codex review, PR #803, fresh
    evidence): ``contract_context.with_resolved_config`` merges *observed*
    overlay evidence (``contract.overlays``/``surface.explicit_scope`` --
    e.g. a ``--post-manifest`` overlay, which no front-end input model
    describes at all) into a *new* config object, but
    ``record_resolved_config`` (both `compare` and `scan`) always leaves the
    unmerged, front-end-only config on ``result.evaluation_config`` too --
    an earlier revision of this function preferred that unmerged copy
    unconditionally, which made the merge itself unreachable for any run
    that actually built a contract context (i.e. every ``--contract`` run),
    silently discarding exactly the observed-overlay data the merge exists
    to carry. Falls back to the bare ``result.evaluation_config`` (a real
    ``CompatibilityEvaluationConfig``, isinstance-checked so a stand-in/test
    double never fools this into the rich tier) when no context exists at
    all -- populated whenever ``--pack``/``--contract`` resolved one, *not
    only* under ``--contract`` (an earlier revision read this only off
    ``contract_context``, which stays unset without ``--contract``, so a
    ``--pack``-only run's real pack identities were silently unreachable at
    report time even though they were genuinely resolved). Falls back to
    the baseline tier last. This is the single function every front end
    (``compare``, the release fan-out, ``scan --against``) should call --
    it is what makes "one digest algorithm" true rather than each front end
    approximating the same shape independently.
    """
    from .compatibility_evaluation_config import CompatibilityEvaluationConfig

    policy_file = getattr(result, "policy_file", None)
    ctx = getattr(result, "contract_context", None)
    if ctx is not None:
        from .contract_evidence import PersistedContractContext

        if isinstance(ctx, PersistedContractContext):
            resolved_config = ctx.evaluation_context.resolved_config
            return effective_config_fields_from_full_config(
                resolved_config,
                result=result,
                policy_file=policy_file,
                require_complete_analysis=require_complete_analysis,
                severity_config=severity_config,
                exit_code_scheme=exit_code_scheme,
            )
    evaluation_config = getattr(result, "evaluation_config", None)
    if isinstance(evaluation_config, CompatibilityEvaluationConfig):
        return effective_config_fields_from_full_config(
            evaluation_config,
            result=result,
            policy_file=policy_file,
            require_complete_analysis=require_complete_analysis,
            severity_config=severity_config,
            exit_code_scheme=exit_code_scheme,
        )
    return effective_config_fields_from_diff_result(
        result,
        severity_config=severity_config,
        exit_code_scheme=exit_code_scheme,
        require_complete_analysis=require_complete_analysis,
    )

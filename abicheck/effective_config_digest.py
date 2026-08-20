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

* **Rich tier** -- when the comparison ran under ``--contract``/``--pack``,
  :class:`~abicheck.checker_types.DiffResult.contract_context` carries a
  full, already-resolved ``CompatibilityEvaluationConfig`` (``evaluation_
  context.resolved_config``), including real pack identities
  (``ImmutableIdentity(id, version, sha256)``). :func:`effective_config_
  fields` reads it directly.
* **Baseline tier** -- otherwise, the digest is built from the fields every
  comparison *does* resolve regardless: the active policy name and its
  overrides/internal-namespaces (``DiffResult.policy``/``policy_file``),
  and the resolved severity/exit-code-scheme gate (the same ``SeverityConfig``
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
"""

from __future__ import annotations

import hashlib
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
    "surface.internal_namespaces",
    "contract.mode",
    "contract.unresolved",
    "gate.exit_code_scheme",
    "gate.severity.abi_breaking",
    "gate.severity.potential_breaking",
    "gate.severity.quality_issues",
    "gate.severity.addition",
    "assurance.require_evidence",
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


def _namespaces_str(namespaces: Any) -> str:
    return ";".join(sorted(str(ns) for ns in (namespaces or ())))


def _packs_str(*pack_groups: Any) -> str:
    """``id@version:sha256`` for every pack identity across *pack_groups*."""
    identities: set[str] = set()
    for group in pack_groups:
        for identity in group or ():
            identities.add(
                f"{getattr(identity, 'id', '')}@{getattr(identity, 'version', '')}:"
                f"{getattr(identity, 'sha256', '')}"
            )
    return ";".join(sorted(identities))


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


def effective_config_fields_from_full_config(resolved_config: Any) -> dict[str, str]:
    """Rich-tier fields from a real ``CompatibilityEvaluationConfig``.

    *resolved_config* is ``DiffResult.contract_context.evaluation_context.
    resolved_config`` -- only reachable when the comparison ran with
    ``contract_evaluation=True`` (``--contract``/``--pack``). Reads every
    field straight off the six namespaces D7 already resolved; nothing here
    re-derives a value D7 precedence already decided.
    """
    policy = getattr(resolved_config, "policy", None)
    surface = getattr(resolved_config, "surface", None)
    contract = getattr(resolved_config, "contract", None)
    gate = getattr(resolved_config, "gate", None)
    severity = getattr(gate, "severity", None)
    assurance = getattr(resolved_config, "assurance", None)
    pack_groups = (
        getattr(policy, "packs", ()),
        getattr(gate, "packs", ()),
        getattr(contract, "packs", ()),
        getattr(surface, "packs", ()),
    )
    return {
        "_tier": "contract",
        "policy.base": str(getattr(getattr(policy, "base", None), "id", "") or ""),
        "policy.overrides": _overrides_str(getattr(policy, "overrides", {})),
        "surface.internal_namespaces": _namespaces_str(
            getattr(surface, "internal_namespaces", ())
        ),
        "contract.mode": _enum_value(getattr(contract, "mode", None)),
        "contract.unresolved": str(getattr(contract, "unresolved", "") or ""),
        "gate.exit_code_scheme": str(getattr(gate, "exit_code_scheme", "") or ""),
        "gate.severity.abi_breaking": _severity_field(severity, "abi_breaking"),
        "gate.severity.potential_breaking": _severity_field(
            severity, "potential_breaking"
        ),
        "gate.severity.quality_issues": _severity_field(severity, "quality_issues"),
        "gate.severity.addition": _severity_field(severity, "addition"),
        "assurance.require_evidence": str(
            bool(getattr(assurance, "require_evidence", True))
        ),
        "packs": _packs_str(*pack_groups),
    }


def effective_config_fields_from_diff_result(
    result: Any,
    *,
    severity_config: SeverityConfig | None,
    exit_code_scheme: str,
) -> dict[str, str]:
    """Baseline-tier fields, resolved from an ordinary comparison.

    *result* is the ``DiffResult`` every comparison produces; *severity_config*/
    *exit_code_scheme* are the same already-resolved pair
    :mod:`abicheck.reporter_contract_blocks`'s ``add_contract_context``
    already receives for the ``exit`` block (``None``/``"legacy"`` when no
    severity setting is in effect) -- read here, never re-derived.
    """
    policy_file = getattr(result, "policy_file", None)
    return {
        "_tier": "baseline",
        "policy.base": str(getattr(result, "policy", "") or ""),
        "policy.overrides": _overrides_str(getattr(policy_file, "overrides", {})),
        "surface.internal_namespaces": _namespaces_str(
            getattr(policy_file, "internal_namespaces", ())
        ),
        "contract.mode": "",
        "contract.unresolved": "",
        "gate.exit_code_scheme": str(exit_code_scheme or ""),
        "gate.severity.abi_breaking": _severity_field(severity_config, "abi_breaking"),
        "gate.severity.potential_breaking": _severity_field(
            severity_config, "potential_breaking"
        ),
        "gate.severity.quality_issues": _severity_field(
            severity_config, "quality_issues"
        ),
        "gate.severity.addition": _severity_field(severity_config, "addition"),
        "assurance.require_evidence": "",
        "packs": "",
    }


def effective_config_fields(
    result: Any,
    *,
    severity_config: SeverityConfig | None,
    exit_code_scheme: str,
) -> dict[str, str]:
    """The digest field dict for *result*, picking the richest available tier.

    Reads ``result.contract_context.evaluation_context.resolved_config``
    when present (a real ``CompatibilityEvaluationConfig``, isinstance-
    checked so a stand-in/test double never fools this into the rich tier);
    falls back to the baseline tier otherwise. This is the single function
    every front end (``compare``, the release fan-out, ``scan --against``)
    should call -- it is what makes "one digest algorithm" true rather than
    each front end approximating the same shape independently.
    """
    ctx = getattr(result, "contract_context", None)
    if ctx is not None:
        from .contract_evidence import PersistedContractContext

        if isinstance(ctx, PersistedContractContext):
            resolved_config = ctx.evaluation_context.resolved_config
            return effective_config_fields_from_full_config(resolved_config)
    return effective_config_fields_from_diff_result(
        result, severity_config=severity_config, exit_code_scheme=exit_code_scheme
    )

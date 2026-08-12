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

"""Custom policy file support for abicheck.

A policy file is a YAML document that maps ChangeKind names to severity levels,
allowing users to define project-specific verdict rules.

Format example (``my_policy.yaml``)::

    # abicheck policy file
    # Maps ChangeKind slug -> severity: break | warn | ignore
    #
    # break  -> BREAKING verdict (exit code 4)
    # warn   -> API_BREAK verdict (exit code 2)
    # risk   -> COMPATIBLE_WITH_RISK verdict (deployment risk, needs review)
    # ignore -> COMPATIBLE verdict (exit code 0)
    #
    # Any kind not listed falls back to the base policy (default: strict_abi).

    base_policy: strict_abi          # optional; default strict_abi

    overrides:
      enum_member_renamed:   ignore
      field_renamed:         ignore
      param_renamed:         ignore
      calling_convention_changed: warn

    # Optional: selector-scoped reclassification (see reclassify.py). Unlike
    # `overrides:` above, each rule is scoped to a symbol/pattern/namespace
    # selector -- same grammar as suppress:'s Suppression rules -- instead of
    # applying to every symbol of that kind project-wide. Unlike suppress:,
    # the finding is kept (reclassified), never deleted.
    reclassify:
      - kind: func_visibility_changed
        symbol_pattern: "_ZN6oneapi3dal.*"
        to: risk
        reason: "COMDAT-inline demotions; consumers already embed their own copy"

Usage::

    abicheck compare old.json new.json --policy-file my_policy.yaml

Notes:
- ``--policy-file`` overrides ``--policy`` when both are supplied.
- Checks are always executed; only verdict classification changes.
- An unknown ``ChangeKind`` slug is a hard load error (ADR-049 D8):
  warning-and-skip is unsafe because a renamed or misspelled kind can
  silently disable a release rule.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .checker_policy import (
    VALID_BASE_POLICIES,
    ChangeKind,
    Verdict,
    compute_verdict,
    policy_kind_sets,
)
from .errors import PolicyError

# NOTE: `.reclassify` is deliberately imported lazily (function-local) below,
# never at module top level. `reclassify.py` imports `.suppression`, which
# imports `.checker_types`, which imports `PolicyFile` from *this* module --
# a top-level import here would complete that cycle. This mirrors the
# existing convention every `SuppressionList` consumer in this codebase
# already follows for the same reason (see e.g. `checker.py`, `service.py`).
if TYPE_CHECKING:
    from collections.abc import Iterator

    from .reclassify import ReclassifyRule

# Severity name -> Verdict mapping
_SEVERITY_MAP: dict[str, Verdict] = {
    "break": Verdict.BREAKING,
    "warn": Verdict.API_BREAK,
    "risk": Verdict.COMPATIBLE_WITH_RISK,
    "ignore": Verdict.COMPATIBLE,
}


def parse_severity_value(value: Any) -> Verdict | None:
    """Map a policy-file severity spelling to its :class:`Verdict`.

    Case-insensitive, matching ``break``/``warn``/``risk``/``ignore``; returns
    ``None`` for anything else. Public (unlike ``_SEVERITY_MAP`` itself) so
    another loader for the same severity vocabulary -- ADR-049 D8's
    pack-manifest ``kind: policy`` assignments
    (``compatibility_evaluation_packs.py``) -- shares one source of truth
    instead of re-declaring the four spellings a second time.
    """
    return _SEVERITY_MAP.get(str(value).lower())


_VALID_BASE_POLICIES = VALID_BASE_POLICIES  # re-export alias for backward compat

# ADR-033 D7 — evidence-aware policy controls. Each knob maps a *category* of
# build/source evidence finding (not a single ChangeKind) to a verdict ceiling
# applied via Change.effective_verdict. Only acts when the user sets the knob;
# unset (None) leaves the finding's default category untouched so existing
# behaviour and the FP-rate gate are unchanged.
_EVIDENCE_ACTION_VERDICT: dict[str, Verdict] = {
    "ignore": Verdict.COMPATIBLE,
    "warn": Verdict.COMPATIBLE_WITH_RISK,
    "fail": Verdict.API_BREAK,
    "fail-api": Verdict.API_BREAK,
    # fail-release fails the same (exit-2) gate today: a source-only finding can
    # never be a hard (artifact-proven) BREAKING break per ADR-028 D3, so it
    # maps to API_BREAK. The distinct name is preserved for forward-compatible
    # release-gate semantics.
    "fail-release": Verdict.API_BREAK,
    "fail-on-abi-relevant": Verdict.API_BREAK,  # conditional; see _evidence_verdict
}

#: Allowed values per D7 knob.
_SOURCE_ONLY_ACTIONS = frozenset({"ignore", "warn", "fail-api", "fail-release"})
_BUILD_DRIFT_ACTIONS = frozenset({"ignore", "warn", "fail-on-abi-relevant"})
_GRAPH_RISK_ACTIONS = frozenset({"ignore", "warn", "fail"})
_REQUIRE_EVIDENCE_KEYS = frozenset({"build_context", "source_abi", "graph_summary"})


def builtin_policy_path(name: str) -> Path | None:
    """Resolve a bare built-in policy name (e.g. ``"security"``) to its file.

    Returns the packaged ``abicheck/policies/<name>.yaml`` path if *name*
    exactly matches a shipped policy stem, else ``None``. Only bare names are
    accepted so path-like values cannot traverse or accidentally resolve as
    built-ins.
    """
    from .policies import POLICIES_DIR, builtin_policy_names

    if name not in builtin_policy_names():
        return None

    candidate = POLICIES_DIR / f"{name}.yaml"
    return candidate if candidate.is_file() else None

# Kinds that are especially dangerous to downgrade — removing a function
# or changing its signature always causes hard crashes.
_CRITICAL_BREAKING_KINDS: frozenset[ChangeKind] = frozenset({
    ChangeKind.FUNC_REMOVED,
    ChangeKind.FUNC_RETURN_CHANGED,
    ChangeKind.FUNC_PARAMS_CHANGED,
    ChangeKind.TYPE_SIZE_CHANGED,
    ChangeKind.TYPE_VTABLE_CHANGED,
    ChangeKind.VAR_REMOVED,
    ChangeKind.VAR_TYPE_CHANGED,
    ChangeKind.SONAME_CHANGED,
})


def _parse_evidence_action(
    block: dict[str, Any], key: str, allowed: frozenset[str], path: Path
) -> str | None:
    """Parse one ADR-033 D7 string knob from an ``evidence_policy`` block."""
    if key not in block:
        return None
    val = block[key]
    if not isinstance(val, str) or val.lower() not in allowed:
        raise PolicyError(
            f"evidence_policy.{key} in {path}: invalid value {val!r}. "
            f"Valid values: {sorted(allowed)}"
        )
    return val.lower()


def _parse_require_evidence(raw: Any, path: Path) -> dict[str, bool]:
    """Parse the ADR-033 D7 ``require_evidence`` mapping (layer -> bool)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError(
            "evidence_policy.require_evidence must be a YAML mapping, got "
            + type(raw).__name__
        )
    out: dict[str, bool] = {}
    for layer, want in raw.items():
        if str(layer) not in _REQUIRE_EVIDENCE_KEYS:
            raise PolicyError(
                f"evidence_policy.require_evidence in {path}: unknown layer "
                f"{layer!r}. Valid keys: {sorted(_REQUIRE_EVIDENCE_KEYS)}"
            )
        if not isinstance(want, bool):
            raise PolicyError(
                f"evidence_policy.require_evidence.{layer} must be a boolean, got "
                + type(want).__name__
            )
        out[str(layer)] = want
    return out


def _parse_base_policy(raw: dict[str, Any]) -> str:
    """Extract and validate the ``base_policy`` field from a raw YAML mapping."""
    base_policy = raw.get("base_policy", "strict_abi")
    if not isinstance(base_policy, str):
        raise PolicyError(
            "'base_policy' must be a string, got " + type(base_policy).__name__
        )
    if base_policy not in _VALID_BASE_POLICIES:
        raise PolicyError(
            f"Unknown base_policy {base_policy!r}. "
            f"Valid values: {sorted(_VALID_BASE_POLICIES)}"
        )
    return base_policy


def _parse_overrides(overrides_raw: Any, path: Path) -> dict[ChangeKind, Verdict]:
    """Validate and parse the ``overrides`` block into a ChangeKind -> Verdict mapping."""
    if not isinstance(overrides_raw, dict):
        raise PolicyError("'overrides' must be a YAML mapping of kind -> severity")

    slug_to_kind = {k.value: k for k in ChangeKind}
    overrides: dict[ChangeKind, Verdict] = {}
    unknown_kinds: list[str] = []
    unknown_severities: list[str] = []

    for slug, severity in overrides_raw.items():
        kind = slug_to_kind.get(str(slug))
        if kind is None:
            unknown_kinds.append(str(slug))
            continue
        verdict = parse_severity_value(severity)
        if verdict is None:
            unknown_severities.append(f"{slug}: {severity!r}")
            continue
        overrides[kind] = verdict

    if unknown_kinds:
        # ADR-049 D8: "An unknown ChangeKind in a custom policy is a hard
        # load error; warning-and-skip is unsafe because a renamed kind can
        # silently disable a release rule." A silently-skipped override for
        # e.g. a renamed `func_removed` slug would leave that kind on its
        # (possibly stricter) base-policy verdict with no indication the
        # override was ever dropped.
        raise PolicyError(
            f"Unknown ChangeKind slugs in {path}: {sorted(unknown_kinds)}. "
            "Rename or remove them -- see `abicheck --help` or "
            "docs/reference/change-kinds.md for valid slugs."
        )
    if unknown_severities:
        raise PolicyError(
            f"Invalid severity values in {path}: {unknown_severities}. "
            "Valid values: break, warn, risk, ignore"
        )
    return overrides


def _parse_reclassify_expires(raw: Any, path: Path, index: int) -> date | None:
    """Parse one ``reclassify[i].expires`` value (ISO 8601 string, or a
    YAML-native date PyYAML already decoded)."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError as e:
            raise PolicyError(
                f"reclassify[{index}].expires in {path}: invalid date {raw!r} "
                "(expected ISO 8601, e.g. 2026-06-01)"
            ) from e
    raise PolicyError(
        f"reclassify[{index}].expires in {path}: must be a date, got "
        + type(raw).__name__
    )


def _parse_reclassify(raw: Any, path: Path) -> list[ReclassifyRule]:
    """Validate and parse the ``reclassify:`` block (A: selector-scoped
    reclassification) into a list of :class:`ReclassifyRule`.

    Unlike ``overrides:`` (keyed by ``ChangeKind`` alone), each entry here
    carries the same selector grammar :mod:`abicheck.suppression` already
    implements, plus a required ``to:`` severity -- see
    ``abicheck/reclassify.py``'s module docstring.
    """
    from .reclassify import RECLASSIFY_KNOWN_KEYS, ReclassifyRule

    if not isinstance(raw, list):
        raise PolicyError(
            "'reclassify' must be a YAML list of rules, got " + type(raw).__name__
        )

    rules: list[ReclassifyRule] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise PolicyError(
                f"reclassify[{i}] in {path}: must be a YAML mapping, got "
                + type(entry).__name__
            )
        unknown = set(entry) - RECLASSIFY_KNOWN_KEYS
        if unknown:
            raise PolicyError(
                f"reclassify[{i}] in {path}: unknown key(s) {sorted(unknown)}. "
                f"Valid keys: {sorted(RECLASSIFY_KNOWN_KEYS)}"
            )
        if "to" not in entry:
            raise PolicyError(f"reclassify[{i}] in {path}: missing required 'to' field")
        to_raw = entry["to"]
        to_verdict = parse_severity_value(to_raw)
        if to_verdict is None:
            raise PolicyError(
                f"reclassify[{i}] in {path}: invalid 'to' value {to_raw!r}. "
                "Valid values: break, warn, risk, ignore"
            )
        kind = entry.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise PolicyError(f"reclassify[{i}].kind in {path}: must be a string")

        try:
            rule = ReclassifyRule(
                to_verdict=to_verdict,
                to=str(to_raw),
                symbol=entry.get("symbol"),
                symbol_pattern=entry.get("symbol_pattern"),
                type_pattern=entry.get("type_pattern"),
                member_name=entry.get("member_name"),
                namespace=entry.get("namespace"),
                entity_namespace=entry.get("entity_namespace"),
                cause_namespace=entry.get("cause_namespace"),
                source_location=entry.get("source_location"),
                change_kind=kind,
                reason=entry.get("reason"),
                label=entry.get("label"),
                expires=_parse_reclassify_expires(entry.get("expires"), path, i),
            )
        except ValueError as e:
            raise PolicyError(f"reclassify[{i}] in {path}: {e}") from e
        rules.append(rule)
    return rules


def _parse_frozen_namespaces(frozen_raw: Any) -> list[str]:
    """Validate and parse the ``frozen_namespaces`` list of glob patterns."""
    if not isinstance(frozen_raw, list):
        raise PolicyError(
            "'frozen_namespaces' must be a YAML list of glob patterns, got "
            + type(frozen_raw).__name__
        )
    result: list[str] = []
    for i, pat in enumerate(frozen_raw):
        if not isinstance(pat, str):
            raise PolicyError(
                f"frozen_namespaces[{i}]: expected string, got {type(pat).__name__}"
            )
        result.append(pat)
    return result


def _parse_internal_namespaces(raw: Any) -> list[str]:
    """Validate and parse the ``internal_namespaces`` list of namespace tokens."""
    if not isinstance(raw, list):
        raise PolicyError(
            "'internal_namespaces' must be a YAML list of namespace tokens, got "
            + type(raw).__name__
        )
    result: list[str] = []
    for i, token in enumerate(raw):
        if not isinstance(token, str):
            raise PolicyError(
                f"internal_namespaces[{i}]: expected string, got {type(token).__name__}"
            )
        result.append(token)
    return result


# Severity ordering used for frozen-namespace floor comparisons.
_VERDICT_ORDER: list[Verdict] = [
    Verdict.NO_CHANGE,
    Verdict.COMPATIBLE,
    Verdict.COMPATIBLE_WITH_RISK,
    Verdict.API_BREAK,
    Verdict.BREAKING,
]


def _raw_verdict_for_kind(
    kind: Any,
    breaking: frozenset[Any],
    api_break: frozenset[Any],
    compatible: frozenset[Any],
    risk: frozenset[Any],
) -> Verdict:
    """Return the raw base-policy verdict for *kind* given the four policy sets.

    Mirrors the frozen-namespace floor logic in :meth:`PolicyFile.compute_verdict`:
    an unrecognised kind is treated as BREAKING (fail-safe default).
    """
    if kind in breaking:
        return Verdict.BREAKING
    if kind in api_break:
        return Verdict.API_BREAK
    if kind in risk:
        return Verdict.COMPATIBLE_WITH_RISK
    if kind in compatible:
        return Verdict.COMPATIBLE
    return Verdict.BREAKING


def _resolve_change_verdict(
    change: Any,
    base_policy: str,
    overrides: dict[Any, Verdict],
    breaking: frozenset[Any],
    api_break: frozenset[Any],
    compatible: frozenset[Any],
    risk: frozenset[Any],
    reclassify: list[ReclassifyRule] | None = None,
) -> Verdict:
    """Resolve the effective verdict for a single *change* object.

    Priority order (highest first):
    1. ``change.effective_verdict`` — ADR-025 / ADR-033 D7 modulation (frozen-
       namespace floor still applied).
    2. ``reclassify:`` — the first selector-scoped rule (in file order) whose
       selectors match this change (frozen-namespace floor still applied).
       Consulted ahead of the kind-global ``overrides:`` entry for the same
       kind since a rule scoped to a selector is strictly more specific than
       one scoped to a bare kind (see ``abicheck/reclassify.py``).
    3. ``overrides[kind]`` — per-kind policy override (frozen-namespace floor
       still applied; downgrades on frozen symbols are silently rejected).
    4. Base policy verdict.
    """
    from .reclassify import first_matching_reclassify_verdict

    kind = change.kind
    fnv = getattr(change, "frozen_namespace_violation", None)
    frozen = isinstance(fnv, str) and bool(fnv)

    eff = getattr(change, "effective_verdict", None)
    if isinstance(eff, Verdict):
        raw = _raw_verdict_for_kind(kind, breaking, api_break, compatible, risk)
        if frozen and _VERDICT_ORDER.index(eff) < _VERDICT_ORDER.index(raw):
            return raw
        return eff

    base_v = compute_verdict([change], policy=base_policy)

    reclass_v = first_matching_reclassify_verdict(reclassify or [], change)
    if reclass_v is not None:
        if frozen and _VERDICT_ORDER.index(reclass_v) < _VERDICT_ORDER.index(base_v):
            return base_v
        return reclass_v

    if kind in overrides:
        override_v = overrides[kind]
        if frozen and _VERDICT_ORDER.index(override_v) < _VERDICT_ORDER.index(base_v):
            return base_v
        return override_v
    return base_v


def _parse_evidence_policy(
    ev_raw: Any, path: Path
) -> tuple[str | None, str | None, str | None, dict[str, bool]]:
    """Validate and parse the ``evidence_policy`` block (ADR-033 D7).

    Returns a 4-tuple of
    ``(source_only_findings, build_context_drift, graph_risk_findings, require_evidence)``.
    """
    if not isinstance(ev_raw, dict):
        raise PolicyError(
            "'evidence_policy' must be a YAML mapping, got "
            + type(ev_raw).__name__
        )
    source_only = _parse_evidence_action(
        ev_raw, "source_only_findings", _SOURCE_ONLY_ACTIONS, path
    )
    build_drift = _parse_evidence_action(
        ev_raw, "build_context_drift", _BUILD_DRIFT_ACTIONS, path
    )
    graph_risk = _parse_evidence_action(
        ev_raw, "graph_risk_findings", _GRAPH_RISK_ACTIONS, path
    )
    require_evidence = _parse_require_evidence(ev_raw.get("require_evidence"), path)
    return source_only, build_drift, graph_risk, require_evidence


@dataclass
class PolicyFile:
    """Parsed custom policy file.

    Attributes:
        base_policy: Base built-in policy to start from (default: ``strict_abi``).
        overrides: Mapping of ChangeKind -> Verdict as specified in the file.
        source_path: Path to the loaded file (for error reporting).
    """

    base_policy: str = "strict_abi"
    overrides: dict[ChangeKind, Verdict] = field(default_factory=dict)
    # A: selector-scoped reclassification (abicheck/reclassify.py) — the
    # third policy-file primitive, between overrides: (kind-global, no
    # selector) and suppress: (selector-scoped, but deletes the finding).
    # Consulted ahead of `overrides[kind]` for the same kind in
    # `compute_verdict`/`_resolve_change_verdict`, since a selector-scoped
    # rule is strictly more specific than a bare-kind one.
    reclassify: list[ReclassifyRule] = field(default_factory=list)
    source_path: Path | None = None
    #: sha256 of the exact raw bytes :meth:`load` read, when it loaded this
    #: document from a file. Captured at parse time on purpose: a consumer
    #: that re-read ``source_path`` later would digest whatever is on disk
    #: *then*, which for a file edited in between identifies content this
    #: object was never built from (Codex review, ADR-049 D6 replay).
    source_sha256: str | None = None
    # Glob patterns identifying namespaces whose symbols / types are
    # contractually frozen (e.g. a versioned internal namespace such as
    # `**::detail::r1` or `**::detail::v1`). Any finding whose symbol or caused_by_type lies in
    # one of these namespaces is tagged via Change.frozen_namespace_violation
    # and is exempt from policy_override downgrades. Patterns use fnmatch
    # globbing against ``::``-joined namespace segments; ``**`` matches any
    # number of leading segments. Empty list = no extra namespaces.
    frozen_namespaces: list[str] = field(default_factory=list)
    # ADR-044 P1 item 5 — project-configurable internal-implementation-namespace
    # convention (e.g. ``priv`` instead of the hard-coded default ``detail``/
    # ``impl``/``internal``/``__detail``/``_impl`` five-token set). Threaded
    # into MarkReachability/DetectInternalLeaks/DemoteUnreachableInternalChurn
    # via PipelineContext.internal_namespaces (mirrors frozen_namespaces'
    # threading). Empty list = use each step's own DEFAULT_INTERNAL_NAMESPACES.
    # Deliberately NOT threaded into DetectNamespacePatterns's
    # experimental_namespaces constructor parameter: that governs a distinct
    # convention (the `experimental::` graduation namespace, DEFAULT_EXPERIMENTAL_NAMESPACES)
    # unrelated to internal-implementation-detail namespaces, despite the ADR's
    # roadmap text grouping all four steps together.
    internal_namespaces: list[str] = field(default_factory=list)
    # Whether the document actually carried the ``internal_namespaces`` key.
    # An explicit empty list is a *statement* ("this project has none"), which
    # a parsed empty list alone cannot be told apart from the key being absent
    # — and the two must not behave alike where something else would otherwise
    # fill the field in (ADR-049 D8: a selected pack never overrides a stated
    # value). Set by ``load()``; a directly-constructed PolicyFile leaves it
    # False, so nothing that builds one in code starts claiming a statement it
    # never made.
    internal_namespaces_stated: bool = False
    # ADR-033 D7 — evidence-aware policy controls. ``None`` means "unset": the
    # finding keeps its default category (current behaviour). A set value maps
    # the whole category of build/source evidence findings to a verdict ceiling.
    source_only_findings: str | None = None  # ignore|warn|fail-api|fail-release
    build_context_drift: str | None = None   # ignore|warn|fail-on-abi-relevant
    graph_risk_findings: str | None = None    # ignore|warn|fail
    # require_evidence — fail the run when a declared-required evidence layer is
    # absent (enforced in the compare evidence pipeline, ADR-033 D7). Empty =
    # nothing required.
    require_evidence: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PolicyFile:
        """Load and validate a policy file from *path*.

        Raises:
            ValueError: If the file is malformed, ``base_policy`` is not a string,
                has an unknown policy name, ``overrides`` is not a mapping,
                contains an unknown ``ChangeKind`` slug (ADR-049 D8: a hard
                load error, not a warning), or contains invalid severity
                strings.
            OSError: If the file cannot be read.
        """
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "PyYAML is required for --policy-file support. "
                "Install it with: pip install pyyaml"
            ) from exc

        # A bare built-in policy name (e.g. "security") must resolve to the
        # packaged policy before consulting the working directory. Otherwise an
        # attacker-controlled checkout can shadow the built-in with a local file
        # named "security" and silently downgrade security-hardening verdicts.
        builtin = builtin_policy_path(str(path))
        if builtin is not None:
            path = builtin

        # Digest the *raw bytes*, then decode those same bytes to parse:
        # `read_text()` performs universal-newline translation, so hashing its
        # result would make a CRLF file digest identically to its LF twin --
        # a real byte-for-byte content change the receipt could not detect
        # (Codex review). PyYAML handles CRLF line breaks per spec, so parsing
        # the undecoded-then-decoded text is unchanged.
        raw_bytes = path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        raw: Any = yaml.safe_load(raw_bytes.decode("utf-8"))
        if raw is None:
            return cls(source_path=path, source_sha256=digest)
        if not isinstance(raw, dict):
            raise PolicyError(
                f"Policy file must be a YAML mapping, got {type(raw).__name__}"
            )

        base_policy = _parse_base_policy(raw)
        overrides = _parse_overrides(raw.get("overrides", {}), path)
        reclassify = _parse_reclassify(raw.get("reclassify", []), path)
        frozen_namespaces = _parse_frozen_namespaces(raw.get("frozen_namespaces", []))
        internal_namespaces = _parse_internal_namespaces(raw.get("internal_namespaces", []))
        source_only, build_drift, graph_risk, require_evidence = _parse_evidence_policy(
            raw.get("evidence_policy", {}), path
        )

        return cls(
            base_policy=base_policy,
            overrides=overrides,
            reclassify=reclassify,
            source_path=path,
            source_sha256=digest,
            frozen_namespaces=frozen_namespaces,
            internal_namespaces=internal_namespaces,
            internal_namespaces_stated="internal_namespaces" in raw,
            source_only_findings=source_only,
            build_context_drift=build_drift,
            graph_risk_findings=graph_risk,
            require_evidence=require_evidence,
        )

    def evidence_verdict(self, category: str, abi_relevant: bool = False) -> Verdict | None:
        """Resolve the ADR-033 D7 verdict ceiling for an evidence *category*.

        *category* is one of ``"source_only"``, ``"build_context"``,
        ``"graph_risk"``. Returns the :class:`Verdict` the matching knob forces
        for that finding, or ``None`` when the knob is unset (leave the default
        category). ``abi_relevant`` selects the conditional branch of
        ``build_context_drift: fail-on-abi-relevant`` (only ABI-relevant build
        drift fails; the rest stays a risk).
        """
        action = {
            "source_only": self.source_only_findings,
            "build_context": self.build_context_drift,
            "graph_risk": self.graph_risk_findings,
        }.get(category)
        if action is None:
            return None
        if action == "fail-on-abi-relevant":
            return Verdict.API_BREAK if abi_relevant else Verdict.COMPATIBLE_WITH_RISK
        return _EVIDENCE_ACTION_VERDICT.get(action)

    def compute_verdict(self, changes: list[Any]) -> Verdict:
        """Compute verdict for *changes* applying base_policy then overrides.

        Algorithm:
        1. Compute base verdict using the configured base_policy.
        2. For each change, if its kind has an override, apply the override
           verdict (can upgrade or downgrade).
        3. Final verdict = worst (most severe) verdict across all changes.
        """
        if not changes:
            return Verdict.NO_CHANGE

        # Raw per-kind category sets (no effective_verdict) for the frozen-namespace
        # severity floor: a finding on a contractually frozen symbol must never be
        # downgraded below this, whether by an override or a modulation.
        _b, _a, _c, _r = policy_kind_sets(self.base_policy)

        verdicts = [
            _resolve_change_verdict(
                change, self.base_policy, self.overrides, _b, _a, _c, _r,
                self.reclassify,
            )
            for change in changes
        ]

        # Worst verdict wins.
        return max(verdicts, key=lambda v: _VERDICT_ORDER.index(v) if v in _VERDICT_ORDER else 0)

    def describe(self) -> str:
        """Return a human-readable summary of this policy."""
        lines = [f"base_policy: {self.base_policy}"]
        if self.overrides:
            lines.append("overrides:")
            for kind, verdict in sorted(
                self.overrides.items(), key=lambda x: x[0].value
            ):
                sev = next(s for s, v in _SEVERITY_MAP.items() if v == verdict)
                lines.append(f"  {kind.value}: {sev}")
        else:
            lines.append("overrides: (none)")
        if self.reclassify:
            lines.append("reclassify:")
            for rule in self.reclassify:
                lines.append(f"  {rule.describe()}")
        return "\n".join(lines)

    def validate_overrides(self) -> list[str]:
        """Check for high-risk or suspicious overrides and return warnings.

        Returns a list of human-readable warning strings.  Empty list = no issues.

        Checks:
        - Downgrading known-dangerous BREAKING kinds to COMPATIBLE
          (e.g., func_removed → ignore).  These almost certainly mask real breaks.
        - Downgrading BREAKING to COMPATIBLE_WITH_RISK for critical kinds.

        An override is only ever flagged relative to *this* base policy's own
        verdict for that kind (via ``_raw_verdict_for_kind``), not merely
        because the kind is in ``_CRITICAL_BREAKING_KINDS``: some critical
        kinds (``soname_changed``) are already COMPATIBLE_WITH_RISK under
        e.g. ``strict_abi``, so an override that merely restates that verdict
        (``soname_changed: risk``) is not a downgrade and must not warn (Codex
        review) — only an override that is strictly *weaker* than the base
        policy's own verdict for that kind is a real downgrade.
        """
        # Derive the base policy's own kind sets so that policy-specific sets
        # (e.g. plugin_abi) are correctly flagged, both for the breaking-kind
        # check below and for the per-kind base verdict comparison above.
        base_breaking, base_api_break, base_compatible, base_risk = policy_kind_sets(
            self.base_policy
        )

        warnings: list[str] = []
        for kind, verdict in self.overrides.items():
            base_verdict = _raw_verdict_for_kind(
                kind, base_breaking, base_api_break, base_compatible, base_risk
            )
            if _VERDICT_ORDER.index(verdict) >= _VERDICT_ORDER.index(base_verdict):
                continue  # not a downgrade from this base policy's own verdict
            if kind in _CRITICAL_BREAKING_KINDS:
                if verdict == Verdict.COMPATIBLE:
                    warnings.append(
                        f"HIGH RISK: '{kind.value}' downgraded to 'ignore' — "
                        f"this is almost certainly a mistake. "
                        f"This kind causes hard crashes when the ABI changes."
                    )
                elif verdict == Verdict.COMPATIBLE_WITH_RISK:
                    warnings.append(
                        f"RISK: '{kind.value}' downgraded to 'risk' — "
                        f"this kind usually causes binary incompatibility. "
                        f"Consider keeping it as 'break'."
                    )
            elif kind in base_breaking and verdict == Verdict.COMPATIBLE:
                warnings.append(
                    f"'{kind.value}' (BREAKING) downgraded to 'ignore' — "
                    f"verify this is intentional."
                )
        return warnings


# ── validate_overrides() warning dedup (Codex review, PR #730) ──────────────
#
# A single `--policy-file` can be loaded several times over the course of one
# CLI invocation -- notably `compare-release`, whose per-library fan-out
# reloads it once per library (plus again for JUnit's re-run), and whose
# strict-suppression-early-validation and probe-matrix paths each load it
# again on top of that, through *two different* call sites
# (`cli_params._load_suppression_and_policy`, which warns via `click.echo`,
# and `service.load_suppression_and_policy`, which warns via the logger).
# Without deduplication a single risky override logs the identical warning
# once per load, flooding stderr for a large release.
#
# Lives here, in the leaf module both loaders already import, rather than in
# either `cli_params.py` or `service.py`, so the *same* dedup scope covers
# both call sites uniformly -- a fix that lived in only one of them would
# still leave the other's warnings undeduplicated.
_warning_dedup_var: contextvars.ContextVar[set[tuple[str, str]] | None] = (
    contextvars.ContextVar("_policy_override_warning_dedup", default=None)
)
# Guards the check-then-add on `_warning_dedup_var`'s set below. The GIL
# makes each individual `set` op atomic, but "is this key already present,
# and if not add it" is two ops -- without this lock, two worker threads
# racing to be first past `dedup_validate_overrides_warnings()`'s ``set()``
# (`compare-release`'s ThreadPoolExecutor fan-out, both starting on the
# first, identical policy-file load) can both observe "not yet seen" and
# both emit, reproducing the exact flooding this whole mechanism exists to
# prevent. One process-wide lock is deliberately simple: the critical
# section is a handful of dict/set ops per warning, never I/O, so contention
# cost is negligible next to correctness (confirmed by a real intermittent
# test failure under `ThreadPoolExecutor` before this lock was added).
_warning_dedup_lock = threading.Lock()


@contextlib.contextmanager
def dedup_validate_overrides_warnings() -> Iterator[None]:
    """Scope repeated ``validate_overrides()`` loads to warn at most once.

    Within this context, a given (policy-file content, warning text) pair is
    returned by :func:`pending_validate_overrides_warnings` the first time it
    is seen and omitted on every later call for the same pair, regardless of
    which loader (`cli_params._load_suppression_and_policy` or
    `service.load_suppression_and_policy`) made the call. Outside any such
    scope (the default), every call returns every warning every time --
    unchanged behaviour for a single-shot caller such as a plain `compare`,
    `scan --against`, or a direct Python API `run_compare` call, none of
    which ever enter this scope.

    Only dedupes within one OS process: a `ContextVar` set here is not
    automatically visible to a *different* process, so a caller that forks
    or spawns a subprocess to do its per-library work would need to
    propagate this scope itself (nothing in this codebase currently does).
    `compare-release`'s own parallel fan-out uses a `ThreadPoolExecutor`
    (real OS threads, not separate processes) precisely so this scope
    *can* reach it -- see `cli_compare_release._compare_release_parallel`'s
    own docstring for how it explicitly propagates a `contextvars.Context`
    copy into each worker thread, since `ThreadPoolExecutor` does not do
    that automatically either.
    """
    token = _warning_dedup_var.set(set())
    try:
        yield
    finally:
        _warning_dedup_var.reset(token)


def pending_validate_overrides_warnings(pf: PolicyFile) -> list[str]:
    """Return *pf*'s :meth:`~PolicyFile.validate_overrides` warnings not yet
    surfaced in the current :func:`dedup_validate_overrides_warnings` scope
    (or all of them, unfiltered, outside any such scope), recording whichever
    are returned as seen. Both `cli_params._load_suppression_and_policy` and
    `service.load_suppression_and_policy` call this instead of
    `pf.validate_overrides()` directly, so the same warning is never surfaced
    twice within one dedup scope no matter which loader reloaded the file.
    """
    warnings = pf.validate_overrides()
    if not warnings:
        return warnings
    seen = _warning_dedup_var.get()
    if seen is None:
        return warnings
    key_base = pf.source_sha256 or str(pf.source_path)
    fresh = []
    with _warning_dedup_lock:
        for warning in warnings:
            key = (key_base, warning)
            if key in seen:
                continue
            seen.add(key)
            fresh.append(warning)
    return fresh

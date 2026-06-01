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

Usage::

    abicheck compare old.json new.json --policy-file my_policy.yaml

Notes:
- ``--policy-file`` overrides ``--policy`` when both are supplied.
- Checks are always executed; only verdict classification changes.
- Unknown kind names emit a warning and are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .checker_policy import (
    VALID_BASE_POLICIES,
    ChangeKind,
    Verdict,
    compute_verdict,
    policy_kind_sets,
)
from .errors import PolicyError

log = logging.getLogger(__name__)

# Severity name -> Verdict mapping
_SEVERITY_MAP: dict[str, Verdict] = {
    "break": Verdict.BREAKING,
    "warn": Verdict.API_BREAK,
    "risk": Verdict.COMPATIBLE_WITH_RISK,
    "ignore": Verdict.COMPATIBLE,
}

_VALID_BASE_POLICIES = VALID_BASE_POLICIES  # re-export alias for backward compat

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
    source_path: Path | None = None
    # Glob patterns identifying namespaces whose symbols / types are
    # contractually frozen (e.g. a versioned internal namespace such as
    # `**::detail::r1` or `**::detail::v1`). Any finding whose symbol or caused_by_type lies in
    # one of these namespaces is tagged via Change.frozen_namespace_violation
    # and is exempt from policy_override downgrades. Patterns use fnmatch
    # globbing against ``::``-joined namespace segments; ``**`` matches any
    # number of leading segments. Empty list = no extra namespaces.
    frozen_namespaces: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> PolicyFile:
        """Load and validate a policy file from *path*.

        Raises:
            ValueError: If the file is malformed, ``base_policy`` is not a string,
                has an unknown policy name, ``overrides`` is not a mapping, or
                contains invalid severity strings.
            OSError: If the file cannot be read.

        Note:
            Unknown kind names in ``overrides`` emit a ``log.warning`` and are
            skipped — they do not raise. This is intentional to tolerate typos
            in large policy files without aborting the run.
        """
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "PyYAML is required for --policy-file support. "
                "Install it with: pip install pyyaml"
            ) from exc

        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return cls(source_path=path)
        if not isinstance(raw, dict):
            raise PolicyError(
                f"Policy file must be a YAML mapping, got {type(raw).__name__}"
            )

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

        overrides_raw = raw.get("overrides", {})
        if not isinstance(overrides_raw, dict):
            raise PolicyError("'overrides' must be a YAML mapping of kind -> severity")

        overrides: dict[ChangeKind, Verdict] = {}
        unknown_kinds: list[str] = []
        unknown_severities: list[str] = []

        slug_to_kind = {k.value: k for k in ChangeKind}

        for slug, severity in overrides_raw.items():
            kind = slug_to_kind.get(str(slug))
            if kind is None:
                unknown_kinds.append(str(slug))
                continue
            verdict = _SEVERITY_MAP.get(str(severity).lower())
            if verdict is None:
                unknown_severities.append(f"{slug}: {severity!r}")
                continue
            overrides[kind] = verdict

        if unknown_kinds:
            log.warning(
                "policy file %s: unknown ChangeKind slugs (skipped): %s",
                path,
                ", ".join(sorted(unknown_kinds)),
            )
        if unknown_severities:
            raise PolicyError(
                f"Invalid severity values in {path}: {unknown_severities}. "
                "Valid values: break, warn, risk, ignore"
            )

        # frozen_namespaces: optional list of glob patterns identifying
        # contractually-frozen namespaces (e.g. a versioned detail::r1).
        frozen_raw = raw.get("frozen_namespaces", [])
        if not isinstance(frozen_raw, list):
            raise PolicyError(
                "'frozen_namespaces' must be a YAML list of glob patterns, got "
                + type(frozen_raw).__name__
            )
        frozen_namespaces: list[str] = []
        for i, pat in enumerate(frozen_raw):
            if not isinstance(pat, str):
                raise PolicyError(
                    f"frozen_namespaces[{i}]: expected string, got {type(pat).__name__}"
                )
            frozen_namespaces.append(pat)

        return cls(
            base_policy=base_policy,
            overrides=overrides,
            source_path=path,
            frozen_namespaces=frozen_namespaces,
        )

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

        # Start from base policy verdict
        verdicts: list[Verdict] = []
        order = [
            Verdict.NO_CHANGE,
            Verdict.COMPATIBLE,
            Verdict.COMPATIBLE_WITH_RISK,
            Verdict.API_BREAK,
            Verdict.BREAKING,
        ]
        for change in changes:
            kind = change.kind
            base_v = compute_verdict([change], policy=self.base_policy)
            if kind in self.overrides:
                override_v = self.overrides[kind]
                # A change that violates a frozen-namespace contract MUST
                # NOT be downgraded by a policy override. The user's
                # `overrides` block can still upgrade severity (e.g. mark
                # something more severe than the base classification), but
                # downgrades on tagged changes are silently rejected so
                # users cannot accidentally mask a documented invariant.
                # ``isinstance(..., str)`` guards against MagicMock-style
                # test doubles where any attribute access returns a truthy
                # mock; only a real glob-pattern string counts as a tag.
                fnv = getattr(change, "frozen_namespace_violation", None)
                if isinstance(fnv, str) and fnv and (
                    order.index(override_v) < order.index(base_v)
                ):
                    verdicts.append(base_v)
                else:
                    verdicts.append(override_v)
            else:
                verdicts.append(base_v)

        # Worst verdict wins (reuse the `order` list from above).
        return max(verdicts, key=lambda v: order.index(v) if v in order else 0)

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
        return "\n".join(lines)

    def validate_overrides(self) -> list[str]:
        """Check for high-risk or suspicious overrides and return warnings.

        Returns a list of human-readable warning strings.  Empty list = no issues.

        Checks:
        - Downgrading known-dangerous BREAKING kinds to COMPATIBLE
          (e.g., func_removed → ignore).  These almost certainly mask real breaks.
        - Downgrading BREAKING to COMPATIBLE_WITH_RISK for critical kinds.
        """
        # Derive breaking kinds from the configured base policy so that
        # policy-specific sets (e.g. plugin_abi) are correctly flagged.
        base_breaking, _, _, _ = policy_kind_sets(self.base_policy)

        warnings: list[str] = []
        for kind, verdict in self.overrides.items():
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

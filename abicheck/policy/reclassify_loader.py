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

"""Loader for the ``reclassify:`` block of a policy document.

Moved out of ``policy_file.py`` (which carries a ``no_growth`` architecture
debt baseline) when P2 added the optional ``id:`` key: the validation is the
same responsibility as before, so it moves to its owner, the ``policy``
package, rather than growing the capped file. ``policy_file.py`` remains the
only caller and passes its own ``parse_severity_value`` in.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..errors import PolicyError
from ..model.change_catalog.registry import Verdict
from .reclassify import RECLASSIFY_KNOWN_KEYS, ReclassifyRule


def _parse_reclassify_expires(raw: Any, path: Path, index: int) -> date | None:
    """Parse one ``reclassify[i].expires`` value (ISO 8601 string, or a
    YAML-native date/datetime PyYAML already decoded).

    Checks ``datetime`` before ``date`` (Codex review): a ``datetime`` is
    itself a ``date`` subclass, so an unquoted YAML timestamp like
    ``2026-08-12T00:00:00`` -- which PyYAML decodes as a ``datetime``, not a
    ``date`` -- would otherwise pass the ``isinstance(raw, date)`` branch
    unnormalized and later crash ``Suppression.is_expired()``'s ``date.today()
    > self.expires`` comparison (``TypeError``: can't compare ``date`` and
    ``datetime``). Normalized to ``.date()`` here, mirroring
    ``policy/suppression/rules.py``'s ``_parse_expires`` handling of the identical case.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
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


#: reclassify[i] keys whose value must be a string when present -- every
#: selector plus the free-text `reason`/`label` fields. `kind`/`to`/`expires`
#: have their own dedicated, differently-shaped validation below and are
#: deliberately not in this set.
_RECLASSIFY_STRING_FIELDS: tuple[str, ...] = (
    "symbol",
    "symbol_pattern",
    "type_pattern",
    "member_name",
    "namespace",
    "entity_namespace",
    "cause_namespace",
    "source_location",
    "binding",
    "reason",
    "label",
)


def _require_reclassify_string_field(
    entry: dict[str, Any], key: str, index: int, path: Path
) -> str | None:
    """Validate one optional string-valued ``reclassify[i]`` field.

    Codex review: a non-string value here (e.g. a bare YAML ``42``) either
    crashes with an uncaught ``TypeError`` deep inside ``re.compile``/
    ``fnmatch.translate`` (``symbol_pattern``/``type_pattern``/every
    ``namespace`` variant) or loads silently and simply never matches
    (``symbol``, compared via ``==`` against the always-string
    ``change.symbol``) -- neither is the hard, up-front ``PolicyError`` this
    module's other selector validation already gives every other bad value.
    """
    val = entry.get(key)
    if val is None or isinstance(val, str):
        return val
    raise PolicyError(
        f"reclassify[{index}].{key} in {path}: must be a string, got "
        + type(val).__name__
    )


def parse_reclassify(
    raw: Any, path: Path, *, parse_severity: Callable[[Any], Verdict | None]
) -> list[ReclassifyRule]:
    """Validate and parse the ``reclassify:`` block (A: selector-scoped
    reclassification) into a list of :class:`ReclassifyRule`.

    Unlike ``overrides:`` (keyed by ``ChangeKind`` alone), each entry here
    carries the same selector grammar :mod:`abicheck.policy.suppression.rules` already
    implements, plus a required ``to:`` severity -- see
    ``abicheck/policy/reclassify.py``'s module docstring.

    *parse_severity* is ``policy_file.parse_severity_value``, passed in rather
    than imported: that module imports this one, so a static import here would
    close a cycle.

    P2: an optional ``id:`` names the rule for the audit. It must be a non-empty
    string and unique within the document; a repeated one is a hard error,
    because two rules sharing an id would be indistinguishable in every report.
    """
    if not isinstance(raw, list):
        raise PolicyError(
            "'reclassify' must be a YAML list of rules, got " + type(raw).__name__
        )

    rules: list[ReclassifyRule] = []
    seen_ids: dict[str, int] = {}
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
        to_verdict = parse_severity(to_raw)
        if to_verdict is None:
            raise PolicyError(
                f"reclassify[{i}] in {path}: invalid 'to' value {to_raw!r}. "
                "Valid values: break, warn, risk, ignore"
            )
        kind = entry.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise PolicyError(f"reclassify[{i}].kind in {path}: must be a string")
        string_fields = {
            key: _require_reclassify_string_field(entry, key, i, path)
            for key in _RECLASSIFY_STRING_FIELDS
        }
        user_id = _parse_rule_id(entry, i, path, seen_ids)

        try:
            rule = ReclassifyRule(
                to_verdict=to_verdict,
                to=str(to_raw),
                symbol=string_fields["symbol"],
                symbol_pattern=string_fields["symbol_pattern"],
                type_pattern=string_fields["type_pattern"],
                member_name=string_fields["member_name"],
                namespace=string_fields["namespace"],
                entity_namespace=string_fields["entity_namespace"],
                cause_namespace=string_fields["cause_namespace"],
                source_location=string_fields["source_location"],
                binding=string_fields["binding"],
                change_kind=kind,
                reason=string_fields["reason"],
                label=string_fields["label"],
                expires=_parse_reclassify_expires(entry.get("expires"), path, i),
                id=user_id,
            )
        except ValueError as e:
            raise PolicyError(f"reclassify[{i}] in {path}: {e}") from e
        rules.append(rule)
    return rules


def _parse_rule_id(
    entry: dict[str, Any], index: int, path: Path, seen: dict[str, int]
) -> str | None:
    """P2: validate one entry's optional ``id:`` and reject a repeat within *path*.

    *seen* maps each id already declared earlier in the same document to its
    entry index, so the error can name both occurrences.
    """
    if "id" not in entry or entry["id"] is None:
        return None
    raw = entry["id"]
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyError(
            f"reclassify[{index}].id in {path}: must be a non-empty string, got {raw!r}"
        )
    if raw in seen:
        raise PolicyError(
            f"reclassify[{index}].id in {path}: duplicate id {raw!r} "
            f"(already used by reclassify[{seen[raw]}]); ids must be unique per file"
        )
    seen[raw] = index
    return raw

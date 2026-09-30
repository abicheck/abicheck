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

"""ADR-062 D9 / storage-format-v2 A1.6: the ``.abicheck.yml``
``bundle_variants:`` block, as a typed, strictly validated model.

A *bundle variant* is one build configuration of the same project -- a
target triple, a compiler family, and a set of feature toggles -- that a
multi-variant capture (``abicheck project capture-variants``) records as its
own :class:`~abicheck.storage.package.VariantRef` inside one
``ProjectSnapshot`` package::

    bundle_variants:
      linux-x86_64-gcc:
        target_triple: x86_64-linux-gnu
        compiler_family: gcc
        feature_toggles: {simd: avx2, threads: true}
        required: true          # default
      linux-aarch64-clang:
        target_triple: aarch64-linux-gnu
        compiler_family: clang
        required: false

:meth:`BundleVariantSpec.declared` is the ``VariantRef.declared`` map,
knowable from config alone before any capture runs:
``{"target_triple": ..., "compiler_family": ..., **feature_toggles}``.
What the capture actually *observed* is a separate map
(``VariantRef.captured``), filled by ``workflows.bundle_variants_capture``;
the two are never merged, which is what lets a later comparison tell a
variant-boundary change apart from an ordinary version bump.

This is a deliberately new design, not a restoration of the deleted
``bundle_variants_config.py`` (removed in ADR-065 S1; see the plan's A1.6
entry). Validation is total and eager, matching ``ReleaseSelection.
from_dict``'s and ``PolicyFile``'s "hard load error, not warning-and-skip"
convention: every finding in the block is collected and reported at once.

A leaf ``model`` module (standard library only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = [
    "BUNDLE_VARIANTS_KEY",
    "DECLARED_COORDINATE_KEYS",
    "VARIANT_SPEC_KEYS",
    "BundleVariantSpec",
    "BundleVariantsConfig",
    "BundleVariantsConfigError",
    "bundle_variants_findings",
    "parse_bundle_variants",
]

#: The ``.abicheck.yml`` top-level key this module owns.
BUNDLE_VARIANTS_KEY = "bundle_variants"

#: The two fixed identity coordinates every variant declares. A feature
#: toggle may not reuse either name -- it would silently overwrite the
#: coordinate in the flat ``declared`` map.
DECLARED_COORDINATE_KEYS = ("target_triple", "compiler_family")

#: Every key a variant entry may carry.
VARIANT_SPEC_KEYS = frozenset(
    {"target_triple", "compiler_family", "feature_toggles", "required"}
)

# A variant name becomes a literal filename (`refs/variants/<name>.json`), so
# it is held to a conservative, portable grammar here -- a strict subset of
# what `storage.ref_ids.safe_ref_id` accepts, checked at config-load time
# rather than discovered after a long capture run.
_VARIANT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class BundleVariantsConfigError(ValueError):
    """A structurally invalid ``bundle_variants:`` block; the message lists
    every finding, ``"; "``-joined."""

    def __init__(self, findings: list[str]) -> None:
        self.findings = tuple(findings)
        super().__init__("; ".join(findings))


@dataclass(frozen=True)
class BundleVariantSpec:
    """One declared variant. ``feature_toggles`` values are canonical
    strings (a YAML ``true``/``false`` becomes ``"true"``/``"false"``, an
    integer its decimal spelling) because ``VariantRef.declared`` is a
    ``str -> str`` map."""

    name: str
    target_triple: str
    compiler_family: str
    feature_toggles: Mapping[str, str] = field(default_factory=dict)
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "feature_toggles",
            MappingProxyType(dict(sorted(self.feature_toggles.items()))),
        )

    def declared(self) -> dict[str, str]:
        """The ``VariantRef.declared`` map for this variant."""
        return {
            "target_triple": self.target_triple,
            "compiler_family": self.compiler_family,
            **self.feature_toggles,
        }


@dataclass(frozen=True)
class BundleVariantsConfig:
    """Every declared variant, in declaration order. Never empty."""

    variants: tuple[BundleVariantSpec, ...]

    def __post_init__(self) -> None:
        if not self.variants:
            raise BundleVariantsConfigError(
                ["bundle_variants must declare at least one variant"]
            )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variants)

    def get(self, name: str) -> BundleVariantSpec | None:
        return next((v for v in self.variants if v.name == name), None)

    @property
    def required_names(self) -> frozenset[str]:
        return frozenset(v.name for v in self.variants if v.required)


def _variant_name_findings(name: object) -> list[str]:
    if not isinstance(name, str):
        return [
            f"bundle_variants: variant name must be a string, got "
            f"{type(name).__name__}: {name!r}"
        ]
    if not _VARIANT_NAME_RE.fullmatch(name) or name.endswith("."):
        return [
            f"bundle_variants: invalid variant name {name!r} (use 1-100 "
            "characters from [A-Za-z0-9._-], starting with a letter or digit "
            "and not ending in '.')"
        ]
    if name.split(".", 1)[0].upper() in _RESERVED_STEMS:
        return [f"bundle_variants: variant name {name!r} is a reserved device name"]
    return []


def _toggle_value(value: object) -> str | None:
    """A toggle value's canonical spelling, or ``None`` if unsupported."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return None


def _entry_findings(name: str, entry: object) -> list[str]:
    where = f"bundle_variants.{name}"
    if not isinstance(entry, dict):
        return [f"{where} must be a mapping, got {type(entry).__name__}: {entry!r}"]
    findings = [
        f"unknown key {where}.{key!r} (expected one of "
        f"{', '.join(sorted(VARIANT_SPEC_KEYS))})"
        for key in entry
        if key not in VARIANT_SPEC_KEYS
    ]
    for key in DECLARED_COORDINATE_KEYS:
        if key not in entry:
            findings.append(f"{where}.{key} is required")
        elif not isinstance(entry[key], str) or not entry[key].strip():
            findings.append(
                f"{where}.{key} must be a non-empty string, got "
                f"{type(entry[key]).__name__}: {entry[key]!r}"
            )
    if "required" in entry and not isinstance(entry["required"], bool):
        findings.append(
            f"{where}.required must be a boolean, got "
            f"{type(entry['required']).__name__}: {entry['required']!r}"
        )
    toggles = entry.get("feature_toggles")
    if toggles is None:
        return findings
    if not isinstance(toggles, dict):
        return findings + [
            f"{where}.feature_toggles must be a mapping, got "
            f"{type(toggles).__name__}: {toggles!r}"
        ]
    for key, value in toggles.items():
        if not isinstance(key, str) or not key.strip():
            findings.append(
                f"{where}.feature_toggles: key must be a non-empty string, got {key!r}"
            )
        elif key in DECLARED_COORDINATE_KEYS:
            findings.append(
                f"{where}.feature_toggles.{key} collides with the fixed "
                f"'{key}' coordinate"
            )
        if _toggle_value(value) is None:
            findings.append(
                f"{where}.feature_toggles.{key} must be a non-empty string, "
                f"a boolean or an integer, got {type(value).__name__}: {value!r}"
            )
    return findings


def bundle_variants_findings(raw: object) -> list[str]:
    """Every structural problem in a raw ``bundle_variants:`` value (``[]``
    when it is valid). Shared by :func:`parse_bundle_variants` and the
    ``.abicheck.yml`` ingestion gate, so the two cannot disagree."""
    if not isinstance(raw, dict):
        return [
            f"bundle_variants must be a mapping of variant name -> spec, got "
            f"{type(raw).__name__}: {raw!r}"
        ]
    if not raw:
        return ["bundle_variants must declare at least one variant"]
    findings: list[str] = []
    folded: dict[str, str] = {}
    for name, entry in raw.items():
        name_findings = _variant_name_findings(name)
        findings += name_findings
        if name_findings:
            continue
        # Two names one case-insensitive filesystem treats as one ref file.
        other = folded.setdefault(name.casefold(), name)
        if other != name:
            findings.append(
                f"bundle_variants: variant names {other!r} and {name!r} differ "
                "only by case"
            )
        findings += _entry_findings(name, entry)
    return findings


def parse_bundle_variants(raw: Any) -> BundleVariantsConfig:
    """Parse a raw ``bundle_variants:`` value; raises
    :class:`BundleVariantsConfigError` naming every finding."""
    findings = bundle_variants_findings(raw)
    if findings:
        raise BundleVariantsConfigError(findings)
    variants = []
    for name, entry in raw.items():
        toggles = entry.get("feature_toggles") or {}
        variants.append(
            BundleVariantSpec(
                name=name,
                target_triple=entry["target_triple"].strip(),
                compiler_family=entry["compiler_family"].strip(),
                feature_toggles={
                    key: str(_toggle_value(value)) for key, value in toggles.items()
                },
                required=entry.get("required", True),
            )
        )
    return BundleVariantsConfig(variants=tuple(variants))

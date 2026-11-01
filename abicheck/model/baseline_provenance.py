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

"""Where the baseline operand came from, as the caller asserts it.

The root GitHub Action resolves its baseline itself (a legacy release asset,
a release-contract baseline-set archive, or a file path) before it calls
``compare``. It hands that record to the CLI through
``--set report.baseline_provenance=@FILE`` and the CLI copies it into the
compare report's (schema 6.8) and the release report's (schema 2.8) top-level
``baseline_provenance`` block, so a consumer reading the JSON report alone can
tell which baseline was checked.

The CLI does not verify any of these values: it did not fetch the asset or
read the archive manifest. The block therefore says ``asserted_by: "action"``.
It describes the whole baseline operand (``applies_to: "baseline_operand"``).
The schema reserves ``members``, keyed by member name, for per-member records
that ADR-065's acquisition record can fill later with values the CLI did
observe; nothing emits it yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "BASELINE_PROVENANCE_PATH",
    "BASELINE_PROVENANCE_SOURCES",
    "BaselineProvenance",
    "BaselineProvenanceError",
]

#: The ``.abicheck.yml`` / ``--set`` path that names the provenance file.
BASELINE_PROVENANCE_PATH = "report.baseline_provenance"

#: How the Action resolved its baseline (its ``baseline-source`` output).
BASELINE_PROVENANCE_SOURCES: tuple[str, ...] = (
    "legacy-asset",
    "baseline-set",
    "direct-path",
)

_STRING_FIELDS: tuple[str, ...] = ("asset_name", "project_ref", "profile")
_KNOWN_FIELDS: frozenset[str] = frozenset({"source", "generation", *_STRING_FIELDS})


class BaselineProvenanceError(ValueError):
    """The provenance document is not the shape :class:`BaselineProvenance` reads."""


@dataclass(frozen=True)
class BaselineProvenance:
    """One baseline operand's asserted provenance.

    ``source`` is one of :data:`BASELINE_PROVENANCE_SOURCES`. The other fields
    are ``None`` when the source has no such value (only ``baseline-set``
    carries ``project_ref``, ``generation`` and ``profile``).
    """

    source: str
    asset_name: str | None = None
    project_ref: str | None = None
    generation: int | None = None
    profile: str | None = None

    @classmethod
    def from_mapping(cls, data: object) -> BaselineProvenance:
        """Read one provenance document; :class:`BaselineProvenanceError` if malformed.

        Unknown keys are refused, not dropped, so a misspelled field never
        reads as an absent one. An empty string is read as absent (``None``),
        since that is how the Action spells an output it did not set.
        """
        if not isinstance(data, Mapping):
            raise BaselineProvenanceError(
                f"must be a JSON object, got {type(data).__name__}"
            )
        unknown = sorted(str(k) for k in data if k not in _KNOWN_FIELDS)
        if unknown:
            raise BaselineProvenanceError(
                f"unknown key(s) {', '.join(unknown)}; expected "
                f"{', '.join(sorted(_KNOWN_FIELDS))}"
            )
        source = data.get("source")
        if source not in BASELINE_PROVENANCE_SOURCES:
            raise BaselineProvenanceError(
                f"source must be one of {', '.join(BASELINE_PROVENANCE_SOURCES)}, "
                f"got {source!r}"
            )
        strings: dict[str, str | None] = {}
        for name in _STRING_FIELDS:
            value = data.get(name)
            if value is not None and not isinstance(value, str):
                raise BaselineProvenanceError(
                    f"{name} must be a string or null, got {type(value).__name__}"
                )
            strings[name] = value or None
        generation = data.get("generation")
        if generation == "":
            generation = None
        if generation is not None and (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise BaselineProvenanceError(
                f"generation must be a non-negative integer or null, got {generation!r}"
            )
        return cls(
            source=str(source),
            asset_name=strings["asset_name"],
            project_ref=strings["project_ref"],
            generation=generation,
            profile=strings["profile"],
        )

    def to_report_dict(self) -> dict[str, object]:
        """The report's ``baseline_provenance`` block; every key is always present."""
        return {
            "asserted_by": "action",
            "applies_to": "baseline_operand",
            "source": self.source,
            "asset_name": self.asset_name,
            "project_ref": self.project_ref,
            "generation": self.generation,
            "profile": self.profile,
        }

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

"""ADR-063 Track 4 (8B), second slice: the `"graph"` D8 legacy section's own
typed DTO -- the second section beyond `semantic_ir` to be promoted off
`storage.dto.legacy_section_to_dto`'s generic "pass the payload through
verbatim" envelope, chosen next by `types_section_codec.py`'s own stated
heuristic ("the section this lands for first" is the one with no
field-presence ambiguity for a typed wrapper to get wrong).

**Why `"graph"` is the next section, by that same heuristic.**
`storage.legacy_sections._SECTION_FIELDS["graph"]` is exactly one field
(`("surface_graph",)`) -- structurally identical to `"types"`'s own
one-field shape. `split_legacy_document` only ever creates a `"graph"`
section entry when the `surface_graph` key is actually present in the
source document (a section with none of its fields present is omitted
entirely, per that function's own docstring), and `"graph"` has no *other*
field it could instead carry -- so whenever a `"graph"` section is present
at all, its payload is unconditionally `{"surface_graph": <value>}`. This
holds even though `storage.legacy_sections._REQUIRED_SECTION_FIELDS["graph"]`
is an empty `frozenset()`, not `{"surface_graph"}` (unlike `"types"`, whose
required set names its own single field directly): that table is derived
empirically from `tests/fixtures/schema/v1.json` (`_REQUIRED_SECTION_FIELDS`'s
own docstring), and `surface_graph` was introduced well after schema v1
(ADR-063 Phase 3 D5, schema v29) -- so it is correctly absent from a
v1-derived "always present" set, without that meaning the field is
genuinely optional *within* a `"graph"` section that exists at all. The two
facts combine to the same guarantee `"types"` already relies on: a present
`"graph"` section's payload has exactly one possible shape.

**What "typed" means here, precisely** -- identical boundary to
`types_section_codec.py`'s own: the *field* is what gets a real wrapper,
not that field's internal shape. `surface_graph`'s own value is already
`storage.surface_graph_codec.encode_surface_graph`'s output (in turn
`SourceGraphSummary.to_dict()`) by the time `split_legacy_document` ever
sees it -- decoding that structure into a typed `SourceGraphSummary` here
would require importing `model.source_graph`, which is legitimate per
`storage/AGENTS.md`'s "may depend on model" rule, but is deliberately not
attempted in this slice for the same reason `types_section_codec.py`
leaves `RecordType`/`EnumType` entries undecoded: it is real,
separately-scoped future work, not this slice's stated goal (a versioned
wrapper *around* the field, not a second decoder for its contents).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .canonical import canonical_form

__all__ = ["GraphSection"]


def _freeze(value: Any) -> Any:
    """Mirrors `types_section_codec._freeze` (in turn `storage.dto._freeze`)
    exactly, for the identical reason: a `frozen=True` dataclass whose one
    field is a plain `dict`/list tree is not actually immutable unless every
    reachable container is frozen too."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _unfreeze(value: Any) -> Any:
    """The inverse of `_freeze` — a fresh, ordinary, mutable `dict`/`list`
    tree, detached from this DTO's own frozen storage. Mirrors
    `types_section_codec._unfreeze` exactly."""
    if isinstance(value, MappingProxyType):
        return {key: _unfreeze(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_unfreeze(item) for item in value]
    return value


@dataclass(frozen=True)
class GraphSection:
    """The `"graph"` D8 legacy section's one field, typed.

    `surface_graph` holds the already-serialized `AbiSnapshot.surface_graph`
    value verbatim (a plain JSON dict, `SourceGraphSummary.to_dict()`'s own
    shape) -- see this module's own docstring for why decoding that internal
    shape further is out of scope here. `__post_init__` runs it through
    `canonical_form` + `_freeze` (identical two-step to `TypesSection`'s own
    `__post_init__`), so nothing reachable from a constructed `GraphSection`
    ever aliases a caller's own mutable objects; `to_document()` deep-thaws
    it back to ordinary `dict`/`list` for JSON-shaped storage.
    """

    surface_graph: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "surface_graph", _freeze(canonical_form(dict(self.surface_graph)))
        )

    def to_document(self) -> dict[str, Any]:
        """The `{"surface_graph": {...}}` payload shape `storage.dto
        .graph_to_dto` stores -- the exact section-payload shape
        `storage.legacy_sections.split_legacy_document` already produces for
        this section, so a round trip through this wrapper changes nothing
        about the stored bytes."""
        return {"surface_graph": _unfreeze(self.surface_graph)}

    @classmethod
    def from_document(cls, payload: Mapping[str, Any]) -> GraphSection:
        """The inverse of `to_document` — *payload* is a `"graph"` section's
        own payload mapping (already validated, by the caller, to carry only
        this section's own allowlisted key).

        Raises `ValueError` if `surface_graph` is missing or is not a
        mapping -- the same "a section whose object hashes and decodes fine
        can still have lost content within its own JSON" defect
        `storage.import_v1.export_legacy_snapshot`'s own
        `missing_required_section_fields` check exists to catch for every
        other legacy section, made structural here instead of a separate
        post-hoc check (mirrors `TypesSection.from_document` exactly).
        """
        if not isinstance(payload, Mapping):
            raise ValueError(
                f"a 'graph' section payload must be a mapping, not "
                f"{type(payload).__name__}"
            )
        raw = payload.get("surface_graph")
        if not isinstance(raw, Mapping):
            raise ValueError(
                "a 'graph' section payload must carry a 'surface_graph' "
                f"mapping -- got {raw!r}"
            )
        extra = set(payload) - {"surface_graph"}
        if extra:
            raise ValueError(
                "a 'graph' section payload may only carry 'surface_graph', "
                f"not {sorted(extra)}"
            )
        # `__post_init__` freezes this, so the constructor's own dict(...)
        # here need not defend against aliasing itself.
        return cls(surface_graph=dict(raw))

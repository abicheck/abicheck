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

"""ADR-062 D19: the declared catalog of optional extension facts.

A new optional fact -- one whose absence an older reader already reads as
"not collected" -- is stored as a named entry of its section header's D14
``extensions`` map, not as a new top-level field behind a global
``SCHEMA_VERSION`` bump. Each such fact is declared here once, as an
:class:`ExtensionFact`, with the semantics an *older* reader gets when it
meets the fact without knowing it (:class:`ReaderSemantics`):

* ``OPTIONAL`` -- the older reader keeps the value verbatim and reads the
  fact as not collected. Its result stays right, only less informed.
* ``SECTION_MUST_UNDERSTAND`` -- ignoring the value would make an older
  reader's result *wrong* for the analyses reading the fact's section, so
  the writer also names the fact in that section header's
  ``must_understand`` list: the older reader blocks exactly that section's
  readers (``not comparable``) and compares everything else.
* ``DOCUMENT_MUST_UNDERSTAND`` -- ignoring it would make every analysis
  wrong, so the name goes on the document-level ``must_understand`` list
  and the older reader refuses the document.

``must_understand_when`` narrows a must-understand fact to the values that
need it (ADR-079's ``complete`` export read needs no understanding; a
``partial`` one does), so the common case costs an older reader nothing.

This build *understands* exactly the names in :data:`EXTENSION_FACTS`
(`wire_extensions.UNDERSTOOD_EXTENSIONS` is derived from it). The catalog
is a static tuple in this module, never filled by registration calls from
elsewhere: what a reader understands must not depend on import order.

Writing and reading a fact on a snapshot is `extension_facts.py`. The
global-bump side of the rule is `snapshot_schema_versions.SCHEMA_BUMP_REASONS`,
which `scripts/schema_bump_gate.py` checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

from ..model.vocabulary import ScopeOrigin
from .dto import SECTION_SCHEMA_VERSIONS

__all__ = [
    "BINARY_SHA256",
    "CU_STACK_PROTECTOR",
    "CXX_CONCEPT_CONSTRAINTS",
    "CXX_TEMPLATE_DEFAULTS",
    "CXX_CONCEPT_HEADER_SCAN",
    "EXPORT_READ",
    "EXTENSION_FACTS",
    "NOEXCEPT_UNOBSERVED",
    "STD_REEXPORTS",
    "TYPEDEF_ORIGINS",
    "VOLATILE_CAPTURE_FACTS",
    "evidence_header_extras",
    "ExtensionFact",
    "ExtensionFactRegistry",
    "ReaderSemantics",
]

T = TypeVar("T")

#: ``<namespace>.<name>``: lowercase dotted, at least one dot. A section kind
#: never contains a dot, so a fact name can never be read as one -- the two
#: share the ``must_understand`` namespace (D14).
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_OWNER = re.compile(r"^ADR-\d{3}\b")
_IDENTITY_SECTION = "provenance"


class ReaderSemantics(StrEnum):
    """What a reader that does not know the fact does when it meets it."""

    OPTIONAL = "optional"
    SECTION_MUST_UNDERSTAND = "section_must_understand"
    DOCUMENT_MUST_UNDERSTAND = "document_must_understand"


@dataclass(frozen=True, eq=False)
class ExtensionFact(Generic[T]):
    """One optional fact stored in its section header's ``extensions`` map.

    *encode* turns the runtime value into canonical JSON (D5); *decode* is
    its inverse and raises ``TypeError``/``ValueError`` on a malformed
    stored value. *absent_reads_as* states, for a reviewer, what a reader
    concludes when the fact is missing -- the claim that makes the fact
    extension-eligible. *owner* names the deciding ADR.
    """

    name: str
    section: str
    semantics: ReaderSemantics
    absent_reads_as: str
    owner: str
    encode: Callable[[T], Any]
    decode: Callable[[Any], T]
    must_understand_when: Callable[[T], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.match(self.name):
            raise ValueError(
                f"extension fact name {self.name!r} must be a lowercase dotted "
                "'<namespace>.<name>' (it shares must_understand with section kinds)"
            )
        if (
            not isinstance(self.section, str)
            or self.section not in SECTION_SCHEMA_VERSIONS
        ):
            raise ValueError(
                f"extension fact {self.name!r}: unknown section {self.section!r}"
            )
        if not isinstance(self.semantics, ReaderSemantics):
            raise TypeError(
                f"extension fact {self.name!r}: semantics must be a ReaderSemantics"
            )
        if (
            self.semantics is ReaderSemantics.SECTION_MUST_UNDERSTAND
            and self.section == _IDENTITY_SECTION
        ):
            raise ValueError(
                f"extension fact {self.name!r}: blocking the identity section "
                "refuses the document; declare DOCUMENT_MUST_UNDERSTAND instead"
            )
        if (
            not isinstance(self.absent_reads_as, str)
            or not self.absent_reads_as.strip()
        ):
            raise ValueError(
                f"extension fact {self.name!r} must state what its absence reads as"
            )
        if not isinstance(self.owner, str) or not _OWNER.match(self.owner):
            raise ValueError(
                f"extension fact {self.name!r}: owner {self.owner!r} must name an ADR"
            )
        if not callable(self.encode) or not callable(self.decode):
            raise TypeError(
                f"extension fact {self.name!r}: encode/decode must be callable"
            )
        if self.must_understand_when is not None:
            if self.semantics is ReaderSemantics.OPTIONAL:
                raise ValueError(
                    f"extension fact {self.name!r}: must_understand_when needs "
                    "a must-understand semantics"
                )
            if not callable(self.must_understand_when):
                raise TypeError(
                    f"extension fact {self.name!r}: must_understand_when must be callable"
                )

    def needs_understanding(self, value: T) -> bool:
        """Whether writing *value* must name this fact in ``must_understand``."""
        if self.semantics is ReaderSemantics.OPTIONAL:
            return False
        if self.must_understand_when is None:
            return True
        decision = self.must_understand_when(value)
        if not isinstance(decision, bool):
            raise TypeError(
                f"extension fact {self.name!r}: must_understand_when returned "
                f"{type(decision).__name__}, not bool"
            )
        return decision


class ExtensionFactRegistry:
    """An immutable set of :class:`ExtensionFact` declarations keyed by name."""

    __slots__ = ("_facts",)

    def __init__(self, facts: Iterable[ExtensionFact[Any]] = ()) -> None:
        if isinstance(facts, (str, bytes)) or not isinstance(facts, Iterable):
            raise TypeError("an extension fact registry takes an iterable of facts")
        table: dict[str, ExtensionFact[Any]] = {}
        for fact in facts:
            if not isinstance(fact, ExtensionFact):
                raise TypeError(f"not an ExtensionFact: {fact!r}")
            if fact.name in table:
                raise ValueError(f"extension fact {fact.name!r} is declared twice")
            table[fact.name] = fact
        self._facts = table

    def __contains__(self, fact: object) -> bool:
        return isinstance(fact, ExtensionFact) and self._facts.get(fact.name) is fact

    def __iter__(self) -> Iterator[ExtensionFact[Any]]:
        return iter(self._facts[name] for name in sorted(self._facts))

    def __len__(self) -> int:
        return len(self._facts)

    def names(self) -> frozenset[str]:
        return frozenset(self._facts)

    def extended(self, *facts: ExtensionFact[Any]) -> ExtensionFactRegistry:
        """A registry holding this one's facts plus *facts* (a newer build's)."""
        return ExtensionFactRegistry((*self._facts.values(), *facts))


#: The platform blocks ``export_read`` covers, and the states it may record
#: for one (``complete`` is never written: an unlisted platform is complete).
_EXPORT_READ_PLATFORMS = frozenset({"elf", "pe", "macho"})
_EXPORT_READ_LISTED_STATES = frozenset({"partial", "failed", "unrecorded"})
_EXPORT_READ_GAP_STATES = frozenset({"partial", "failed"})


def _export_read_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError(
            f"export_read must map a platform to its read state, got "
            f"{type(value).__name__}"
        )
    out: dict[str, str] = {}
    for platform, state in value.items():
        if platform not in _EXPORT_READ_PLATFORMS:
            raise ValueError(f"export_read names unknown platform {platform!r}")
        if state not in _EXPORT_READ_LISTED_STATES:
            raise ValueError(
                f"export_read {platform!r} state {state!r} is not "
                f"one of {sorted(_EXPORT_READ_LISTED_STATES)}"
            )
        out[platform] = state
    return {k: out[k] for k in sorted(out)}


#: ADR-079 (amended by ADR-062 D19): how completely each platform block's
#: export table was read (``model/export_read.ExportReadState``). Written on
#: the ``binary`` section whenever a block records its read, as
#: ``{platform: state}`` for every block that is *not* complete (``{}``: all
#: complete). Absent, nothing was recorded (a writer that predates the fact,
#: or a hand-built block): the blocks read ``unrecorded``, never complete. A
#: ``partial``/``failed`` value is must-understand for ``binary``: a reader
#: that ignored it would read a cut-short table as complete.
EXPORT_READ: ExtensionFact[dict[str, str]] = ExtensionFact(
    name="abicheck.export_read",
    section="binary",
    semantics=ReaderSemantics.SECTION_MUST_UNDERSTAND,
    absent_reads_as=(
        "unrecorded: no record of the export-table read; the older rule "
        "(a block is read when it holds an export or its header was parsed) "
        "decides, never complete"
    ),
    owner="ADR-079",
    encode=_export_read_map,
    decode=_export_read_map,
    must_understand_when=lambda value: any(
        state in _EXPORT_READ_GAP_STATES for state in value.values()
    ),
)


def _strict_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"expected a bool, got {type(value).__name__}")
    return value


#: Whether a castxml or clang run's public headers define a C++20 concept or
#: spell a requires-clause, which neither header backend models
#: (``AbiSnapshot.cxx_concept_header_scan``, extractor generation 41). Written
#: on the ``debug`` section whenever a scan ran. Absent, no scan ran and no
#: concept gap is named -- the answer every older snapshot already gives. An
#: older reader that ignores it reads the run as it always did (the verdict is
#: unaffected; only the coverage note is missing), so it is optional.
CXX_CONCEPT_HEADER_SCAN: ExtensionFact[bool] = ExtensionFact(
    name="abicheck.cxx_concept_header_scan",
    section="debug",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="no scan ran: no C++20 concept gap is named",
    owner="ADR-062",
    encode=_strict_bool,
    decode=_strict_bool,
)


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise TypeError("expected a {str: str} map")
    return dict(sorted(value.items()))


#: The public headers' C++20 concepts and template constraints the clang
#: header backend modelled (``AbiSnapshot.cxx_concept_constraints``, extractor
#: generation 42; keys and encoding in ``model/concept_constraint.py``).
#: Written on the ``debug`` section whenever a run modelled them, the empty
#: map included. Absent, nothing was modelled: no constraint change can be
#: assessed and the concept scan's coverage gap stays named. An older reader
#: that ignores it keeps that gap and reports no concept change -- what it
#: always did -- so it is optional.
CXX_CONCEPT_CONSTRAINTS: ExtensionFact[dict[str, str]] = ExtensionFact(
    name="abicheck.cxx_concept_constraints",
    section="debug",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="not modelled: no concept change is assessed, the gap is named",
    owner="ADR-062",
    encode=_str_map,
    decode=_str_map,
)


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise TypeError("expected a list of str")
    return sorted(set(value))


#: The functions whose ``noexcept`` exception specification the producer did
#: not observe (``Function.is_noexcept_fact`` ``NOT_COLLECTED``, extractor
#: generation 46): a castxml run whose clang join could not read a header that
#: spells one. Their mangled names, written on the ``declarations`` section
#: when there is at least one. Absent, every stored ``is_noexcept`` was
#: observed. An older reader that ignores it reads those functions as not
#: ``noexcept`` -- what a castxml snapshot always stored -- so it is optional.
NOEXCEPT_UNOBSERVED: ExtensionFact[list[str]] = ExtensionFact(
    name="abicheck.noexcept_unobserved",
    section="declarations",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="every stored noexcept specification was observed",
    owner="ADR-062",
    encode=_str_list,
    decode=_str_list,
)


def _template_default_map(value: Any) -> dict[str, list[str | None] | None]:
    """A ``{key: [default | null, ...] | null}`` map (each template's spelled
    defaults, ``null`` when not known; ``model/template_default_table.py``)."""
    if not isinstance(value, dict):
        raise TypeError("expected a {str: list | null} map")
    out: dict[str, list[str | None] | None] = {}
    for key in sorted(value):
        row = value[key]
        if not isinstance(key, str):
            raise TypeError("expected a str key")
        if row is None:
            out[key] = None
            continue
        if not isinstance(row, (list, tuple)) or not all(
            d is None or isinstance(d, str) for d in row
        ):
            raise TypeError("expected a list of str or null")
        out[key] = list(row)
    return out


#: The public headers' template-parameter defaults (ADR-085 D1) of every
#: class, function, alias and variable template, read from the clang AST by
#: both header backends (``AbiSnapshot.cxx_template_defaults``, extractor
#: generation 47). Written on the ``debug`` section whenever a run modelled
#: them, the empty map included. Absent, nothing was modelled: the
#: header-scan gap stays named and no default change is assessed from the
#: table -- what an older reader that ignores it also does -- so it is
#: optional.
CXX_TEMPLATE_DEFAULTS: ExtensionFact[dict[str, list[str | None] | None]] = (
    ExtensionFact(
        name="abicheck.cxx_template_defaults",
        section="debug",
        semantics=ReaderSemantics.OPTIONAL,
        absent_reads_as=(
            "not modelled: no default change is assessed from it, the gap is named"
        ),
        owner="ADR-085",
        encode=_template_default_map,
        decode=_template_default_map,
    )
)

_STACK_PROTECTOR_LEVELS = frozenset(
    {"none", "explicit", "basic", "strong", "all", "unrecorded"}
)


def _stack_protector_census(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        raise TypeError("expected a non-empty {level: units} map")
    for level, units in value.items():
        if level not in _STACK_PROTECTOR_LEVELS:
            raise ValueError(f"unknown stack-protector level {level!r}")
        if isinstance(units, bool) or not isinstance(units, int) or units < 1:
            raise ValueError(f"stack-protector level {level!r}: bad count {units!r}")
    return dict(sorted(value.items()))


#: Every compiled unit's stack-protector setting from its ``DW_AT_producer``
#: (``AbiSnapshot.cu_stack_protector``, ``model/stack_protector.py``,
#: extractor generation 49). Written on the ``debug`` section whenever DWARF
#: units were read. Absent, no unit was read: a lost ``__stack_chk_fail``
#: keeps its RISK with the cause unestablished -- the answer every older
#: snapshot gives. An older reader that ignores it reports that same RISK,
#: so it is optional.
CU_STACK_PROTECTOR: ExtensionFact[dict[str, int]] = ExtensionFact(
    name="abicheck.cu_stack_protector",
    section="debug",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="no unit read: a lost stack canary's cause is unknown",
    owner="ADR-062",
    encode=_stack_protector_census,
    decode=_stack_protector_census,
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _sha256_hex(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.match(value):
        raise ValueError(
            f"binary_sha256 must be 64 lowercase hex digits, got {value!r}"
        )
    return value


#: The SHA-256 of the binary's bytes a snapshot was dumped from
#: (``AbiSnapshot.binary_sha256``, extractor generation 51), read through a
#: GNU ld linker-script chain to its final target like a live operand's hash.
#: Written on the ``binary`` section by every dump of a binary file. It lets
#: ADR-090 D1(c) prove a stored dump and a live binary (or two stored dumps)
#: one input. Absent, the identity is unproven: a stored-vs-live pair without
#: type-level evidence reads UNCONFIRMED, the conservative answer every older
#: snapshot already gives. An older reader that ignores it gives that same
#: answer, so it is optional.
BINARY_SHA256: ExtensionFact[str] = ExtensionFact(
    name="abicheck.binary_sha256",
    section="binary",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as=(
        "not recorded: the dumped binary's identity is unknown, so no "
        "identical-input exemption is proven from it"
    ),
    owner="ADR-090",
    encode=_sha256_hex,
    decode=_sha256_hex,
)

_TYPEDEF_ORIGINS = frozenset(
    o.value for o in ScopeOrigin if o is not ScopeOrigin.UNKNOWN
)


def _typedef_origin_map(value: Any) -> dict[str, str]:
    """A ``{typedef key: origin}`` map; ``unknown`` is never stored (an
    absent key already reads as unknown)."""
    if not isinstance(value, dict):
        raise TypeError("expected a {typedef key: origin} map")
    for key, origin in value.items():
        if not isinstance(key, str) or not key:
            raise TypeError(f"typedef_origins key {key!r} must be a non-empty str")
        if origin not in _TYPEDEF_ORIGINS:
            raise ValueError(f"typedef_origins {key!r}: unknown origin {origin!r}")
    return {k: str(value[k]) for k in sorted(value)}


#: Each typedef's ADR-015 header origin (``AbiSnapshot.typedef_origins``,
#: extractor generation 62), classified by ``apply_provenance`` from the
#: header backend's declaring header -- the provenance functions, records and
#: variables already carry. Written on the ``declarations`` section when at
#: least one typedef was classified. Absent (DWARF, no public-header set, an
#: older snapshot), every typedef's origin is unknown: never private, so
#: scoping keeps its findings with the ``no-provenance`` disclosure. An older
#: reader that ignores it gives that same answer, so it is optional.
TYPEDEF_ORIGINS: ExtensionFact[dict[str, str]] = ExtensionFact(
    name="abicheck.typedef_origins",
    section="declarations",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="every typedef's header origin is unknown, never private",
    owner="ADR-015",
    encode=_typedef_origin_map,
    decode=_typedef_origin_map,
)


def _std_reexport_map(value: Any) -> dict[str, str]:
    """A ``{declared name: std:: target}`` map of namespace-scope
    using-declarations; both sides qualified, the target in ``std::``."""
    if not isinstance(value, dict):
        raise TypeError("expected a {declared name: std:: target} map")
    for key, target in value.items():
        if not isinstance(key, str) or "::" not in key or key.startswith("std::"):
            raise ValueError(
                f"std_reexports key {key!r} must be a non-std qualified name"
            )
        if not isinstance(target, str) or not target.startswith("std::"):
            raise ValueError(
                f"std_reexports {key!r}: target {target!r} is not in std::"
            )
    return {k: str(value[k]) for k in sorted(value)}


#: The public headers' namespace-scope ``using std::X;`` re-exports
#: (``AbiSnapshot.std_reexports``, extractor generation 64): each declared
#: library name and the ``std::`` entity it names. Written on the
#: ``declarations`` section whenever a header backend modelled them -- an
#: empty map included, which says "none declared", unlike an absent fact.
#: Absent (DWARF, no public-header set, an older snapshot) the re-exports are
#: not modelled, so no re-export can be reported removed; an older reader
#: that ignores it gives that same answer, so it is optional.
STD_REEXPORTS: ExtensionFact[dict[str, str]] = ExtensionFact(
    name="abicheck.std_reexports",
    section="declarations",
    semantics=ReaderSemantics.OPTIONAL,
    absent_reads_as="std:: re-exports not modelled: none can be reported removed",
    owner="ADR-084",
    encode=_std_reexport_map,
    decode=_std_reexport_map,
)

#: Facts that record the capture, not the evidence: like ``build_id`` among
#: ``evidence_digest.VOLATILE_CAPTURE_FIELDS``, they stay out of the ADR-062
#: D16 evidence digest, so two dumps whose extracted facts agree keep one
#: evidence identity whatever bytes they were read from.
VOLATILE_CAPTURE_FACTS = frozenset({BINARY_SHA256.name})


def evidence_header_extras(
    header_extras: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Section header extras without :data:`VOLATILE_CAPTURE_FACTS`: what the
    evidence digest hashes. The writer still persists them; a section left
    with nothing is dropped, so a capture-only fact alone changes nothing."""
    out: dict[str, dict[str, Any]] = {}
    for section, extras in header_extras.items():
        kept = dict(extras)
        facts = kept.get("extensions")
        if isinstance(facts, Mapping):
            facts = {k: v for k, v in facts.items() if k not in VOLATILE_CAPTURE_FACTS}
            if facts:
                kept["extensions"] = facts
            else:
                del kept["extensions"]
        if kept:
            out[section] = kept
    return out


#: This build's optional extension facts (ADR-062 D19). Add a fact by
#: declaring it as a module constant above and listing it here.
EXTENSION_FACTS = ExtensionFactRegistry(
    (
        BINARY_SHA256,
        CU_STACK_PROTECTOR,
        CXX_CONCEPT_HEADER_SCAN,
        CXX_CONCEPT_CONSTRAINTS,
        CXX_TEMPLATE_DEFAULTS,
        EXPORT_READ,
        NOEXCEPT_UNOBSERVED,
        STD_REEXPORTS,
        TYPEDEF_ORIGINS,
    )
)

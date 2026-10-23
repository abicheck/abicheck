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

"""The instruction-set target a build was compiled for, as a value.

A library built with ``-mavx2`` and one built with ``-mavx512f`` can differ in
struct layout, mangled names and inlined code, and the second one may not run
at all on a CPU the first one ran on. Those are *consequences*; the root is
the compiled target. :class:`TargetIsa` is that root as data.

``features`` is the set of ISA features a CPU must provide to run the code
(GCC ``-m`` spellings, e.g. ``avx2``), closed under GCC's implication rules
(``-mavx2`` also requires ``avx`` and ``sse4.2``) so two builds that spell
the same requirement differently compare equal. ``arch`` is the ``-march=``
(or ``-mcpu=``) value as recorded, kept for display and for the named-CPU
case where the feature set cannot say more.
"""

from __future__ import annotations

from dataclasses import dataclass

from .execution_cache import memoized

__all__ = [
    "LEVEL_FEATURES",
    "TargetIsa",
    "feature_closure",
    "feature_disable",
    "level_of",
    "parse_note_target_isa",
    "parse_producer_target_isa",
    "target_isa_from_sources",
]

#: Features each x86-64 psABI micro-architecture level requires, keyed by the
#: level token. ``x86-64`` is the baseline and requires no extension beyond
#: SSE2. These sets are the GCC ``-m`` spellings of the psABI lists; the level
#: itself is what a CPU is checked against at load time.
_V2 = frozenset({"sse3", "ssse3", "sse4.1", "sse4.2", "popcnt", "cx16"})
_V3 = _V2 | {"avx", "avx2", "bmi", "bmi2", "f16c", "fma", "lzcnt", "movbe", "xsave"}
_V4 = _V3 | {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
LEVEL_FEATURES: dict[str, frozenset[str]] = {
    "x86-64": frozenset(),
    "x86-64-v2": _V2,
    "x86-64-v3": _V3,
    "x86-64-v4": _V4,
}

#: ``feature -> the features its -m flag turns on besides itself`` (GCC's
#: direct implications). Only the x86 SIMD chain is listed: the features a
#: real build's ``-m`` flags spell that CPUs can lack.
_IMPLIES: dict[str, frozenset[str]] = {
    "avx512f": frozenset({"avx2"}),
    "avx512bw": frozenset({"avx512f"}),
    "avx512cd": frozenset({"avx512f"}),
    "avx512dq": frozenset({"avx512f"}),
    "avx512vl": frozenset({"avx512f"}),
    "avx2": frozenset({"avx"}),
    "avx": frozenset({"sse4.2"}),
    "sse4.2": frozenset({"sse4.1"}),
    "sse4.1": frozenset({"ssse3"}),
    "ssse3": frozenset({"sse3"}),
    "sse3": frozenset({"sse2"}),
}

#: Every feature name the target parser recognises as an ISA feature.
ISA_FEATURES: frozenset[str] = (
    frozenset(_IMPLIES)
    | frozenset().union(*_IMPLIES.values())
    | _V4
    | {"sse2", "fma4", "aes", "pclmul", "sha", "vaes", "avx512vnni", "avx512bf16"}
    | {"avx512fp16", "avxvnni", "xsavec", "xsaveopt"}
)


def feature_closure(features: frozenset[str] | set[str]) -> frozenset[str]:
    """*features* plus everything they imply, transitively."""
    out = set(features)
    pending = list(out)
    while pending:
        for implied in _IMPLIES.get(pending.pop(), frozenset()):
            if implied not in out:
                out.add(implied)
                pending.append(implied)
    return frozenset(out)


def feature_disable(features: frozenset[str], name: str) -> frozenset[str]:
    """*features* with *name* removed and every feature that implies it, as a
    ``-mno-<name>`` flag does (``-mno-avx`` also drops ``avx2``)."""
    return frozenset(
        f for f in features if f != name and name not in feature_closure({f})
    )


def level_of(features: frozenset[str]) -> str:
    """The strongest psABI level whose requirements *features* satisfies
    (``x86-64`` when none does)."""
    best = "x86-64"
    for level, required in LEVEL_FEATURES.items():
        if required <= features and len(required) >= len(LEVEL_FEATURES[best]):
            best = level
    return best


@dataclass(frozen=True)
class TargetIsa:
    """The ISA a build requires: its recorded ``-march=`` value and the
    feature set a CPU must provide. ``arch`` is ``None`` when only a note
    (no producer string) carried the requirement."""

    arch: str | None
    features: frozenset[str]

    def describe(self) -> str:
        """Short human form: the recorded arch (else the level the features
        satisfy), followed by the top-level extensions beyond that level, e.g.
        ``x86-64 (+avx2)`` for ``-mavx2``."""
        base = self.arch if self.arch in LEVEL_FEATURES else level_of(self.features)
        top = _top_features(self.features) - LEVEL_FEATURES[base]
        head = self.arch if self.arch is not None else base
        if not top:
            return head
        return f"{head} (+{', '.join(sorted(top))})"


def _top_features(features: frozenset[str]) -> frozenset[str]:
    """Features in *features* that no other member of it implies: ``avx2``
    (implied by ``avx512f``) is dropped, ``avx512f`` kept."""
    return frozenset(
        f
        for f in features
        if not any(f in feature_closure({g}) - {g} for g in features if g != f)
    )


_ARCH_FLAGS = ("-march=", "-mcpu=")
#: The note's level tokens (``elf_notes``) and the psABI level each names.
_NOTE_LEVEL_TOKENS: dict[str, str] = {
    "x86-64-baseline": "x86-64",
    "x86-64-v2": "x86-64-v2",
    "x86-64-v3": "x86-64-v3",
    "x86-64-v4": "x86-64-v4",
}


@memoized(maxsize=4096)
def parse_producer_target_isa(producer: str | None) -> TargetIsa | None:
    """The target a ``DW_AT_producer`` string records, or ``None`` when it records
    no ``-march=``/``-mcpu=`` and no ISA feature flag (clang, or a stripped
    producer): the target is then unknown, not baseline.

    Flags are applied in order, so ``-mavx2 -mno-avx`` ends with ``avx2`` off,
    as the compiler does. ``-mtune=``, ``-mabi=``, ``-mfpmath=`` and the other
    non-ISA options are ignored.
    """
    if not producer:
        return None
    toks = producer.split()
    arch: str | None = None
    for tok in toks:
        for prefix in _ARCH_FLAGS:
            if tok.startswith(prefix):
                arch = tok[len(prefix) :]
    # The arch sets the base requirement; -m flags then apply in order on top
    # of it, so `-march=x86-64-v2 -mno-sse4.2` drops sse4.2 from the level.
    features = LEVEL_FEATURES.get(arch or "", frozenset())
    seen = arch is not None
    for tok in toks:
        if not tok.startswith("-m") or tok.startswith(_ARCH_FLAGS):
            continue
        name, enable = tok[2:], True
        if name.startswith("no-"):
            name, enable = name[3:], False
        if name not in ISA_FEATURES:
            continue
        seen = True
        if enable:
            features = feature_closure(features | {name})
        else:
            features = feature_disable(features, name)
    if not seen:
        return None
    return TargetIsa(arch=arch, features=feature_closure(features))


@memoized(maxsize=4096)
def parse_note_target_isa(levels: frozenset[str]) -> TargetIsa | None:
    """The target a GNU-property ISA-needed note requires (the strongest level
    named), or ``None`` when no level token is present."""
    best: str | None = None
    for token in levels:
        level = _NOTE_LEVEL_TOKENS.get(token)
        if level is None:
            continue
        if best is None or len(LEVEL_FEATURES[level]) > len(LEVEL_FEATURES[best]):
            best = level
    if best is None:
        return None
    return TargetIsa(arch=None, features=feature_closure(LEVEL_FEATURES[best]))


@memoized(maxsize=4096)
def target_isa_from_sources(
    producer: TargetIsa | None, note: TargetIsa | None
) -> TargetIsa | None:
    """Merge the producer and note readings. A note requirement is added to the
    producer's features (the image needs both); ``arch`` comes from the producer
    alone, since the note records a requirement, not a ``-march=`` value."""
    if producer is None:
        return note
    if note is None:
        return producer
    return TargetIsa(
        arch=producer.arch,
        features=feature_closure(producer.features | note.features),
    )

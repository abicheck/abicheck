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

"""Capability-aware selection + fallback across source-ABI extractor backends.

abicheck ships several source-ABI front-ends behind one contract
(:class:`~abicheck.buildsource.source_extractors.base.SourceAbiExtractor`):
``clang`` (richest — inline/template/constexpr *bodies*, default arguments,
concepts, constructor mangling, macros), ``castxml`` (declarations / types /
public const-constexpr values, but blind to bodies/macros/concepts and to
user-declared constructor mangling — the root cause behind case78/105/106/111),
and a pre-captured ``android`` header-abi adapter.

The CLI previously hard-coded ``clang else castxml`` with no graceful path:
asking for ``clang`` when it was absent simply *disabled* source checks instead
of falling back to castxml, and castxml's blind spots were never surfaced. This
module owns the missing "evaluate the tools and pick a path" logic:

* a declarative :class:`SourceExtractorProfile` per backend (what it can observe);
* ``auto`` selection that picks the most capable *available* backend;
* an explicit fallback chain when the requested backend is unavailable;
* a report of the chosen backend's capability gaps so a construct the tool
  cannot see (e.g. concept tightening under castxml) is logged, not silent.

It is intentionally free of any subprocess call: availability is injected, so
the whole resolver is unit-testable without clang/castxml installed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .castxml import CastxmlSourceExtractor
    from .clang import ClangSourceExtractor

#: Canonical backend identifiers accepted on the CLI.
CLANG = "clang"
CASTXML = "castxml"
ANDROID = "android"
AUTO = "auto"

#: The full set of source-ABI capabilities a backend may provide. Ordered most-
#: to-least commonly decisive so a capability-gap report reads naturally.
ALL_CAPABILITIES: tuple[str, ...] = (
    "declarations",            # function/type/variable declarations + signatures
    "types",                   # record/enum/union layout-relevant type info
    "const_values",            # public const/constexpr scalar values
    "default_arguments",       # default-argument expressions on parameters
    "macros",                  # object-/function-like macro definitions
    "inline_bodies",           # inline/template/constexpr function *body* fingerprints
    "concepts",                # C++20 concept constraints / requires-expressions
    "constructor_mangling",    # mangled names for user-declared constructors
)


@dataclass(frozen=True)
class SourceExtractorProfile:
    """What one backend can observe + how it ranks for ``auto`` selection.

    ``rank`` is a coarse capability score (higher = more capable); ``auto``
    prefers the highest-ranked *available* backend. ``capabilities`` is the set
    of :data:`ALL_CAPABILITIES` the backend actually fills, so callers can warn
    about the gap between what was asked of the run and what the chosen tool can
    see (the case78/105/106/111 blind spots).
    """

    name: str
    rank: int
    capabilities: frozenset[str]

    def missing(self) -> tuple[str, ...]:
        """Capabilities in :data:`ALL_CAPABILITIES` this backend cannot provide."""
        return tuple(c for c in ALL_CAPABILITIES if c not in self.capabilities)


#: Declarative capability profiles. Encodes the documented blind spots: castxml
#: emits declarations/types/const values/default-arg expressions but NOT bodies,
#: macros, concepts, or constructor mangling; clang (source AST) sees all of
#: those; the Android adapter normalizes a pre-captured header-abi dump (decls /
#: types only). Keep in sync with source_extractors/CLAUDE.md (ADR-030 D3 table).
PROFILES: dict[str, SourceExtractorProfile] = {
    CLANG: SourceExtractorProfile(
        name=CLANG,
        rank=30,
        capabilities=frozenset(ALL_CAPABILITIES),
    ),
    CASTXML: SourceExtractorProfile(
        name=CASTXML,
        rank=20,
        capabilities=frozenset({
            "declarations", "types", "const_values", "default_arguments",
        }),
    ),
    ANDROID: SourceExtractorProfile(
        name=ANDROID,
        rank=10,
        capabilities=frozenset({"declarations", "types"}),
    ),
}

#: Preference order consulted by ``auto`` (and as the tail of a fallback chain):
#: most capable first. Android is excluded — it needs an explicit pre-captured
#: dump and is never auto-selected.
AUTO_PREFERENCE: tuple[str, ...] = (CLANG, CASTXML)


@dataclass
class SourceExtractorChoice:
    """The outcome of resolving a requested backend against what is available."""

    #: The backend chosen, or ``None`` when nothing usable was found.
    selected: str | None
    #: Backends considered but skipped, with the reason (e.g. "not on PATH").
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Human-readable explanation of the decision (for extractor records / logs).
    reason: str = ""
    #: Whether the selection differs from what the caller requested (fallback).
    fell_back: bool = False

    @property
    def capability_gaps(self) -> tuple[str, ...]:
        """Capabilities the *selected* backend cannot observe (empty if none/ideal)."""
        if self.selected is None:
            return ALL_CAPABILITIES
        profile = PROFILES.get(self.selected)
        return profile.missing() if profile else ()

    def gap_note(self) -> str:
        """One-line, user-facing note about the selected backend's blind spots."""
        gaps = self.capability_gaps
        if self.selected is None:
            return "no source-ABI extractor available — source-only checks disabled"
        if not gaps:
            return f"{self.selected}: full source-ABI capability"
        return (
            f"{self.selected}: cannot observe {', '.join(gaps)} — source-level "
            "changes in those constructs are invisible to this backend"
        )


def _availability_for(
    name: str,
    available: Callable[[str], bool] | None,
) -> bool:
    """Resolve availability for *name*, defaulting to 'assumed available'.

    The probe is injected so the resolver stays subprocess-free and testable;
    when no probe is given (or it raises), the backend is treated as available
    and the downstream ``extract()`` still degrades to partial coverage if the
    real tool turns out to be missing.
    """
    if available is None:
        return True
    try:
        return bool(available(name))
    except Exception:
        return True


def resolve_source_extractor(
    requested: str,
    *,
    available: Callable[[str], bool] | None = None,
    fallback: bool = True,
    preference: Iterable[str] = AUTO_PREFERENCE,
) -> SourceExtractorChoice:
    """Pick a source-ABI backend, evaluating capability + availability.

    Args:
        requested: ``"auto"``, ``"clang"``, ``"castxml"``, or ``"android"``.
        available: probe ``name -> bool`` (e.g. ``backend.available()``); when
            ``None`` every backend is assumed available.
        fallback: when True (default), an unavailable explicit request falls
            back along the capability-ordered chain rather than failing. When
            False, an unavailable explicit request yields ``selected=None`` so
            the caller can hard-fail / surface the unavailability verbatim.
        preference: capability-ordered chain consulted for ``auto`` and as the
            fallback tail (defaults to clang → castxml).

    Returns:
        A :class:`SourceExtractorChoice` describing the selection, any skipped
        backends, whether a fallback occurred, and the chosen backend's gaps.
    """
    requested = (requested or AUTO).lower()
    pref = [p for p in preference if p in PROFILES]

    # Android is only ever used when explicitly requested (needs a dump file).
    if requested == ANDROID:
        if _availability_for(ANDROID, available):
            return SourceExtractorChoice(selected=ANDROID, reason="android adapter (explicit)")
        return SourceExtractorChoice(
            selected=None,
            skipped=[(ANDROID, "android dump adapter unavailable")],
            reason="android requested but unavailable",
        )

    # Build the ordered list of candidates to try.
    if requested == AUTO:
        chain = pref
        lead = "auto"
    elif requested in PROFILES:
        # Requested backend first. The fallback tail is restricted to *less
        # capable* backends (lower rank): a clang request may degrade to
        # castxml, but a castxml request must never silently upgrade to clang —
        # that would hide a missing castxml dependency and change extractor
        # semantics for a castxml-specific run. So castxml-absent yields
        # selected=None (unavailable) rather than clang.
        req_rank = PROFILES[requested].rank
        tail = [p for p in pref if p != requested and PROFILES[p].rank < req_rank] if fallback else []
        chain = [requested] + tail
        lead = requested
    else:
        # Unknown name: treat as auto but record it.
        chain = pref
        lead = "auto"

    skipped: list[tuple[str, str]] = []
    for name in chain:
        if _availability_for(name, available):
            fell_back = name != requested and requested != AUTO
            if requested == AUTO:
                reason = f"auto-selected {name} (most capable available backend)"
            elif fell_back:
                reason = f"{requested} unavailable; fell back to {name}"
            else:
                reason = f"{name} (requested)"
            return SourceExtractorChoice(
                selected=name, skipped=skipped, reason=reason, fell_back=fell_back,
            )
        skipped.append((name, "not available (tool not found in PATH)"))

    return SourceExtractorChoice(
        selected=None,
        skipped=skipped,
        reason=f"no usable source-ABI backend for request {lead!r}",
    )


def select_source_backend(
    extractor: str,
    *,
    clang_bin: str = "clang",
) -> tuple[SourceExtractorChoice, ClangSourceExtractor | CastxmlSourceExtractor | None]:
    """Construct the clang/castxml backends, probe availability, and resolve one.

    Returns ``(choice, impl)`` where ``impl`` is the chosen backend instance, or
    ``None`` when nothing is usable (``choice.selected is None``). Keeps the
    backend construction + availability probing out of the CLI so the selection
    path lives next to the policy that drives it.
    """
    # Import via the package (not the submodules) so tests that monkeypatch
    # ``source_extractors.ClangSourceExtractor`` / ``CastxmlSourceExtractor``
    # still take effect here. The package is fully initialized by call time, so
    # this late import is cycle-safe.
    from . import CastxmlSourceExtractor, ClangSourceExtractor

    backends: dict[str, ClangSourceExtractor | CastxmlSourceExtractor] = {
        CLANG: ClangSourceExtractor(clang_bin=clang_bin),
        CASTXML: CastxmlSourceExtractor(),
    }

    def _probe(name: str) -> bool:
        backend = backends.get(name)
        return bool(backend and backend.available())

    choice = resolve_source_extractor(extractor, available=_probe)
    impl = backends[choice.selected] if choice.selected in backends else None
    return choice, impl

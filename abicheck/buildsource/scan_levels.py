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

"""Deterministic level resolution for the ``scan`` orchestrator (ADR-035, G19.3).

Two internal axes drive resolution (ADR-035 D1), but only one is public:

- **L = evidence depth** (:class:`EvidenceDepth`, the coarse ``--depth`` knob):
  the *what* + authority. This is the **only** axis exposed on the CLI
  (ADR-037 D5) — ``binary``/``headers``/``build``/``source``/``full``.
- **S = source-analysis method** (:class:`SourceMethod`, ``s0..s6`` + ``auto``):
  the *how* — six cost-ordered techniques that produce L3-L5 evidence, used
  internally to resolve a depth into a concrete collection mode. The old
  ``--source-method``/``--mode`` CLI flags that let a caller pin an S-method or
  preset directly are **deprecated, hidden aliases for one release**
  (:func:`abicheck.cli_scan._warn_deprecated_scan_aliases`); they still parse
  and, for backward compatibility, still resolve through this module's
  precedence — the S→L map is **lossy** (``build``→S1 not S2, and S3 has no
  ``--depth`` form), so an explicitly-passed ``--source-method`` is the more
  precise knob and wins if both are given. New code should use ``--depth``.

A ``--mode`` (:class:`ScanMode`) is a **fixed preset** of (S, L) — not
risk-varying — so a CI gate that pins a mode produces the same scan for the same
inputs. The numeric risk score (``risk.py``) is consulted **only** for
``--source-method auto`` (opt-in), never to silently change a pinned level
(ADR-035 D3).

The resolved S-method maps onto the existing ADR-033 D2 CI evidence mode that
``embed_build_source`` / ``collect_inline_pack`` already understand, so ``scan``
adds no new collection machinery — it is a front-end over ``dump``/``compare``.

Pure functions over enums and strings; fully unit-tested.
"""

from __future__ import annotations

from enum import Enum


class ScanMode(str, Enum):
    """A fixed (S, L) preset selecting *when*/*how deep* the scan runs (D9)."""

    PR = "pr"  # always-on tier + targeted S5 (the cheap PR default)
    PR_DEEP = "pr-deep"  # PR + the L5 graph edges
    BASELINE = "baseline"  # full S6 dump + full source analysis (amortized once)
    AUDIT = "audit"  # intra-version single-build hygiene lint, no baseline


class SourceMethod(str, Enum):
    """The S-axis: six cost-ordered source-analysis methods, plus ``auto`` (D1)."""

    S0 = "s0"  # diff classifier (risk tags/score) — no L output
    S1 = "s1"  # compile-DB / build-flag scan → L3
    S2 = "s2"  # preprocessor: macro values / include graph → L3→L5
    S3 = "s3"  # lexical pattern scan (compiler-free) → pre-scan facts
    S4 = "s4"  # symbol/reference index → L5
    S5 = "s5"  # targeted semantic AST (selected TUs) → L4 (+L5 edges)
    S6 = "s6"  # full AST (all TUs) → L4 full-scope
    AUTO = "auto"  # risk-driven escalation (opt-in, local/dev only)


class EvidenceDepth(str, Enum):
    """The coarse L-axis ``--depth`` selector (maps lossily to a representative S)."""

    BINARY = "binary"  # L0/L1 exported symbols + binary metadata only (no L2 AST)
    HEADERS = "headers"  # L2 only
    BUILD = "build"  # L3 (S1)
    SOURCE = "source"  # L4 scoped + L5 edges (S5)
    FULL = "full"  # INTERNAL only (ADR-043 D2); not a user --depth rung. ``source``
    # and ``full`` differ only in replay *scope* (see SourceScope), not in the
    # kind of evidence collected — the public ladder has one SOURCE rung and
    # scope is resolved by the calling command, never by a second depth value.
    GRAPH = "graph"  # L5 edges (S4) — INTERNAL only (ADR-037 D6); not a user --depth


#: The user-facing ``--depth`` ladder (ADR-037 D5, narrowed by ADR-043 D2).
#: Exactly four public rungs. ``FULL``/``GRAPH`` are internal-only: the L5 graph
#: is an implementation consequence of ``--depth source`` (D6), and the old
#: ``full`` rung collapsed into ``source`` — replay *scope* (changed/target/all,
#: see :class:`SourceScope`), not a deeper depth, is what used to distinguish them.
USER_DEPTHS: tuple[EvidenceDepth, ...] = (
    EvidenceDepth.BINARY,
    EvidenceDepth.HEADERS,
    EvidenceDepth.BUILD,
    EvidenceDepth.SOURCE,
)

#: ``DEPTH_PARAM`` (``cli_params.py``) checks membership in ``USER_DEPTHS`` and
#: raises a plain "not one of ..." error for anything else (``symbols``/
#: ``full``/``graph`` included) -- there is no CLI-visible alias/translation
#: (ADR-043 D2). The internal Python service API (:func:`parse_user_depth`,
#: used by ``ScanRequest``/MCP-adjacent programmatic callers, never by the
#: CLI's own ``--depth`` parsing) keeps the historical ``symbols`` alias and
#: accepts the internal ``full``/``graph`` rungs verbatim -- those rungs still
#: exist as real :class:`EvidenceDepth` values for mode-preset-driven internal
#: callers (e.g. ``pr-deep`` resolves ``GRAPH``).

#: ``parse_user_depth``'s one remaining alias: the historical ``symbols``
#: spelling for the CLI-named ``binary`` rung, kept for non-CLI callers.
_SERVICE_DEPTH_ALIASES: dict[str, EvidenceDepth] = {
    "symbols": EvidenceDepth.BINARY,
}


class SourceScope(str, Enum):
    """Internal replay-scope axis for ``EvidenceDepth.SOURCE`` (ADR-043 D2/D3).

    Never a public CLI flag. The public ``--depth source`` rung always means
    "collect source-semantic facts"; *which* translation units get replayed is
    resolved by the calling command from its own inputs, not by a second public
    depth value (that was the old, removed ``full`` rung):

    - ``dump``/``compare`` always resolve ``TARGET`` — the TUs owned by the
      resolved library target (or every available compile unit, with a
      warning, when target ownership cannot be resolved).
    - ``scan`` resolves ``CHANGED`` when a change seed (``--since``/
      ``--changed-path``) is present, else ``TARGET`` (never a zero-TU
      no-op).
    """

    CHANGED = "changed"
    TARGET = "target"
    ALL = "all"  # reserved: no command selects this scope yet


#: ``SourceScope`` → the ADR-033 D2 collect mode for the S5 (non-graph) source
#: method. ``ALL`` reuses ``source-target``'s "no target id ⇒ every unit"
#: fallback (see ``source_replay.select_compile_units``) rather than needing a
#: distinct collect mode.
_SOURCE_SCOPE_TO_COLLECT_MODE: dict[SourceScope, str] = {
    SourceScope.CHANGED: "source-changed",
    SourceScope.TARGET: "source-target",
    SourceScope.ALL: "source-target",
}


#: Fixed per-mode preset of (source_method, depth) — ADR-035 D9. ``PR`` pins the
#: cheap targeted S5; ``BASELINE`` the full S6; ``AUDIT`` reuses the PR depth but
#: runs intra-version (no baseline). These are deterministic, not risk-varying.
_MODE_PRESET: dict[ScanMode, tuple[SourceMethod, EvidenceDepth]] = {
    ScanMode.PR: (SourceMethod.S5, EvidenceDepth.SOURCE),
    ScanMode.PR_DEEP: (SourceMethod.S5, EvidenceDepth.GRAPH),
    ScanMode.BASELINE: (SourceMethod.S6, EvidenceDepth.FULL),
    ScanMode.AUDIT: (SourceMethod.S5, EvidenceDepth.SOURCE),
}

#: Lossy ``--depth`` → representative S-method (ADR-035 plan table). ``HEADERS``
#: reaches no S-method (L2 is intrinsic header AST). ``BUILD`` is S1 — S2
#: (preprocessor) is *not* reachable via ``--depth`` and needs ``--source-method``.
_DEPTH_TO_METHOD: dict[EvidenceDepth, SourceMethod | None] = {
    EvidenceDepth.BINARY: None,  # L0/L1 only — no source method, no L2 AST
    EvidenceDepth.HEADERS: None,
    EvidenceDepth.BUILD: SourceMethod.S1,
    EvidenceDepth.GRAPH: SourceMethod.S4,
    EvidenceDepth.SOURCE: SourceMethod.S5,
    EvidenceDepth.FULL: SourceMethod.S6,
}

#: Resolved S-method → the ADR-033 D2 CI evidence mode that drives inline
#: collection (``collect_inline_pack`` / ``embed_build_source``). The lexical
#: S0/S3 tiers collect no inline pack (the always-on pattern scan covers S3); the
#: semantic tiers select the matching replay scope. ``S2`` has no collection
#: backend yet (Phase 3b) and the ``scan`` CLI rejects it before this map is
#: consulted — the placeholder entry keeps the map total for the enum.
_METHOD_TO_COLLECT_MODE: dict[SourceMethod, str] = {
    SourceMethod.S0: "off",
    SourceMethod.S1: "build",
    SourceMethod.S2: "build",  # L3 build context; the S2 preprocessor pre-scan
    # (preprocessor_scan.run_preprocessor_scan) then runs over that L3 evidence
    SourceMethod.S3: "off",
    SourceMethod.S4: "graph-build",  # L3+L5 graph only — no costly L4 replay
    SourceMethod.S5: "source-changed",
    SourceMethod.S6: "graph-full",
}


#: Resolved S-method → the representative L-depth it reaches, for **honest
#: reporting** (Codex review): the report must state the depth of what actually
#: ran, not the requested mode/depth. Inverse of ``_DEPTH_TO_METHOD`` with the
#: depth-less methods (S0 diff / S2 preprocessor / S3 lexical) mapped to their
#: nearest reportable L: S0/S3 reach only L0-L2 (``headers``), S2 lands L3
#: (``build``).
_METHOD_TO_DEPTH: dict[SourceMethod, EvidenceDepth] = {
    SourceMethod.S0: EvidenceDepth.HEADERS,
    SourceMethod.S1: EvidenceDepth.BUILD,
    SourceMethod.S2: EvidenceDepth.BUILD,
    SourceMethod.S3: EvidenceDepth.HEADERS,
    SourceMethod.S4: EvidenceDepth.GRAPH,
    SourceMethod.S5: EvidenceDepth.SOURCE,
    SourceMethod.S6: EvidenceDepth.FULL,
}


def parse_user_depth(value: str | None) -> EvidenceDepth | None:
    """Resolve a ``ScanRequest.depth`` string to an ``EvidenceDepth`` (service API).

    ``None``/empty → ``None``. Honors the historical ``symbols`` alias so
    non-CLI callers (``service.run_scan``/``estimate_scan``, and internal
    mode-preset-driven callers) keep working; the internal ``full``/``graph``
    rungs are accepted verbatim here too. This is the Python service layer,
    not the public CLI: the ``--depth`` *flag* only ever accepts the four
    public rungs, enforced independently by ``cli_params.DEPTH_PARAM`` (ADR-043
    D2), which never calls this function.
    """
    if not value:
        return None
    v = str(value).lower()
    if v in _SERVICE_DEPTH_ALIASES:
        return _SERVICE_DEPTH_ALIASES[v]
    return EvidenceDepth(v)


def mode_preset(mode: ScanMode) -> tuple[SourceMethod, EvidenceDepth]:
    """The fixed (source_method, depth) preset for *mode* (deterministic)."""
    return _MODE_PRESET[mode]


def depth_to_method(depth: EvidenceDepth) -> SourceMethod | None:
    """The representative S-method for a coarse ``--depth`` (lossy; may be None)."""
    return _DEPTH_TO_METHOD[depth]


def method_to_depth(method: SourceMethod) -> EvidenceDepth:
    """The representative L-depth a *resolved* S-method reaches (for reporting).

    ``AUTO`` must be resolved to a concrete method first (via
    :func:`resolve_source_method`); passing it here is a programming error.
    """
    if method is SourceMethod.AUTO:
        raise ValueError("method_to_depth requires a resolved S-method, not AUTO")
    return _METHOD_TO_DEPTH[method]


def method_to_collect_mode(method: SourceMethod) -> str:
    """Map a *resolved* S-method to its ADR-033 D2 CI evidence collect mode.

    ``AUTO`` must be resolved to a concrete method first (via
    :func:`resolve_source_method`); passing it here is a programming error.
    """
    if method is SourceMethod.AUTO:
        raise ValueError(
            "method_to_collect_mode requires a resolved S-method, not AUTO"
        )
    return _METHOD_TO_COLLECT_MODE[method]


def resolve_source_method(
    *,
    mode: ScanMode,
    source_method: SourceMethod | None = None,
    depth: EvidenceDepth | None = None,
    auto_method: str | None = None,
) -> SourceMethod:
    """Resolve the explicit, deterministic S-method for a scan (ADR-035 D1/D3).

    ``--depth`` is the only S/L-selecting flag on the public CLI (ADR-037 D5);
    ``--source-method``/``--mode`` are deprecated, hidden aliases kept for one
    release of backward compatibility. Precedence when more than one is given
    (highest first):

    1. an explicit ``--source-method`` (deprecated; the more precise knob, so it
       wins over ``--depth`` if both are passed);
    2. an explicit ``--depth`` (coarse, lossy → representative S);
    3. the ``--mode`` preset (deprecated; the default when neither is given).

    ``AUTO`` is resolved with ``auto_method`` — the risk-driven S-method from
    :func:`risk.recommend_source_method` — which the caller computes only when the
    user opted into ``auto`` (it never fires for a pinned level). If ``AUTO`` is
    selected with no ``auto_method`` supplied, it falls back to the ``mode``
    preset so the result is always concrete.

    A ``--depth headers`` (no S-method) resolves to ``S0`` — only the intrinsic
    L0-L2 artifact/header tiers plus the always-on S3 pattern scan run.
    """
    if source_method is not None:
        if source_method is SourceMethod.AUTO:
            if auto_method:
                return SourceMethod(auto_method)
            return mode_preset(mode)[0]
        return source_method
    if depth is not None:
        resolved = depth_to_method(depth)
        return resolved if resolved is not None else SourceMethod.S0
    return mode_preset(mode)[0]


def resolve_level(
    *,
    mode: ScanMode,
    source_method: SourceMethod | None = None,
    depth: EvidenceDepth | None = None,
    auto_method: str | None = None,
) -> tuple[SourceMethod, EvidenceDepth]:
    """Resolve both the deterministic S-method **and** its effective L-depth.

    Returning the depth (not just the method) keeps ``--mode`` presets that pin a
    *deeper* depth than their method implies — notably ``pr-deep`` = ``(S5,
    GRAPH)`` vs ``pr`` = ``(S5, SOURCE)`` — distinct: collapsing to the method
    alone made the two modes identical (Codex review). Depth precedence mirrors
    :func:`resolve_source_method`:

    - an explicit/``auto`` ``--source-method`` reports the *resolved method's*
      representative depth (so ``s6`` reads ``full``, not the mode preset);
    - an explicit ``--depth`` is taken verbatim;
    - otherwise the ``--mode`` preset's depth is preserved (``pr-deep`` keeps
      ``GRAPH``).
    """
    method = resolve_source_method(
        mode=mode, source_method=source_method, depth=depth, auto_method=auto_method
    )
    if source_method is not None:
        eff_depth = method_to_depth(method)
    elif depth is not None:
        eff_depth = depth
    else:
        eff_depth = mode_preset(mode)[1]
    return method, eff_depth


def level_to_collect_mode(
    method: SourceMethod,
    depth: EvidenceDepth,
    *,
    source_scope: SourceScope | None = None,
) -> str:
    """The ADR-033 D2 CI evidence mode for a resolved (method, depth) level.

    Depth-aware for the S5 graph case only: ``pr-deep`` ((S5, GRAPH)) resolves to
    ``graph-full`` — a genuinely deeper collection than ``pr``'s ``source-changed``
    (full replay scope vs. changed-only), not just a relabel; ``graph-summary``
    maps to the *same* changed scope/layers as ``source-changed`` downstream, so
    it would not actually collect more (Codex review). S4 (``--depth graph``) keeps
    its own ``graph-build`` mode (L3+L5, no costly L4 replay) — it is graph-only,
    not source-ABI (Codex review). All other levels use the method's default mode.

    ``source_scope`` (ADR-043 D2/D3) lets the caller pin the S5 replay scope
    (changed vs target) explicitly instead of always defaulting to
    ``"source-changed"`` — this is the fix for the zero-TU defect where an
    explicit deep depth without a change seed silently selected no translation
    units. Only S5 is scope-sensitive; every other method ignores the
    parameter.
    """
    base = method_to_collect_mode(method)
    if depth is EvidenceDepth.GRAPH and base == "source-changed":
        return "graph-full"
    if source_scope is not None and method is SourceMethod.S5:
        return _SOURCE_SCOPE_TO_COLLECT_MODE[source_scope]
    return base

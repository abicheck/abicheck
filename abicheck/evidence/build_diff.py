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

"""Build-evidence diff and findings (ADR-029 D9).

Compares the ``BuildEvidence`` of two evidence packs and classifies build-flag,
toolchain, export-policy, and generated-file drift. Per ADR-028 D3 these
findings are never ``BREAKING`` on their own: a build change that actually
breaks the shipped ABI is caught separately by the artifact diff (L0/L1/L2);
these kinds explain and localize it.
"""
from __future__ import annotations

from ..checker_policy import ChangeKind
from ..checker_types import Change
from .build_evidence import BuildEvidence, BuildOption

#: Canonical option keys whose drift specifically indicates a toolchain change
#: (compiler/stdlib/sysroot), routed to TOOLCHAIN_VERSION_CHANGED rather than
#: the generic ABI-flag finding.
_TOOLCHAIN_OPTION_KEYS = frozenset({"target", "sysroot"})


def check_header_parse_drift(
    build_evidence: BuildEvidence,
    *,
    headers_parsed_with_context: bool,
) -> list[Change]:
    """Flag when the header AST was parsed without the real build context.

    This is the ADR-020a problem generalized (ADR-029 D9): when L3 build
    evidence shows ABI-relevant compile flags (``-std``, ABI macros, etc.) but
    the L2 public-header AST was parsed *without* those flags, header-derived
    API facts may be unreliable. Returns a single RISK finding in that case.

    ``headers_parsed_with_context`` is True when the dump consumed the build's
    ``compile_commands.json`` (ADR-020a ``-p``/``--compile-db``); when False and
    ABI-relevant flags exist, the parse context drifted.
    """
    if headers_parsed_with_context:
        return []
    abi_flags = sorted(
        {opt.key for opt in build_evidence.build_options if opt.abi_relevant}
    )
    if not abi_flags:
        return []
    return [
        Change(
            kind=ChangeKind.HEADER_PARSE_CONTEXT_DRIFT,
            symbol="header-parse:context",
            description=(
                "Public headers were parsed without the build's ABI-relevant "
                f"context ({', '.join(abi_flags)}); header-derived API facts may "
                "be unreliable. Re-run the dump with the build's "
                "compile_commands.json (-p/--compile-db) to restore confidence."
            ),
            new_value=", ".join(abi_flags),
        )
    ]


def diff_build_evidence(old: BuildEvidence, new: BuildEvidence) -> list[Change]:
    """Return build-context findings for the old→new build-evidence transition.

    The result is an ordinary list of :class:`Change` objects ready to fold
    into a ``DiffResult`` and run through the existing verdict/policy pipeline.
    """
    changes: list[Change] = []
    changes.extend(_diff_options(old, new))
    changes.extend(_diff_toolchains(old, new))
    changes.extend(_diff_export_policy(old, new))
    changes.extend(_diff_generated_files(old, new))
    return changes


# -- build options -----------------------------------------------------------


def _option_map(ev: BuildEvidence) -> dict[str, BuildOption]:
    return {opt.key: opt for opt in ev.build_options}


def _diff_options(old: BuildEvidence, new: BuildEvidence) -> list[Change]:
    old_opts = _option_map(old)
    new_opts = _option_map(new)
    changes: list[Change] = []

    for key in sorted(set(old_opts) | set(new_opts)):
        o = old_opts.get(key)
        n = new_opts.get(key)
        old_val = o.value if o else None
        new_val = n.value if n else None
        if old_val == new_val and (o is not None) == (n is not None):
            continue

        abi_relevant = (o.abi_relevant if o else False) or (n.abi_relevant if n else False)
        # Toolchain-shaped options route to the dedicated toolchain finding.
        if key in _TOOLCHAIN_OPTION_KEYS and abi_relevant:
            changes.append(
                Change(
                    kind=ChangeKind.TOOLCHAIN_VERSION_CHANGED,
                    symbol=f"build-option:{key}",
                    description=f"Toolchain option {key!r} changed: {old_val!r} -> {new_val!r}",
                    old_value=old_val,
                    new_value=new_val,
                )
            )
            continue

        kind = (
            ChangeKind.ABI_RELEVANT_BUILD_FLAG_CHANGED
            if abi_relevant
            else ChangeKind.BUILD_CONTEXT_CHANGED
        )
        verb = "added" if old_val is None else "removed" if new_val is None else "changed"
        changes.append(
            Change(
                kind=kind,
                symbol=f"build-option:{key}",
                description=f"Build option {key!r} {verb}: {old_val!r} -> {new_val!r}",
                old_value=old_val,
                new_value=new_val,
            )
        )
    return changes


# -- toolchains --------------------------------------------------------------


def _toolchain_fingerprints(ev: BuildEvidence) -> dict[str, str]:
    """Map language → "compiler_id version target" fingerprint."""
    out: dict[str, str] = {}
    for tc in ev.toolchains:
        key = tc.language or tc.id
        out[key] = f"{tc.compiler_id} {tc.version} {tc.target_triple}".strip()
    return out


def _diff_toolchains(old: BuildEvidence, new: BuildEvidence) -> list[Change]:
    old_fp = _toolchain_fingerprints(old)
    new_fp = _toolchain_fingerprints(new)
    changes: list[Change] = []
    for lang in sorted(set(old_fp) & set(new_fp)):
        if old_fp[lang] != new_fp[lang]:
            changes.append(
                Change(
                    kind=ChangeKind.TOOLCHAIN_VERSION_CHANGED,
                    symbol=f"toolchain:{lang}",
                    description=(
                        f"Toolchain for {lang} changed: "
                        f"{old_fp[lang]!r} -> {new_fp[lang]!r}"
                    ),
                    old_value=old_fp[lang],
                    new_value=new_fp[lang],
                )
            )
    return changes


# -- export policy -----------------------------------------------------------


def _export_policy(ev: BuildEvidence) -> dict[str, str]:
    """Map target id → version-script/export-map fingerprint from link units."""
    out: dict[str, str] = {}
    for lu in ev.link_units:
        if lu.version_script or lu.soname:
            out[lu.target_id or lu.id] = f"{lu.version_script}|{lu.soname}"
    return out


def _diff_export_policy(old: BuildEvidence, new: BuildEvidence) -> list[Change]:
    old_ep = _export_policy(old)
    new_ep = _export_policy(new)
    changes: list[Change] = []
    for target in sorted(set(old_ep) | set(new_ep)):
        ov = old_ep.get(target)
        nv = new_ep.get(target)
        if ov != nv:
            changes.append(
                Change(
                    kind=ChangeKind.LINK_EXPORT_POLICY_CHANGED,
                    symbol=f"link:{target}",
                    description=(
                        f"Export policy for {target} changed: {ov!r} -> {nv!r}. "
                        "If exported symbols were removed, see the artifact diff "
                        "for the authoritative breaking findings."
                    ),
                    old_value=ov,
                    new_value=nv,
                )
            )
    return changes


# -- generated files ---------------------------------------------------------


def _diff_generated_files(old: BuildEvidence, new: BuildEvidence) -> list[Change]:
    """Flag generated-file dependency instability surfaced in diagnostics.

    Ninja's ``missingdeps`` tool (ADR-029 D5) and similar signals land in
    ``diagnostics``; a new instability signal in the new pack is a risk.
    """
    changes: list[Change] = []
    marker = "missingdeps"
    old_has = any(marker in d for d in old.diagnostics)
    new_has = any(marker in d for d in new.diagnostics)
    if new_has and not old_has:
        changes.append(
            Change(
                kind=ChangeKind.GENERATED_FILE_DEPENDENCY_UNSTABLE,
                symbol="build-graph:generated-files",
                description=(
                    "Build graph reports missing/unstable generated-file "
                    "dependencies in the new build; generated public "
                    "declarations may differ from what was analyzed."
                ),
            )
        )
    return changes

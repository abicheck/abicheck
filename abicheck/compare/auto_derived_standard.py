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

"""A header-dialect difference neither user chose bounds, not refuses, a
comparison (ADR-050 amendment, 2026-11; catalog case98).

Since the header parse takes its ``-std=`` from each binary's own DWARF
producer (``extract/binary_dialect.py``), two builds of one library under
different standards (C++17 -> C++20) were parsed under different dialects
and the pair was refused as ``profile_mismatch`` (exit 16, no verdict),
although no user asked for different analysis modes. The difference is a
fact about the *product's* build, not a misconfigured comparison.

Refusing discards the whole comparison; ignoring the difference would hide
that a declaration guarded by ``__cplusplus`` may differ for that reason
alone. The bounded middle: when ``language_standard`` is the only
unexplained profile difference and **both** sides recorded it as
auto-derived (``ast_toolchain["language_standard_explicit"] == "0"``), the
comparison proceeds -- provided at least one side's standard was implied by
its binary's build (``ast_toolchain["implied_options"]``), so the split has
a build cause rather than the header-content heuristic -- and the ``declaration``/``layout`` dimensions the
standard can affect are reported unverified, with the reason naming the
input that completes the check. An explicit pin on either side, a legacy
snapshot without the provenance stamp, or any other differing field keeps
the refusal. Nothing here reads a build fact: whether the product's
standard floor changed is still decided only by L3 build evidence
(``abi_relevant_build_flag_changed``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def _auto_derived(snap: AbiSnapshot) -> bool:
    toolchain = getattr(snap, "ast_toolchain", None) or {}
    return toolchain.get("language_standard_explicit") == "0"


def _binary_implied_standard(snap: AbiSnapshot) -> bool:
    """Whether this side's ``-std=`` was implied by its own binary's build
    (``extract/binary_dialect.record_implied_options``)."""
    toolchain = getattr(snap, "ast_toolchain", None) or {}
    implied = str(toolchain.get("implied_options") or "").split()
    return any(token.startswith("-std=") for token in implied)


def _standard(snap: AbiSnapshot) -> str:
    contract = snap.contract
    fields = contract.profile_fields if contract is not None else {}
    return (fields or {}).get("language_standard", "") or "unrecorded"


def auto_derived_standard_reason(
    old: AbiSnapshot,
    new: AbiSnapshot,
    unknown_differing: set[str],
    unexplained: set[str],
) -> str | None:
    """The reason a non-fatal (bounding) profile mismatch carries for an
    auto-derived-only dialect difference, or ``None`` when the refusal must
    stand."""
    if unknown_differing or unexplained != {"language_standard"}:
        return None
    if not (_auto_derived(old) and _auto_derived(new)):
        return None
    # The difference must come from the product's builds: at least one side's
    # standard was implied by its binary. A split the header-content
    # heuristic or the default probe produced on its own (the same headers
    # parsed under two dialects for no build reason) keeps the refusal.
    if not (_binary_implied_standard(old) or _binary_implied_standard(new)):
        return None
    return (
        "the public headers were parsed under different language standards "
        f"(old: {_standard(old)}, new: {_standard(new)}), each derived "
        "automatically from that binary's own build, not chosen by the "
        "user. Declarations and layout that depend on the standard (e.g. "
        "guarded by __cplusplus) are not verified for this pair. To verify "
        "them, re-dump both sides with the same standard (`--set "
        "compile.std=<std>`); to check whether the product's standard "
        "floor itself changed, supply build data (`--build-info`)."
    )

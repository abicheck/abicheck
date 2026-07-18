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

"""Composition-compatibility ChangeKind registry entries (Wave A).

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap, following the same pattern as
``change_registry_coverage.py``. These entries are spliced into the single
``REGISTRY`` at import time — declaring a kind here is exactly equivalent to
declaring it in ``change_registry.py``.

"Composition compatibility" (as opposed to a single-library declaration
diff) covers failures that only appear when independently-valid artifacts
are combined at runtime: a symbol resolving to a different provider DSO, a
reordered dependency list changing which library wins a lookup, an
ordinal-only Windows import silently retargeted, or a build-flag drift
(wchar_t model) that no symbol-level check can see.
"""
from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

COMPOSITION_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    # ── Runtime symbol-binding rebound ───────────────────────────────────────
    _E("runtime_symbol_provider_changed", _R,
       impact="A consumer's reference to this symbol resolves to a different "
              "provider DSO than it did in the baseline environment — neither "
              "DSO's own export table necessarily changed, so a per-library ABI "
              "diff is silent. Caused by dependency reordering, a sibling "
              "library gaining/losing the export, or interposition drift. "
              "Whether this actually breaks the consumer depends on whether the "
              "new provider's signature is compatible; review the new "
              "provider's own diff for this symbol.",
       description_template="Runtime binding for '{symbol}' in consumer '{name}' moved from provider '{old}' to '{new}' between the baseline and candidate environments."),
    _E("runtime_weak_resolution_changed", _R,
       impact="A weak symbol reference's resolution status flipped between the "
              "baseline and candidate environments — a reference that used to "
              "resolve is now unresolved (acceptable at runtime for a weak ref, "
              "but the consumer loses the optional functionality it gated on "
              "it), or one that was unresolved now binds to a live "
              "implementation (the consumer's optional-feature code path "
              "activates for the first time).",
       description_template="Weak symbol '{symbol}' resolution for consumer '{name}' changed from '{old}' to '{new}' between the baseline and candidate environments."),

    # ── Ordered loader contract ──────────────────────────────────────────────
    _E("needed_order_changed", _R,
       impact="The DT_NEEDED dependency list was reordered while the set of "
              "dependencies stayed the same. The System V ABI's dynamic linker "
              "searches dependencies breadth-first in DT_NEEDED order, so a "
              "pure reorder can silently change which DSO wins the lookup for "
              "a non-versioned symbol defined in more than one dependency. Not "
              "proven breaking on its own — pair with a runtime binding check "
              "to confirm an actual provider changed.",
       description_template="DT_NEEDED order changed: {old} → {new}"),
    _E("symbolic_binding_mode_changed", _R,
       impact="DT_SYMBOLIC/DF_SYMBOLIC was toggled. When set, the object "
              "resolves its own references against its own definitions first, "
              "before the global symbol scope — a lookup-precedence change "
              "that can silently stop honoring an LD_PRELOAD or another "
              "library's intended interposition of a symbol this object also "
              "defines.",
       description_template="Symbolic binding mode changed: {old} → {new}"),
    _E("text_relocation_introduced", _R,
       impact="DF_TEXTREL/DT_TEXTREL was gained: the dynamic loader must write "
              "into the (nominally read-only, shared) text segment to apply "
              "relocations. This defeats W^X and page-level text-segment "
              "sharing across processes, and on hardened systems the loader "
              "may refuse to load the object at all.",
       description_template="Text relocations introduced (DF_TEXTREL/DT_TEXTREL set): the loader must write into the text segment, defeating W^X and text-segment sharing"),
    _E("text_relocation_removed", _C,
       impact="DF_TEXTREL/DT_TEXTREL was dropped; the text segment stays "
              "read-only and shared again. A hardening improvement.",
       description_template="Text relocations removed (DF_TEXTREL/DT_TEXTREL cleared): text segment is read-only/shared again"),

    # ── Consumer-aware PE contracts ──────────────────────────────────────────
    _E("pe_ordinal_retargeted", _B,
       impact="A consumer imports this DLL function purely by ordinal number "
              "(no name in its import table). The DLL still exports that "
              "ordinal, but it now names a *different* function than it did in "
              "the old library — PE ordinals are commonly auto-assigned and "
              "reused when the export table shifts, so an ordinal-only "
              "consumer silently calls the wrong function with no link or load "
              "error.",
       description_template="PE export ordinal retargeted: {name} named '{old}' in the old library, now names '{new}' — a consumer that imports by ordinal silently calls a different function"),
    _E("pe_import_load_mode_changed", _R,
       impact="An imported DLL function moved between the eager import table "
              "(IMAGE_DIRECTORY_ENTRY_IMPORT, resolved at process load) and the "
              "delay-load table (IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT, resolved "
              "on first call). The two have different failure-timing "
              "contracts: an eager import that fails aborts the process at "
              "load; a delay import that fails surfaces only when the "
              "consumer first calls it — a deployment/error-handling risk "
              "even though the DLL and symbol both still exist.",
       description_template="Import load mode changed for '{name}': {old} → {new}"),
    _E("consumer_required_symbol_removed", _B,
       impact="A real consumer binary's own dynamic-symbol table (ELF undefined "
              "symbol / PE import / Mach-O undefined symbol, collected via "
              "--used-by, ADR-005/043) required this exact symbol from the "
              "library at load time — empirical ground truth independent of "
              "any header/namespace/visibility reasoning. The new library no "
              "longer exports it: the consumer's existing binary will fail to "
              "load, or crash the first time it calls the symbol, with no "
              "recompilation involved.",
       description_template="Consumer '{name}' requires symbol '{symbol}', which the new library no longer exports"),
    _E("consumer_runtime_load_failed", _R,
       impact="The --verify-runtime execution harness (ADR-044 P2 item 2) ran "
              "this real consumer binary with LD_BIND_NOW=1: it loaded and ran "
              "cleanly against the old library, but the dynamic linker itself "
              "reported an undefined symbol against the new one. This is "
              "empirical, dynamic corroboration alongside the static scanner, "
              "not a replacement for it — an execution environment can fail "
              "for reasons unrelated to the library (missing unrelated "
              "dependency, sandboxing), so this never manufactures a BREAKING "
              "verdict on its own.",
       description_template="Consumer '{name}' loads against the old library but the dynamic linker reports undefined symbol '{symbol}' against the new one"),

    # ── Fundamental compiler data-model flags ────────────────────────────────
    _E("wchar_model_changed", _R,
       impact="The -fshort-wchar compiler flag drifted between builds. GCC and "
              "Clang document that objects built with and without "
              "-fshort-wchar are not binary compatible: the flag switches "
              "wchar_t between the platform default (commonly 4-byte signed on "
              "Linux/macOS) and a 2-byte unsigned type. Any public function "
              "parameter, return value, or struct field carrying wchar_t "
              "changes size and signedness with no symbol-level signal, so a "
              "symbol-only check is blind to it.",
       description_template="wchar_t model changed: {old} → {new}. Objects built with and without -fshort-wchar are not binary compatible for any public wchar_t parameter, field, or return value."),
]

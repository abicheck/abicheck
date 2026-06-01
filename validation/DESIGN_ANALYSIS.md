# Design analysis: root causes & architectural fixes for validation false positives

Companion to `validation/REPORT.md`. For each false-positive class observed
against real upstream binaries, this maps the **code-level root cause** (with
file:line), evaluates the **architectural fix** (preferring existing
infrastructure over point patches), and records **status** + the **regression
test** that guards it (`tests/test_real_world_false_positives.py`).

Boundary principle used throughout: abicheck core should hardcode only
exclusions that are *universally* non-ABI (toolchain/standard-library/anonymous
types). Anything *library-specific* (e.g. `tbb::detail`, `*::internal::*`)
belongs in the policy / frozen-namespace layer, not in core.

---

## FP-1 — Standard-library types treated as the library's own ABI surface

**Symptom.** oneTBB 2021.5→2021.9 (ABI-compatible) reported 216 breaks; 54 were
on `std::`/`__gnu_cxx` types (`std::__cxx11::basic_string::npos`,
`std::integral_constant::value`). Proven artifact: the two builds used different
GCC (9.4 vs 11.3 + LTO), which emit different static-member DIEs.

**Root cause.**
- `abicheck/model.py` — `is_compiler_internal_type()` / `COMPILER_INTERNAL_TYPES`
  only excluded **13 hardcoded** names; no `std::`/`__gnu_cxx::`/`__cxxabiv1::`.
- `abicheck/diff_types.py:41-42` — `_diff_types()` builds `old_map`/`new_map`
  filtering solely with `is_compiler_internal_type`, so std:: type records flow
  into `TYPE_REMOVED`/`TYPE_FIELD_REMOVED`.
- The symbol-level filter that *does* know std:: (`dumper._STDLIB_PREFIXES` /
  `_is_abi_relevant_symbol`) is never applied to DWARF-derived **types**.

**Architectural fix (IMPLEMENTED).** Extend the single-source-of-truth predicate
in `model.py` to `is_non_abi_surface_type()` — a superset of
`is_compiler_internal_type` adding standard-library/runtime namespace prefixes
(`std::`, `__gnu_cxx::`, `__cxxabiv1::`, `__cxx11::`). Apply it where the old
predicate was applied (`diff_types._diff_types` map construction). One predicate,
used by both the differ and (future) the DWARF extractor, keeps "what is surface"
consistent.

**Guard rail.** The std:: exclusion is scoped: when the inspected DSO *is* the
C++ runtime (`libstdc++`/`libc++`/`libc++abi`/`libsupc++`, via
`model.is_cxx_runtime_library`), `std::` records are that library's own surface
and are kept (`is_non_abi_surface_type(..., exclude_stdlib_namespaces=False)`),
so a real `std::basic_string` size change in libstdc++ is still a break. Anonymous
and compiler-internal exclusions always apply.

**Measured impact.** oneTBB T1 `libtbb`: 216 → 168 breaking (std:: class
eliminated). Full fast suite: 7056 passed, 0 regressions.

**Test.** `test_stdlib_type_change_is_not_breaking`,
`test_stdlib_size_change_is_breaking_when_target_is_the_runtime`,
`test_is_cxx_runtime_library`, `test_is_non_abi_surface_type_keeps_stdlib_when_requested`.

**Residual / follow-up.** The remaining 168 are dominated by `tbb::detail`
(library-internal). That is deliberately *not* hardcoded — see FP-1b.

---

## FP-1b — Library-internal namespaces (`tbb::detail`, `*::internal::*`)

**Symptom.** 139 of oneTBB T1's breaks are `tbb::detail::*` — oneTBB's
documented-internal, unstable namespace.

**Root cause.** Frozen-namespace machinery exists
(`policy_file.frozen_namespaces`, `post_processing.EscalateFrozenNamespaceViolations`,
`suppression.namespace`) but is **purely opt-in**: `frozen_namespaces` defaults
to empty; there is no built-in internal-namespace profile.

**Architectural fix (PROPOSED, not implemented — library-specific, needs design).**
Do **not** hardcode `tbb::detail` in core. Instead:
- ship an opt-in built-in profile (e.g. `--policy internal_namespaces`) that
   pre-loads common internal globs (`*::detail::*`, `*::internal::*`,
  `*::impl::*`), and/or
- let a library declare its own internal namespaces in a policy file, and
  surface them in the `internal_churn` breakdown bucket rather than `public`.

This keeps the universal/library-specific boundary clean. The validation run
already demonstrates the value: a 6-rule namespace suppression
(`validation/suppress_internal.yaml`) collapses T1 from 216→2.

**Test (future).** Add once the profile shape is decided.

---

## FP-2 — Anonymous / local types (lambdas, unnamed structs)

**Symptom.** Residual oneTBB finding `type_removed: <lambda()>`.

**Root cause.** Same map-construction in `diff_types.py:41-42`; anonymous type
names were never excluded. Lambda/unnamed types have no stable cross-version ABI
identity.

**Architectural fix (IMPLEMENTED).** `is_non_abi_surface_type()` also matches
anonymous markers (`<lambda`, `{lambda`, `(anonymous`, `(unnamed`, `<unnamed`)
— covering gcc and clang spellings.

**Test.** `test_anonymous_type_removal_is_not_breaking` (passes).

---

## FP-3 — RTTI/typeinfo of anonymous lambdas scored as breaking `var_removed`

**Symptom.** Protobuf 6.33.2→6.33.5 (a *patch*) → BREAKING from 6 `var_removed`,
all `_ZTIZN6google…EUl…E_` / `_ZTSZN…` (typeinfo of internal lambdas).

**Root cause (two gaps on the same path).**
1. `abicheck/dumper.py` `_elf_classify_symbols()` (~785) builds the OBJECT/FUNC
   symbol sets straight from `elf_meta.symbols` and **never calls
   `_is_abi_relevant_symbol()`** — which already filters `_ZTI`/`_ZTS`/`_ZNSt`.
   The filter is applied in `_pyelftools_exported_symbols` and the Mach-O path,
   but bypassed here, so `_ZTI…`/`_ZTS…` OBJECTs become public `Variable`s.
2. `abicheck/diff_symbols.py` `_var_removed` (~442) has no `_ZTI`/`_ZTS`
   (or local-scope `Z…E` / lambda `Ul…E`) guard, so the removed RTTI object
   emits `VAR_REMOVED` (breaking).

**Architectural fix (PROPOSED).** Two complementary, low-risk options:
- Apply the existing `_is_abi_relevant_symbol()` in `_elf_classify_symbols()`
  (closes the inconsistency at the source — same filter every other path uses).
- And/or route removed `_ZTI`/`_ZTS` symbols of **local/anonymous** types
  (mangling contains `Ul…E` lambda or `…E` local-scope encoding) into the
  existing `rtti_churn` bucket instead of breaking `VAR_REMOVED`.

Deferred because applying `_is_abi_relevant_symbol` in the primary path has a
wider blast radius (it also filters stdlib FUNC symbols) and warrants its own
focused validation pass.

**Test.** `test_lambda_rtti_removal_is_not_breaking` (xfail, strict — flips to
PASS when fixed).

---

## FP-4 — Mixed coverage (old has DWARF, new is stripped) fabricates removals

**Symptom.** libxml2 2.9.7 (DWARF) → 2.9.9 (stripped) → 1149 breaks, incl.
`type_removed: _xmlNode` (a core public type that still exists) and 142
`func_return_changed` with `new: ?`.

**Root cause.** `abicheck/diff_types.py` emits `TYPE_REMOVED` (~46-51) and
`TYPE_FIELD_REMOVED` (~192-210) whenever a name is absent from `new_map`, with
**no guard for asymmetric type coverage**. When the new snapshot has no
type-level DWARF, *every* old type/field/signature looks removed/changed.
Absence of debug info is absence of *evidence*, not evidence of *removal*.

**Architectural fix (PROPOSED).** Detect the asymmetry up front (e.g. old has
type records but `new.types` is empty and `new` is `elf_only_mode` with no
DWARF) and either:
- degrade to a **symmetric symbols-only** comparison (compare only what both
  sides can see), or
- emit such findings as `unconfirmed` (excluded from the verdict) and cap the
  verdict at manual-review, consistent with the existing low-coverage
  fallback warning.

Deferred: changing cross-snapshot coverage semantics is a verdict-affecting
design change that should be specified (and golden-tested) deliberately.

**Test.** `test_stripped_new_side_does_not_fabricate_type_removals` (xfail,
strict).

---

## Summary

| FP | Class | Root cause (file) | Fix | Status | Test |
|----|-------|-------------------|-----|--------|------|
| FP-1 | std:: types as surface | `model.py`, `diff_types.py:41` | `is_non_abi_surface_type` | ✅ done | passes |
| FP-1b | `tbb::detail` internal ns | `policy_file`/`post_processing` (opt-in only) | built-in internal-ns profile | ⏳ proposed | future |
| FP-2 | anonymous/lambda types | `diff_types.py:41` | anonymous markers in predicate | ✅ done | passes |
| FP-3 | lambda RTTI → var_removed | `dumper.py:785`, `diff_symbols.py:442` | apply existing symbol filter / rtti_churn route | ⏳ proposed | xfail guard |
| FP-4 | mixed DWARF↔stripped | `diff_types.py:46,192` | asymmetric-coverage guard | ⏳ proposed | xfail guard |

The two universal, low-risk fixes (FP-1, FP-2) are implemented and measured
(oneTBB 216→168 breaking, zero suite regressions). The three remaining items are
either library-specific (FP-1b) or verdict-semantics changes (FP-3, FP-4) that
warrant their own focused design + validation pass; each is guarded by a strict
xfail regression test that will turn green the moment the fix lands.

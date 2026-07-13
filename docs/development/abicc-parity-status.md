# ABI Checker Gap Analysis — abicheck vs ABICC vs libabigail

> Generated: 2026-03-09; content reviewed 2026-06-07. The scenario matrix is a historical ABICC/libabigail parity snapshot; the current ChangeKind total is **352** — see the [Change Kind Reference](../reference/change-kinds.md) for the authoritative list.
> abicheck version: HEAD of `abicheck/abicheck`
> Compared against: ABICC (lvc/abi-compliance-checker) + libabigail (abidiff/abidw)

---

## Summary

- **abicheck covers:** ~55/55 de-duplicated ABI break scenarios (~100%) after recent releases
- **Key differentiator:** abicheck uses multi-tier analysis (castxml headers + ELF symbols + DWARF layout) -- works on **release builds** with headers + `.so`, no debug symbols required for core checks. ABICC needs GCC `-fdump-lang-spec`, abidiff needs DWARF debug info.
- **Closed gaps:** All original P0/P1/P2 scenarios are now detected, including enum rename, field/param rename, field qualifiers (const/volatile/mutable), pointer level changes, access level changes, param default value tracking, and anonymous struct/union fields.
- **Coverage: exceeds ABICC** — the original ABICC-equivalent matrix remains covered, and the full current catalog has grown to 352 ChangeKinds.
- **Test coverage:** ChangeKind assertion coverage is enforced by `tests/test_changekind_completeness.py`; parity and example coverage now live in the expanded `tests/` and `examples/` suites.

> Note: ABICC has 90+ rules total, but many are sub-rules of the same scenario. The 55-row coverage table below is the expanded scenario count for the current implementation.
>

---

## What abicheck ALREADY covers

| Case | abicheck | ABICC | abidiff | Notes |
|------|----------|-------|---------|-------|
| Function removed | ✅ `FUNC_REMOVED` | ✅ | ✅ | Via ELF/PE/Mach-O symbol metadata |
| Function added | ✅ `FUNC_ADDED` | ✅ | ✅ | |
| Return type changed | ✅ `FUNC_RETURN_CHANGED` | ✅ | ✅ | |
| Parameter type changed | ✅ `FUNC_PARAMS_CHANGED` | ✅ | ✅ | |
| noexcept added | ✅ `FUNC_NOEXCEPT_ADDED` | ✅ | ✅ | C++17: part of function type |
| noexcept removed | ✅ `FUNC_NOEXCEPT_REMOVED` | ✅ | ✅ | |
| Method became virtual | ✅ `FUNC_VIRTUAL_ADDED` | ✅ | ✅ | Mangled name changes |
| Method became non-virtual | ✅ `FUNC_VIRTUAL_REMOVED` | ✅ | ✅ | |
| Global var removed | ✅ `VAR_REMOVED` | ✅ | ✅ | |
| Global var added | ✅ `VAR_ADDED` | ✅ | ✅ | |
| Global var type changed | ✅ `VAR_TYPE_CHANGED` | ✅ | ✅ | |
| Struct/class size changed | ✅ `TYPE_SIZE_CHANGED` | ✅ | ✅ | |
| Alignment changed | ✅ `TYPE_ALIGNMENT_CHANGED` | ✅ | ✅ | |
| Field removed | ✅ `TYPE_FIELD_REMOVED` | ✅ | ✅ | |
| Field added (breaking) | ✅ `TYPE_FIELD_ADDED` | ✅ | ✅ | |
| Field offset changed | ✅ `TYPE_FIELD_OFFSET_CHANGED` | ✅ | ✅ | |
| Field type changed | ✅ `TYPE_FIELD_TYPE_CHANGED` | ✅ | ✅ | |
| Base class changed | ✅ `TYPE_BASE_CHANGED` | ✅ | ✅ | |
| Vtable changed | ✅ `TYPE_VTABLE_CHANGED` | ✅ | ✅ | |
| Type removed | ✅ `TYPE_REMOVED` | ✅ | ✅ | |
| Type added | ✅ `TYPE_ADDED` | ✅ | ✅ | |
| SONAME missing | ✅ case05 | ✅ | ✅ | ELF policy |
| Symbol visibility leak | ✅ case06 | ✅ | ✅ | ELF policy |
| Symbol versioning missing | ✅ case13 | ✅ | ✅ | ELF policy |
| Dependency ABI leak | ✅ case18 | ⚠️ partial | ⚠️ partial | Via transitive header analysis |

---

## GAPS — Closed (historical, now implemented)

> **All P0, P1, and P2 gaps are now closed.** The following sections preserve historical context for previously uncovered areas.

## Historical GAPS (now closed) — what abicheck previously did not cover

### P0 — Critical (binary ABI breaks silently missed)

| Case | ABICC | abidiff | Notes | Impact |
|------|-------|---------|-------|--------|
| **Method became static / non-static** | ✅ `Method_Became_Static` | ✅ | Changes mangled name (static lacks implicit `this`) → old binaries get `undefined symbol`. `FUNC_STATIC_CHANGED` covers both directions. | Crash at runtime |
| **Method became const / non-const** | ✅ `Method_Became_Const` | ✅ | Itanium ABI encodes cv-qualifier on `this` (`_ZNK...` for const) | `undefined symbol` |
| **Method became volatile / non-volatile** | ✅ `Method_Became_Volatile` | ✅ | Part of mangled name; rare in practice but still a hard ABI break | `undefined symbol` |
| **Enum member value changed** | ✅ `Enum_Member_Value` | ✅ | Old binaries pass stale integer value → switch corruption in library. (Note: technically UB only if library switch has no default; guaranteed behavioral mismatch regardless.) | Silent corruption |
| **Virtual method position changed** | ✅ `Virtual_Method_Position` | ✅ | vtable slot reorder — old binary calls wrong function via stale slot index. No symbol error. Current scope: single-inheritance detection only; full multi-inheritance requires hierarchy-aware vtable reconstruction. | Silent corruption |
| **Added pure virtual method** | ✅ `Added_Pure_Virtual_Method` | ✅ | Old derived class vtable has null/placeholder slot for the new pure virtual → null function pointer call at runtime. Distinct from "added virtual". | Crash at runtime |
| **Enum member removed** | ✅ `Enum_Member_Removed` | ✅ | Old binaries pass removed enum value → potential UB in library switch statements; guaranteed behavioral mismatch. | Silent corruption |
| **Union field changes** | ✅ `Added/Removed_Union_Field` | ✅ | abicheck detects union size change but NOT field-level changes. castxml exposes union members; gap is in checker, not data availability. | Missed layout bugs |
| **Virtual method became pure** | ✅ `Virtual_Method_Became_Pure` | ✅ | Adding `= 0` to existing virtual: old derived class vtable has no implementation slot → null pointer call. Same severity as "added pure virtual". *(Promoted from P1.)* | Crash at runtime |
| **Base class position reordered** | ✅ `Base_Class_Position` | ✅ | `this` pointer adjustment offsets change → existing binaries calling methods on wrong base silently corrupt memory. Multiple inheritance scenario. *(Promoted from P1.)* | Silent corruption |

### P1 — Important (real-world ABI issues, not always immediate crashes)

| Case | ABICC | abidiff | Notes |
|------|-------|---------|-------|
| **Function became deleted** (`= delete`) | ✅ | ❌ | Hard break: previously callable function now deleted. Old binaries fail at link or runtime. |
| **Enum member renamed** (same value) | ✅ `Enum_Member_Name` | ❌ | Source break, semantic confusion |
| **Enum last member value changed** | ✅ `Enum_Last_Member_Value` | ✅ | Boundary/sentinel value changes break switch ranges |
| **Parameter default value changed/removed** | ✅ `Parameter_Default_Value_Changed` | ❌ | Source-level break; old callers pass stale defaults |
| **Global data value changed** (initial value) | ✅ `Global_Data_Value_Changed` | ✅ | Old binaries use compile-time-inlined old value |
| **Global data became const / non-const** | ✅ `Global_Data_Became_Const` | ✅ | Write to now-const data → SIGSEGV |
| **Typedef base type changed** | ✅ `Typedef_BaseType` | ✅ | `typedef int T` → `typedef long T` — size/semantic change. **Note: treat as P0 for library CI** (dimension typedefs, primitive impl typedefs). |
| **Type became opaque** | ✅ `Type_Became_Opaque` | ✅ | Was complete struct, now forward-decl only; breaks stack allocation |
| **Anonymous struct/union changes** | ✅ | ✅ (test44,45) | `ANON_FIELD_CHANGED` and castxml anonymous-field expansion cover nested anonymous members. |
| **Base class became virtual/non-virtual** | ✅ `Base_Class_Became_Virtually_Inherited` | ✅ | Diamond inheritance layout change |
| **Destructor ABI changes** | ✅ | ✅ | Itanium ABI has D0/D1/D2 destructors with separate vtable slots. Adding/removing virtual destructor, or trivial→non-trivial change, has specific ABI impact. |

### P2 — Nice to have (completeness / tooling quality)

| Case | ABICC | abidiff | abicheck | Notes |
|------|-------|---------|---------------------|-------|
| **Renamed field** | ✅ `Renamed_Field` | ❌ | ✅ `FIELD_RENAMED` | Heuristic: same offset+type, different name |
| **Renamed parameter** | ✅ `Renamed_Parameter` | ❌ | ✅ `PARAM_RENAMED` | Same type+position, different name |
| **Field became mutable** | ✅ `Field_Became_Mutable` | ❌ | ✅ `FIELD_BECAME_MUTABLE` | |
| **Field became volatile** | ✅ `Field_Became_Volatile` | ❌ | ✅ `FIELD_BECAME_VOLATILE` | |
| **Field became const** | ✅ `Field_Became_Const` | ❌ | ✅ `FIELD_BECAME_CONST` | |
| **Return type pointer level change** | ✅ | ✅ | ✅ `RETURN_POINTER_LEVEL_CHANGED` | `T*` → `T**` |
| **Parameter pointer level change** | ✅ | ✅ | ✅ `PARAM_POINTER_LEVEL_CHANGED` | Missed dereference depth |
| **Symbol alias handling** | ⚠️ | ✅ (test18) | ⚠️ | Alias vs real symbol distinction |
| **Calling convention changes** | ✅ (register/stack) | ✅ | ✅ `CALLING_CONVENTION_CHANGED` (DWARF) | Headers-only: undetectable; DWARF: ✅ |
| **Cross-architecture ABI diff** | ❌ | ✅ (test23) | ❌ | Out of scope: 32-bit vs 64-bit comparison |
| **Bitfield layout changes** | ✅ | ✅ | ✅ `FIELD_BITFIELD_CHANGED` | |
| **Constant added/removed/changed** | ✅ | ❌ | ⚠️ | `#define` / `constexpr` constant changes |
| **Anonymous struct/union** | ⚠️ | ✅ (test44,45) | ✅ `ANON_FIELD_CHANGED` | Supported |
| **Template instantiation ABI** | ⚠️ | ⚠️ | ⚠️ | Partial: explicit instantiations via ELF symtab |
| **Move constructor/assignment ABI** | ❌ | ✅ | ❌ | Out of scope: requires binary analysis |
| **CRC/ABI fingerprint** | ❌ | ✅ | ❌ | Kernel modules — out of scope |
| **BTF/CTF format support** | ❌ | ✅ | ❌ | Kernel/BPF use cases — out of scope |

---

## Open Issues in Upstream Projects

### ABICC (lvc/abi-compliance-checker)

> ABICC's feature set is effectively **frozen** (last release 2023, very low issue velocity on GitHub). The 90+ rules in `RulesBin.xml` represent a stable, complete catalog — all major C++ ABI break patterns are already enumerated there. Open issues (#132-#136) are toolchain/maintenance items, not feature requests.
>
> **Opportunity for abicheck:** implement ABICC's full rule catalog with a modern, CI-friendly architecture. Key differentiators we can offer that ABICC doesn't: no GCC dependency, header-only analysis, structured JSON output, suppression files, Python API.

### libabigail (sourceware.org)

libabigail is actively maintained. Key themes from recent work:

- **PR24552**: Qualified type handling (const/volatile array folding) — affects field type change detection
- **PR27985**: Anonymous struct/union diff accuracy — relevant to our P1 gap
- **PR27616**: Compressed diff output for large libraries — output format inspiration
- **PR25058**: Real-world lttng-ctl regression test
- **PR18166/18791**: libtirpc/complex type diffs

libabigail's focus is DWARF accuracy and kernel/BTF support. Our headers-based approach is complementary, not competing.

---

## Architecture: abicheck vs abidiff

```text
abicheck workflow:         abidiff workflow:
  headers + .so             debug .so (with DWARF)
       ↓                         ↓
  castxml (Clang AST)       DWARF parser
       ↓                         ↓
  type graph                type graph
       ↓                         ↓
  binary metadata          DWARF symtab
       ↓                         ↓
  diff engine               diff engine
```

**Unique advantage:** Release builds (no `-g`) + headers → works in CI/CD without debug artifacts.

**Limitations:**
- Cannot detect calling-convention register/stack changes (not in AST)
- **Header/binary mismatch risk:** if the headers used for analysis don't exactly match what was compiled (e.g., internal headers were used during build), castxml produces a different view than what's in the binary. This is a fundamental correctness risk — abicheck results are only as accurate as the provided headers.
- Cannot detect inline function body changes (inlined calls disappear from symtab)
- Exception handling table changes (`.eh_frame`/LSDA) are binary-level only

---

## Coverage Summary Table

| Category | abicheck (current) | ABICC | abidiff |
|----------|-------------------------|-------|---------|
| Function symbol ABI | 12/12 | 12/12 | 10/12 |
| Type/struct layout | 10/10 | 10/10 | 10/10 |
| C++ vtable | 5/5 | 5/5 | 5/5 |
| Enums | 5/5 | 5/5 | 3/5 |
| Qualifiers (const/volatile/mutable) | 8/8 | 6/8 | 4/8 |
| ELF/policy | 4/4 | 3/4 | 4/4 |
| Union | 4/4 | 4/4 | 4/4 |
| Calling convention (DWARF) | 3/4 | 4/4 | 4/4 |
| Pointer level changes | 2/2 | 2/2 | 2/2 |
| Access level changes | 2/2 | 2/2 | 0/2 |
| Param defaults | 2/2 | 2/2 | 0/2 |
| Field/param renames | 2/2 | 2/2 | 0/2 |
| Anonymous struct/union | 1/1 | 0/1 | 1/1 |
| **Total** | **~55/55 (100%)** | **~48/55** | **~44/55** |

> Current implementation closes all remaining gaps in this matrix. abicheck now exceeds ABICC coverage:
> - ABICC lacks: anonymous struct field tracking, combined access+qualifier detection
> - abidiff lacks: enum renames, param defaults, access level changes, field/param renames
> - Remaining non-core parity items are tracked as separate workflow gaps where applicable: cross-architecture guardrails are G13, while kernel BTF/CTF type-layout workflows are now covered by G6.

---

## Cases 171–181: modern-detector coverage vs ABICC/abidiff

The original 55-scenario matrix above predates a set of newer detector
families added by the G23 work (loader/runtime, kernel kABI, deep C++
multiple-inheritance layout, security hardening, source-graph cross-checks).
Cases 171–181 in the example catalog were added to give each of these a
concrete demonstration; running them through `scripts/benchmark_comparison.py`
against real `abidiff` 2.4.0 and `abi-compliance-checker` 2.3 (frozen results
in `scripts/frozen_competitor_results.json`) shows how much of this newer
surface those tools cover at all:

| ChangeKind | Expected | abicheck | abidiff 2.4.0 | ABICC 2.3 (`abi-dumper`) |
|---|---|---|---|---|
| `static_tls_introduced` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ❌ NO_CHANGE | ❌ COMPATIBLE |
| `vtable_thunk_offset_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING |
| `vtt_slot_count_changed` | BREAKING | ✅ BREAKING | ❌ COMPATIBLE | ✅ BREAKING |
| `secondary_vtable_group_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING |
| `kabi_crc_changed` | BREAKING | ✅ (verified directly — see note) | N/A — no `Module.symvers` concept | N/A — no `Module.symvers` concept |
| `kabi_symbol_namespace_changed` | BREAKING | ✅ (verified directly — see note) | N/A — no `Module.symvers` concept | N/A — no `Module.symvers` concept |
| `long_double_abi_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING |
| `unnamed_type_in_public_abi` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ❌ COMPATIBLE | ❌ COMPATIBLE |
| `cet_protection_weakened` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ❌ NO_CHANGE | ❌ COMPATIBLE |
| `symbol_binding_lost_unique` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ❌ NO_CHANGE | ❌ COMPATIBLE |
| `public_to_internal_dependency` | (L5 cross-check finding) | ✅ MATCH | N/A — no L5 source-graph concept | N/A — no L5 source-graph concept |

**abicheck: 9/9 scored (100%)** — the two kABI rows are excluded from the
harness's own scoring because `benchmark_comparison.py` only knows how to
drive compiled-`.so` cases; it has no `Module.symvers` input path, so it
reports `NO_SOURCE` for both tools *including abicheck's own column* in the
raw run. That is a benchmark-harness gap, not a detection gap: `abicheck
compare v1.symvers v2.symvers` correctly reports `kabi_crc_changed` /
`kabi_symbol_namespace_changed` for both (verified directly — see
`examples/case175_kabi_crc_changed/README.md` and
`examples/case176_kabi_symbol_namespace_changed/README.md` — and covered by
`tests/test_kabi_examples.py`).

**abidiff: 3/8 scored (37%), ABICC: 4/8 scored (50%).** Both misses cluster in
the same place: neither tool reads anything outside DWARF type/layout info +
symbol table presence, so ELF dynamic-section/GNU-property facts
(`DF_STATIC_TLS`, `.note.gnu.property` CET bits, `STB_GNU_UNIQUE` binding) are
invisible to both — they report `NO_CHANGE`/`COMPATIBLE` on a real security or
loader-contract regression. `vtt_slot_count_changed` is the one case where
abidiff's own binary-diff heuristics miss a signal abicheck and ABICC's
dumper both catch (the `_ZTT` construction-vtable size change). Neither tool
has any concept of Linux kABI manifests or an L5 source-dependency graph, so
those rows are a structural "N/A", not a false negative — nothing to score
against.

### abicheck's own modes on the same 11 cases

The table above uses abicheck's native `compare` command (the actual product
surface for these cases — 9/9, 100%). abicheck ships three other entry
points benchmarked alongside it; their scores on this batch aren't a second
opinion on detection capability, they're each mode doing exactly what it's
designed to do:

| ChangeKind | Expected | `abicheck compare` | `abicheck compat` | `compat -s` (strict) | `abicheck_full` (L3-L5 plugin) |
|---|---|---|---|---|---|
| `static_tls_introduced` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | BREAKING | ERROR (infra) |
| `vtable_thunk_offset_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING | ERROR (infra) |
| `vtt_slot_count_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING | ERROR (infra) |
| `secondary_vtable_group_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING | ERROR (infra) |
| `long_double_abi_changed` | BREAKING | ✅ BREAKING | ✅ BREAKING | ✅ BREAKING | ERROR (infra) |
| `unnamed_type_in_public_abi` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | BREAKING | ERROR (infra) |
| `cet_protection_weakened` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | BREAKING | ERROR (infra) |
| `symbol_binding_lost_unique` | COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | ✅ COMPATIBLE_WITH_RISK | BREAKING | ERROR (infra) |

- **`abicheck compat`** (`abicheck/compat/cli.py`, the ABICC drop-in-replacement
  CLI — takes ABICC-format XML descriptors, invoked via `compat check`) —
  **8/8 (100%), same detection accuracy as native `compare`.** Its *report
  text* prints the precise verdict (`Verdict: COMPATIBLE_WITH_RISK`) for all
  four RISK cases, confirmed by inspecting the raw output directly — nothing
  about compat mode's detection is coarser. What *is* coarser, by design, is
  its **exit code**: compat mode's exit scheme (0/1/2, mirroring ABICC's own
  compatible/incompatible/API-break codes) has no separate "risk" bucket, so
  `COMPATIBLE_WITH_RISK` and `COMPATIBLE` both exit 0 — a CI gate keyed only
  on exit code, not report text, would treat them the same. (This table
  originally under-reported compat mode as 4/8 because
  `scripts/benchmark_comparison.py`'s own scoring script only parsed the exit
  code plus a couple of substring checks and never looked for
  `compatible_with_risk` in the text — a benchmark-harness bug, now fixed;
  it did not reflect a real gap in the tool.)
- **`compat -s`/strict** (the `-s` strict flag, documented to promote
  `API_BREAK` → `BREAKING`) — **4/8 (50%), and notably these are the same
  four RISK cases, now promoted via the exit code all the way to `BREAKING`
  (confirmed: exit code 1, not just report text).** That's a wider promotion
  than the flag's stated `API_BREAK`→`BREAKING` description — strict mode is
  treating any non-`NO_CHANGE` finding as a hard release gate, which is a
  legitimate "when in doubt, block the release" security-conscious policy
  choice for a CI gate, but means strict mode over-calls exactly the four
  cases whose whole teaching point is "compatible but should not silently
  ship" (CET, TLS, GNU_UNIQUE, the lambda leak) as full breaks — worth
  knowing if you wire `-s` into a release gate expecting graduated severity
  rather than a binary pass/fail.
- **`abicheck_full`** (the Clang-plugin-instrumented L3–L5 lane, builds each
  case with `contrib/abicheck-clang-plugin` and merges the resulting source
  pack before comparing) — reported `ERROR` for every case in this run,
  **including pre-existing catalog cases (verified against case01/case02)**,
  because the clang plugin wasn't built in the environment that produced
  this table. This is a local toolchain gap in how the table was generated,
  not a result to read anything into; rerun with the plugin built
  (`contrib/abicheck-clang-plugin/`) to get real L3+ numbers for this batch.

Reproduce: `PYTHONPATH=. python3 scripts/benchmark_comparison.py --cases
case171 case172 case173 case174 case175 case176 case177 case178 case179
case180 case181` (add `--freeze abidiff abidiff_headers abicc_dumper
abicc_xml` only when re-running against the **full** catalog — passing it
alongside `--cases` overwrites the entire frozen file with just the
filtered subset).

---

## Upstream Issue Tracking

| Issue | Topic | Status | Evidence | Notes |
|------|-------|--------|----------|-------|
| [#100](https://github.com/lvc/abi-compliance-checker/issues/100) | `= delete` functions | **Covered; parity follow-up optional** | `tests/test_func_deleted.py` (`TestFuncDeletedDetection`, `TestFuncDeletedEdgeCases`) | Checker behavior is covered, including guarded ELF/DWARF fallback paths; additional ABICC fixture mirroring is optional parity polish rather than an open detector gap. |

# G24 — Linux ABI/API detection gap closure

**Origin:** ABI/API break-coverage evaluation (July 2026) — a sweep of the full
break universe against the `ChangeKind` catalog. The catalog has full
ABICC/libabigail scenario parity; the residual gaps below are in deep Itanium
C++ machinery, modern ELF metadata, toolchain extraction robustness, and
ecosystem-specific contracts. Phase A (ELF artifact facts) is implemented; the
remaining phases are scoped below.
**Effort:** phased — A: S–M per item · B: L–XL (phased) · C: M · D: S–M per item
**Risk:** low for A/C/D (additive detectors over already-parsed or cheap-to-parse
facts); medium for B (vtable reconstruction is genuinely hard; scoped tri-state
guards keep it from fabricating findings).

Windows and macOS gaps found in the same evaluation are **deferred** — recorded
in [Deferred: macOS / Windows](#deferred-macos--windows-later-stage) so they are
not lost, but the active work is Linux/ELF.

---

## Shared checklist — every new `ChangeKind` in this plan

Per the root `CLAUDE.md` procedure, each new kind must ship with **all** of:

1. Enum member in `checker_policy.py`, placed in exactly one of
   `BREAKING_KINDS` / `API_BREAK_KINDS` / `COMPATIBLE_KINDS` / `RISK_KINDS`
   (import-time assertion + `changekind-partition` gate).
2. A `change_registry.py` entry (verdict, `impact` text, `description_template`)
   — the registry is the single source of truth the policy/impact dicts derive
   from.
3. A detector in the appropriate `diff_*` module (`changekind-detector` gate:
   no orphaned kinds).
4. Unit tests over synthetic snapshots (fast lane, no external tools).
5. A docs mention (`docs/reference/change-kinds.md` or the owning feature page
   — `changekind-docs` gate), and the headline-count updates the
   `doc-count-sync` gate enforces (`len(ChangeKind)` counts quoted in docs).
6. Where fixture-backed: an `examples/caseNNN_*/` pair with `README.md` +
   `ground_truth.json` entry, then `python scripts/gen_examples_docs.py`
   (`examples-ground-truth` / `examples-readme-sync` gates).
7. Where the detector is heuristic (B1, D2, D3): a labelled FP-corpus case in
   the `check_fp_rate.py` corpus so the 0-FP baseline covers it, and a
   tier-accuracy case where the kind is tier-dependent.

---

## Phase A — ELF artifact facts (quick wins)

Additive captures in `elf_metadata.py` + diff rules in `diff_platform.py`.
Each item is independently landable.

### A1. Static-TLS drift (`DF_STATIC_TLS`)

**Problem.** A library that switches to initial-exec/local-exec TLS
(`-ftls-model=initial-exec`, or an LTO/optimization side effect) sets
`DF_STATIC_TLS` and can no longer be reliably `dlopen`ed — the loader may fail
with "cannot allocate memory in static TLS block". Today only the flag-level
L3 signal `TLS_MODEL_CHANGED` (RISK, build-evidence-only) exists; the fact is
**artifact-provable** from the binary itself and should not require an L3 pack.

**Detection.** Read `DT_FLAGS` for `DF_STATIC_TLS` (and record whether the
dynamic symbol table carries *any* `STT_TLS` entries — defined **or
undefined** — to suppress the finding for TLS-free libraries; an initial-exec
reference to an external `extern __thread` variable sets `DF_STATIC_TLS` with
no TLS definitions of its own, and that import-only case is exactly as
dlopen-hostile). Diff the tri-state in `diff_platform.py`.

**Kinds.**

- `STATIC_TLS_INTRODUCED` → **RISK** by default (breaks *dlopen* consumers, not
  link-time consumers), gateable to break via the plugin/security policy
  profiles. Cross-reference `TLS_MODEL_CHANGED` in the impact text: L3
  localizes the flag, this kind proves the artifact effect.
- `STATIC_TLS_REMOVED` → **COMPATIBLE** (quality/informational), mirroring the
  `EXECUTABLE_STACK_REMOVED` improved-counterpart convention.

**Tests & fixture.** Unit: synthetic ELF metadata pairs. Fixture:
`examples/caseNNN_static_tls_introduced/` built with
`-ftls-model=global-dynamic` vs `-ftls-model=initial-exec` (Linux gcc lane).

**Effort:** S.

### A2. `.note.gnu.property` hardening drift (CET / BTI / PAC)

**Problem.** The checksec surface (G12) covers RELRO/PIE/canary/FORTIFY/W^X
but not the modern control-flow protections carried in `PT_GNU_PROPERTY`:
x86 `GNU_PROPERTY_X86_FEATURE_1_AND` (IBT, SHSTK) and AArch64
`GNU_PROPERTY_AARCH64_FEATURE_1_AND` (BTI, PAC). A release that silently drops
`-fcf-protection` / `-mbranch-protection` weakens the process-wide guarantee
(a single non-IBT DSO can disable enforcement for the whole link map).

**Detection.** Parse the `.note.gnu.property` note in `elf_metadata.py`
(pyelftools exposes the note; decode the two feature words). Store as a set of
feature strings; diff per-feature in `diff_platform.py`.

**Kinds.**

- `CET_PROTECTION_WEAKENED` (IBT and/or SHSTK bit dropped) → **RISK**.
- `BRANCH_PROTECTION_WEAKENED` (BTI and/or PAC bit dropped) → **RISK**.
- `CET_PROTECTION_IMPROVED` / `BRANCH_PROTECTION_IMPROVED` → **COMPATIBLE**
  (informational counterparts, matching G12 style).

Extend `policies/security.yaml` so the security preset gates the two weakened
kinds to break.

**Tests & fixture.** Unit: synthetic property sets. Fixture: x86-64 pair built
with/without `-fcf-protection=full` (native on the Linux CI lane). AArch64
covered at unit level by a checked-in note blob (no cross-toolchain requirement
in CI).

**Effort:** S–M.

### A3. ELF identity / ABI-flags guard (extends G13)

**Problem.** PE has `PE_MACHINE_CHANGED` and Mach-O has
`MACHO_CPU_TYPE_CHANGED`, but the ELF side has no equivalent artifact check for
`e_machine`, `EI_CLASS` (32↔64-bit), `EI_OSABI`, or the per-arch ABI bits in
`e_flags` — which on ARM32/RISC-V/MIPS encode the **float ABI** (e.g.
`EF_ARM_ABI_FLOAT_HARD`, RISC-V float-ABI mask, MIPS ABI bits). Today float-ABI
drift is only the flag-level `FLOAT_ABI_CHANGED` (L3, RISK); the `e_flags`
fact makes it artifact-proven, the same promotion the plan applied to
`STRUCT_RETURN_CONVENTION_CHANGED`.

**Detection.** Capture `e_machine` / `EI_CLASS` / `EI_OSABI` / `e_flags` in the
ELF snapshot; decode the known per-arch ABI masks (ARM float ABI + EABI
version, RISC-V `EF_RISCV_FLOAT_ABI_*` + RVC/RVE, MIPS ABI/arch bits); diff in
`diff_platform.py`. Undecoded architectures diff the raw masked value with a
generic description.

**Kinds.**

- `ELF_MACHINE_CHANGED` → **BREAKING** (different architecture = different
  binary contract; also acts as the compare-input guardrail G13 wanted).
- `ELF_CLASS_CHANGED` (32↔64-bit) → **BREAKING**.
- `ELF_ABI_FLAGS_CHANGED` (decoded float-ABI/EABI drift) → **BREAKING**;
  impact text cross-references `FLOAT_ABI_CHANGED` (flag-level RISK stays the
  explanatory signal, this is the artifact proof).
- `ELF_OSABI_CHANGED` → **RISK**.

**Tests & fixture.** Unit: synthetic header fields (the malformed-input test
helpers already patch ELF headers). Fixture: none required in CI (cross
toolchains unavailable); a checked-in pre-built pair is optional follow-up.

**Effort:** S–M.

### A4. `STB_GNU_UNIQUE` binding transitions

**Problem.** `SYMBOL_BINDING_CHANGED` / `SYMBOL_BINDING_STRENGTHENED` model
GLOBAL↔WEAK, but not GNU unique symbols. A symbol becoming `STB_GNU_UNIQUE`
(inline statics / template statics under `-fgnu-unique`) changes loader
semantics: uniqueness is enforced process-wide and the object becomes
non-unloadable (`dlclose` is inhibited). Dropping uniqueness re-introduces
per-DSO duplication for consumers that relied on it.

**Detection.** The binding is already read from `.dynsym`; extend the
classification to route UNIQUE transitions to dedicated kinds instead of the
generic GLOBAL/WEAK pair.

**Kinds.**

- `SYMBOL_BINDING_BECAME_UNIQUE` → **RISK** (dlclose inhibition, process-wide
  uniqueness semantics).
- `SYMBOL_BINDING_LOST_UNIQUE` → **RISK** (ODR-uniqueness guarantee consumers
  may depend on disappears).

**Tests & fixture.** Unit: synthetic binding pairs. Fixture: pair built with
`-fgnu-unique` / `-fno-gnu-unique` exporting an inline static (gcc lane).

**Effort:** S.

---

## Phase B — Itanium multi-inheritance vtable machinery (flagship gap)

**Problem.** Virtual-method *position* detection is documented as
single-inheritance-only ([parity status](../abicc-parity-status.md)). Nothing
diffs the multi-inheritance/virtual-base machinery the Itanium ABI generates:
secondary vtables, non-virtual/virtual thunks (`_ZThn…` / `_ZTv…`), covariant
return thunks (`_ZTch…`), VTTs (`_ZTT`), and construction vtables (`_ZTC`). A
vtable reorder confined to a secondary base, or a virtual-base offset change,
silently corrupts calls with **no symbol error** — exactly the highest-severity
class of break. `buildsource/source_link.py` already *classifies* these symbol
categories for provenance; nothing *diffs* them.

Deliberately phased: B1 is a symbol-size/set diff in the proven
`diff_elf_layout.py` style (works on **stripped** binaries); B2 is the DWARF
reconstruction that names the exact slot.

### B1 — L0 thunk / VTT surface diff (M)

**Detection** (extend `diff_elf_layout.py`, same guarded style as
`VTABLE_SLOT_COUNT_CHANGED` / `RTTI_INHERITANCE_CHANGED`):

- Group exported thunk symbols by (class, target method) from their mangled
  form: `_ZThn<offset>_…` (non-virtual), `_ZTv<voffset>_…` (virtual),
  `_ZTch…` (covariant). Diff the per-class sets **and the encoded offsets**:
  a thunk appearing/disappearing, or its `n<offset>` changing, proves a base
  subobject moved or the hierarchy shape changed — even with identical `_ZTV`
  size.
- Diff `_ZTT<class>` symbol size → VTT slot count (virtual-base construction
  scaffolding changed).
- Diff the set of `_ZTC` (construction vtable) symbols per class as
  supporting detail on the VTT finding.

**Kinds.**

- `VTABLE_THUNK_OFFSET_CHANGED` → **BREAKING** (a `this`-adjustment encoded in
  old consumers' vtables is now wrong).
- `VTABLE_THUNK_SET_CHANGED` (thunk added/removed for an existing class) →
  **BREAKING**.
- `VTT_SLOT_COUNT_CHANGED` → **BREAKING**.

**Interaction with existing kinds.** `diff_filtering.py` must dedupe these
against `VTABLE_SLOT_COUNT_CHANGED` / `TYPE_VTABLE_CHANGED` when both fire for
the same class (keep the most specific finding, same policy as today's
layout-kind dedup).

**Tests & fixtures.** Unit: synthetic symbol tables. Fixtures (gcc lane):
diamond hierarchy where a secondary base gains a virtual method; a base
reorder that shifts thunk offsets with unchanged `_ZTV` sizes (the case today's
L0 diff provably misses — this is the acceptance fixture).

### B2 — L1 DWARF vtable reconstruction (L) — **done**

**Landed.** Implemented in `diff_vtable_layout.py` as a registry detector,
reconstructing per-class vtable-group structure at *diff time* from the
inheritance fields already on `RecordType` (`bases` / `virtual_bases` / `vtable`)
plus the snapshot's type map — no DWARF/serialization re-plumbing. Two new kinds,
both BREAKING and both scoped to gaps the existing per-type diff cannot reach:
`secondary_vtable_group_changed` (a base's *polymorphism* changed, restructuring
the derived class's secondary groups even though its own base list is unchanged —
a cross-type effect) and `virtual_base_offset_changed` (a same-set virtual-base
reorder, invisible to the non-virtual `base_class_position_changed`). Every
reconstruction is tri-state guarded: an indeterminate base (absent on that side)
yields `None` and emits nothing, degrading to B1's L0 view. The originally-planned
virtual-base *offset* modelling and secondary-group `TYPE_VTABLE_CHANGED`
localization are deferred — the constant vbase offset is not reliably
DWARF-derivable, and the two new kinds already cover the structural breaks.

*Accepted limitation:* a base whose vtable is captured in only one side's DWARF
(its virtual methods live in a CU present in only one library) flips
`_is_polymorphic` and is locally indistinguishable from a base that genuinely
gained/lost virtuals — both read as an empty→populated vtable. The
entirely-missing-base case is already tri-state-skipped; this residual
asymmetric-capture case would need cross-CU completeness tracking, and any real
change is independently reported on the base type itself.

**Original design.** New module `vtable_layout.py` (keeps `dwarf_metadata.py`
under the size cap):

1. Reconstruct per-class vtable groups from DWARF: primary + one secondary
   group per non-primary polymorphic base, ordered by
   `DW_TAG_inheritance` → `DW_AT_data_member_location`, with
   `DW_AT_virtuality` marking virtual bases; slot order within a group from
   `DW_AT_vtable_elem_location` on member functions, walking the base-class
   chain for inherited slots.
2. Model virtual-base offsets (vbase offset entries) per the Itanium layout
   algorithm; store the reconstruction on the snapshot behind optional fields
   (tri-state guarded, like the layout-closure work — absent evidence must
   never fabricate a finding, and an evidence-tier downgrade must degrade to
   B1's L0 findings, never invert).
3. Diff in a new `diff_vtable_layout.py`.

**Kinds.**

- Extend the existing single-inheritance position detector to secondary
  groups — reuses `TYPE_VTABLE_CHANGED` with a slot-precise description
  (no new kind; the verdict is the same, the *localization* improves).
- `VIRTUAL_BASE_OFFSET_CHANGED` → **BREAKING** (vbase pointer adjustments in
  old binaries land on the wrong subobject).
- `SECONDARY_VTABLE_GROUP_CHANGED` (group added/removed/reordered for an
  existing class) → **BREAKING**.

**Cross-tier wiring.** Add the diamond/secondary-base cases to the
`check_tier_accuracy.py` corpus: L0 sees the thunk/VTT delta (B1), L1 names the
slot (B2) — the under-call monotonicity gate then *proves* the layering claim.
Mutation-test scope: add `diff_vtable_layout` to the mutmut paths.

**Out of scope for B.** Full vtable dumps for virtual inheritance under
alternative ABIs (MSVC vtordisp etc.) — Itanium/Linux only; MSVC parity is a
deferred Windows item.

**Effort:** B1 M, B2 L. **Risk:** medium — reconstruction correctness. Mitigate
by validating the reconstructed slot counts against the L0 `_ZTV` sizes on
every fixture (self-cross-check assertion in tests).

---

## Phase C — clang toolchain-flag extraction robustness (M) — **done**

**Landed.** The producer scan already matched clang's `-grecord-command-line`
form (verified end-to-end: `enum_underlying_size_changed` +
`toolchain_flag_drift` both fire on a clang pair built with
`-grecord-command-line -fshort-enums`). The remaining gap — flags recorded on a
non-first CU being dropped — is closed: `dwarf_advanced._process_cu` now unions
`abi_flags`/`vector_abi_flags` across every CU instead of "first CU wins". Steps
2–3 (L3 fallback, `not_collected` coverage honesty) are left as future work; the
default-clang false negative is resolved by step 1.

**Problem.** `TOOLCHAIN_FLAG_DRIFT` (and `case…` fixtures with
`known_gap_toolchains: clang`) read compiler flags from `DW_AT_producer`. GCC
records them by default; clang records them **only** with
`-grecord-command-line`, so on default clang builds the detector is blind and
returns NO_CHANGE. This is a real-world false negative on the majority
toolchain of several ecosystems.

**Steps.**

1. **Widen the producer scan:** accept clang's `-grecord-command-line` form,
   and scan *all* CUs (flags can differ per TU; report the union + flag
   conflicts).
2. **L3 fallback into the same detector:** when producer strings carry no
   flags but an L3 build pack (`compile_commands`-derived flags) is present,
   feed those flags to the same drift detector and emit
   `TOOLCHAIN_FLAG_DRIFT` with the evidence source recorded in the finding
   detail (L3-sourced findings stay RISK per ADR-028 D3 — this is already the
   kind's default, so no verdict special-casing).
3. **Coverage honesty:** when neither producer flags nor L3 evidence exist,
   record flag-coverage as `not_collected` in the scan coverage block (the
   same mechanism the L2 header-context warning uses), so users know the
   drift check did not run rather than passed.
4. **Docs + ground truth:** update the affected `ground_truth.json`
   `known_gap_toolchains` entries once the clang lane goes green with
   `-grecord-command-line` fixtures, and document the flag in the
   scan-levels user guide.

**Files.** `dwarf_metadata.py` (producer scan), `buildsource/` adapter →
detector plumbing, `dwarf_advanced.py` (drift detector), docs.

**Effort:** M. No new kinds.

---

## Phase D — ecosystem detectors

### D1. Kernel module kABI — `Module.symvers` / genksyms CRC (M)

**Problem.** BTF/CTF parsing exists (`btf_metadata.py`, `ctf_metadata.py`),
but the canonical kernel-ABI stability signal — exported-symbol CRCs from
genksyms/`Module.symvers`, the thing distro kABI guarantees are built on — has
no support.

**Design.** New adapter `symvers_metadata.py` parsing `Module.symvers` (TSV:
CRC, symbol, module, export type, **namespace** — the fifth column may be
empty, and pre-5.4 kernels omit it entirely, so the parser accepts both the
4- and 5-field forms; see
[kbuild/modules](https://docs.kernel.org/kbuild/modules.html)). Accept a
symvers file as a compare input side (precedent: `debian_symbols.py`
adapter). Diff:

- `KABI_SYMBOL_REMOVED` → **BREAKING** (out-of-tree modules fail to load).
- `KABI_CRC_CHANGED` → **BREAKING** (modversions reject the module even though
  the symbol exists — the type signature changed).
- `KABI_EXPORT_TYPE_CHANGED` (`EXPORT_SYMBOL` ↔ `EXPORT_SYMBOL_GPL`) →
  **API_BREAK** (license-gated availability change; loads fail only for
  non-GPL consumers).
- `KABI_SYMBOL_NAMESPACE_CHANGED` (symbol gained/moved its export namespace,
  `EXPORT_SYMBOL_NS*`) → **BREAKING** for gain/move (modules without the
  matching `MODULE_IMPORT_NS` fail to load); dropping the namespace is
  **COMPATIBLE**.
- `KABI_SYMBOL_ADDED` → **COMPATIBLE**.

Where BTF is also supplied, cross-reference the CRC finding with the BTF type
diff in the finding detail (localize *what* changed, not just that the CRC
did).

**Tests & fixtures.** Unit: synthetic symvers text pairs (no kernel build
needed — the format is trivial and stable). Example case with two checked-in
symvers files.

**Effort:** M (mostly plumbing an input kind through `service.resolve_input`).

### D2. `long double` ABI transition (S–M)

**Problem.** ppc64le IEEE128↔IBM double-double migrations (and
`-mlong-double-64`) change the floating-point representation behind the same
source type. The Itanium mangling distinguishes them (`e` vs `g` vs
`u9__ieee128`), so the break *is* visible in symbols — but today it surfaces
as an unexplained `FUNC_REMOVED`+`FUNC_ADDED` pair rather than a named,
policy-addressable transition.

**Detection.** Two complementary paths, because not every long-double
transition changes the mangled name:

1. *Symbol-pair path* (mangling changes): in the removed↔added pairing pass
   (`binary_fingerprint.py` / rename-detection precedent), when a removed/added
   pair demangles to the same signature except for a long-double-family
   mangling substitution (`e` ↔ `g` ↔ `u9__ieee128`), emit the specific kind
   instead of the generic `FUNC_REMOVED`+`FUNC_ADDED`. This covers the ppc64
   IEEE128↔IBM double-double case.
2. *Same-symbol path* (mangling unchanged): `-mlong-double-64` on x86-64 keeps
   the **same** mangled name (`_Z1fe` both before and after) while changing the
   representation from 80-bit to 64-bit — so there is no removed/added pair to
   match, and path 1 alone would report nothing. Detect this at L1 from the
   `DW_AT_byte_size` of the `long double` parameter/return type on a symbol that
   persists across versions (a size change on a type whose `DW_AT_encoding` is
   float and whose source name is `long double`), or from an L3 `-mlong-double-*`
   flag flip. Emit the same kind.

**Kind.** `LONG_DOUBLE_ABI_CHANGED` → **BREAKING**.

**Tests.** Unit: synthetic mangled pairs (`_Z1fe` vs `_Z1fu9__ieee128`); no
cross-compilation needed. FP-corpus case: a genuine rename that happens to
touch an `e` in the mangling must not match (pairing requires the
long-double token substitution specifically).

### D3. Unnamed-type (lambda) leakage into the public ABI (S)

**Problem.** Mangled names containing unnamed-type components (`Ul…E_` lambda,
`Ut_` unnamed struct) are TU- and compiler-ordering-fragile: recompiling can
renumber them, so exporting them is an ABI time bomb. This is a *hygiene*
anti-pattern, best caught before it breaks.

**Detection.** Single-snapshot audit (the `surface-report` /
`crosscheck.py` anti-pattern precedent, like
`POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR`): flag exported symbols whose mangling
contains unnamed-type components. At diff time, report only when **newly
introduced** (same rule as the other single-snapshot RISK kinds).

**Kind.** `UNNAMED_TYPE_IN_PUBLIC_ABI` → **RISK**.

**Tests.** Unit: synthetic symbol names. Fixture: exported function returning
`auto` lambda-holder (gcc lane).

---

## Sequencing

| Milestone | Contents | New kinds | Effort | Status |
|---|---|---|---|---|
| M1 | A1–A4 (ELF facts) | 12 | 4 × S–M, independently landable | **done** |
| M2 | B1 (L0 thunk/VTT diff) | 3 | M | **done** |
| M3 | B2 (DWARF vtable reconstruction) | 2 | L | **done** |
| M4 | C (clang flag extraction) | 0 | M | **done** |
| M5 | D1–D3 (kABI, long double, unnamed types) | 7 | M + S–M + S | **done** |

Every milestone leaves the gates green: partition assertion, detector/docs
coverage, doc-count-sync headline counts, FP-rate and tier-accuracy corpora
where applicable.

## Acceptance criteria (plan-level)

- [x] **Phase A implemented** — 12 kinds (`static_tls_introduced`/`_removed`,
      `cet_protection_weakened`/`_improved`, `branch_protection_weakened`/`_improved`,
      `elf_machine_changed`, `elf_class_changed`, `elf_abi_flags_changed`,
      `elf_osabi_changed`, `symbol_binding_became_unique`/`_lost_unique`) with
      ELF metadata capture, registry entries, detectors, evidence-tier mapping,
      security-policy gating for the hardening kinds, and unit tests
      (`tests/test_g23_elf_facts.py`). All AI-readiness/mypy/ruff/FP-rate/tier
      gates green.
- [x] **Phase B1 implemented** — 3 kinds (`vtable_thunk_offset_changed`,
      `vtable_thunk_set_changed`, `vtt_slot_count_changed`) in `diff_elf_layout.py`
      from `.dynsym` thunk/VTT names + sizes; thunk symbols excluded from the
      generic func surface so they don't double-count. The acceptance fixture —
      a multi-inheritance base reorder that shifts a secondary-base override's
      thunk offset with unchanged `_ZTV` size — is BREAKING on a **stripped**
      pair (verified end-to-end via `abicheck compare`; `tests/test_g23_vtable_b1.py`).
- [x] **Phase B2 implemented** — 2 kinds (`secondary_vtable_group_changed`,
      `virtual_base_offset_changed`) in `diff_vtable_layout.py`, reconstructing
      vtable-group structure from DWARF inheritance with tri-state guarding, plus
      registry/tier/docs/completeness/architecture wiring and unit tests
      (`tests/test_g23_vtable_b2.py`). Deferred within B2: constant vbase-offset
      modelling (not reliably DWARF-derivable) and secondary-group
      `TYPE_VTABLE_CHANGED` slot localization.
- [x] **Phase C implemented** — the producer scan matches clang's
      `-grecord-command-line` form and now unions `abi_flags` across all CUs;
      `toolchain_flag_drift` fires on a clang `-grecord-command-line` fixture
      pair (`tests/test_compiler_record_cross_toolchain.py`). L3-flag fallback
      with no producer flags is deferred.
- [x] Each Phase B/D kind lands with the full shared checklist (enum, registry,
      detector, evidence tier, completeness test, docs, unit tests).
- [x] `-fcf-protection` removal fails under the security policy preset
      (`cet_protection_weakened` gated in `policies/security.yaml`).
- [x] A `Module.symvers` pair with one CRC drift returns BREAKING with the
      symbol named (`tests/test_g23_phase_d.py::TestKabiDiff`).

## Deferred: macOS / Windows (later stage)

Recorded from the same evaluation; **not** part of this plan's active scope.

| Platform | Gap | Notes |
|---|---|---|
| Windows/PE | Export **ordinal renumbering** of named exports | Clients linked by ordinal (`.def` `NONAME` or import-by-ordinal) break silently; ordinals are already parsed, only the diff is missing. |
| Windows/PE | Delay-load import drift | New/changed delay-load DLL deps change failure timing (load → first call). |
| Windows/MinGW | Unwind model mismatch (SJLJ / DWARF / SEH) | Classic cross-runtime crash; `_CPPUNWIND` is scanned at L3 but no artifact-level check exists. |
| Windows/MSVC | Promote the `windows-msvc` e2e lane to blocking; castxml+`cl.exe` header path validation | Tracked in [backlog](../backlog.md). |
| macOS | `LC_ID_DYLIB` `current_version` diff | Parsed in `macho_metadata.py`, never diffed (`compat_version` is). |
| macOS | `LC_REEXPORT_DYLIB` drop | Parsed, never diffed — dropping a re-export removes every re-exported symbol from the umbrella surface without any per-symbol finding. |
| macOS | Weak-import / weak-def transitions | Loader-semantics drift, analogous to the ELF binding kinds. |
| macOS | castxml extraction gaps (default args, constexpr initializers, `final`, access) | Known `known_gap_platforms: macos` entries in `ground_truth.json`; likely route is the clang AST frontend (`--ast-frontend clang`) on macOS rather than waiting on Homebrew castxml. |
| macOS | ObjC / Swift metadata surface | Recommend an explicit non-goal (or dedicated ADR) rather than an implicit gap. |
| Cross-platform | C++20 module interface (BMI) surface diff | Frontier item; needs its own ADR — BMI formats are compiler-specific and unstable. |

## Out of scope

- Behavioral/semantic equivalence of implementations (undecidable; the
  L4/L5 body-drift RISK kinds remain the honest boundary per ADR-026/028).
- Static archive analysis (G8 non-goal).
- Non-Itanium C++ ABIs in Phase B (MSVC vtable layout is a deferred Windows
  item).

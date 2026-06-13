# ADR-034: Managed-Runtime and Non-C ABI Frontends

**Date:** 2026-06-12
**Status:** Proposed
**Decision maker:** Nikolay Petrov

---

## Context

abicheck's detector core, evidence model (L0–L4), policy system, and example
catalog are built around the **native C/C++ ABI** as expressed in ELF, PE/COFF,
and Mach-O with DWARF/PDB debug info. Within that scope coverage is strong: 234
`ChangeKind`s span symbol surface, layout, the C++ object model, calling
convention, mangling, and loader/identity metadata.

A recurring request — and a recurring theme in cross-ecosystem ABI guidance — is
to extend abicheck to ecosystems whose "binary compatibility" is real but is
**not** native object layout:

- **Java** — binary compatibility is defined at the class-file / symbolic-reference
  level (JLS Ch. 13). Breaks surface as `NoSuchMethodError`, `NoSuchFieldError`,
  `AbstractMethodError`, `IllegalAccessError`, `IncompatibleClassChangeError`,
  `VerifyError` when a precompiled client meets a changed provider.
- **.NET / CLR** — compatibility is the public-contract metadata plus assembly
  identity (name, public key/strong name), method/IL signatures, parameter shape,
  and virtual/abstract/static semantics. Tooling baseline: `ApiCompat` / Package
  Validation.
- **Go** — the Go 1 promise is **source** compatibility, not compiled-package ABI.
  Module evolution is governed by "add, don't change/remove" and major-version
  module paths (`/v2`). `go plugin` is a separate, stricter same-toolchain
  provenance contract.
- **Rust** — **no stable Rust ABI**. `repr(Rust)` layout may change every
  compilation; only a deliberately designed C-FFI surface (`extern "C"` +
  `#[repr(C)]`, fixed export names) is a meaningful binary contract. Intra-Rust
  evolution is a Cargo SemVer/API question.

The Part-A policy catalog already added a `rust_c_ffi` profile (the Rust C-FFI
surface is just the C ABI, which the native core already handles). But Java,
.NET, and Go-source/Go-plugin are **not** expressible as policy files — they need
new *parse → snapshot → diff* frontends, because the inputs (`.class`/`.jar`,
managed assemblies, Go export data) are not native binaries.

The question this ADR settles: **do we build these frontends, and if so, how do
they fit the existing pipeline without diluting the native core?**

## Decision

### D1. Treat each non-C ecosystem as a pluggable *frontend*, not a core change

Keep `AbiSnapshot` / `DiffResult` / `ChangeKind` / `Verdict` as the shared spine.
A new ecosystem contributes:

1. a **metadata parser** (`<eco>_metadata.py`) that reads the artifact;
2. a **snapshot adapter** that maps it onto `AbiSnapshot` (reusing the existing
   symbol/type/member structures where they fit);
3. ecosystem-specific **diff rules** and any new `ChangeKind`s, partitioned into
   the existing `BREAKING_/API_BREAK_/COMPATIBLE_/RISK_KINDS` sets;
4. a **policy profile** (`<eco>.yaml`) for verdict shaping;
5. **example cases** with ground truth and an **execution oracle** (precompiled
   client vs new provider) where the ecosystem promises binary compatibility.

This mirrors how BTF/CTF/SYCL were added as frontends without disturbing the ELF
core, and aligns with the evidence-extractor plugin direction of ADR-032.

### D2. Prioritize by contract clarity and demand

| Ecosystem | Effort | Verdict model | Priority |
|---|---|---|---|
| **Rust C-FFI** | done (policy only) | native C ABI + `rust_c_ffi` profile | shipped |
| **Java (class/jar)** | medium | class-file linkage; run-the-linker oracle | **1st** — rules are crisp and runtime-verifiable |
| **.NET (assembly)** | medium | public metadata + assembly identity; wrap `ApiCompat` | **2nd** |
| **Go modules (source)** | low–medium | source-compat; wrap/embed `apidiff` + module-path rules | **3rd** |
| **Go plugin** | low | same-toolchain provenance fingerprint | bundled with Go |

### D3. Be honest about what each frontend can claim

- **Java/.NET**: emit verdicts only from compiled artifacts and, where feasible,
  back them with an execution oracle (the precompiled-client/new-provider test),
  because that is what these specs actually define.
- **Go (non-plugin)**: do **not** claim compiled-package ABI stability. Scope the
  frontend to source/API compatibility (`apidiff`) and module-path discipline.
- **Go plugin**: the contract is build provenance (toolchain version, build tags,
  flags, common-dependency source), not API shape — implement it as a fingerprint
  comparison, not a symbol diff.

### D4. Keep the native core's coverage claims unqualified

The README/docs "234 change types across ELF/PE/Mach-O" claim stays native-scoped.
Non-C frontends advertise their own coverage and maturity (Experimental →
Stable) independently, so adding an early Java frontend never weakens the native
guarantees.

## Consequences

**Positive**

- Each ecosystem ships independently behind a frontend flag; the native core and
  its 95% coverage floor are untouched.
- Reuses the policy, suppression, reporting (SARIF/JUnit/HTML), and example/
  ground-truth machinery already in place.
- The execution-oracle requirement keeps managed-runtime verdicts grounded in
  the failures the specs actually define, not in guesses.

**Negative / costs**

- New runtime/tool dependencies, isolated behind markers like the existing
  `integration`/`libabigail`/`abicc` lanes (e.g. a `jvm` marker needing a JDK, a
  `dotnet` marker needing the SDK/`ApiCompat`, a `go` marker needing the Go
  toolchain). The silent-skip guard (`ABICHECK_MIN_EXECUTED`) must cover them.
- New `ChangeKind`s enlarge the enum; each must satisfy the partition and
  detector/docs readiness gates.
- Cross-ecosystem verdict semantics differ (e.g. adding a default interface
  method is judgment-based in .NET, adding an interface method can break Java
  implementers) — profiles must encode these, and the docs must avoid implying a
  single universal "ABI break" answer.

**Neutral**

- No native behavior changes from this ADR; it is a roadmap + integration
  contract. Implementation of any single frontend is a follow-up ADR/PR.

## Alternatives considered

1. **Bolt managed-runtime checks onto the native core directly.** Rejected —
   pollutes ELF/DWARF-shaped code with class-file/assembly concepts and risks the
   native coverage floor.
2. **Ship thin policy profiles only (as for Rust C-FFI).** Works for Rust because
   its stable surface *is* the C ABI; fails for Java/.NET/Go whose artifacts are
   not native binaries — there is nothing for the native parser to read.
3. **Defer entirely / out of scope.** Rejected — the demand and the primary-source
   clarity (especially Java's linkage rules) make at least the Java frontend a
   high-value, well-bounded addition; an explicit roadmap is better than silence.

## References

- JLS Chapter 13 (Binary Compatibility); JVM linking errors.
- .NET "Breaking changes" guidance; `ApiCompat` / Package Validation.
- Go 1 compatibility promise; Go modules `v2+` major-path rule; `golang.org/x/exp/apidiff`; `plugin` package constraints.
- The Rust Reference (type layout, ABI); Cargo SemVer guide.
- ADR-010 (Policy Profile System), ADR-032 (Evidence Extractor Plugin Interface).

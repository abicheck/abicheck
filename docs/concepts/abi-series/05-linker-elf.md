# Part 5 — ELF & Linker-Level Concerns

> **Series navigation:** [0. Product Contract](00-product-contract.md) ·
> [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> **5. Linker & ELF** ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md) ·
> [8. Detecting Breaks](08-detection.md)

**What you'll learn on this page**

- The *second* contract — the one enforced not by the language but by the
  dynamic linker itself — and where it is recorded in the `.so`.
- How **SONAME** establishes library identity, and why its major number *is* the
  ABI epoch.
- **Visibility**, **symbol versioning**, **calling-convention attributes**,
  **security metadata** (executable stack, RPATH), **language linkage**, and the
  **TLS access model** — each a load-time contract you can break without
  touching a single source-level declaration.

Prerequisites: [Part 1 — Foundations](01-foundations.md) (the dynamic loader).
This page is Linux/ELF-centric; the
[PE/COFF and Mach-O parallels](#pecoff-and-mach-o-parallels) section near the end
maps every mechanism to its Windows and macOS peer.

---

## The second contract

Above the source-level ABI sits a contract enforced by the dynamic linker:
SONAME, visibility bits, version nodes, calling-convention attributes, and the
TLS access model — all recorded *in the `.so`* and consulted at load time.
You can satisfy every source-level rule from the previous pages and still break
consumers here.

---

## 1. SONAME and library identity

The **SONAME** is how the loader answers "is this the library you asked for?" It
lives in the `DT_SONAME` entry of `.dynamic`, set via
`-Wl,-soname,libfoo.so.MAJOR` at link time. When an app links against
`libfoo.so`, the linker copies the **SONAME** — not the filename — into
`DT_NEEDED`; at runtime the loader searches for a file (usually an
`ldconfig`-managed symlink) matching that string.

```text
  link:   app  --(records)-->  DT_NEEDED: libfoo.so.1
  run:    ld.so finds  libfoo.so.1 -> libfoo.so.1.4.2   (via ldconfig symlink)
```

Two failure modes:

- **No SONAME at all** ([case05](../../examples/case05_soname.md)): `DT_NEEDED`
  points at the bare `libfoo.so`, which `ldconfig` can't manage — shipping
  `libfoo.so.1` later breaks every consumer.
- **Wrong major** ([case50](../../examples/case50_soname_inconsistent.md)): a 1.x
  release tagged `libfoo.so.0`; packaging generates dependencies on the wrong
  major and the cutover forces a distribution-wide rebuild.

> **Rule: SONAME major equals ABI epoch, and it never silently changes.** A
> deliberate SONAME bump is the *correct* way to ship a breaking change —
> `libfoo.so.1` and `libfoo.so.2` coexist on disk, and old binaries keep loading
> the old one.

---

## 2. Symbol visibility

Every `.dynsym` entry has an `st_other` visibility byte: `STV_DEFAULT` (public,
interposable), `STV_HIDDEN`, `STV_PROTECTED` (exported but not interposable), or
`STV_INTERNAL`. **Without `-fvisibility=hidden`, every non-`static` function
defaults to `STV_DEFAULT`** — dragging the entire translation unit into your
public ABI.

| Case | Scenario | Verdict |
|------|----------|---------|
| [case06](../../examples/case06_visibility.md) | `internal_helper` was never meant to be public, but lacking `static` consumers resolved it; the later "cleanup" that hides it **breaks them** | 🔴 BREAKING (the removal), 🟢 quality (the original leak) |
| [case53](../../examples/case53_namespace_pollution.md) | exporting unprefixed names like `init` that collide in the process's flat symbol namespace | 🔴 BREAKING |
| [case51](../../examples/case51_protected_visibility.md) | `DEFAULT` → `PROTECTED`: ABI-compatible for normal callers, but silently defeats `LD_PRELOAD` interposition | 🟢 COMPATIBLE (quality) |

The trap is asymmetric: leaving visibility open is a 🟢 *quality* warning today,
but it commits you to maintaining those symbols forever — **fixing it later is a
🔴 BREAKING removal.**

---

## 3. Symbol versioning

A version script (`-Wl,--version-script=libfoo.map`) groups symbols into named
nodes like `LIBFOO_1.0`, recorded in `.gnu.version_d` and tagged in
`.gnu.versym`; consumers carry matching `.gnu.version_r` entries. This lets one
`.so` ship multiple ABI generations side by side — the mechanism glibc uses to
stay loadable across decades.

```text
libfoo.map:
  LIBFOO_1.0 { global: foo; bar; local: *; };
  LIBFOO_2.0 { global: baz; } LIBFOO_1.0;
```

- **Adding** a version script ([case13](../../examples/case13_symbol_versioning.md))
  is backward compatible — old binaries have no `DT_VERNEED`, so the loader
  resolves by name. 🟢 COMPATIBLE.
- **Removing** a node ([case65](../../examples/case65_symbol_version_removed.md))
  deletes every symbol it tagged: `version 'FOO_1.0' not found`. 🔴 BREAKING.

glibc's `GLIBC_2.0` has been *append-only since 1997* — which is why a binary
built against an old glibc still loads against a current one, and why OpenSSL
3.0's version-node removals forced the SONAME bump from `libssl.so.1.1` to
`.so.3`.

---

## 4. Calling conventions

A calling convention is the register-and-stack contract: which registers hold
args, which are callee-saved, how the return comes back. On x86-64 you meet
System V AMD64 (Linux/macOS/BSD, args in `rdi, rsi, rdx, rcx, r8, r9`) and
Microsoft x64 (Windows or `__attribute__((ms_abi))`, args in `rcx, rdx, r8,
r9`). On 32-bit x86 the zoo is larger: `cdecl`, `stdcall`, `fastcall`,
`thiscall`, `vectorcall`.

[case64](../../examples/case64_calling_convention_changed.md) flips the attribute
silently: the v1 caller loads pointers into `rdi`/`rsi`, the v2 `ms_abi` callee
reads `rcx`/`rdx`, and the function operates on stale register contents — zero
results or a segfault. The *signature is unchanged*, so name-and-type-only checks
miss it.

!!! note "How abicheck sees it"
    `calling_convention_changed` → 🔴 **BREAKING**, by diffing the
    `DW_AT_calling_convention` DWARF attribute.

---

## 5. Security metadata

Two ELF-level properties are part of the contract even though they aren't "code":

- **Executable stack.** The `PT_GNU_STACK` program header advertises whether the
  process stack must be executable; the linker *unions* it across input objects,
  so a single assembly file missing its `.note.GNU-stack` annotation promotes the
  entire `.so` (and every process that loads it) to an executable stack.
  [case49](../../examples/case49_executable_stack.md): `readelf -l` reports `RWE`
  instead of `RW`; rpmlint and Debian lintian both reject the package.
- **RPATH/RUNPATH leaks.** `DT_RPATH`/`DT_RUNPATH` hold extra linker search
  paths. [case52](../../examples/case52_rpath_leak.md) bakes
  `/home/build/myproject/lib` into the artifact: it only works on the build host,
  and anyone who can write that path gets a library-injection primitive. Use
  `$ORIGIN`-relative paths or strip RPATH entirely.

These are 🟢 *quality* findings (the binary still loads) but they are release
blockers for distributions and a genuine security exposure.

---

## 6. Language linkage and TLS

**Language linkage.**
[case66](../../examples/case66_language_linkage_changed.md) removes `extern "C"`
during a C++ modernization: the source still compiles, but the `.dynsym` symbol
flips from unmangled `parse_config` to mangled `_Z12parse_configPKc`, and every
pre-linked consumer fails at load. **Treat `extern "C"` blocks as part of the
public ABI.**

**Thread-Local Storage.** TLS has four access models:

| Model | Property |
|-------|----------|
| `global-dynamic` | default for `.so`; **`dlopen`-safe** |
| `local-dynamic` | for TLS used only within the same module |
| `initial-exec` | faster, but requires the variable be present at startup — **`dlopen` fails** |
| `local-exec` | main executable only |

Libraries intended to be `dlopen`ed must avoid `initial-exec`. And any exported
`__thread` struct whose layout shifts corrupts consumers per-thread
([case67](../../examples/case67_tls_var_size_changed.md)) — freeze the size,
layout, and access model of TLS exports as first-class ABI.

---

## PE/COFF and Mach-O parallels

Everything above is the **ELF/SysV** model. Windows and macOS solve the same
problems — identity, export surface, versioning, lazy resolution, thread-local
storage — with different mechanisms. abicheck parses all three (PE/COFF via the
export table + optional PDB; Mach-O via load commands + optional DWARF/dSYM);
the table below maps each ELF concept to its peer. The full support matrix is in
the [Platform Support reference](../../reference/platforms.md).

| ELF concept | Windows PE/COFF | macOS Mach-O |
|-------------|-----------------|--------------|
| **Symbol lookup** by name | By **name *or ordinal*** — an integer index into the export table. Ordinal-bound callers ignore the name; reordering exports breaks them. | **Two-level namespace**: each import records the *source library*, so the same bare name from a different library is a different symbol. |
| **SONAME** (library identity) | DLL file name + the **import library** (`.lib`) used at link time. | **Install name** (`LC_ID_DYLIB`) baked into both the dylib and its clients; `@rpath`/`@loader_path`/`@executable_path` make it relocatable. |
| **Symbol versioning** (`GLIBC_2.x` nodes) | No symbol versions; new ABI ⇒ new DLL name or **side-by-side assemblies**. | No GNU-style version nodes; uses **compatibility version** + **current version** numbers on the dylib. |
| **Lazy binding** (PLT/GOT) | **Delay-load** DLLs (`/DELAYLOAD`) resolve on first use. | Lazy/`__stubs` binding; **weak imports** allow a missing symbol to resolve to null at load instead of failing. |
| **Visibility** (`-fvisibility=hidden`) | Explicit **`__declspec(dllexport)`** / `.def` file — nothing is exported unless named. | `-fvisibility=hidden` + `__attribute__((visibility))`, same as ELF. |
| **Mangling / decoration** | MSVC name **decoration** differs from Itanium; `extern "C"` still adds leading underscores / `@N` stdcall suffixes. | Itanium C++ ABI (same as Linux Clang). |
| **Packaging unit** | The DLL, plus its **import library** and PDB for debug info. | The dylib, optionally inside a **framework** bundle, optionally a **universal (fat) binary** carrying multiple arch slices. |
| **CRT / allocator boundary** | Each DLL may link its **own CRT**; `malloc` in one module must not be `free`d in another — a hard cross-module rule with no ELF analog. | Single system libc; less acute, but cross-dylib `delete` of a type with an inline destructor has the same Itanium pitfalls as Linux. |

!!! tip "Practical consequences for abicheck users"
    - On **Windows**, prefer exporting **by name** and keep a stable `.def` so a
      rebuild can't shuffle ordinals; supply the **PDB** for layout/calling-convention
      checks (symbol-only mode sees names but not offsets).
    - On **macOS**, treat the **install name** and **compatibility version** as
      part of the contract, design for **weak imports** when adding symbols a
      client may run against an older dylib, and remember a **universal binary**
      can differ slice-by-slice — compare the matching architecture.

---

## How to govern the linker-level contract

!!! tip "Design patterns for Part 5"
    - **Version scripts as the source of truth.** A `.map` file enumerating
      every intentional export is the canonical place to negotiate API surface
      — and it doubles as your `-fvisibility=hidden` allowlist.
    - **`ABI_EXPORT` macro discipline.** Build with `-fvisibility=hidden` and
      annotate public functions with a project-specific macro expanding to
      `__attribute__((visibility("default")))` (ELF) /
      `__declspec(dllexport)` (PE).
    - **CI gate on every PR.** Dump the previous release, compare the
      candidate, fail on any 🔴 BREAKING not paired with a SONAME bump.
    - **Never link with absolute `--rpath`.** Use `$ORIGIN` or install-time
      rewriting; absolute build paths are non-portable and a security hazard.
    - **Declare TLS access models explicitly.** If a TLS variable is ever
      reached via `dlopen`, pin `-ftls-model=global-dynamic`.

---

## Next

So far every break has been visible in the library's own declarations. The
nastiest family is the one where the exported symbol table is *byte-identical*
yet consumers still corrupt memory — breaks that travel through a *transitive*
dependency the library doesn't even define.

➡️ **[Part 6 — Subtle & Transitive Breaks](06-transitive-breaks.md)**

*See also:* [ABI Cheat Sheet](../abi-cheat-sheet.md) ·
[Platforms reference](../../reference/platforms.md) ·
[Quality examples](../../examples/by-category/quality.md)

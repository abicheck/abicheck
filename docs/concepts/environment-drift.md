# Environment & Toolchain Drift

Two builds of the *same source* can differ in ABI-relevant ways because the
**build environment** moved — a different compiler, different binutils/linker
defaults, or a different glibc/sysroot. These findings answer a different
question than interface changes: not "did the API move?" but "did the build
environment move, and what does that do to where the binary can run?"

abicheck groups these under a dedicated **Environment & Toolchain Drift**
section in Markdown reports, and classifies most of them as
`COMPATIBLE_WITH_RISK` — binary-compatible for existing consumers, but a
deployment-envelope or loader-semantics change worth reviewing.

## The glibc side: deployment floors

Linking on a host with a newer glibc rebinds imports to newer version nodes
with zero source change — merely *relinking* on glibc ≥ 2.34 rebinds
`__libc_start_main` to `@GLIBC_2.34`. The binary is interface-identical but no
longer loads on older distros.

- **`symbol_version_required_added`** — one finding per new version node
  (e.g. `GLIBC_2.34` from `libc.so.6`) that is newer than the old maximum.
- **`runtime_floor_raised`** — the roll-up: one headline finding per provider
  library and version-tag prefix naming the old → new floor
  (`GLIBC_2.28 → GLIBC_2.34`) and **which imported symbols pulled it up**.
  A floor raised only by `__libc_start_main` is a pure relink artifact; a
  floor raised by a real API symbol (say `pthread_cond_clockwait`) means the
  code genuinely depends on the newer runtime.
- **`time64_abi_changed`** — the 32-bit time64/LFS flip: `time_t`/`off_t`-family
  typedefs resized together (`_TIME_BITS=64` / `_FILE_OFFSET_BITS=64`,
  glibc ≥ 2.34). This one is `BREAKING` — every public function or struct
  carrying those typedefs changed layout — and is reported as a single
  root-cause diagnostic alongside the per-symbol findings.

### Declaring a target floor (`--env-matrix`)

Without a declared deployment target, a raised floor can only be a *risk*.
Declare one and it becomes a decidable verdict:

```yaml
# env-matrix.yaml
runtime_floors:
  GLIBC: "2.28"        # we ship to RHEL 8 / Ubuntu 20.04
  GLIBCXX: "3.4.28"
```

```console
$ abicheck compare old.so new.so --env-matrix env-matrix.yaml
```

A new requirement **at or below** a declared floor is `COMPATIBLE` (every
declared target already ships it); one **above** the floor is `BREAKING` (a
declared target can no longer load the binary); prefixes you did not declare
keep the default `COMPATIBLE_WITH_RISK`. Keys are ELF version-node prefixes
(`GLIBC`, `GLIBCXX`, `CXXABI`, …), matched case-insensitively. A declared
`GLIBC` floor also settles `dt_relr_introduced` (implied requirement:
glibc ≥ 2.36).

## The binutils side: linker default drift

Newer binutils (often distro-patched) flip linker defaults that land in the
artifact:

- **`dt_relr_introduced` / `dt_relr_removed`** — packed relative relocations
  (`-z pack-relative-relocs`, a binutils ≥ 2.38 distro default). A `DT_RELR`
  binary requires glibc ≥ 2.36; glibc marks this with a synthetic
  `GLIBC_ABI_DT_RELR` version requirement, which abicheck folds into the
  DT_RELR finding instead of reporting a cryptic unparseable version. Fix:
  `-z nopack-relative-relocs` if you must support older runtimes.
- **`rpath_type_changed`** — `DT_RPATH` ↔ `DT_RUNPATH` flip
  (`--enable-new-dtags`). Same paths, different lookup semantics: `DT_RPATH`
  covers the whole dependency subtree and beats `LD_LIBRARY_PATH`;
  `DT_RUNPATH` covers only direct deps and is overridden by it.
- **`hash_style_removed`** — a symbol hash-table style (`.hash` SysV /
  `.gnu.hash` GNU) present in the old binary was dropped (`--hash-style`).
  Loaders that only support the dropped style can no longer resolve symbols.
- CET/branch-protection drift (`cet_protection_*`, `branch_protection_*`),
  static-TLS drift (`static_tls_*`) — see
  [Security Hardening](../user-guide/security-hardening.md) for the
  `.note.gnu.property` coverage.

## The compiler/stdlib side

Compiler and standard-library drift is covered by `toolchain_version_changed`
(L3 build evidence), `toolchain_flag_drift` (DWARF producer flags),
`stdlib_implementation_changed`, `glibcxx_dual_abi_flip_detected`,
`integer_model_changed`, and `long_double_abi_changed` — see the
[Change Kind Reference](../reference/change-kinds.md).

## Reproducibility note

abicheck parses ELF in pure Python (pyelftools), so its *analysis results* do
not depend on the host's installed binutils version — unlike tools that shell
out to `readelf` or link against elfutils.

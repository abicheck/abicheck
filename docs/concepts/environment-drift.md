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
(`GLIBC`, `GLIBCXX`, `CXXABI`, …), matched case-insensitively — quote the
versions (`"2.40"` unquoted is YAML for `2.4`). A declared `GLIBC` floor also
settles `dt_relr_introduced` (implied requirement: glibc ≥ 2.36).

### Not just glibc: any versioned dependency

The floor detection and the contract are **generic over every `DT_NEEDED`
dependency that uses ELF symbol versioning**, not special-cased to glibc.
`runtime_floor_raised` fires per *(provider library, version-tag prefix)*, so
a rebuild that starts requiring `OPENSSL_3.0` from `libssl.so.3`, a newer
`ZLIB_1.2.9` node, or a newer `LIBFOO_2` node from your own SDK dependency is
reported the same way — and `runtime_floors: {OPENSSL: "3.0"}` gates it the
same way. glibc/libstdc++ dominate the examples only because relinking on a
newer distro moves them silently. The limits: a dependency that does *not*
version its symbols only surfaces through `needed_added`/`needed_removed` and
SONAME changes (there is no per-version evidence in the artifact to compare),
and non-library requirements (kernel version, drivers) are outside what a
binary records — the [environment matrix](../concepts/environment-drift.md)'s
SYCL/CUDA blocks exist for declaring those constraints explicitly.

### Warn by default, gate by choice

You do **not** need a matrix to be told about drift. Without one, every
finding on this page is still detected and reported — as
`COMPATIBLE_WITH_RISK`, which under the default (legacy) exit-code scheme
exits **0**: CI stays green, the report and the
`Environment & Toolchain Drift` section carry the warning. Declaring floors
is the opt-in that turns the warning into a gate (`BREAKING`, exit 4) when a
declared target is actually cut off. Teams that want risk findings to block
CI even without floors can do that independently via the severity knobs
(`--severity-potential-breaking error`).

### Why a separate file and not `.abicheck.yml`?

The environment matrix (ADR-020b) describes a **deployment target**, while
`.abicheck.yml` describes the **project**. One project routinely checks the
same pair of binaries against several targets — "does this break our RHEL 8
tier?" and "our Ubuntu 24.04 tier?" are two invocations with two matrices and
possibly two different verdicts — so the matrix rides per-invocation
(`--env-matrix <file>`), like `--policy-file`, rather than being a single
project-wide setting. It is also the same file that declares SYCL/CUDA
deployment constraints, which are equally target-specific. A convenience
`environment:` block in `.abicheck.yml` for single-target projects would be a
reasonable follow-up, but the per-target file stays the primitive.

### CI / GitHub Action usage

Commit the matrix next to your workflow and pass it through; with the
[GitHub Action](../user-guide/github-action.md), use `extra-args`:

```yaml
- uses: abicheck/abicheck-action@v1
  with:
    old: baseline/libfoo.so
    new: build/libfoo.so
    extra-args: '--env-matrix .github/env-rhel8.yaml'
```

Run the step once per supported target (matrix strategy over
`env-*.yaml` files) to gate each deployment tier independently; drop
`extra-args` to keep drift findings warning-only.

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

# Dependency & Runtime Floors

The [ABI/API handling series](abi-api-handling.md) treats compatibility as a
question about your library's own surface — the symbols, layouts, and headers
*you* publish. This page covers the mirror-image contract: what your binary
**requires from the platform underneath it** — libc, the C++ runtime, OpenSSL,
a vendor SDK. abicheck calls the minimum version of each such dependency the
binary demands its **runtime floor** (detected as
[`runtime_floor_raised`](../reference/change-kinds.md), gated with
`compare --env-matrix`).

The page builds the idea up from the trivial case to the one that surprises
performance-library maintainers: a release whose only change is a new
CPU-specific kernel can stop loading on older OS releases for *every* user.

> **Tool-track companion:** this page teaches the *concept*. The exact
> detector semantics, `--env-matrix` file format, and CI recipes live in
> [Environment & Toolchain Drift](environment-drift.md); the runnable fixture
> is [case170](../examples/case170_env_runtime_floor_raised.md).

## The simple version: your binary states a minimum runtime

On Linux, glibc and libstdc++ version every symbol they export. When you link
against them, each import in your `.so` is bound to a specific **version
node** — `memcpy@GLIBC_2.14`, `std::filesystem` symbols at `GLIBCXX_3.4.26` —
and the ELF file records the set of required nodes (`.gnu.version_r`). At load
time the dynamic loader checks that list **eagerly, before running any of your
code**: if the host's libc doesn't provide `GLIBC_2.34`, the load fails with
`version GLIBC_2.34 not found` — a hard, immediate break, not a subtle one.

So the highest version node your binary references *is* its deployment floor,
and the floor translates directly into **which OS releases can run it**,
because each distro ships one glibc/libstdc++ for its lifetime:

| Requires | Runs on (examples) | Cut off |
|----------|--------------------|---------|
| `GLIBC_2.28` | RHEL 8+, Ubuntu 20.04+, Debian 11+ | CentOS 7 (2.17) |
| `GLIBC_2.31` | Ubuntu 20.04+, RHEL 9+ | RHEL 8 (2.28) |
| `GLIBC_2.34` | RHEL 9+, Ubuntu 22.04+ | RHEL 8, Ubuntu 20.04 |
| `GLIBC_2.39` | Ubuntu 24.04+, Fedora 40+ | everything above |
| `GLIBCXX_3.4.29` (GCC 11 runtime) | distros shipping libstdc++ ≥ GCC 11 | RHEL 8's default libstdc++ |

That is why a floor change is a *compatibility* event even though your own
API/ABI surface is byte-identical: raising the floor from `GLIBC_2.28` to
`GLIBC_2.34` de-supports every consumer on RHEL 8 and Ubuntu 20.04 as surely
as deleting a symbol would — they just find out from the loader instead of
the linker.

## The floor moves even when you change nothing

The uncomfortable part: **merely rebuilding on a newer distro raises the
floor.** Linking on a glibc ≥ 2.34 host rebinds startup plumbing like
`__libc_start_main` to `@GLIBC_2.34` with zero source change. abicheck
reports this at two granularities
(worked fixture: [case170](../examples/case170_env_runtime_floor_raised.md)):

- `symbol_version_required_added` — the per-node fact (`GLIBC_2.34` from
  `libc.so.6` is newer than the old maximum);
- `runtime_floor_raised` — the roll-up headline per *(provider library,
  version prefix)*: `GLIBC_2.28 → GLIBC_2.34`, **with the list of imported
  symbols that pulled the floor up**.

That evidence list is the diagnostic: a floor pulled up only by
`__libc_start_main` is a relink artifact (fix: build on your oldest supported
distro or a matching sysroot — the manylinux approach); a floor pulled up by a
real API symbol means the code now genuinely depends on the newer runtime
(see [Adopting new runtime features](#adopting-new-runtime-features-on-purpose)
below).

## Making it decidable: declare your supported OS matrix

Both findings above are 🟡 `COMPATIBLE_WITH_RISK` by default — whether anyone
breaks depends on deployment targets the binary can't name. They become
decidable the moment you declare your targets:

```yaml
# env-rhel8.yaml — "we still ship to RHEL 8 / Ubuntu 20.04"
runtime_floors:
  GLIBC: "2.28"
  GLIBCXX: "3.4.25"
```

With `--env-matrix env-rhel8.yaml`, a new requirement at or below the declared
floor is 🟢 `COMPATIBLE`; one above it is 🔴 `BREAKING` (exit 4 — CI gates).
Run the check once per supported tier ("does this cut off RHEL 8?" and "does
it cut off Ubuntu 22.04?" are different invocations with possibly different
verdicts). The mechanism is generic over **every versioned `DT_NEEDED`
dependency**, not just glibc — a rebuild that starts requiring `OPENSSL_3.0`
or a newer version node from your own SDK dependency reports and gates the
same way. Full flag/CI details, including the GitHub Action wiring:
[Environment & Toolchain Drift](environment-drift.md).

## macOS and Windows: same contract, different plumbing

The floor concept exists on every platform; only ELF makes it per-symbol and
machine-checkable from the artifact alone
(see [Platform Support](../reference/platforms.md)):

- **macOS** — no symbol versioning; the floor is declared up front as the
  **deployment target** (`-mmacosx-version-min`, recorded in the
  `LC_BUILD_VERSION`/`LC_VERSION_MIN_MACOSX` load command, which abicheck
  parses as `min_os_version`). Rebuilding with a newer SDK's default target is
  the exact analogue of the glibc relink drift. The escape hatch is
  **weak linking** + availability attributes: a symbol marked
  `__attribute__((availability(macos, introduced=14.0)))` resolves to `NULL`
  on older systems instead of failing the load, so code can check at run time —
  macOS's idiomatic answer to "use the new API without raising the floor".
  Dylibs also carry a coarse `compatibility_version` — a single number playing
  the role ELF version nodes play per-symbol.
- **Windows** — imports are (DLL name, function) pairs with **no version
  node**, so the floor hides in *which* DLLs and functions you import: pull in
  a function that only exists in a newer `kernel32.dll` or a newer
  `api-ms-win-*` API set, and the DLL fails to load on older Windows with
  "entry point not found" — same eager pre-execution check, less evidence in
  the artifact. The C runtime adds a second axis (UCRT vs classic
  `msvcrt`, the `vcruntime140.dll` redistributable version), and the PE header
  carries a coarse `MajorSubsystemVersion` floor. The idiomatic
  keep-the-floor-down patterns are `LoadLibrary`/`GetProcAddress` and
  **delay-loading** — the dynamic counterparts of macOS weak linking.

The detection asymmetry follows the evidence: unversioned imports (Windows,
and any ELF dependency that doesn't version its symbols) surface only as
`needed_added`/`needed_removed` and export-set diffs — there is no per-version
fact in the artifact to compare — while ELF's versioned deps give abicheck
enough to name the exact old → new floor and the symbols responsible.

## Adopting new runtime features on purpose

Sometimes the floor raise is the point: you switched to
`pthread_cond_clockwait` (glibc 2.30), `arc4random` (2.36), C++20 library
features whose symbols live in a newer `GLIBCXX_3.4.x` node. Then
`runtime_floor_raised`'s evidence list shows real API symbols, and you have a
product decision, not a build bug:

1. **Raise the supported-OS floor** — legitimate, but it is a compatibility
   break at the product level: version it, release-note it, and update the
   `runtime_floors` in your env matrices so CI ratifies the new floor rather
   than fighting it.
2. **Keep the floor** — take the new API through a run-time lookup
   (`dlsym`/`dlvsym` with a fallback path, weak references), so the version
   node never enters your `.gnu.version_r` and old targets keep loading.

Either answer is fine; shipping the raise *unknowingly* is the failure mode
the detector exists to prevent.

## Dynamic dispatch and new hardware: the oneDAL / OpenBLAS scenario

Performance libraries (oneDAL, OpenBLAS, oneDNN, BLIS…) keep one stable ABI
across wildly different hardware by **runtime CPU dispatch**: a single `.so`
exports one `dgemm`, and at load or first call a resolver (CPUID check, GNU
IFUNC — [case29](../examples/case29_ifunc_transition.md)) picks the SSE2 /
AVX2 / AVX-512 / AMX kernel. Dispatch is exactly the right pattern — callers
see one symbol forever — and abicheck checks its *own* surface too
(`cpu_dispatch_isa_dropped`,
[case83](../examples/case83_cpu_dispatch_isa_dropped.md): silently dropping a
previously-dispatched ISA strands consumers pinned to it).

The subtle interaction with runtime floors: **enabling new hardware often
needs new platform support, and that dependency binds at link time — for
every user, not just the new-hardware ones.** Concrete shapes this takes:

- an AVX-512 kernel calls vectorized libm routines (`libmvec`) — on AArch64,
  vector math variants only exist from glibc 2.38, so the *import itself*
  demands a new floor;
- new ISA state changes low-level plumbing: AVX-512/AMX enlarge signal frames,
  which is what glibc 2.34's dynamic `AT_MINSIGSTKSZ` handling exists for, and
  AMX tile state must be requested from the kernel before use;
- feature detection itself modernizes — `getauxval` hwcap queries, or relying
  on **glibc-hwcaps** directories (glibc ≥ 2.33 selects
  `glibc-hwcaps/x86-64-v3/libfoo.so` automatically) instead of hand-rolled
  CPUID.

Now replay the mechanism from the top of this page: the loader validates *all*
required version nodes **before any code runs**. So a release whose only
change is "added an AMX kernel for the newest Xeons" can refuse to load on a
five-year-old datacenter node that would never execute one AMX instruction —
the Sandy Bridge user pays the Sapphire Rapids kernel's glibc floor. The
dispatch *keeps the ABI* stable and *still moves the deployment envelope*.

What the floor check buys you here is precisely the triage: the
`runtime_floor_raised` evidence list tells you whether the pulled-up symbols
are relink plumbing, core-path API, or confined to the new kernel — and per-tier
`--env-matrix` runs tell you which supported OS versions the release just cut
off. If the answer is "only the AMX path needs it, but it cut off RHEL 8", the
established fixes keep both audiences:

- **isolate the new-HW path behind runtime resolution** — `dlopen` a per-ISA
  sub-library (dispatch loads it only where usable) or `dlvsym` the new libc
  symbols with a fallback, so the main `.so`'s floor stays put;
- **split packaging per target** — glibc-hwcaps directories or per-distro
  builds, each with its own honest floor;
- **declare and gate** — one env matrix per deployment tier in CI, so the day
  a kernel drags a new version node into the shared code path, the RHEL 8
  lane goes red before the release ships.

## Related pages

| You want to… | Go to |
|--------------|-------|
| Detector semantics, `--env-matrix` format, binutils-side drift (DT_RELR, RPATH type, hash style, time64) | [Environment & Toolchain Drift](environment-drift.md) |
| Run the worked fixture yourself | [case170 — Runtime Floor Raised](../examples/case170_env_runtime_floor_raised.md) |
| ELF symbol versioning fundamentals | [Part 5 — Linker & ELF](abi-series/05-linker-elf.md) |
| Per-platform loader/versioning parallels | [Platform Support](../reference/platforms.md) |
| The full change-kind taxonomy | [Change Kind Reference](../reference/change-kinds.md) |

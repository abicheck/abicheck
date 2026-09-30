# Evidence depth, header backends, runtime floors, build systems

The decisions that change *what the check can see*. Each section says which
repository signal triggers it, what to write, and what it costs. See
[evidence and depth](../../shared/evidence-and-depth.md) for the layer model
and [compiler and build profiles](../../shared/compiler-and-build-profiles.md)
for why both sides must be built alike.

---

## 1. Runtime and dependency floors (glibc, libstdc++, …)

**Trigger:** the project states supported distributions or a minimum glibc
("must run on RHEL 8", "Ubuntu 20.04+", manylinux, a `glibc >= 2.28` note),
or ships prebuilt binaries to customers.

**Why it matters.** Rebuilding on a newer runner raises the glibc/libstdc++
versions the library requires (`runtime_floor_raised`,
`symbol_version_required_added`) with no source change. **By default those
findings are `COMPATIBLE_WITH_RISK` and exit 0: the gate does not fail.**
They become decidable only when the floor is declared:

```yaml
# .abicheck.yml (repository root, or pass it with the Action's `build-config`)
deployment:
  runtime_floors:
    GLIBC: "2.28"       # RHEL 8
    GLIBCXX: "3.4.25"   # RHEL 8's default libstdc++ (C++ libraries only)
```

A requirement at or below a declared floor is then `COMPATIBLE`; one above
it is `BREAKING` (exit 4). An undeclared prefix stays a risk. There are no
per-tier profiles under `deployment:` — one supported tier per config; a
second tier is a second config and a second check.

**What to write:**
- the `deployment:` block, from the floors the project actually states —
  never invent a floor the repository does not promise;
- `build-config: .abicheck.yml` on every abicheck step (explicit beats
  relying on discovery);
- pinned runner images (`ubuntu-24.04`, not `ubuntu-latest`) for the check
  *and* the baseline build, and a note in the report: building on a newer
  image than the promise will now fail the gate, which is correct — the fix
  is building on the oldest supported distro (a container or manylinux-style
  image), not relaxing the floor.

**"Will it actually load in that environment?"** is a different question
(the whole dependency graph, not just version nodes). Offer the Action's
`mode: deps-compare` (`new-library`, `old-root`, `new-root` sysroots) or
`mode: deps-tree` (`new-library`, optional `sysroot`) as a separate job when
the user ships into a known rootfs/container.

## 2. Evidence depth: headers (L2) vs build/source (L3–L5)

| `depth` | Sees | Costs |
|---|---|---|
| `headers` (default) | exported symbols, debug info, header declarations and layout | the build + CastXML |
| `build` | + compile flags/defines, per-TU context (L3) | a compile database or a build query |
| `source` | + source-ABI replay: inline bodies, templates, macros, default arguments, `constexpr` (L4), source graph (L5) | L3 **plus clang** |

**Trigger for `source`:** a header-heavy API (inline functions, templates,
macros, default arguments, `constexpr`) or a history of breaks "without
changing an exported symbol". Otherwise keep `headers`.

**What `depth: source` requires — all of it:**
1. **clang on the runner.** The default `dependency-source: conda-forge`
   provides CastXML and GCC but **no clang**; use
   `dependency-source: conda-forge-clang20` (or `system`).
2. **A compile database or a build query.** CMake: configure with
   `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` and pass `compile-db:` /
   `build-info: build`. abicheck can also run a CMake/Bazel query itself when
   given `sources:`. Autotools/Make produce none: wrap the build with
   `bear -- make` (installed by `dependency-source: system`) or accept
   reduced-confidence L3.
3. **Source evidence on the OLD side too.** `sources`/`build-info`/
   `compile-db` feed the **new** side only. The old side's L3–L5 evidence
   must already be inside the old snapshot, so the release-baseline dump
   needs the same inputs (`mode: dump` with `sources: .`, `depth: source`,
   and the compile DB). A merge-base comparison of two native libraries gets
   L4 on the new side only — say so, or use the dump-both-sides pattern.
4. **Generated headers present** before the scan (build first), or L4 reads
   `partial`.

**PR scoping (optional):** `since: origin/${{ github.base_ref }}` (with
`fetch-depth: 0`) or `changed-path`; `budget: 15m` fails the step on overflow
rather than silently shrinking scope. Two-sided single-pair compares only.

**Confirm the depth was reached.** A pinned `depth` is a contract: an
unreachable depth on a live-extracted side exits 7. `evidence_tier` stops at
headers; read `layer_coverage` (per layer `present|partial|not_collected`)
in the JSON report for L3–L5. Put that in the setup report's "Validated"
line only if you saw it.

## 3. Header AST backends

- **Default `castxml`** (Action `ast-frontend: auto`), policy range
  `>=0.6.11,<0.8.0`. Fails closed: no CastXML, no silent switch.
- **`ast-frontend: clang`** — for a project that genuinely cannot use
  CastXML (clang-only toolchain, SYCL/DPC++ device code). It does **not**
  compute record layout (size, alignment, field offsets); layout breaks then
  come only from debug info, so keep `-g` in the check build. Needs clang
  (`dependency-source: conda-forge-clang20`).
- **`hybrid`** — both, reconciled; needs both tools; never automatic.
- **Opt-in fallback** castxml→clang: `extra-args:
  --allow-ast-frontend-fallback` (only for recognised CastXML failures).
- Use the **same frontend for both sides** (release dump and PR compare).
  Mixing is supported but weakens comparability of declaration facts.

Choose `castxml` unless the repository gives a concrete reason not to.

## 4. Build systems

Reuse the repository's own build commands; these are the parts specific to
the check:

| Build | Shared library path | Notes |
|---|---|---|
| CMake | from `add_library(... SHARED)` / `BUILD_SHARED_LIBS` | `-DBUILD_SHARED_LIBS=ON` if the default is static; `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` for `build`/`source` depth |
| Meson | `build/lib<name>.so` | `default_library=shared` |
| Make | whatever the Makefile writes | name the real file, not the dev symlink if versioned; `bear -- make` for L3+ |
| Autotools/libtool | `.libs/lib<name>.so` in the build dir, or `make install DESTDIR=$PWD/stage` then `stage/usr/local/lib/…` | needs `autoreconf -fi` (`autoconf automake libtool`); the installed headers (`include_HEADERS`) are the public set; `bear -- make` for L3+ |
| Bazel | `bazel-bin/<target>` (e.g. `bazel-bin/libkv.so` for `cc_binary(linkshared=True)`) | `bazel-bin` is a symlink into the output base: copy the `.so` out before a second build overwrites it; root targets for L3+ go in `.abicheck.yml` `build.targets` |

**Cross-compiled libraries** (toolchain file, `aarch64-linux-gnu-gcc`, a
sysroot): abicheck reads the target binary statically on an x86_64 runner,
but the header frontend must parse headers **as the target compiler would**:
set `gcc-prefix: aarch64-linux-gnu-` (or `gcc-path:`) and `sysroot:` on every
analysis step, and install the cross toolchain in that job. Alternatively
build and check natively on an arm64 runner (`runs-on: ubuntu-24.04-arm`).

**Several build profiles** (compilers, ISAs, debug/release): one baseline per
profile, encoded in the snapshot name
(`libfoo-linux-x86_64-gcc13.abicheck.json`), and a matrix that never compares
across profiles. For many profiles or targets, point at the reusable
`check-project.yml` workflow instead of hand-rolling the matrix.

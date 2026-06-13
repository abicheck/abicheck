# abicheck field-evaluation — cumulative findings log

Format: each problem has an ID, category (DISCOVERY / USABILITY / PERF / CORRECTNESS),
severity, what happened, repro, and a suggested fix. Timings tracked in `timing.jsonl`
and the per-iteration tables below. Environment: 4c/15GB Linux, abicheck 0.3.0,
gcc 13.3 / clang 18.1, castxml ABSENT.

---

## Iteration 1 — conda-forge dozen (C libs, CMake source phase)

### Problems found

- **P01 [DISCOVERY/high]** Runtime `.so` lives in a *split* conda package, not the
  named one. `zlib`→`libzlib`, `webp`→`libwebp-base`, C lib of zstd/lz4 is `zstd`/`lz4-c`.
  A user pointing abicheck at "the zlib package" finds only a `.so` symlink + headers, no
  runtime object. abicheck has no help here — it's purely the user's problem to locate the
  artifact. *Fix idea:* a `fetch`/discovery helper, or docs on conda split-package layout.

- **P02 [DISCOVERY/med]** conda variant selection: `libxml2` 2.15.0 build I picked shipped
  only `bin/`, no `lib/`. Highest-build-number heuristic can land on a variant without the lib.

- **P03 [USABILITY/high]** `dump --show-data-sources` is **preview-only** — it prints the
  L0–L5 table but does NOT collect/embed. Ran it expecting L3/L4/L5 to be embedded; got a
  no-op (0.3s) and an empty snapshot. Wording ("not collected in --show-data-sources") is
  easy to miss. *Fix idea:* make the flag additive (collect AND show), or louder warning.

- **P04 [USABILITY/high]** Passing `-H <header>` to scope the public surface triggers the
  **L2 castxml path** and hard-errors `castxml not found in PATH` — even though the goal was
  only to designate public headers for L4. The C-front-end requirement leaks into a flag a
  user reaches for naturally. *Fix idea:* `-H` should degrade to header-as-public-marker
  without castxml; only the AST-extraction features should hard-require it.

- **P05 [USABILITY/med]** L4 source replay parsed every TU (paying full clang cost) but
  returned `reachable_declarations/types/matched_symbols = 0` for C libs. The default `clang`
  extractor emits inline/template/constexpr *body fingerprints*; a pure-C public API has none,
  so the spend yields ~nothing. No warning that "this extractor will find little for C".
  *Fix idea:* detect C-only surface and warn, or auto-select castxml decl extractor.

- **P06 [PERF/med]** L4 replay is serial per-TU clang: zstd 92 TUs → 73.8s (0.8s/TU).
  Obvious parallelization target (4 idle cores during the run).

- **P07 [DISCOVERY/med]** Plain `compile_commands.json` yields compile_units but
  `targets/toolchains = 0`. Toolchain/compiler identity needs CMake File API or the binary's
  `.GCC.command.line`/`DW_AT_producer`. So "which compiler built this" is invisible from the
  compile DB alone.

### Timings (iteration 1)
- dump (elf_only): 0.3–0.6s; dump (dwarf_aware, libuv 2.1MB snap): 3.9s; compare: 0.3–0.5s
- L3 build data: 0.3–0.5s flat | L4+L5: 9.8s (zlib/34TU) · 73.8s (zstd/92TU) · 9.0s (snappy/4TU C++)
- abicheck self-runs cmake via `.abicheck.yml build.query` + `--allow-build-query`: +0.95s

---

## Iteration 2 — larger products + build-system discovery (meson vs autotools)

### Binary scan (8 larger products, 81s total)

| lib | old→new | so picked | funcs | verdict | total chg | compare_s | snap |
|---|---|---|---|---|---|---|---|
| icu | 75.1→78.3 | libicui18n | 20k | BREAKING | 16427 | **30.1s** | 18.6MB |
| hdf5 | 1.8.20→2.1.0 | libhdf5 | 9k | BREAKING | 1968 | 1.1s | 4.6MB |
| protobuf | 6.34→7.35 | libprotobuf | 10k | BREAKING | 400 | 1.6s | 8.2MB |
| glib | 2.86→2.88 | libgio | 9.5k | COMPATIBLE | 11 | 0.9s | 4.5MB |
| openssl | 3.6.1→4.0.1 | libcrypto | 19k | BREAKING | 5941 | 2.5s | 11MB |
| gmp | 6.2.1→6.3.0 | libgmp | 1.9k | BREAKING | 7 | 0.5s | 1.7MB |
| flac | 1.4.3→1.5.0 | libFLAC++ | 1.2k | BREAKING | 19 | 0.4s | 1MB |
| openblas | 0.3.8→0.3.9 | libopenblas | 26k | COMPATIBLE(dwarf) | 1 | 4.4s | 23MB |

### Problems found

- **P08 [CORRECTNESS/USABILITY/high]** Symbol-naming conventions create huge noise.
  **ICU** embeds the major version in every symbol (`u_foo_75`→`u_foo_78`) → 5800 removed +
  5898 added + **2134 `func_likely_renamed`** = 16427 changes for a routine ICU upgrade. The
  report is unusable without knowing ICU's convention. **OpenSSL** symbol versioning →
  **5640 `symbol_moved_version_node`**. *Fix idea:* convention-aware renamers / a "versioned
  symbol scheme" suppression preset for ICU/OpenSSL-style libs.

- **P09 [DISCOVERY/high]** **Silent empty result on autotools trees.** `configure` emits NO
  `compile_commands.json` (autotools never does). `abicheck dump --sources <autotools-tree>`
  returns `build_source: False`, 0 compile_units, **and 0 diagnostics** — no hint that a compile
  DB was looked for and not found, nor a suggestion to use `bear`. A user gets nothing and no
  explanation. *Fix idea:* emit a diagnostic "no compile_commands.json found under <tree>
  (looked in: . build out _build cmake-build-debug); for autotools/make run `bear -- make`".

- **P10 [DISCOVERY/med]** Autotools-from-git bootstrap is fragile: `libffi` `./autogen.sh`
  failed `LT_SYS_SYMBOL_USCORE undefined` even with libtool+libtool-bin installed. Realistic
  flow is the release tarball (ships `configure`). Not abicheck's bug but it's the discovery
  reality for autotools L3.

- **P11 [PERF/high]** `compare` scales poorly with surface size + change count: ICU (20k funcs,
  16k changes) = **30s** just for compare; dump of a 20MB `.so` = 6s; openblas 26k-func DWARF
  snapshot = 23MB / 9.5s dump. Large libs make the diff the bottleneck, not parsing.

- **P12 [DISCOVERY/low]** Compile-DB auto-discovery hint dirs are `("", build, out, _build,
  cmake-build-debug)`. Meson's common `builddir` name is NOT covered (only `build`/`_build`).
  A meson user who runs `meson setup builddir` won't be auto-discovered.

### Build-system matrix (source-phase discovery)

| build system | compile DB native? | when produced | abicheck auto-discover | L3 time |
|---|---|---|---|---|
| **CMake** | yes (`-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`) | configure | needs `build/` name | 0.3–0.5s |
| **Meson** | **yes, always** | `meson setup` (no build) | ✅ `build/` auto-found | 0.32s |
| **Autotools** | **no** | never | ❌ silent empty (P09) | — |
| Autotools + `bear` | via intercept | needs **full `make`** | ✅ DB at tree root | 0.30s |

- Meson is the smoothest: native compile DB at *configure* time, abicheck picks it up with zero flags.
- Autotools is the worst: no DB ever; `bear -- make` works but forces a full build (libffi 9.75s;
  scales with project size), and abicheck gives no guidance when the DB is absent.

### Timings (iteration 2)
- meson setup (freetype, 42 TU): 0.85s | autotools configure (libffi): 6.5s | bear -- make (libffi, 20 TU): 9.75s
- L3 (meson auto-discover): 0.32s | L3+L4+L5 (freetype 42 TU): 31.9s (~0.76s/TU)
- compare on huge surfaces: icu 30.1s, openssl 2.5s, openblas 4.4s

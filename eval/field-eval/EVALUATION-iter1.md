# abicheck end-to-end evaluation — conda-forge libraries

Environment: 4 cores / 15 GB / Linux. gcc/g++ + **clang 18** present; GNU tar + **zstd** present; **castxml absent**.
abicheck 0.3.0 (editable install). All timings wall-clock on this box.

## 1. The flow I exercised

```
anaconda.org API  →  download .conda  →  extract .so (zstd/tar)  →  abicheck dump (×2)  →  abicheck compare
                                                                         │
                  git clone @tag → cmake configure → compile_commands.json → dump --sources (L3/L4/L5)
```

Conda-forge wrinkles worth knowing:
- Runtime `.so` is often in a **split package** (`zlib`→`libzlib`, `webp`→`libwebp-base`,
  C lib of `zstd`/`lz4` is `zstd`/`lz4-c`). Had to resolve the package that actually ships the `.so.N.M`.
- `libxml2` 2.15.0 build variant I picked shipped only binaries (no `lib/`) — variant selection matters.
- conda release `.so`s are **`not stripped` but carry 0 DWARF sections** → abicheck runs `elf_only`
  (LOW confidence, exported symbols only). Exceptions seen: `bzip2`, `libuv` shipped DWARF → `dwarf_aware`.

## 2. Binary scan — 14 libraries (≈25 s total for fetch+dump+compare of 13)

| lib | old→new | tier | verdict | break/risk/add |
|---|---|---|---|---|
| zlib | 1.2.13→1.3.1 | elf_only | COMPATIBLE_WITH_RISK | 0/1/0 |
| zstd | 1.5.5→1.5.7 | elf_only | **BREAKING** | 3/2/10 |
| xz/liblzma | 5.6.4→5.8.3 | elf_only | COMPATIBLE | 0/0/7 |
| bzip2 | 1.0.6→1.0.8 | dwarf_aware | COMPATIBLE_WITH_RISK | 0/1/0 |
| lz4 | 1.9.3→1.10.0 | elf_only | COMPATIBLE_WITH_RISK | 0/2/10 |
| libpng | 1.6.55→1.6.58 | elf_only | COMPATIBLE | 0/0/1 |
| libjpeg-turbo | 3.0.0→3.1.4.1 | elf_only | COMPATIBLE_WITH_RISK | 0/1/4 |
| pcre2 | 10.44→10.47 | elf_only | COMPATIBLE | 0/0/4 |
| libsodium | 1.0.18→1.0.22 | elf_only | COMPATIBLE_WITH_RISK | 0/2/150 |
| c-ares | 1.34.3→1.34.6 | elf_only | COMPATIBLE | 0/0/5 |
| libssh2 | 1.10.0→1.11.1 | elf_only | **BREAKING** | 128/1/12 |
| snappy | 1.1.10→1.2.2 | elf_only | COMPATIBLE_WITH_RISK | 0/4/8 |
| libuv | 1.49.2→1.52.1 | dwarf_aware | **BREAKING** | 3/1/12 |
| libwebp | 1.4.0→1.6.0 | elf_only | COMPATIBLE_WITH_RISK | 0/1/2 |

Notable real catches: **libssh2** dropped 126 exported symbols at the **same SONAME**
(`libssh2.so.1.0.1`) → 128 breaking. **zstd** 1.5.5→1.5.7 removed 2 exports +
recommends a SONAME bump. **libuv** (DWARF present) caught struct `type_field_removed`
+ `typedef_removed` — only visible because it shipped debug info.

Per-call timing (binary path): **dump ≈ 0.3–3.9 s** (libuv 3.9 s on a 2 169 KB DWARF snapshot;
plain `elf_only` ≈ 0.3–0.6 s), **compare ≈ 0.3–0.5 s**. Fetch+extract ≈ 0.2–1 s/version.

## 3. Build/source data — timing (the headline question)

Measured on three real checkouts at the matching tag:

| lib | lang | TUs | clone | cmake configure | **L3 (build)** | **L3+L4+L5** | **L4+L5 (src+graph)** | per-TU |
|---|---|---|---|---|---|---|---|---|
| zlib | C | 34 | 0.48 s | 1.0 s | **0.36 s** | 10.2 s | **9.8 s** | 0.29 s |
| zstd | C | 92 | 0.63 s | 2.78 s | **0.54 s** | 74.4 s | **73.8 s** | 0.80 s |
| snappy | C++ | 4 | 0.54 s | 4.09 s | **0.33 s** | 9.33 s | **9.0 s** | 2.25 s |

- **Build data (L3) is essentially free: ~0.3–0.5 s**, flat regardless of project size — it just
  parses `compile_commands.json` into normalized build evidence. (Same cost as a plain dump.)
- **Source ABI + graph (L4+L5) is dominated by clang re-parsing every TU**, so it scales with
  **TU count × per-TU header weight**: 0.29 s/TU (simple C) → 0.8 s/TU (zstd, intrinsics-heavy) →
  2.25 s/TU (C++ headers). zstd's 92 TUs ⇒ ~74 s. The L5 graph *fold itself is negligible* — the
  cost is the L4 replay; the graph is built from L4's output for free.
- The L4 replay appears **serial** (per-TU clang); it is the obvious parallelization target.

What you get for L4+L5 (zlib example): L5 graph = 51 nodes (34 compile_unit + 17 source) / 34 edges;
zstd = 141 nodes / 92 edges.

### Caveat that matters: L4 produced an *empty* reachable surface here
For all three, `reachable_declarations / reachable_types / matched_symbols = 0` even though every TU
was parsed. Two reasons: (a) the default **clang** extractor emits inline/template/constexpr **body
fingerprints**, and a pure-C lib like zlib has none of those in its public API; (b) the full
declaration/type table needs the **castxml** backend, which is **not installed** here. So in this
environment you pay the full per-TU clang cost but L4's decl/type findings only really light up for
**C++** libraries (and ideally with castxml available). The artifact tiers (L0/L1) stay authoritative
regardless — L4 being empty never blocks the verdict.

## 4. Who runs cmake / produces compile units?

abicheck is **post-build and non-executing by default** (its core safety rule). It does **not** run
your build unless you opt in. Three ways the compile DB gets produced:

1. **Tree already has one** — `--sources <tree>` auto-discovers `compile_commands.json`
   (looks in `.`, `build/`, `out/`, `_build/`, `cmake-build-debug/`). Zero manual steps.
2. **You generate it** — `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` / `bear -- make` / etc., then
   point `--build-info` at it. (What I did for the table above.)
3. **abicheck runs it for you** — add `.abicheck.yml` with a `build.query` command and pass
   `--allow-build-query`. Verified: a clean zlib checkout with no compile DB →
   `dump --sources --allow-build-query` ran cmake itself and produced **34 compile units in 1.26 s**
   (vs 0.31 s and **0 units** without the flag). The opt-in costs the same as running cmake manually.

Compile units are required because **L4 replay runs clang on each TU with its exact flags/includes**
(needed to find generated headers, `-D` defines, include paths). Without those flags clang can't
parse the TUs correctly — hence the compile DB is the pivot of the whole source-side pipeline.

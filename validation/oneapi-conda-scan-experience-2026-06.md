# oneAPI conda-forge `scan` UX run — findings & follow-ups (2026-06)

A real `abicheck scan` 0.4.0 run over four oneAPI / UXL Foundation libraries,
**binaries from conda-forge, sources from GitHub**, exercising L2 and every
deeper source-scan depth. The brief was deliberately **not** "find ABI bugs in
the libraries" but "stress the scanner itself — UX, ergonomics, and operational
failure modes — on inputs we did not hand-pick." Raw per-run data in
[`data/oneapi-conda-scan-2026-06.json`](data/oneapi-conda-scan-2026-06.json).

This is complementary to [`uxl-scan-levels-timing-2026-06.md`](uxl-scan-levels-timing-2026-06.md)
(which measured the *compare* cost model on curated version pairs); here every
run is a single-version `scan --audit` on an as-shipped conda binary against its
upstream source tree.

## Setup

| | |
|---|---|
| Host | Linux x86_64, Python 3.11, **clang-18 only (no castxml)** → every run forced `ABICHECK_AST_FRONTEND=clang` |
| Binaries | conda-forge `linux-64` `.conda` packages, extracted with `zstandard` (no `conda`/`zstd` on the box) |
| Headers | matching conda-forge `*-devel` / `*-include` packages |
| Sources | `github.com/uxlfoundation/<lib>` shallow clone at the tag matching the conda version |
| Command | `abicheck scan --binary <lib> -H <umbrella.h> -I <include> --public-header-dir <include> --lang c++ --audit --depth <D> [--sources <tree>]` |

| Library | conda pkg | binary | git tag | L0 funcs / vars | L2 types | source files / MB |
|---|---|---|---|---|---|---|
| oneTBB | `tbb 2023.0.0` | `libtbb.so.12.18` (388 K) | `v2023.0.0` | 24 603 / 1 214 | 913 | 688 / 7.7 |
| oneDNN | `onednn 3.12` (omp) | `libdnnl.so.3.12` (72 M) | `v3.12` | 13 127 / 290 | 518 | 3 867 / 67.1 |
| oneDAL | `dal 2026.1.0` | `libonedal.so.4` (11 M) | `2026.1.0` | 20 708 / 1 126 | 954 | 3 072 / 15.9 |
| oneCCL | `oneccl-devel 2022.0.0` | **`libccl.a` (static only)** | `2022.0.0` | — | — | 54 / 0.5 |

## Timing (audit, no diff seed)

| Library | headers (L2) | build (L3) | source (L4/L5) | full |
|---|---|---|---|---|
| oneTBB | 10.0 s | 30.7 s | 30.9 s | 30.5 s |
| oneDNN | 11.4 s | **TIMEOUT > 900 s** | (same root cause) | (same root cause) |
| oneDNN *(+`--changed-path`)* | — | **9.2 s** | — | — |
| oneDAL | 11.0 s | **TIMEOUT > 600 s** | (same root cause) | (same root cause) |
| oneCCL | n/a (static archive) | — | — | — |

Two of the three shared libraries (oneDNN, oneDAL) could not complete a single
`--depth build` audit within a 600–900 s budget; oneTBB (a 7.7 MB tree) finished
in ~30 s. The cliff is the source tree size the pattern pre-scan must chew
through, **not** the binary or the depth itself.

Verdict was **COMPATIBLE** for every run that completed — expected, since these
are single-version audits with no baseline to break against. The exercise was
about *getting a clean run at all*, and that is where the friction lives.

## Conclusions (what the run actually taught us)

1. **L2 works and is fast and uniform** — 10–11 s to dump a 11–72 MB stripped
   conda `.so` + parse its public umbrella header with the clang backend, across
   all three shared libraries. The coverage table (`L0…L5` present/not_collected
   with a remedy string per row) is genuinely good UX.
2. **The headline `scan --audit` hygiene checks silently do nothing on ELF**
   (P1, High) — a real wiring bug, not a coverage gap. `--public-header-dir` is
   dropped on the ELF snapshot path, so 4 of the 8 cross-checks report "skipped:
   no public-header provenance" on *every* run even when you supply it.
3. **Source-depth audit has no scope guard and no progress output** (P2, High) —
   on oneDNN the always-on lexical pattern pre-scan walked the entire 67 MB /
   3 867-file tree single-threaded and ran past 900 s with zero output, looking
   exactly like a hang; oneDAL (16 MB) timed out the same way past 600 s. The fix
   the user must discover is `--changed-path` / `--since` (900 s → 9 s), but
   nothing tells them that.
4. **Getting L2 to parse at all takes non-obvious flag archaeology** (P3, Med) —
   pointing `-H` at the include *directory* (the natural first try) fails on
   real libraries (preview headers with `#error` guards, unresolved relative
   includes). You must instead pass the single umbrella header as `-H` **and**
   re-add the directory as `-I` **and** `--public-header-dir`.
5. **`--depth build/source/full` collect nothing without a compile DB** (P4,
   Med) — pointing `--sources` at an unbuilt checkout (the common case) silently
   degrades L3/L4/L5 to "not_collected"; only the lexical pattern tier runs. The
   message is clear, but the deep depths are effectively unreachable for anyone
   who has not already done a CMake build with `-DCMAKE_EXPORT_COMPILE_COMMANDS`.
6. **conda-forge availability is uneven** (P5, Low) — oneCCL ships only a static
   `libccl.a` (no `.so`), so it cannot be scanned as a shared-library subject at
   all; oneDAL's headers live in a *third* package (`dal-include`) separate from
   both `dal` and `dal-devel`. Neither is abicheck's fault, but both are real
   onboarding cliffs for "scan my conda library."

## Open problems & status

| # | Sev | Status | Problem & evidence |
|---|-----|--------|--------------------|
| **P1** | High | ⏳ open (bug) | **`--public-header-dir` is ignored on the ELF `scan` path → 4 cross-checks always skip.** `scan` builds its snapshot via `service.resolve_input` → `_dump_elf`, which never forwards `public_headers`/`public_header_dirs`; `apply_provenance` runs only on the PE/Mach-O branch (`_apply_native_provenance`). The `dump`/`compare` CLI applies provenance separately (`cli_resolve._resolve_input`), so the two paths disagree. **Reproduced:** `abicheck dump <tbb.so> --public-header-dir <inc>` → 3 440 PUBLIC functions / 377 PUBLIC types; the identical args through `service.resolve_input` → **all 24 603 UNKNOWN**, `_origin_resolvable=False`. Net effect: `exported_not_public`, `public_not_exported`, `private_header_leak`, `rtti_for_internal_type` (the ADR-035 D8 single-release hygiene audit — the *reason* to run `scan --audit`) never fire on any ELF library. Contradicts the "cross-checks now run" status of P1 in `uxl-scan-levels-timing-2026-06.md` for the `scan` entry point specifically. |
| **P2** | High | ⏳ open | **Whole-tree pattern pre-scan, single-threaded, no cap, no progress → looks hung on big repos.** oneDNN `--depth build` timed out > 900 s; py-spy showed it pinned in `pattern_scan.scan_text`. **Not** catastrophic backtracking — a full bounded sweep found 0 files > 2 s. It is pure volume: `iter_source_files` walks the entire `--sources` tree with no exclusion of `third_party/`, `tests/`, or `.git/`, and `_is_scannable` also accepts **extensionless** files — on oneDNN that pulls in 827 extensionless files dominated by multi-MB benchdnn *test-data fixtures* (`tests/benchdnn/inputs/conv/option_set_fwks_ext_gpu` = 3.4 MB) and `.git/index` (522 KB), all run through every ABI regex. Single-threaded at ~0.4 MB/s over 67 MB (oneDNN); oneDAL's 16 MB tree ran even slower per-byte and still blew the 600 s budget. Mitigations that would each help: skip VCS/build/vendor/test dirs by default, size/sniff-cap the extensionless heuristic, parallelize, and emit a progress line. |
| **P3** | Med | ⏳ open | **No working `-H <dir>` story for real libraries.** `-H <include-dir>` (the obvious invocation) hard-fails on all three: oneTBB pulls in `blocked_rangeNd.h` (`#error Set TBB_PREVIEW_BLOCKED_RANGE_ND`) and can't resolve `oneapi/tbb/detail/_utils.h` because the include root isn't auto-added to the search path. Working recipe is the non-obvious triple: `-H <umbrella.h> -I <include> --public-header-dir <include>`. The dir-glob umbrella build should (a) add each `-H`/`-I` dir to the compiler search path automatically, and (b) skip/῾isolate headers that `#error` on missing preview macros rather than failing the whole TU. |
| **P4** | Med | ⏳ open | **`--depth build/source/full` silently collect nothing from an unbuilt `--sources` tree.** No `compile_commands.json` ⇒ L3/L4/L5 = `not_collected`; only the lexical tier runs. Message names the remedy, but there is no auto-discovery of an in-tree build, and the deep depths are unreachable for the majority case (fresh checkout). Consider a `--depth headers`-equivalent fast path note, or an opt-in "configure-only" CMake probe to synthesize a compile DB. |
| **P5** | Low | ⏳ open | **Onboarding cliffs from conda packaging (not abicheck bugs, but worth a docs/runbook note).** oneCCL on conda-forge is static-only (`libccl.a`) → `scan` correctly rejects it with a good message, but there is no shared-library subject to scan at all. oneDAL headers are in `dal-include`, a third package distinct from `dal` (runtime) and `dal-devel` (cmake/pkgconfig only). A "scanning a conda library" guide should call out: fetch the runtime pkg for the `.so`, the `*-include`/`*-devel` pkg for headers, and check for static-only packages. |
| **P6** | Low | ⏳ open | **C-vs-C++ auto-detect warning fires on pure-`#include` umbrellas even with `--lang c++`.** oneTBB's `oneapi/tbb.h` (no inline code) triggered "clang failed to find a C++ standard header parsing in C mode … Retrying in C++ mode. Pass --lang c++" — but `--lang c++` *was* passed and is the `scan` default. The clang backend appears to probe C first regardless; the warning should be suppressed when `--lang c++` is explicit. |

## Testing follow-ups

- **P1:** an integration test that runs `scan --audit -H <umbrella> --public-header-dir <dir>`
  on a *clang-only* host against an ELF fixture and asserts the four provenance
  cross-checks report `present`/run (not `skipped`). Mirror the existing
  `dump`-path provenance assertion so the two snapshot builders can't drift.
- **P2:** a unit test that `iter_source_files` excludes `**/.git/**` and (by
  default) `**/third_party/**` + `**/tests/**`, and a guard that the
  extensionless-header heuristic is byte-capped (a 3.4 MB extensionless data
  file is not a header). A perf smoke asserting a >1 GB-equivalent tree stays
  under a wall-clock budget in `--audit` with no seed.
- **P3:** a `scan -H <include-dir>` test on a tree containing a preview header
  that `#error`s without a macro, asserting the scan still produces an L2
  snapshot (the offending header isolated, not the whole TU failed).
- **P6:** assert no C-mode warning is emitted when `--lang c++` is explicit.

## Reproduction

```bash
# binaries (no conda needed): download + extract the conda-forge .conda zips
#   tbb 2023.0.0 / onednn 3.12 / dal 2026.1.0 + dal-include / oneccl-devel 2022.0.0
# sources: git clone --depth 1 --branch <tag> https://github.com/uxlfoundation/<lib>
export ABICHECK_AST_FRONTEND=clang   # clang-only host
abicheck scan --binary libtbb.so.12.18 \
  -H include/oneapi/tbb.h -I include --public-header-dir include \
  --lang c++ --audit --depth headers          # L2, ~10 s
# deeper depths need a compile_commands.json under --sources; add
#   --changed-path <file> or --since <ref> to keep the pattern pre-scan bounded.
```

# Source-Scan Mapping — epics-base/pvxs (2026-07)

**Date:** 2026-07-06
**Project:** [`epics-base/pvxs`](https://github.com/epics-base/pvxs) — the
PVAccess protocol library (C++11, template-heavy, EPICS Make build system).
**Question:** can abicheck's source scan produce a *complete* source→binary
symbol mapping for a real, non-synthetic library — i.e. get "100% mapping"
(zero exported symbols left unaccounted)?

**Answer: yes.** All **834** exported symbols of `libpvxs.so.1.5` are mapped or
classified — **`unmatched_symbols: 0`**, `symbols_without_decl: 0`. Raw data:
[`data/pvxs_source_scan_mapping_2026-07.json`](data/pvxs_source_scan_mapping_2026-07.json).

---

## Headline

- **100% accounted:** 834/834 exported symbols mapped or explained, 0 unmatched.
- The mapping is *honest*, not force-fit: symbols with no public source
  declaration are **classified** (stdlib instantiations, internal `pvxs::impl`
  exports), never mislabelled as public API — exactly the "every-accounted"
  coverage model in `abicheck/buildsource/source_link.py`.
- Produced through the **portable enabling producer** — the `abicheck-cc`
  compiler wrapper (ADR-038 Flow B) — during a normal EPICS `make`, with no
  compile-DB and no second manual step. pvxs is the running example in
  `contrib/abicheck-clang-plugin/README.md`; the wrapper is the plugin's
  supported, compiler-version-independent equivalent.

## Accounting breakdown (834 exported symbols)

| Bucket | Count | % | Meaning |
|---|---:|---:|---|
| `matched_symbols` | 471 | 56.5% | Exported symbol maps directly to a public source declaration in a pvxs header |
| `synthesized_symbols_matched` | 86 | 10.3% | Compiler-synthesized export attributed to its owning public type/function |
| `template_instantiation_symbols_matched` | 0 | 0% | — |
| `allocator_interposer_symbols_matched` | 0 | 0% | — |
| `non_public_symbols_classified` | 277 | 33.2% | Exported but not public API — classified with a reason, not force-matched |
| **`unmatched_symbols`** | **0** | **0%** | **Nothing left over** |
| **Accounted total** | **834** | **100%** | 471 + 86 + 277 |

### Synthesized attributions (86)

| Kind | Count |
|---|---:|
| `typeinfo` | 29 |
| `typeinfo-name` | 29 |
| `vtable` | 24 |
| `thunk` | 4 |

These `_ZTV…`/`_ZTI…`/`_ZTS…`/thunk symbols carry no source declaration of
their own; abicheck attributes each to the public type/function that owns it, so
they count as accounted rather than unmatched.

### Non-public classifications (277)

| Reason | Count | Example |
|---|---:|---|
| `dependency:stdlib` | 217 | `std::__cxx11::regex_traits<char>::lookup_classname…` |
| `cpp_export_without_public_source_decl` | 52 | `_ZGVZNKSt8__detail11_AnyMatcher…` (function-local static guard) |
| `own_export_without_public_source_decl` | 8 | `pvxs::impl::UDPListener::cnt_UDPListener` (internal counter) |

The 217 `dependency:stdlib` symbols are libstdc++ template instantiations
(`std::regex`, `std::basic_string`, …) emitted into the `.so` — correctly held
*out* of pvxs's public API. The 8 `own_export_*` are `pvxs::impl` internal
symbols that leak as exports but originate from non-public sources; they surface
as classified evidence rather than as public-ABI matches.

### Surface reached (source side)

`reachable_declarations: 28347`, `reachable_types: 4396`,
`reachable_templates: 2365`, `reachable_inline_bodies: 9864`,
`reachable_macros: 924`, across all 34 library translation units.
`odr_conflicts: 578` — expected for a template-heavy library whose types appear
in many TUs; recorded as L4 evidence, not a verdict.

The mapping direction that matters for completeness is *export → declaration*
(`symbols_without_decl: 0`). The reverse, `decls_without_symbol: 1535`, is large
and expected: most public declarations are inline/header-only or otherwise not
emitted as exported symbols, so they legitimately have no binary counterpart —
it is not an unmapped-export gap.

## Method (reproducible)

Toolchain: host compiler **GCC 13.3.0** / **libstdc++ (GLIBCXX up to 3.4.32)** /
**glibc 2.39** (the wrapper passes the compile through to `g++`, so the binary and
its export table — including the 217 `dependency:stdlib` symbols — are produced by
the *host* toolchain); source-fact extractor **LLVM/clang 18.1.3**; abicheck
**0.4.0**; EPICS Base **7.0** (`cf85a1a`); pvxs `0b3fcca`. `castxml` was **not**
installed — the wrapper used the clang AST backend (`ABICHECK_CC_EXTRACTOR=clang`).
A different host libstdc++ can emit a different STL-instantiation export set, so
the exact 834 total is toolchain-specific even though the *accounting* (0
unmatched) is not.

```bash
# 1. Build EPICS Base 7.0 (provides libCom, ca). Pin the exact commit so the
#    export set stays reproducible (fetch-by-SHA needs a full, non-shallow clone).
git clone https://github.com/epics-base/epics-base.git
git -C epics-base checkout cf85a1a5eb86928323d4c62d4d3c553f362a7d04
make -C epics-base -j"$(nproc)"

# 2. Point pvxs at it, use system libevent. Pin the pvxs commit too — the report's
#    834-symbol accounting is specific to this checkout, not to moving HEAD.
git clone https://github.com/epics-base/pvxs.git
git -C pvxs checkout 0b3fcca1fae2c33934c55a20011797fee2637daa
cd pvxs
echo "EPICS_BASE=$PWD/../epics-base" > configure/RELEASE.local
echo "CHECK_RELEASE = NO"          > configure/CONFIG_SITE.local
make -C configure && make -C setup          # bootstrap (installs public headers)

# 3. Build the library through the abicheck-cc wrapper (Flow B) — captures
#    each TU's source ABI during the real compile, no compile DB needed.
export ABICHECK_CC_EXTRACTOR=clang
export ABICHECK_CC_HEADERS="$PWD/include"   # public-header root (how <pvxs/*.h> resolves)
export ABICHECK_INPUTS_DIR="$PWD/abicheck_inputs"
export ABICHECK_CC_LIBRARY=pvxs
# Concurrency of fact extraction in Flow B is bound by `make -jN`, NOT by
# ABICHECK_L4_JOBS (that env var only throttles the Flow-A source-replay path,
# which the wrapper does not use). Each `abicheck-cc` invocation extracts its one
# TU synchronously, so `-jN` = up to N concurrent clang AST dumps. Template-heavy
# TUs can each need multiple GiB; use `-j1` for strict serialization on
# memory-constrained hosts. This run used `-j2`.
make -C src -j2 CC="abicheck-cc gcc" CCC="abicheck-cc g++"

# 4. Dump the binary side, then fold in the captured source facts.
abicheck dump lib/linux-x86_64/libpvxs.so.1.5 -o libpvxs.so.json
abicheck merge libpvxs.so.json ./abicheck_inputs/ -o libpvxs.baseline.json
```

`merge` stderr:

```text
Merged baseline written to libpvxs.baseline.json
  base ABI surface: libpvxs.so.json
  build_source contributors: 1/2
  L3_build: not_collected
  L4_source_abi: present (471/834 symbols matched)
  L5_source_graph: present
```

The headline `471/834` counts **direct decl matches only**; the full
`coverage` dict on the merged surface (extracted into the data artifact) shows
the remaining 363 are all accounted via synthesized attribution (86) and
non-public classification (277), leaving `unmatched_symbols: 0`.

## Notes / caveats

- **`public-roots` must match how headers *resolve*.** pvxs installs public
  headers to `<top>/include/pvxs/…` and the library compiles with `-I<top>/include`,
  so `ABICHECK_CC_HEADERS=<top>/include` matches the resolved paths. A mismatch
  here is the documented empty-pack trap (`contrib/abicheck-clang-plugin/README.md`).
- **The Clang plugin (Flow C) was not used.** It needs `libclang-<N>-dev` for the
  loader's LLVM major and is ABI-locked to it; the `abicheck-cc` wrapper is the
  portable path and reaches the same `abicheck_inputs/` protocol, so the mapping
  result is identical.
- One benign `dump` note — `Unknown DWARF type tag: DW_TAG_ptr_to_member_type` —
  does not affect the export set or the mapping.
- Binaries are intentionally not committed (see `validation/CLAUDE.md`);
  reproduce from the commits above.

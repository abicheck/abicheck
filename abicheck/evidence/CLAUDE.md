# CLAUDE.md — `abicheck/evidence/`

Optional source/build/graph evidence layers (ADR-028 umbrella; ADR-029–033).
See `docs/development/adr/028-source-build-evidence-pack.md` for the
architecture and `docs/concepts/evidence-pack.md` for the user-facing guide.

## The one rule that governs everything here

**Artifact-backed L0/L1/L2 evidence stays authoritative for shipped ABI
verdicts.** Evidence from L3/L4/L5 may *explain, localize, scope, add
confidence/provenance, or correlate* an artifact-proven break — but it must
**never silently delete** one (ADR-028 D3). Findings produced *only* by
L3/L4/L5 are ordinary `ChangeKind` entries that default to `API_BREAK_KINDS`
(source-level breaks) or `RISK_KINDS` (deployment/context risk), never
`BREAKING_KINDS` unless an artifact diff also proves the break.

## Module map

| Module | Role | ADR |
|--------|------|-----|
| `model.py` | `EvidencePackManifest`, `EvidencePackRef`, `LayerCoverage`, `EvidenceEntity`, `EvidenceLayer`/`EvidenceConfidence`/`CoverageStatus` enums | 028 D1/D5/D7/D8 |
| `pack.py` | `EvidencePack` — on-disk layout, content addressing, write/load, `to_ref()` | 028 D1/D4 |
| `build_evidence.py` | `BuildEvidence` normalized model: `Target`, `CompileUnit`, `LinkUnit`, `Toolchain`, `Generator`, `BuildOption` | 029 D1/D2 |
| `build_diff.py` | `diff_build_evidence()` → build-flag/toolchain drift findings | 029 D9 |
| `source_abi.py` | `SourceAbiTu` (per-TU dump) + `SourceAbiSurface` (linked `source_abi.json`) schemas, `SourceEntity`/`SourceLocation`, `L4_SOURCE_ABI` boundary | 030 D4/D5/D10 |
| `source_link.py` | `link_source_abi()` — fold per-TU dumps into a per-library surface; map decls→exported symbols; ODR detection | 030 D5 |
| `source_diff.py` | `diff_source_abi()` → the 9 source-replay findings (macros/default-args/inline/template/constexpr/…); never BREAKING | 030 D6 |
| `source_extractors/` | `SourceAbiExtractor` interface + castxml (phase 2), clang (phase 5, body fingerprints), Android adapter (phase 6) | 030 D3 |
| `source_replay.py` | `select_compile_units()` (D7 scopes), `SourceAbiCache` (D8 per-TU cache), `run_source_replay()` driver, `scope_for_ci_mode()` | 030 D7/D8 (phase 7) |
| `redaction.py` | `RedactionPolicy` — strip secrets/abs paths from command lines | 032 D7 |
| `adapters/compile_db.py` | `compile_commands.json` → `CompileUnit`s (reuses `build_context.py`) | 029 D3 |
| `adapters/cmake_file_api.py` | CMake File API reply → targets/toolchains/fileSets | 029 D4 |
| `adapters/ninja.py` | Ninja `-t compdb`/`graph` (live or pre-captured) | 029 D5 |
| `adapters/bazel.py` | Bazel `cquery`/`aquery` jsonproto → targets/compile+link units (live or pre-captured) | 029 D6 |
| `adapters/make.py` | Make `-n`/`--trace` dry-run transcript → reduced-confidence compile units | 029 D7 |
| `compiler_record.py` | ELF `.GCC.command.line` + DWARF `DW_AT_producer` → toolchain/options (advisory) | 029 D8 |

## Versioning

Four *independent* schema versions — do not conflate:
- `EVIDENCE_PACK_VERSION` (`model.py`) — pack manifest/layout.
- `BUILD_EVIDENCE_VERSION` (`build_evidence.py`) — L3 normalized model.
- `SOURCE_ABI_VERSION` (`source_abi.py`) — L4 `SourceAbiTu`/`SourceAbiSurface`.
- `serialization.SCHEMA_VERSION` — the `AbiSnapshot`, which only stores an
  `EvidencePackRef` (so old snapshot readers ignore it; ADR-015).

## Conventions

- Every dataclass carries `to_dict()`/`from_dict()` with defensive `.get()`
  parsing so a newer/hand-edited pack never aborts a load (forward-compat).
- Normalized facts are the only stable input to comparison/reporting; raw
  tool output under `raw/` is provenance only (ADR-028 D4) and never feeds the
  content hash.
- Adapters must be **post-build and non-executing by default** (ADR-028 D6):
  inspect existing build outputs / query interfaces only. Anything that builds
  or runs project code is explicit opt-in (ADR-032 D5) — not implemented here.
- Adding an L3/L4/L5 `ChangeKind`: follow the four-step procedure in the root
  `CLAUDE.md`, place it in `API_BREAK_KINDS`/`RISK_KINDS` per the rule above,
  and emit it from `build_diff.py` (or the relevant diff module).

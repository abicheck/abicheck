---
doc_type: reference
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - snapshot-storage-compression
lifecycle: active
generated: false
---

# Snapshot Format (`.abi.json`)

`abicheck dump` writes a **snapshot** — a serializable, JSON representation of a
library's ABI surface — and `abicheck compare` reads two snapshots (or a live
binary and a saved snapshot) to produce a verdict. Checking a snapshot into your
repository as a baseline is the recommended way to detect ABI drift over time
(see [Baseline Management](../use/baseline-management.md)).

This page documents the snapshot contract: its schema version, its
compatibility rules, and its top-level structure.

> **Snapshots are not reports.** A snapshot describes *one* library's ABI
> surface. The JSON that `compare` emits is a separate **comparison report** with
> its own version field (`report_schema_version`). The two are versioned
> independently — see [Two contracts](#two-contracts-snapshot-vs-report) below.

---

## Schema version

Every snapshot carries a top-level **`schema_version`** field — a single
**integer** (not `MAJOR.MINOR`). The current value is **`32`** (see
`abicheck/serialization.py`'s `SCHEMA_VERSION` for the authoritative,
up-to-date value and the full per-version history comment).

```json
{
  "schema_version": 32,
  "library": "libfoo.so.1",
  "version": "1.2.3"
}
```

The field is placed at the top level so a loader can inspect it without parsing
the full snapshot. Its history is additive: each bump added fields without
changing the meaning of existing ones — provenance metadata, PE/Mach-O
support, build-mode capture, declaration provenance (`source_header`/`origin`),
embedded build/source evidence, CastXML CV-qualifier reliability, the hybrid
AST frontend's per-fact producer map, the resolved AST toolchain identity,
(v12) the owner class of a hidden friend (`Function.hidden_friend_owner`),
(v13) the CastXML version-gate outcome (`ast_toolchain_supported` /
`ast_toolchain_unsupported_reasons`), (v14) extraction-contract fingerprints
proving two snapshots were compared under a comparable profile/scope
(`AbiSnapshot.contract`, ADR-050 D1 — *verdict-blocking*: see the
compatibility table below), (v15) structured compile-context provenance
for the header-AST parse (`ast_resolved_standard`, `ast_cplusplus_macro`,
`ast_compile_args`, `ast_sysroot`), (v16) DWARF-vs-header-AST layout
coherence (`dwarf_layout_coherence`, `dwarf_layout_coherence_mismatches` —
see "Compile-context provenance" below), (v17) which SYCL/DPC++ AST pass
a header-AST snapshot was built from (`frontend_context_kind`, ADR-050 D5),
(v18) whether `dump`'s default toolchain/system-header exclusion was
applied (`dependency_scope`, see `dumper_scoping.py`), (v19) whether the
direct-clang backend's `deprecated`/`is_scoped` facts are reliable
(`clang_deprecation_facts_reliable`, G31 Phase C — those facts became
genuinely populated by the clang backend at this version; see
`dumper_clang.py`), and (v20) whether the direct-clang backend's
`TypeField.default` (default member initializer) facts are reliable
(`clang_field_initializer_facts_reliable`, G31 Phase C — same shape as v19,
one fact and one version later; see `dumper_clang_expr._field_initializer_value`),
and (v21) whether the direct-clang backend's `RecordType.vtable`/
`vptr_offset_bits` facts are reliable (`clang_vtable_facts_reliable`, G31
Phase C — the direct-clang backend's vtable/vptr reconstruction; see
`dumper_clang_vtable.py`), and (v22) whether the direct-clang backend's
`Param.is_restrict` facts are reliable (`clang_restrict_facts_reliable`, G31
Phase C — castxml was that fact's only producer until this version; see
`dumper_clang._clang_param_is_restrict`), and (v23) whether the direct-clang
backend's `Param.is_va_list` facts are reliable
(`clang_va_list_facts_reliable`, G31 Phase C continued — no backend had
populated this fact at all before this version, and only for the x86-64
System V spelling; see `dumper_clang_qualifiers._clang_param_is_va_list`),
and (v24) whether the castxml backend's `Variable.access` facts are
reliable (`castxml_var_access_facts_reliable`, G31 Phase C continued — no
backend had populated this fact at all before this version; see
`dumper_castxml._CastxmlParser._access_level`), and (v25) a fully-qualified-
name-keyed twin of `typedefs` (`AbiSnapshot.typedefs_qualified`, G31 Phase C
continued) that closes a bare-name collision between two member typedefs
sharing a spelling in different classes/namespaces — needs no reliability
flag, since an empty dict degrades identically to "no typedefs at all" for
a pre-v25 snapshot, unlike the real-but-wrong scalar defaults v19-v23 above
guard against, (v26) `Fact[T]` siblings for `RecordType.bases_fact`/
`virtual_bases_fact`/`vtable_fact`/`vptr_offset_bits_fact` and
`Param.is_va_list_fact` (ADR-063 Phase 0, see `storage/fact_codec.py`),
(v27) `Function.is_compiler_generated` — closes a castxml L4 extractor bug
where a compiler-synthesized implicit special member leaked into the source
graph as if it were genuine public API; needs no reliability flag, since
`None` (a pre-v27 snapshot's default) degrades cleanly to today's inclusive
behavior rather than being misread as "confirmed user-written", (v28) each
declaration's `entity_id` carrier persisted through its own codec
(`storage/entity_id_codec.py`), (v29) `AbiSnapshot.surface_graph` — the
unconditional public-surface/L5 evidence graph (ADR-063 Phase 3 D5, see
"Fields" below) persisted through its own `to_dict()` encoding, not
`asdict()`'s naive recursion, and (v30) `RecordType.is_final_fact` — the
fact/capability registry's (ADR-063 Phase 5 D7,
`abicheck/model/fact_registry.py`) first registered `Fact[T]` conversion;
needs no reliability flag, since `is_final`'s own `None` already
unambiguously means "not captured", (v31) `RecordType.is_abstract_fact`/
`data_size_bits_fact`/`is_standard_layout_fact`/`is_trivially_copyable_fact`/
`qualified_name_fact`/`source_header_fact` — the same registry's next batch
of case-(b) conversions (fields already `X | None`-typed, so the existing
"`None` already unambiguously means not captured" bridge applies directly);
`qualified_name_fact` is the one field in this batch both header backends
construct as an explicit `Fact.present(...)` rather than relying on the
generic bridge, since a `None` qualified name at global scope is itself a
confirmed determination, not missing evidence, and (v32)
`EnumType.qualified_name_fact`/`source_header_fact` — the identical
case-(b) pattern applied to `EnumType`'s own twin fields.

### Forward / backward compatibility

abicheck loads a snapshot best-effort and never migrates it in place. The rule
is determined entirely by comparing the file's `schema_version` against the
`SCHEMA_VERSION` the running abicheck supports:

| File `schema_version` | Behavior on load |
|-----------------------|------------------|
| **Missing** | Treated as `1` (the pre-versioning format) and loaded normally. |
| **Older or equal** to this build (`<= 32`) | Loaded cleanly. Fields introduced by newer versions are absent and fall back to their defaults (`None`, empty, or a tri-state `None` that suppresses the detectors depending on that evidence). No warning. |
| **Newer** than this build, **and** `< 14` | Loaded **best-effort** with a `UserWarning` ("Data may be incomplete or misinterpreted. Upgrade abicheck…"). The load is **not** aborted — unrecognised keys are ignored and recognised keys are read. |
| **Newer** than this build, **and** `>= 14` | **Hard-rejected** — `IncompatibleSnapshotSchemaError` — instead of warn-and-continue. |

Two consequences worth internalising:

- **Reading is version-tolerant in both directions.** An older baseline
  produced by an earlier abicheck loads without error against a newer abicheck;
  missing fields simply take defaults. This is what makes checked-in baselines
  durable across tool upgrades.
- **A newer snapshot usually warns rather than fails — but not once a
  verdict-blocking field exists.** Prior to v14 every bump was purely
  additive, so an older reader can safely ignore a field it doesn't
  recognise. Starting at v14, `AbiSnapshot.contract` (ADR-050 D1) makes a
  bump *verdict-blocking*: a reader that silently dropped it could compare
  two possibly-incomparable snapshots and produce an ordinary, wrong
  verdict. `snapshot_from_dict` therefore hard-rejects (rather than
  warns-and-loads) any file `schema_version` that is both newer than the
  running build's `SCHEMA_VERSION` and `>= 14`
  (`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` in
  `abicheck/serialization.py`) — this only protects readers built from that
  guard's introduction onward; see the `SCHEMA_VERSION` history comment for
  the full explanation of what it can and cannot retroactively protect.
  Upgrade abicheck to read a newer snapshot faithfully.

---

## Storage encoding (ADR-059)

Everything above describes the **logical** snapshot — the decoded JSON
payload. On disk, that payload may be stored plain, gzip-compressed, or
zstd-compressed; compression is a storage/transport envelope around the
identical JSON, never a new schema, and never changes `schema_version`,
`AbiSnapshot.contract`, evidence depth, `build_source`, or the verdict two
snapshots produce when compared.

| Encoding | Canonical suffix | Notes |
|---|---|---|
| plain | `.abicheck.json` / `.abi.json` | debugging, small Git-reviewable snapshots |
| gzip | `.abicheck.json.gz` / `.abi.json.gz` | universal interoperability |
| zstd | `.abicheck.json.zst` / `.abi.json.zst` | **preferred** for baseline/release/cache storage |

`compare`, `scan --against`, and the Python API
(`abicheck.serialization.load_snapshot`) all *read* every encoding
transparently — detected from magic bytes, not just the filename suffix.
`abicheck dump` *produces* one: it infers the encoding from `-o/--output`'s
suffix by default (`--compression auto`), or accepts an explicit
`--compression {none,gzip,zstd}`; `write_snapshot` is the Python API
equivalent for writing. See [ADR-059](../contribute/adr/059-compressed-snapshot-storage.md)
for the full storage-envelope model (determinism, atomic writes,
decompression limits, and what's still deferred).

---

## Top-level structure

A snapshot is a single JSON object. The keys below are the ones written by the
serializer (`abicheck/serialization.py`) from the `AbiSnapshot` model
(`abicheck/model/snapshot.py`). Optional keys are omitted or `null` when there is no data
(for example, a pure-ELF dump has no `dwarf` or `build_source`).

### Identity and provenance

| Key | Type | Meaning |
|-----|------|---------|
| `schema_version` | int | Snapshot format version (currently `32`). |
| `library` | string | Library identity, e.g. `libfoo.so.1`. |
| `version` | string | Library version string, e.g. `1.2.3`. |
| `source_path` | string \| null | Original path the snapshot was taken from. |
| `platform` | string \| null | `elf`, `pe`, `macho`, or null. |
| `language_profile` | string \| null | `c`, `cpp`, `sycl`, or null. |
| `git_commit` | string \| null | Git SHA captured at dump time. |
| `git_tag` | string \| null | Git tag (e.g. `v2.0.0`), supplied or auto-detected. |
| `created_at` | string \| null | ISO 8601 timestamp set at dump time. |
| `build_id` | string \| null | Opaque CI identifier (run ID, build number). |
| `contract` | object \| null | ADR-050 D1 extraction-contract fingerprints (schema v14, *verdict-blocking* — see "Forward / backward compatibility" above): `profile_fingerprint`/`scope_fingerprint` plus their named resolved sub-inputs, proving two snapshots were extracted under a comparable profile/scope. `null` when no producer populated it yet. |
| `dependency_scope` | string \| null | (schema v18) `"filtered"` when the toolchain/system-header exclusion (`dumper_scoping.py`) was applied, `"full"` when opted out via `--include-system-declarations`. `dump` and `compare`'s live-binary dumping (`service.run_dump`) both filter by default (`include_dependencies=False`) and tag `"filtered"`; a Python API caller of `service.run_dump`/`resolve_input` gets the opposite default (`include_dependencies=True`, tagging `"full"`), preserving every existing caller that doesn't opt in explicitly. `scan`'s own candidate is the one exception: it also filters by default, but derives its actual mode from a `--against`/`--baseline` JSON snapshot's own explicit tag (`scan_engine._scan_candidate_include_dependencies`) — unfiltered only when that baseline is itself explicitly tagged `"full"`, since `scan` has no `--include-system-declarations` flag of its own to request that directly. `null` on any pre-v18 snapshot or any snapshot with no header-derived declarations. `comparability.check_contracts_comparable` raises `ScopeMismatchError` only when BOTH sides carry an explicit, non-null value and they differ — `null` is deliberately NOT treated as `"full"` (an ordinary pre-v18 baseline is usually already-filtered content that simply predates this tag; assuming `"full"` for it would spuriously flag the routine "compare a cached baseline against a fresh dump" workflow), so a genuinely ambiguous untagged snapshot is left unchecked on this axis rather than guessed at. |

### Compile-context provenance (schema v15, header-AST parses only)

Populated only when the snapshot came from a header-AST parse (`from_headers`
true); `null`/empty on a DWARF/symbols-only or binary-only snapshot, and on
any pre-v15 snapshot. `ast_compile_args` and `ast_sysroot` are redacted via
the same `RedactionPolicy` every L3 build-evidence adapter applies (secret-
looking `-D` values and absolute home-prefixed paths are stripped/normalized
before persistence — see `abicheck/buildsource/redaction.py`).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `ast_resolved_standard` | string \| null | `null` | The C/C++ standard actually used for the header parse: an explicit `-std=`/`--std=`/`/std:` value verbatim, or `"gnu++20"` when the requires/concept heuristic forced it. `null` means the frontend's own unpinned default was used (never guessed at). |
| `ast_cplusplus_macro` | string \| null | `null` | The standard-mandated `__cplusplus` literal for `ast_resolved_standard` (e.g. `"201703L"` for `"gnu++17"`), looked up from a static ISO-standard table. `null` when `ast_resolved_standard` is unset or not a recognized C++ edition. |
| `ast_compile_args` | array of strings | `[]` | The ordered extra compiler arguments passed to the header frontend (`--compiler-option` tokens, then a shlex-split composed-flags string derived from the compilation database `--build-info` resolves to), redacted. |
| `ast_sysroot` | string \| null | `null` | The `--sysroot` passed to the header frontend, if any, redacted. |

`ast_toolchain` (`dict[str, str]`, populated since schema v9) carries the
exact tool identity behind the header-AST parse. It is untyped/free-form —
new keys are additive and never require a schema bump — but these keys are
stable and machine-checked by `tests/test_tool_identity.py`/
`tests/test_castxml_policy.py`:

| Key | Meaning |
|-----|---------|
| `selected` / `compiler_selected` | The exact frontend/host-compiler executable path selected from `PATH` (or an explicit `--compiler`). |
| `realpath` / `compiler_realpath` | The same path with symlinks resolved. |
| `sha256` / `compiler_sha256` | SHA-256 of the executable's file contents, so a same-version binary rebuild/repackage still changes provenance. |
| `version` / `compiler_version` | The raw, bounded `--version` transcript for that exact executable revision. |
| `target_triple` / `compiler_target_triple` | The `<tool> -dumpmachine` output for that executable (GCC/G++/Clang/Clang++ only — omitted, not empty, for a tool that doesn't support the flag, e.g. castxml itself or MSVC `cl.exe`). |
| `castxml_version` | CastXML's own release version (e.g. `"0.7.0"`), parsed from `version` — castxml-producer snapshots only. |
| `castxml_bundled_clang_version` | The bundled/linked Clang's `major.minor` (e.g. `"18.1"`), parsed from `version` — castxml-producer snapshots only. Kept separate from `castxml_version` since the two floors (`MIN_CASTXML`, `MIN_CASTXML_CLANG_MAJOR` in `castxml_policy.py`) are independently enforced by the version gate. |

A `hybrid` snapshot (`ast_producer == "hybrid"`) namespaces every key from
both runs instead of picking one — `castxml_selected`, `castxml_version`,
`castxml_castxml_version`, `clang_selected`, `clang_target_triple`, and so
on (`dumper_hybrid.py`'s merge is a generic `castxml_`/`clang_`-prefixed
dict union, so a key that already started with `castxml_` on the castxml
side is not special-cased).

### DWARF-vs-header-AST layout coherence (schema v16)

The clang L2 header backend is layout-blind (no `size_bits`/`alignment_bits`/
field `offset_bits`) — when the binary being dumped also carries DWARF debug
info, `dumper_layout_backfill.backfill_dwarf_layout()` backfills that layout
from the same binary's DWARF, but only for a record it can corroborate as
the *same* declaration (matching name, kind, and field/base overlap — see
that function's docstring for the exact rules). These two fields make that
corroboration outcome visible instead of silent; they never change *what*
gets backfilled, only report on it.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `dwarf_layout_coherence` | string \| null | `null` | One of `"matched"` (every record eligible for backfill was corroborated, or none needed it), `"partial"` (some corroborated, some had no DWARF candidate at all — benign, e.g. declared-but-never-instantiated), `"mismatch"` (at least one record found a uniquely-named DWARF candidate but the two disagreed — backfill already refused to merge that record's layout), or `"unavailable"` (the clang backend ran but the binary carried no usable DWARF at all). `null` on any snapshot not built via the clang L2 backend (a castxml snapshot computes layout directly — not a coherence question) and on any pre-v16 snapshot. |
| `dwarf_layout_coherence_mismatches` | array of strings | `[]` | Header record names backfill found a uniquely-named DWARF candidate for but rejected as uncorroborated — populated only when `dwarf_layout_coherence == "mismatch"`. |

### SYCL/DPC++ frontend context (schema v17, header-AST parses only)

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `frontend_context_kind` | string \| null | `null` | Which AST pass (`"host"` or `"device"`) this header-AST snapshot's clang backend selected via `--frontend-context` (ADR-050 D5, `sycl_context.py`). `null` on any non-SYCL/DPC++ invocation and on any pre-v17 snapshot. |

### ABI surface

| Key | Type | Meaning |
|-----|------|---------|
| `functions` | array | Exported functions (name, mangled name, return type, params, virtuality, access, provenance). |
| `variables` | array | Exported global/static variables. |
| `types` | array | Records (struct/class/union) with fields, bases, vtable, and layout descriptors. |
| `enums` | array | Enumerations with members and underlying type. |
| `typedefs` | object | Typedef name → underlying type. Bare-name-keyed; two distinct member typedefs sharing a spelling in different classes/namespaces collide onto one key (see `typedefs_qualified`). |
| `typedefs_qualified` | object | Fully-qualified-name-keyed twin of `typedefs` (schema v25) — collision-free. Empty for a pre-v25 snapshot or one produced without per-class qualified typedef scoping (e.g. DWARF-only). |
| `constants` | object | Preprocessor/compile-time constants (name → value). |

### Evidence-tier and mode flags

| Key | Type | Meaning |
|-----|------|---------|
| `elf_only_mode` | bool | True when dumped without headers (all functions carry ELF-only provenance). |
| `from_headers` | bool | True when the surface was parsed from public headers (drives the header-aware evidence tier). Omitted from the file when it was only *inferred* on load, so a reload re-runs the same inference. |
| `scope_fallback` | string \| null | Public-scope fallback marker. |
| `parsed_with_build_context` | bool | True when parsed with build-context evidence (ADR-029). |

### Platform and debug metadata (optional)

| Key | Type | Meaning |
|-----|------|---------|
| `elf` | object \| null | ELF metadata: SONAME, `DT_NEEDED`, version defs/reqs, symbols, imports, hardening flags. |
| `pe` | object \| null | PE/COFF metadata (Windows DLL exports, machine, characteristics). |
| `macho` | object \| null | Mach-O metadata (dylib exports, CPU slices, install name). |
| `dwarf` | object \| null | DWARF struct/enum layout. |
| `dwarf_advanced` | object \| null | Toolchain, calling conventions, value-ABI traits. |
| `sycl` | object \| null | SYCL plugin-interface metadata. |
| `dependency_info` | object \| null | Resolved dependency graph (nodes, edges, unresolved). |
| `build_mode` | object \| null | Normalized compiler/stdlib/standard capture (ADR build-mode work). |

### Embedded build/source evidence (optional)

| Key | Type | Meaning |
|-----|------|---------|
| `build_source_pack` | object \| null | Reference to an out-of-band build/source pack (ADR-028). Older snapshots may store this under the legacy key `evidence_pack`, which the loader still reads. |
| `build_source` | object \| null | Inline-embedded build/source facts for single-artifact workflows. Omitted when nothing was embedded. |
| `surface_graph` | object \| omitted | (v29, ADR-063 Phase 3 D5) The unconditional public-surface/L5 evidence graph — never gated on `build_source`, unlike the row above. The key is omitted entirely (not written as `null`) for a snapshot predating this field, a binary-only snapshot, or one whose headers were never parsed — `encode_surface_graph()` pops the key rather than writing a null placeholder. When `build_source.source_graph` is the identical object, it is omitted from `build_source`'s own encoding rather than written twice; the loader restores that alias on read. |
| `build_context_defines` | array of strings | The build's active `-D` macro set, harvested from a compile database (ADR-039). Empty when no compile database was supplied. |
| `conditional_fields` | object | `{type: {field: {guard, type, is_bitfield, ...}}}` registry of record fields guarded by a single positive `#ifdef`/`#if defined(...)`, including fields a context-free header parse pruned from `types[].fields` (ADR-039). Feeds the opt-in `--reconcile-build-context` diff pass; empty when no compile database was supplied at dump time. |

> Internal cache fields on the model (`_func_by_mangled`, `_var_by_mangled`,
> `_type_by_name`) and the runtime-only `from_headers_inferred` qualifier are
> **never** serialized.

---

## Two contracts: snapshot vs report

`schema_version` and `report_schema_version` are different fields on different
files:

| | Snapshot (`dump`) | Comparison report (`compare --format json`) |
|-|-------------------|---------------------------------------------|
| **Version field** | `schema_version` | `report_schema_version` |
| **Type** | integer (currently `32`) | string `MAJOR.MINOR` (e.g. `1.0`) |
| **Describes** | one library's ABI surface | the diff between two snapshots |

A snapshot has no `report_schema_version`, and a report has no
`schema_version`; the two version numbers evolve independently. For the report
contract and its stability policy, see
[Output Formats](../use/output-formats.md).

---

## Stability guidance

- **Check baselines into version control.** A saved `.abi.json` is the intended
  input to `compare`; storing one per release lets CI diff each build against
  the last shipped ABI. See
  [Baseline Management](../use/baseline-management.md).
- **Older baselines stay readable.** Because loading fills missing newer fields
  with defaults, a baseline written by an earlier abicheck compares correctly
  against a live binary dumped by a newer one — no regeneration required for a
  routine tool upgrade.
- **Regenerate when you want new evidence.** Fields added in a newer
  `schema_version` (e.g. build-mode or embedded source evidence) are only
  present in freshly-dumped snapshots. Re-dump the baseline to benefit from
  detectors that rely on that evidence.
- **Pin the abicheck version in CI** if a `UserWarning` about a newer
  `schema_version` would be treated as an error in your pipeline.

---

## See also

- [Baseline Management](../use/baseline-management.md) — producing, storing, and comparing snapshots as ABI baselines.
- [Output Formats](../use/output-formats.md) — the comparison-report JSON and `report_schema_version`.
- [ADR-059](../contribute/adr/059-compressed-snapshot-storage.md) — the compressed storage envelope (plain/gzip/zstd).

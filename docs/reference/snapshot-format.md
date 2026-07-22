# Snapshot Format (`.abi.json`)

`abicheck dump` writes a **snapshot** — a serializable, JSON representation of a
library's ABI surface — and `abicheck compare` reads two snapshots (or a live
binary and a saved snapshot) to produce a verdict. Checking a snapshot into your
repository as a baseline is the recommended way to detect ABI drift over time
(see [Baseline Management](../user-guide/baseline-management.md)).

This page documents the snapshot contract: its schema version, its
compatibility rules, and its top-level structure.

> **Snapshots are not reports.** A snapshot describes *one* library's ABI
> surface. The JSON that `compare` emits is a separate **comparison report** with
> its own version field (`report_schema_version`). The two are versioned
> independently — see [Two contracts](#two-contracts-snapshot-vs-report) below.

---

## Schema version

Every snapshot carries a top-level **`schema_version`** field — a single
**integer** (not `MAJOR.MINOR`). The current value is **`13`** (see
`abicheck/serialization.py`'s `SCHEMA_VERSION` for the authoritative,
up-to-date value and the full per-version history comment).

```json
{
  "schema_version": 13,
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
(v12) the owner class of a hidden friend (`Function.hidden_friend_owner`), and
(v13) the CastXML version-gate outcome (`ast_toolchain_supported` /
`ast_toolchain_unsupported_reasons`).

### Forward / backward compatibility

abicheck loads a snapshot best-effort and never migrates it in place. The rule
is determined entirely by comparing the file's `schema_version` against the
`SCHEMA_VERSION` the running abicheck supports:

| File `schema_version` | Behavior on load |
|-----------------------|------------------|
| **Missing** | Treated as `1` (the pre-versioning format) and loaded normally. |
| **Older or equal** to this build (`<= 13`) | Loaded cleanly. Fields introduced by newer versions are absent and fall back to their defaults (`None`, empty, or a tri-state `None` that suppresses the detectors depending on that evidence). No warning. |
| **Newer** than this build (`> 13`) | Loaded **best-effort** with a `UserWarning` ("Data may be incomplete or misinterpreted. Upgrade abicheck…"). The load is **not** aborted — unrecognised keys are ignored and recognised keys are read. |

Two consequences worth internalising:

- **Reading is version-tolerant in both directions.** An older baseline
  produced by an earlier abicheck loads without error against a newer abicheck;
  missing fields simply take defaults. This is what makes checked-in baselines
  durable across tool upgrades.
- **A newer snapshot warns rather than fails.** If a teammate's newer abicheck
  wrote a `schema_version` your build does not know, your build still loads it,
  but emits a warning because renamed or newly-required provenance may be
  silently dropped. Upgrade abicheck to read it faithfully.

---

## Top-level structure

A snapshot is a single JSON object. The keys below are the ones written by the
serializer (`abicheck/serialization.py`) from the `AbiSnapshot` model
(`abicheck/model.py`). Optional keys are omitted or `null` when there is no data
(for example, a pure-ELF dump has no `dwarf` or `build_source`).

### Identity and provenance

| Key | Type | Meaning |
|-----|------|---------|
| `schema_version` | int | Snapshot format version (currently `13`). |
| `library` | string | Library identity, e.g. `libfoo.so.1`. |
| `version` | string | Library version string, e.g. `1.2.3`. |
| `source_path` | string \| null | Original path the snapshot was taken from. |
| `platform` | string \| null | `elf`, `pe`, `macho`, or null. |
| `language_profile` | string \| null | `c`, `cpp`, `sycl`, or null. |
| `git_commit` | string \| null | Git SHA captured at dump time. |
| `git_tag` | string \| null | Git tag (e.g. `v2.0.0`), supplied or auto-detected. |
| `created_at` | string \| null | ISO 8601 timestamp set at dump time. |
| `build_id` | string \| null | Opaque CI identifier (run ID, build number). |

### ABI surface

| Key | Type | Meaning |
|-----|------|---------|
| `functions` | array | Exported functions (name, mangled name, return type, params, virtuality, access, provenance). |
| `variables` | array | Exported global/static variables. |
| `types` | array | Records (struct/class/union) with fields, bases, vtable, and layout descriptors. |
| `enums` | array | Enumerations with members and underlying type. |
| `typedefs` | object | Typedef name → underlying type. |
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
| **Type** | integer (currently `8`) | string `MAJOR.MINOR` (e.g. `1.0`) |
| **Describes** | one library's ABI surface | the diff between two snapshots |

A snapshot has no `report_schema_version`, and a report has no
`schema_version`; the two version numbers evolve independently. For the report
contract and its stability policy, see
[Output Formats](../user-guide/output-formats.md).

---

## Stability guidance

- **Check baselines into version control.** A saved `.abi.json` is the intended
  input to `compare`; storing one per release lets CI diff each build against
  the last shipped ABI. See
  [Baseline Management](../user-guide/baseline-management.md).
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

- [Baseline Management](../user-guide/baseline-management.md) — producing, storing, and comparing snapshots as ABI baselines.
- [Output Formats](../user-guide/output-formats.md) — the comparison-report JSON and `report_schema_version`.

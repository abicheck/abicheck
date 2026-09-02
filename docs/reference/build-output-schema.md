# `build-output.json` Reference

`build-output.json` is a standardized, producer-agnostic contract for a
project's *existing* build to publish once — "build once, scan many"
(G30/ADR-047 §2). abicheck never owns the build: a project's own build
system, or an `install` step, populates an `abicheck-build/` directory that
downstream tooling then validates and consumes.

> **Status.** This page documents the schema and the
> `abicheck project validate-build` command shipped in G30 P1.1. The
> consumers that read a validated `build-output.json` to resolve a
> baseline or run a check — `resolve-baseline`/`check-target` (G30
> P1.2/P1.3) and `abicheck project plan`/[`check-project.yml`](reusable-workflows.md)
> (G30 P1.4) — are all shipped; see the
> [G30 plan](../contribute/plans/g30-github-actions-integration-model.md).
> There is still no `abicheck project emit-build` producer helper; author
> `build-output.json` by hand or from your build's own `install` step.

## Directory layout

```text
abicheck-build/
  build-output.json          # this page's schema
  artifacts/                 # binaries as published by the real build
  headers/                   # public header roots, as-installed layout
  generated-headers/         # codegen/configure output, kept separate from headers/
  evidence/
    compile_commands.json    # if produced
    abicheck_inputs/         # source-facts pack (see producing-source-facts.md)
  provenance/                # toolchain version dumps, build logs digest, etc.
```

`generated-headers/` is deliberately separate from `headers/` so a codegen/
configure step that silently didn't run can't be mistaken for an
as-installed header root — see [Validation rules](#validation-rules) below.

## Schema (`abicheck.build-output/v1`)

```json
{
  "schema": "abicheck.build-output/v1",
  "project": "epics-base/pvxs",
  "head_sha": "b7e2c1a...",
  "source_tree_digest": "sha256:...",
  "profile": {
    "id": "linux-x86_64-gcc13-release",
    "os": "linux", "arch": "x86_64",
    "compiler": {"family": "gcc", "version": "13.2.0"},
    "cxx_abi": "itanium", "stdlib": "libstdc++",
    "config": "release"
  },
  "targets": [
    {
      "id": "libpvxs",
      "binary": "artifacts/lib/libpvxs.so.1.5",
      "public_header_roots": ["headers/pvxs"],
      "generated_header_roots": ["generated-headers/pvxs"],
      "compile_context": {"include_dirs": ["headers", "generated-headers"], "defines": ["PVXS_ENABLE_EXPERT_API"]},
      "bundle": "pvxs-release",
      "evidence": {"kind": "source-facts", "path": "evidence/abicheck_inputs", "projection": "declared"}
    },
    {"id": "libpvxsIoc", "binary": "artifacts/lib/libpvxsIoc.so.1.5", "...": "..."}
  ],
  "bundles": [{"id": "pvxs-release", "targets": ["libpvxs", "libpvxsIoc"]}],
  "evidence_producer": {"kind": "wrapper", "tool": "abicheck-cc", "version": "0.x.y"},
  "digests": {"artifacts/lib/libpvxs.so.1.5": "sha256:..."},
  "diagnostics": {"warnings": [], "skipped_targets": []}
}
```

Every field is optional and defaulted (the `buildsource` package-wide
forward-compatibility convention) — a hand-written or partially-populated
manifest never aborts a load. `abicheck project validate-build` is what turns
missing/inconsistent fields into an actionable report.

### Top-level fields

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | Must be `"abicheck.build-output/v1"`. |
| `project` | string | Free-text project identifier, e.g. `"owner/repo"`. |
| `head_sha` | string | The commit this build was produced from. |
| `source_tree_digest` | string | Content digest of the source tree at that commit. |
| `profile` | object | This build's OS/arch/compiler/config identity — see below. |
| `targets` | array | One entry per library/binary this build produced — see below. |
| `bundles` | array | Named groups of targets built/released together: `{"id", "targets": [...]}`. |
| `evidence_producer` | object | Which tool produced L3/L4/L5 evidence: `{"kind", "tool", "version"}`. |
| `digests` | object | Map of `targets[].binary` path → `"sha256:<hex>"`, checked by the validator. |
| `diagnostics` | object | Free-form producer diagnostics (warnings, skipped targets); informational only. |

**`profile` is singular by design.** A single build produces binaries for
exactly one OS/arch/compiler/config combination, so one `build-output.json`
can only ever describe one profile — never a list. A project matrixing over
several profiles publishes one uniquely-named
`abicheck-build-<profile.id>/` artifact per profile (S17 in the ADR-047
scenario catalog), not one artifact holding several.

**`profile.id` is required, not just recommended, for [`check-project.yml`](reusable-workflows.md) callers.**
Every other field really is optional-and-defaulted per the note above, but
`check-project.yml`'s `plan` job derives each `--build-output PROFILE=DIR`
argument from `profile.id` alone (`find`-ing every downloaded
`build-output.json` and reading its own `profile.id`, rather than the
artifact/directory name — `actions/download-artifact` flattens a
single-artifact match with no subdirectory, so the name is ambiguous by
construction) and hard-fails the `plan` job if a file has no `profile.id`
set. Set it explicitly if your build-output producer targets
`check-project.yml`.

### `targets[]` fields

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | The target's identifier — must be unique within this file. |
| `binary` | string | Path (relative to the `build-output.json` root) to the shipped artifact. |
| `public_header_roots` | array of string | As-installed public header directories for this target. |
| `generated_header_roots` | array of string | Public header directories populated by codegen/configure — kept separate from `public_header_roots` so an empty codegen step is a hard validation failure, not a silent gap. |
| `compile_context` | object | Free-form compile context (`include_dirs`, `defines`, ...), informational. |
| `bundle` | string | The `bundles[].id` this target belongs to, if any. |
| `evidence` | object | This target's L3/L4/L5 evidence pointer — see below. |

### `evidence` fields

| Field | Type | Meaning |
|-------|------|---------|
| `kind` | string | Evidence kind, e.g. `"source-facts"`. |
| `path` | string | Path (relative to the `build-output.json` root) to the evidence — typically an `abicheck_inputs/` pack (see [Producing Source Facts](../use/producing-source-facts.md)). |
| `projection` | string | `"declared"` or `"inferred"` — see below. **Only `"declared"` validates today.** |

**`projection` is the field the P1.1 validator gates on.** `"declared"`
means the build itself asserted this evidence pack belongs to exactly this
target (e.g. per-target compile-DB filtering, or a wrapper invoked once per
link step). `"inferred"` would mean abicheck derived the association from a
build-wide pack via TU→link-unit→DSO attribution — that attribution
mechanism is G30 P2, not built yet, so `abicheck project validate-build`
treats `"inferred"` (and any value other than `"declared"`) as a **hard
validation failure**, not a lower-confidence warning. Until P2 ships, a
build-wide evidence pack may only feed a build-wide source audit or a
per-target header-depth check — never a per-target `effective_depth: source`
claim (see the [multi-DSO recipe's scope
caveat](../use/github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for the practical consequence of this rule).

## Validation rules

`abicheck project validate-build DIRECTORY` checks, per ADR-047 §11.1:

1. **Every declared header root is non-empty.** Each `public_header_roots`/
   `generated_header_roots` entry must resolve to an existing, non-empty
   directory under the `build-output.json` root. An empty
   `generated_header_roots` entry is always a hard error — it almost always
   means a codegen/configure step that was supposed to populate it never
   ran. A target that declares no `generated_header_roots` at all makes no
   claim and is never checked.
2. **Every `targets[].binary` exists and matches `digests{}`.** The binary
   file must exist under the `build-output.json` root, and `digests{}` must
   carry a matching `sha256:<hex>` entry for its exact relative path.
3. **`evidence.projection` must be `"declared"`.** Any other value —
   including the schema-reserved `"inferred"` — is a hard failure (see
   above).
4. **No evidence pack may be shared across targets when `"declared"`.** Two
   `targets[]` entries pointing their `evidence.path` at the *same* pack
   (regardless of whether that pack's translation units carry per-TU
   `target_id` tags) fails both — a pack shared across targets is exactly
   the unprojected, build-wide evidence the `"declared"` claim exists to
   rule out.
5. **A referenced pack's own identity must agree with the target using it.**
   If the pack's `manifest.library` is set, it must equal the referencing
   target's `id`; if any of the pack's translation units carry a
   `target_id` tag, it must name the referencing target too. A
   single-target pack whose translation units carry **no** `target_id` tags
   at all still passes — that's the ordinary output of a legacy Flow-2
   producer, not an integrity gap (see
   [Producing Source Facts](../use/producing-source-facts.md)).

None of these ever *downgrade* to a warning — every one is a hard,
non-zero-exit failure, matching the "fail-loud, no silent shallow success"
principle ADR-047 §11 states for every G30 validator.

### CLI

```console
$ abicheck project validate-build abicheck-build/
build-output validation: abicheck-build/
OK — no errors.

$ abicheck project validate-build abicheck-build/ --format json
{
  "root": "abicheck-build/",
  "ok": true,
  "errors": [],
  "warnings": []
}
```

Exit codes: `0` valid (warnings may still be present), `1` one or more
validation errors, `64` usage error (`DIRECTORY` is not a readable
`build-output.json`).

---

## The L3 compile-action record

The build/source pack's L3 layer stores one record per compile action; this
is the option record the L3 diff actually reads (moved here from
[Source & Build Data](../learn/build-source-data.md), which keeps the
narrative).

Each translation unit the build compiled becomes one `CompileUnit` record.
`abi_relevant_flags` is carried for provenance/localization, but it is **not**
what `build_diff.py` compares:

```json
{
  "id": "cu://src/money.cpp#cfg:9f3a2e",
  "source": "src/money.cpp",
  "compiler": "toolchain://gcc-14-cxx",
  "language": "CXX",
  "standard": "c++20",
  "defines": {"_GLIBCXX_USE_CXX11_ABI": "1", "NDEBUG": "1"},
  "include_paths": ["include/", "build/generated/"],
  "target_triple": "x86_64-linux-gnu",
  "abi_relevant_flags": ["-fvisibility=hidden", "-D_GLIBCXX_USE_CXX11_ABI=1"]
}
```

The actual L3 diff input is `BuildEvidence.build_options[]` — a separate,
flatter `BuildOption` record per canonical option key, which the adapters
*project* from `compile_units[].abi_relevant_flags` (and other sources):

```json
{
  "key": "define:_GLIBCXX_USE_CXX11_ABI",
  "value": "1",
  "abi_relevant": true,
  "scope": "global"
}
```

Comparing this record between old and new is exactly the "L3 build-flag delta"
table you see in a report: change `value` from `"1"` to `"0"` here and
`_diff_options()` emits `abi_relevant_build_flag_changed` — nothing about the
source itself needs to change for that finding to fire. **This is the record a
third-party/build-emitted producer must populate** (see
[Build-emitted facts](../learn/build-source-data.md#build-emitted-facts-the-abicheck_inputs-protocol-flow-2)
below): shipping only `compile_units[].abi_relevant_flags` without the
corresponding `build_options[]` entries silently drops L3 flag-drift detection,
because only the built-in adapters (CMake/Ninja/Bazel/Make) perform that
projection automatically.


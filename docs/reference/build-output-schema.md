# `build-output.json` Reference

`build-output.json` is a standardized, producer-agnostic contract for a
project's *existing* build to publish once — "build once, scan many"
(G30/ADR-047 §2). abicheck never owns the build: a project's own build
system, or an `install` step, populates an `abicheck-build/` directory that
downstream tooling then validates and consumes.

> **Status.** This page documents the schema and the
> `abicheck build-output validate` command shipped in G30 P1.1. The
> consumers that read a validated `build-output.json` to resolve a baseline
> or run a check (`resolve-baseline`, `check-target`) are G30 P1.2/P1.3,
> not built yet — see the
> [G30 plan](../development/plans/g30-github-actions-integration-model.md).
> There is also no `abicheck build-output emit` producer helper yet; author
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
manifest never aborts a load. `abicheck build-output validate` is what turns
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
| `path` | string | Path (relative to the `build-output.json` root) to the evidence — typically an `abicheck_inputs/` pack (see [Producing Source Facts](../user-guide/producing-source-facts.md)). |
| `projection` | string | `"declared"` or `"inferred"` — see below. **Only `"declared"` validates today.** |

**`projection` is the field the P1.1 validator gates on.** `"declared"`
means the build itself asserted this evidence pack belongs to exactly this
target (e.g. per-target compile-DB filtering, or a wrapper invoked once per
link step). `"inferred"` would mean abicheck derived the association from a
build-wide pack via TU→link-unit→DSO attribution — that attribution
mechanism is G30 P2, not built yet, so `abicheck build-output validate`
treats `"inferred"` (and any value other than `"declared"`) as a **hard
validation failure**, not a lower-confidence warning. Until P2 ships, a
build-wide evidence pack may only feed a build-wide source audit or a
per-target header-depth check — never a per-target `effective_depth: source`
claim (see the [multi-DSO recipe's scope
caveat](../user-guide/github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for the practical consequence of this rule).

## Validation rules

`abicheck build-output validate DIRECTORY` checks, per ADR-047 §11.1:

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
   [Producing Source Facts](../user-guide/producing-source-facts.md)).

None of these ever *downgrade* to a warning — every one is a hard,
non-zero-exit failure, matching the "fail-loud, no silent shallow success"
principle ADR-047 §11 states for every G30 validator.

### CLI

```console
$ abicheck build-output validate abicheck-build/
build-output validation: abicheck-build/
OK — no errors.

$ abicheck build-output validate abicheck-build/ --format json
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

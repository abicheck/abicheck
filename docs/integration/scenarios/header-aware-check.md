# Scenario S6: Header-Aware Compatibility

You have public headers for your library, not just the binary — and you want
that to matter: an internal symbol being removed is not the same finding as a
*public* one being removed, and a header-only change (an inline function body,
a default argument) can break API compatibility with no change to the binary
at all. [ADR-047](../../development/adr/047-github-actions-integration-model.md)
§8's S6 is "any" baseline channel plus one requirement: the check must
actually reach the header parse, not silently fall back to a binary-only
comparison.

## What you need

- The public header root(s) for both sides of the comparison (or one root, if
  it's identical on both sides).
- A C/C++ frontend — `castxml` (default) or `clang` (`--ast-frontend clang`).

## The check

Set `requested-depth: headers` (or the equivalent `--depth headers` on the
CLI/root Action) and point at your header root:

```yaml
- uses: abicheck/abicheck/actions/check-target@v1
  with:
    name: libfoo
    requested-depth: headers
    header: include/
    # ... baseline/candidate inputs per your channel, see the other scenarios ...
```

`headers` is one rung on abicheck's evidence-depth ladder
(`binary` → `headers` → `build` → `source`) — see
[Source-Scan Depth](../../user-guide/scan-levels.md#what-each-depth-reaches)
for exactly what each rung adds and what it needs, and
[Evidence & Detectability](../../concepts/evidence-and-detectability.md) for
the underlying model.

## Confirming the header parse actually ran

A `headers`-depth check's report carries `check_evidence_coverage` — read it,
don't assume the parse succeeded silently. A missing header path, a castxml
failure, or a frontend crash on a malformed header should surface as a
finding-driven signal in the report, not a silent fallback to whatever
`binary`-depth alone would have found. See the
[`check-target` report envelope](../../reference/check-target.md) for the
exact field.

## When to move past this scenario

- **You also have a compile database and want build-flag drift detected, or
  full sources and want body-level (inline/template/macro) changes caught**
  → step up to `build`/`source` depth — see
  [Source-Scan Depth](../../user-guide/scan-levels.md) and
  [S7: Source Scan via Compile-DB Replay](source-replay.md).
- **Your headers include generated (codegen) content that must be present
  for the parse to be meaningful** → S10,
  [Build Output Schema](../../reference/build-output-schema.md)'s
  `generated_header_roots` field.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Source-Scan Depth](../../user-guide/scan-levels.md) — the canonical depth-ladder reference.
- [`check-target` Action Reference](../../reference/check-target.md) — the full report envelope.

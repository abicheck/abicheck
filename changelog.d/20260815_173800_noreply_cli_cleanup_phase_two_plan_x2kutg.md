<!-- Codex/CodeRabbit review follow-up round 5 on CLI cleanup phase two, PR 1/PR 2. -->

### Fixed

- **`abicheck.service.render_output(fmt="markdown", ..., show_recommendation=False)`
  now actually suppresses the recommendation section again.** An earlier
  compatibility-shim revision hard-coded `True` into the underlying
  `to_markdown()` call regardless of what the caller passed, silently
  reintroducing the recommendation for a direct Tier-2 caller that
  explicitly asked it be suppressed. The CLI's own wrapper never passes
  this keyword, so its default (`True`) and therefore its own output are
  unaffected.
- **`compare --profile quick`'s printed one-line count no longer disagrees
  with its own exit code under `--show-only`.** The scoped-only/
  missing-contract findings that decide the count were filtered by
  `--show-only` for display purposes (correct elsewhere), but the exit
  code itself never was — so a purely-breaking synthesized scoped finding
  (e.g. `PE_ORDINAL_RETARGETED`) excluded by `--show-only` could exit
  non-zero while printing `no changes (0 total)`. The count now always
  reflects the same unfiltered set the exit code already used.
- `aggregate_manifest_version` validation now requires exactly two
  dot-separated numeric components — `"2"`, `"2.x"`, and `"2.0.1"`
  previously passed validation despite the manifest contract requiring
  `MAJOR.MINOR`, since only the prefix before the first `.` was ever
  inspected.
- A directly-constructed `RunPlan` (not read from JSON) with a bogus gate
  value now fails validation at `to_dict()`/`to_aggregate_manifest()` time,
  instead of silently serializing an artifact this tool's own reader would
  later reject.
- `effective_policy.source`'s new `"explicit"` value (added in the previous
  round) is now listed in the published JSON Schema's enum and description,
  and in the exit-codes/aggregate-reports docs.
- Minor documentation clarifications: `exit-codes.md`'s exit-`1` row now
  states the `missing_required` policy explicitly rather than implying it's
  the only axis; `run-plan-schema.md` now names a missing `schema` field
  alongside a declared `v1`/malformed one as rejected; the
  `github-action-recipes.md` gate tip now states the required manifest
  version bump alongside the `gate` block it recommends.

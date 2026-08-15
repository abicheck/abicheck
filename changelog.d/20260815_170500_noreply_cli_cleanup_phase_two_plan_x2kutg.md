<!-- Codex review follow-up round 2 on CLI cleanup phase two, PR 2/PR 1. -->

### Fixed

- **`abicheck.service.render_output(fmt="json", ..., stat=True)` now
  preserves the pre-existing `--stat --format json` JSON shape again.** An
  earlier compatibility-shim fix collapsed the `stat=True` case onto the
  human one-line renderer regardless of `fmt`, so a JSON caller feeding the
  result to `json.loads()` would get plain text instead. `stat=True` now
  dispatches by *fmt*: `to_stat_json`'s summary-only JSON object (no
  `changes` array) for `fmt="json"`, the human one-line renderer for every
  other *fmt* (unchanged, and equivalent to `fmt=ONELINE_FORMAT`).
- **`aggregate`'s manifest `gate` block is now rejected outright when the
  same manifest explicitly declares a pre-2.0 `aggregate_manifest_version`.**
  Honoring `gate` on such a manifest would recreate the exact version-skew
  inversion the `2.0` MAJOR bump exists to prevent (an old reader silently
  falling back to the hard-coded default policy), just moved to a manifest
  that lies about its own version instead of an old reader misreading a
  correctly-versioned one.
- **`run-plan.json`'s `gate` block now requires the run-plan `schema`
  discriminator to be bumped to `abicheck.run-plan/v2`.** A plan generated
  with an explicit `--gate-missing-required`/`--gate-unexpected-target`
  policy previously kept the unchanged `abicheck.run-plan/v1` schema, so an
  old, pre-gate `RunPlan.from_dict()` would silently ignore the unknown
  `gate` key and project a `1.0` aggregate manifest applying the hard-coded
  default policy instead of what the plan actually asked for. `project
  plan` now stamps `abicheck.run-plan/v2` whenever `gate` is present (a
  gate-less plan is unaffected); a hand-crafted `gate` block paired with a
  declared `v1` (or missing) schema is now a loud, rejected usage error
  instead of a silent misapplication.
